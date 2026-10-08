# -*- coding: utf-8 -*-
"""后台任务管理：串行队列 + 进度/日志 + 落盘留档。

四条与「出了事查不到」直接相关的设计，改动前先读懂：

1. **日志游标是绝对的**。内存里只留最近 `MEM_LOGS` 行（超了丢最旧的 `TRIM_STEP` 行），
   但对外暴露的 `log_next` 是「这个任务从头到现在一共产生了多少行」。
   以前 `log_next = len(logs)`，一旦触发裁剪它就会往回跳，而前端游标只增不减 ——
   `logs[log_from:]` 从此永远返回空，界面上表现为「任务还在跑，日志却再也不更新」。
   绝对游标 + `log_base` 偏移解决这个。

2. **日志落盘** `data/jobs/<id>.log`，行缓冲逐行追加，进程被杀也不丢。
   以前任务表全在内存、进程重启即丢 —— 一次跑了三小时的任务，重启后什么都查不到。

3. **结束的任务不会立刻消失**。`queue_state()` 除 running/queued 之外还给 `recent`
   （done/error/cancelled，24 小时内、未被手动关闭的）。以前任务一取消就同时从两个
   列表里消失，前端队列卡片被整个清空，跑了三小时的日志和进度一瞬间无处可寻。

4. **进程重启后历史仍在**。启动时从 `data/jobs/*.json` 恢复最近的任务元信息；
   重启前还在 running/queued 的一律标成 `interrupted`（中断），因为它们确实没跑完。
"""
import os, io, json, glob, threading, uuid, time, traceback

from .paths import DATA_DIR

LOG_DIR = os.path.join(DATA_DIR, "jobs")
os.makedirs(LOG_DIR, exist_ok=True)

MEM_LOGS = 3000          # 内存里最多留多少行
TRIM_STEP = 1000         # 超了一次丢多少行
# 队列区只挂「刚跑完的」——它是操作区，不是档案馆。更早的全部去「任务日志」抽屉里翻，
# 否则首屏会被十几张已结束卡片顶掉（实测 7 张就把流水线状态推出了一屏）。
RECENT_HOURS = 2         # 结束的任务在队列区再挂多久
RECENT_MAX = 4           # 最多同时挂几张"已结束"卡片
KEEP_FILES = 200         # 磁盘上保留多少个任务的留档
RESTORE_MAX = 60         # 启动时恢复多少条历史任务

LIVE = ("running", "queued")
FINAL = ("done", "error", "cancelled", "interrupted")


def fmt_duration(sec):
    """秒 → 人能读的时长。ETA 用。"""
    if sec is None or sec < 0:
        return ""
    sec = int(sec)
    if sec < 60:
        return f"{sec} 秒"
    if sec < 3600:
        return f"{sec // 60} 分 {sec % 60} 秒"
    return f"{sec // 3600} 小时 {(sec % 3600) // 60} 分"


class Job:
    def __init__(self, kind, title, meta=None, jid=None, restored=False):
        self.id = jid or uuid.uuid4().hex[:12]
        self.kind = kind
        self.title = title
        self.status = "running"      # running | queued | done | error | cancelled | interrupted
        self.progress = 0            # 0-100
        self.done = 0
        self.total = 0
        self.logs = []               # 内存里的尾部窗口
        self.log_base = 0            # 已经滚出内存的行数 —— 绝对游标的偏移
        self.result = None
        self.error = None
        self.stats = {}              # 实时计数器 {名称: 数值}
        self.current = ""            # 当前正在处理的条目
        self.position = 0            # 排队位置（0 = 正在跑）
        self.meta = dict(meta or {})  # {steps:[...], companies:[...]} —— 供前端判重
        self.created = time.time()
        self.started = 0.0
        self.finished = 0.0
        self.dismissed = False
        self.restored = restored     # 从磁盘恢复的（内存里没有完整上下文）
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._fh = None              # 日志文件句柄（行缓冲）

    # ---------- 落盘 ----------
    @property
    def log_path(self):
        return os.path.join(LOG_DIR, self.id + ".log")

    @property
    def meta_path(self):
        return os.path.join(LOG_DIR, self.id + ".json")

    def _write_line(self, line):
        """行缓冲追加。进程被 kill 也只会丢正在写的那一行。"""
        try:
            if self._fh is None:
                self._fh = open(self.log_path, "a", encoding="utf-8", buffering=1)
            self._fh.write(line + "\n")
        except Exception:
            self._fh = None          # 写不了就算了，绝不因为记日志而让任务失败

    def close_log(self):
        try:
            if self._fh:
                self._fh.close()
        except Exception:
            pass
        self._fh = None

    def persist(self):
        """把元信息快照写盘。任务状态每变一次调一次，重启后据此恢复。"""
        try:
            with self._lock:
                d = {"id": self.id, "kind": self.kind, "title": self.title,
                     "status": self.status, "progress": self.progress,
                     "done": self.done, "total": self.total, "stats": dict(self.stats),
                     "error": self.error, "meta": self.meta,
                     "created": self.created, "started": self.started,
                     "finished": self.finished, "dismissed": self.dismissed,
                     "lines": self.log_base + len(self.logs)}
                try:
                    blob = json.dumps({**d, "result": self.result},
                                      ensure_ascii=False, default=str)
                    if len(blob) > 2_000_000:      # 结果太大就不留了，日志才是查问题的依据
                        raise ValueError("too big")
                except Exception:
                    blob = json.dumps({**d, "result": None}, ensure_ascii=False, default=str)
            tmp = self.meta_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(blob)
            os.replace(tmp, self.meta_path)
        except Exception:
            pass

    # ---------- 运行时 ----------
    def set_current(self, msg):
        with self._lock:
            self.current = msg

    def bump(self, key, n=1):
        with self._lock:
            self.stats[key] = self.stats.get(key, 0) + n

    def set_stat(self, key, v):
        with self._lock:
            self.stats[key] = v

    def log(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        with self._lock:
            self.logs.append(line)
            if len(self.logs) > MEM_LOGS:
                del self.logs[:TRIM_STEP]
                self.log_base += TRIM_STEP     # ← 绝对游标的关键：丢多少就往前推多少
            self._write_line(line)

    def step(self, done=None, total=None):
        with self._lock:
            if total is not None:
                self.total = total
            if done is not None:
                self.done = done
            if self.total:
                self.progress = int(self.done * 100 / self.total)

    @property
    def cancelled(self):
        return self._cancel.is_set()

    def eta(self):
        """按已完成条目的平均耗时外推，返回剩余秒数（估不出来就 None）。
        跑了不到 10 秒或还没完成 3 条时不给数 —— 那时候的外推纯属瞎猜。"""
        if self.status != "running" or not self.total or self.done < 3:
            return None
        el = time.time() - (self.started or self.created)
        if el < 10:
            return None
        left = self.total - self.done
        return int(el / self.done * left) if left > 0 else 0

    def to_dict(self, log_from=0, light=False):
        """log_from 是**绝对**行号（前端拿到的 log_next 原样传回来即可）。

        light=True：列表页（「任务日志」抽屉）只要元信息 —— 每行日志可能上万行，
        以前 `/api/jobs` 对 80 条任务逐个整段拷出来、main.py 再 pop 掉，纯白做。
        """
        with self._lock:
            i = log_from - self.log_base
            gap = ""
            if i < 0:                      # 客户端落后于内存窗口：坦白说明，别假装连续
                gap = (f"…（前 {self.log_base} 行已滚出内存窗口，"
                       f"完整日志见「下载日志」）")
                i = 0
            logs = [] if light else (self.logs[i:] if i <= len(self.logs) else [])
            if gap and not light:
                logs = [gap] + logs
            return {"id": self.id, "kind": self.kind, "title": self.title,
                    "status": self.status, "progress": self.progress,
                    "position": self.position,
                    "done": self.done, "total": self.total,
                    "stats": dict(self.stats), "current": self.current,
                    "logs": logs, "log_next": self.log_base + len(self.logs),
                    "log_total": self.log_base + len(self.logs),
                    "eta": self.eta(), "eta_text": fmt_duration(self.eta()),
                    "meta": self.meta, "restored": self.restored,
                    "created": self.created, "started": self.started,
                    "finished": self.finished,
                    "elapsed": int((self.finished or time.time())
                                   - (self.started or self.created)),
                    "result": None if light else self.result, "error": self.error}


class JobManager:
    """任务表 + 一条串行队列。

    队列的由来：写盘的几个步骤（映射/清洗/复查/id映射）必须互斥，它们写的是同一棵输出树。
    最早没有任何闸门，连点两次就是两个线程互相覆盖；后来加了闸门直接 409 拒绝，
    但总览是"按状态分档批量执行"的用法 —— 用户很自然会把几个档位都点一遍然后去干别的，
    每次都被拒绝很烦。所以改成排队：点了就进队，依次跑，界面显示排在第几个。
    """

    def __init__(self):
        self.jobs = {}
        self._lock = threading.Lock()
        self._queue = []            # [(job, fn, args, kwargs, on_done)]
        self._draining = False
        self._restore()

    # ---------- 启动时恢复历史 ----------
    def _restore(self):
        """把磁盘上的任务留档读回来，让「重启后什么都查不到」不再发生。

        重启前还在 running/queued 的一律标成 interrupted —— 进程都没了，
        它们当然没跑完，标成 done 会骗人。
        """
        try:
            metas = sorted(glob.glob(os.path.join(LOG_DIR, "*.json")),
                           key=os.path.getmtime, reverse=True)
        except Exception:
            return
        self._prune(metas)
        for p in metas[:RESTORE_MAX]:
            try:
                d = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            jid = d.get("id")
            if not jid:
                continue
            j = Job(d.get("kind", ""), d.get("title", ""), d.get("meta"),
                    jid=jid, restored=True)
            st = d.get("status", "done")
            j.status = "interrupted" if st in LIVE else st
            for k in ("progress", "done", "total", "error", "created",
                      "started", "finished", "dismissed"):
                if d.get(k) is not None:
                    setattr(j, k, d[k])
            j.stats = d.get("stats") or {}
            j.result = d.get("result")
            if not j.finished:
                j.finished = d.get("created", time.time())
            j.logs, j.log_base = self._tail(j.log_path)
            self.jobs[jid] = j

    @staticmethod
    def _tail(path, n=MEM_LOGS):
        """读日志文件尾部 n 行，返回 (行列表, 前面丢掉了多少行)。"""
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except Exception:
            return [], 0
        if len(lines) <= n:
            return lines, 0
        return lines[-n:], len(lines) - n

    @staticmethod
    def _prune(metas):
        """只保留最近 KEEP_FILES 个任务的留档，别让 data/jobs 无限长。"""
        for p in metas[KEEP_FILES:]:
            for q in (p, p[:-5] + ".log"):
                try:
                    os.remove(q)
                except OSError:
                    pass

    # ---------- 串行队列 ----------
    def submit(self, kind, title, fn, *args, on_done=None, meta=None, **kwargs):
        """排队执行。队列空闲时等同于立即执行。"""
        job = Job(kind, title, meta)
        job.status = "queued"
        with self._lock:
            self.jobs[job.id] = job
            self._queue.append((job, fn, args, kwargs, on_done))
            job.position = len(self._queue) - (0 if self._draining else 1)
            start = not self._draining
            if start:
                self._draining = True
        job.log(f"已排入队列（{'立即执行' if start else f'前面还有 {job.position} 个任务'}）")
        job.persist()
        if start:
            threading.Thread(target=self._drain, daemon=True).start()
        return job

    def _drain(self):
        while True:
            with self._lock:
                if not self._queue:
                    self._draining = False
                    return
                job, fn, args, kwargs, on_done = self._queue.pop(0)
                # ⚠ 状态必须在**同一把锁里**就置成 running。否则 cancel() 的"排队期间取消"
                # 分支会在这条缝里判错：它看到 status 还是 queued，就按"没轮到的任务"处理，
                # 于是和下面那段 `if job.cancelled` 兜底各写一次终态 —— 日志里出现两条
                # "排队期间被取消"，状态也被写两遍。
                job.status = "running"
                for i, (j, *_) in enumerate(self._queue):
                    j.position = i + 1
            if job.cancelled:          # 排队期间被取消
                job.status = "cancelled"
                job.finished = time.time()
                job.log("排队期间被取消，未执行")
                job.close_log()
                job.persist()
                if on_done:
                    try:
                        on_done(job)
                    except Exception:
                        pass
                continue
            job.position = 0
            job.started = time.time()
            job.persist()
            try:
                self._execute(job, fn, args, kwargs, on_done)
            except Exception:
                # _execute 内部已兜住异常；这里再兜一层，绝不让 drain 线程死掉，
                # 否则 _draining 永远是 True，整条队列从此再也不动。
                job.status = "error"
                job.persist()

    def queue_state(self):
        """在跑的 + 排队的 + 最近结束的。

        `recent` 是这次故障暴露出来的缺口：任务一旦 cancelled/error 就同时从
        running 和 queued 里消失，前端据此把整张队列卡片清空 —— 三小时的日志、
        进度、计数器一瞬间无处可寻。结束的任务要在原地留一会儿。
        """
        with self._lock:
            q = [{"id": j.id, "kind": j.kind, "title": j.title,
                  "position": j.position, "status": j.status, "meta": j.meta}
                 for j, *_ in self._queue]
            snapshot = list(self.jobs.values())
        run, recent = [], []
        cut = time.time() - RECENT_HOURS * 3600
        for j in snapshot:
            # light=True：队列卡片不显示日志正文（日志是前端按 id 另外增量拉的，
            # 见 overview.js 的 pullLog），而 /api/queue 是每 1.2 秒轮询一次的接口 ——
            # 一个 3000 行的任务就能让每次轮询多背几十 KB。`meta` 照给，
            # 前端的重复提示 dupWarn 靠它。
            if j.status == "running":
                run.append(j.to_dict(0, light=True))
            elif j.status in FINAL and not j.dismissed and (j.finished or 0) >= cut:
                recent.append(j.to_dict(0, light=True))
        recent.sort(key=lambda x: -(x["finished"] or 0))
        return {"running": run, "queued": q, "recent": recent[:RECENT_MAX]}

    def start(self, kind, title, fn, *args, on_done=None, meta=None, **kwargs):
        """不排队，立即起线程（只给 overview / export 这类只读任务用）。

        on_done(job) 在任务线程里、任务结束后调用（成功/失败/取消都会调）。
        用于把「跑完了要顺手更新总览缓存」这类收尾挂上去 —— 放在这里而不是各业务模块里，
        是为了避开 overview → pipeline → consolidate 的循环导入。
        钩子自身抛错只记日志，绝不影响任务本身的结论。"""
        job = Job(kind, title, meta)
        job.started = time.time()
        with self._lock:
            self.jobs[job.id] = job
        job.persist()
        threading.Thread(target=lambda: self._execute(job, fn, args, kwargs, on_done),
                         daemon=True).start()
        return job

    def _execute(self, job, fn, args, kwargs, on_done):
        """真正跑一个任务。start（立即）与 submit（排队）共用，收尾语义只有这一处。

        ⚠ **收尾钩子跑完才对外宣布结束**。以前是先把 status 置成 done、再跑 on_done，
        而 on_done 正是"把这几家公司的状态刷进总览缓存"——前端一看到任务结束就去重新拉
        总览，拿到的还是刷新前的快照，于是"跑完了状态却没变"。刷新要几秒，这几秒里
        进度条停在 100% 并显示「正在更新状态」，比给出一个过期的结论要诚实。
        """
        if not job.started:
            job.started = time.time()
        try:
            job.result = fn(job, *args, **kwargs)
            final = "cancelled" if job.cancelled else "done"
            if final == "done":
                job.progress = 100
            else:
                job.log(f"任务已取消（已完成 {job.done}/{job.total}）")
        except Exception as e:
            final = "error"
            job.error = f"{e!r}"
            job.log("ERROR: " + traceback.format_exc()[-2000:])
        if on_done:
            job.set_current("正在更新总览状态 …")
            try:
                on_done(job)
            except Exception as e:
                job.log(f"⚠ 收尾钩子失败（不影响本次结论）: {e!r}")
        job.set_current("")
        job.finished = time.time()
        job.status = final
        job.log(f"任务结束：{final}，耗时 {fmt_duration(job.finished - (job.started or job.created))}")
        job.close_log()
        job.persist()

    def get(self, jid):
        return self.jobs.get(jid)

    def read_log(self, jid):
        """完整日志原文（优先读盘，读不到退回内存窗口）。供「下载日志」用。"""
        j = self.jobs.get(jid)
        if not j:
            return None
        try:
            if os.path.isfile(j.log_path):
                return open(j.log_path, encoding="utf-8", errors="replace").read()
        except Exception:
            pass
        with j._lock:
            return "\n".join(j.logs)

    def remove(self, jid):
        """彻底删掉一个任务：内存记录 + 磁盘上的 .log / .json。正在跑的拒绝。"""
        j = self.jobs.get(jid)
        if not j:
            return False, "任务不存在"
        if j.status in LIVE:
            return False, "任务还在运行或排队中，先取消它"
        j.close_log()
        for p in (j.log_path, j.meta_path):
            try:
                os.remove(p)
            except OSError:
                pass
        with self._lock:
            self.jobs.pop(jid, None)
        return True, ""

    def purge(self):
        """清空全部已结束的任务记录（在跑的和排队的原样保留）。"""
        with self._lock:
            ids = [j.id for j in self.jobs.values() if j.status not in LIVE]
        return sum(1 for jid in ids if self.remove(jid)[0])

    def dismiss(self, jid):
        """把一张"已结束"卡片从队列区收起（记录仍在，随时能从历史里翻出来）。"""
        j = self.jobs.get(jid)
        if not j or j.status not in FINAL:
            return False
        j.dismissed = True
        j.persist()
        return True

    def cancel(self, jid):
        j = self.jobs.get(jid)
        if not j:
            return False
        if j.status == "queued":
            # 还没轮到就取消：直接从队列里摘掉，别等它跑起来再打断
            with self._lock:
                still = any(x[0].id == jid for x in self._queue)
                self._queue = [x for x in self._queue if x[0].id != jid]
                for i, (q, *_) in enumerate(self._queue):
                    q.position = i + 1
            j._cancel.set()
            if not still:
                # 已经被 _drain 取走了（它在锁内就把状态改成 running）。上面那句 _cancel.set()
                # 正好赶在它检查 cancelled 之前 —— 终态化交给 _drain 那条路，这里不再写一遍，
                # 否则两条路径各写一次状态与日志。
                return True
            j.status = "cancelled"
            j.finished = time.time()
            j.log("排队期间被取消，未执行")
            j.close_log()
            j.persist()
            return True
        if j.status == "running":
            j._cancel.set()
            j.log("⚠ 收到取消请求，正在停在最近一个安全点（已在跑的 AI 调用会先返回）…")
            return True
        return False

    def list(self, limit=50, light=False):
        """light=True 只给元信息（不拷日志正文、不带 result）——「任务日志」抽屉用。"""
        with self._lock:
            snapshot = list(self.jobs.values())
        snapshot.sort(key=lambda j: -(j.finished or j.started or j.created))
        return [j.to_dict(0, light) for j in snapshot[:limit]]

    def running_in(self, kinds):
        """这些 kind 里正在跑的第一个任务（没有则 None）。

        用来做重入闸门：以前没有任何一层拦得住「连点两次运行」或「两个标签页同时跑」，
        那就是两个线程同时写同一棵输出树 —— 在一个以数据零丢失为最高约束的产品里，
        这是唯一一处没有防线的位置。"""
        with self._lock:
            snapshot = list(self.jobs.values())
        for j in snapshot:
            if j.kind in kinds and j.status == "running":
                return j
        return None

    def latest(self, kind):
        """该类型最近一个任务（未结束的优先，其次最近创建的）。"""
        with self._lock:
            js = [j for j in self.jobs.values() if j.kind == kind]
        if not js:
            return None
        live = [j for j in js if j.status in LIVE]
        pool = live or js
        return max(pool, key=lambda j: j.created)


manager = JobManager()
