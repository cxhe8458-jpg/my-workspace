# -*- coding: utf-8 -*-
"""产品数据扫描：启动后台预扫进内存索引，之后所有请求只读内存。

硬约束：产品数据在 /mnt/d（9p 挂载），每次系统调用约 11ms，是原生盘的 110 倍。
一个 273 个产品的公司走一遍目录树要好几秒——**绝不允许在请求路径里遍历目录**。
索引只在这三种时机更新：启动预扫、显式刷新、单产品改动后就地重扫。
"""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import paths, store

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
SCAN_WORKERS = 8          # /mnt/d 瓶颈是每次调用的延迟而非带宽，并行提速明显
RESCAN_INTERVAL = 900     # 后台自动重扫周期（秒）

_index = {}               # company -> [product]
_index_lock = threading.Lock()
_scan_locks = {}          # company -> Lock，同公司并发扫描去重


def _company_lock(company):
    with _index_lock:
        return _scan_locks.setdefault(company, threading.Lock())


# ---------- 公司根目录：库内 or 已归档 ----------
# 「哪些公司在库内、哪些已归档」这组判断原本每次调用都去 /mnt/d 上 stat 一遍。
# 实测 108 家公司光 is_archived 就要 0.65 秒，而 company_summary 每家会调它两次，
# /api/companies 因此要 1.8~2.3 秒；首扫期间前端每 2.5 秒轮询一次这个接口，
# 请求首尾相接，还和后台扫描抢同一个 9p 挂载 —— 界面就"卡住"了。
# 这里缓存 5 秒。目录增减本来就靠刷新/后台扫描发现，5 秒的滞后没有影响；
# 归档、撤回这类自己造成的变化一律显式作废缓存。
_DIRSTATE_TTL = 5.0
_dirstate = {"time": 0.0, "lib": frozenset(), "arch": {}}
_dirstate_lock = threading.Lock()


def invalidate_dirs():
    """自己动过目录结构后调用，让下次读取立刻重新探测。"""
    with _dirstate_lock:
        _dirstate["time"] = 0.0


def _has_any_file(p: Path) -> bool:
    """目录里还有没有真正的文件（只剩空目录 = 归档没删净的壳，不算数据）。"""
    try:
        return any(f.is_file() for f in p.rglob("*"))
    except OSError:
        return True          # 读不动就按"有数据"处理，宁可保守别误判成空壳


def _dir_state():
    """返回 (库内公司集合, {已归档公司: 目标目录})。"""
    with _dirstate_lock:
        if time.time() - _dirstate["time"] < _DIRSTATE_TTL:
            return _dirstate["lib"], _dirstate["arch"]
    d = paths.data_dir()
    lib = set(x.name for x in d.iterdir() if x.is_dir()) if d.exists() else set()
    arch = {}
    for e in store.load_ledger():
        c = e.get("company")
        if not c or c in arch:
            continue
        if c in lib:
            # 库内存在就一定不算归档（数据被重新抓回来时以库内那份为准）——
            # **但空壳不算数**。归档是"移动整个文件夹"，drvfs 上 rmtree 常被
            # Windows 句柄挡住，留下几个一个文件都没有的空目录（实测连手工 rmdir
            # 都是 Permission denied，得等对方放手）。那种残渣不是"重新抓回来的数据"，
            # 却足以让这家公司永远赖在审核列表上、且台账页天天报「库内又有同名文件夹」。
            # 判空会走一遍目录树，但只对**台账里有、库内又有**的公司做，正常是 0 家；
            # 真有数据时 any() 撞到第一个文件就短路，不会付全树的代价。
            if _has_any_file(d / c):
                continue
            lib.discard(c)
        t = Path(e.get("target", ""))
        # 台账里有一批历史条目的目标目录早被下游流程搬走/改名了，这些公司既没有
        # 产品也打不开，混进审核列表只会变成一堆空卡片。它们仍留在台账里可查。
        if t.is_dir():
            arch[c] = t
    lib = frozenset(lib)
    with _dirstate_lock:
        _dirstate.update({"time": time.time(), "lib": lib, "arch": arch})
    return lib, arch


def list_companies():
    return sorted(_dir_state()[0])


def company_root(company: str):
    """优先库内；不在库内则查归档台账（已归档公司支持只读复查）。"""
    lib, arch = _dir_state()
    if company in lib:
        return paths.data_dir() / company
    return arch.get(company)


def is_archived(company: str) -> bool:
    lib, arch = _dir_state()
    return company not in lib and company in arch


def archived_companies():
    """已归档且**目录还在**的公司。"""
    return sorted(_dir_state()[1])


def known_companies():
    """所有能真正打开的公司：库内 + 已归档且目录还在。"""
    lib, arch = _dir_state()
    return sorted(set(lib) | set(arch))


# ---------- 参数形态 ----------

def _is_records(v):
    """值是否是「记录式」参数列表：[{name, value, ...}, ...]（company-product-records 新格式）。
    判据取 name+value 两个必备字段，避免把其它 list[dict] 误判成记录式。"""
    return (isinstance(v, list) and len(v) > 0
            and all(isinstance(x, dict) and "name" in x and "value" in x for x in v))


def classify_params(p):
    """flat 扁平键值 / nested 多型号或分组 / records 记录式 / images 纯参数图 / empty。

    「参数图」是图片引用键而非参数项，常与真参数混在同一个 dict 里。
    必须先把它剥掉再判断，否则有参数表的产品会被当成纯图片形态、参数表整个不显示。

    records = {型号名或表名: [{group,name,symbol,value,unit,conditions}, ...]}（2026-09 起爬虫的新格式，
    与 nested 的区别是**值是一串「一条参数一条记录」的 dict**，前端要按六列表渲染而不是键值表）。
    """
    if not p:
        return "empty"
    if isinstance(p, list):
        # 整个「参数信息」就是一串参数图文件名（凯普林等站点，实测 12 个产品）。
        # 不能落到 flat：前端对 flat 走 Object.entries()，数组下标会被当成参数名，
        # 界面上渲染出「0 | 参数图_1.jpg」这样一张根本不存在的参数表，
        # 而真正该提示的"参数以图片呈现，见下方参数图"反倒不显示。
        if all(isinstance(x, str) and Path(x).suffix.lower() in IMG_EXTS for x in p):
            return "images"
        return "flat"
    if not isinstance(p, dict):
        return "flat"
    rest = {k: v for k, v in p.items() if k != "参数图"}
    if not rest:
        return "images" if isinstance(p.get("参数图"), list) else "empty"
    if any(_is_records(v) for v in rest.values()):
        return "records"
    if any(isinstance(v, (dict, list)) for v in rest.values()):
        return "nested"
    return "flat"


def _read_info(f: Path):
    try:
        return json.loads(f.read_text(encoding="utf-8")), None
    except Exception as e:
        return None, str(e)


def _build_product(rel_path: str, d: Path, filenames):
    info, err = _read_info(d / "info.json")
    info = info or {}
    main_imgs, param_imgs, pdfs, htmls = [], [], [], []
    for fn in sorted(filenames):
        ext = Path(fn).suffix.lower()
        if ext in IMG_EXTS:
            # 分类完全由文件名前缀决定，和上一代保持一致
            if fn.startswith("主图"):
                main_imgs.append(fn)
            elif fn.startswith("参数"):
                param_imgs.append(fn)
            else:
                main_imgs.append(fn)
        elif ext == ".pdf":
            pdfs.append(fn)
        elif ext in (".html", ".htm"):
            htmls.append(fn)
    params = info.get("参数信息", {})
    return {
        "path": rel_path,
        "name": d.name,
        "categories": list(Path(rel_path).parts[1:-1]),
        "url": info.get("页面URL", ""),
        "manual_url": info.get("手册URL", ""),
        "main_url": info.get("主图URL", ""),
        "feature": info.get("产品特性") or info.get("产品特征") or "",
        "parent": info.get("上级名称") or info.get("所属大类") or "",
        "params": params,
        "param_type": classify_params(params),
        "main_images": main_imgs,
        "param_images": param_imgs,
        "pdfs": pdfs,
        "htmls": htmls,          # 新增：HTML 自动检测，仅展示不阻断
        "has_html": bool(htmls),
        "info_error": err,
    }


# ---------- info.json 里"其余字段"的展示契约 ----------
# 爬虫往 info.json 写的键远不止参数与图片：实测 288 家公司里，产品描述与产品应用
# 各占 85%、产品特点占 27.5%，过去**一个都没在界面上出现过**——审核时看不见，
# 也就无从核对描述是否张冠李戴、应用是否漏采。
#
# 原则是**兜底展示**：凡是没在界面别处露过面的键一律进「产品信息」面板，
# 所以新站点写进来的新键（实测已有 二级分类 / 所属细分类 / 产品参数 / PDF链接 等）
# 不用改代码也能看见。反过来说，这里**只能删不能漏**——要加键就加进 INFO_SURFACED。

# 已经在界面别处展示过的键，不在本面板重复列出
INFO_SURFACED = {
    "页面URL",      # 产品标题下方的官网链接
    "手册URL",      # PDF 面板
    "手册文件",     # PDF 面板（文件本身）
    "主图",         # 主图面板（文件本身）
    "参数信息",     # 参数面板
}

# 展示顺序：产品名（只在与目录名不一致时出现）→ 正文字段 → 分类 → 杂项。
# 不在这张表里的键按 info.json 原始顺序排在最后，绝不丢弃。
INFO_ORDER = ["产品名", "产品描述", "产品特性", "产品特征", "产品特点", "产品应用",
              "所属大类", "上级名称", "二级分类", "所属细分类",
              "产品参数", "产品图", "主图URL", "PDF链接"]

# "爬虫本该采到"的正文字段。整个键都不存在时要明说：
# 「字段为空」和「压根没这个键」对审核是两回事——后者通常是解析器没写这一段。
INFO_CORE = ["产品描述", "产品应用"]


def _is_empty(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, dict, tuple)):
        return not v
    return False


def extra_info(rel_path: str, name: str = "") -> dict:
    """读单个产品的 info.json，挑出「界面别处没展示过」的字段。

    ⚠ 这里直接读了 /mnt/d 上的文件，是**唯一**一处这么做的常规请求路径。它不违反
    "绝不在请求路径里遍历目录"那条铁律：读的是一个已知路径的单个文件（约 11ms），
    且只在有人点开某个产品时发生一次，代价可以忽略。

    刻意**不**放进内存索引：产品描述最长 1225 字，31424 个产品全塞进去会让那份
    47MB 的索引快照再涨一大截，而公司列表与产品树根本用不到这些正文；
    快照结构变了还会让所有老快照失效，重启后要冷扫好几分钟（正是快照要避免的事）。
    """
    d = safe_dir(rel_path)
    if not d:
        return {"fields": [], "missing": [], "error": None}
    info, err = _read_info(d / "info.json")
    if not isinstance(info, dict):
        return {"fields": [], "missing": [], "error": err}
    keys = [k for k in INFO_ORDER if k in info]
    keys += [k for k in info if k not in INFO_ORDER and k not in INFO_SURFACED]
    fields = []
    for k in keys:
        v = info[k]
        # 产品名与目录名一致时没必要占一行；不一致才是要看的信号（爬虫串行/改过名）
        if k == "产品名" and str(v).strip() == (name or Path(rel_path).name):
            continue
        fields.append({"key": k, "value": v, "empty": _is_empty(v)})
    return {"fields": fields,
            "missing": [k for k in INFO_CORE if k not in info],
            "error": err}


def _do_scan(company: str):
    root = company_root(company)
    if root is None:
        return []
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        if "info.json" not in filenames:
            continue
        d = Path(dirpath)
        rel = (Path(company) / d.relative_to(root)).as_posix()
        out.append(_build_product(rel, d, filenames))
    out.sort(key=lambda p: p["path"])
    return out


def scan_company(company: str, force=False):
    if not force:
        with _index_lock:
            if company in _index:
                return _index[company]
    with _company_lock(company):
        if not force:                      # 等锁期间别的线程可能已经扫完
            with _index_lock:
                if company in _index:
                    return _index[company]
        products = _do_scan(company)       # 耗时段不持有 _index_lock
        with _index_lock:
            _index[company] = products
    return products


def company_ready(company: str) -> bool:
    with _index_lock:
        return company in _index


def refresh_company(company: str):
    """强制重扫，返回 (products, 新增数, 内容变化数)。"""
    with _index_lock:
        old = {p["path"]: p for p in _index.get(company, [])}
    products = scan_company(company, force=True)
    if not old:
        return products, 0, 0
    new = sum(1 for p in products if p["path"] not in old)
    changed = sum(1 for p in products if p["path"] in old and old[p["path"]] != p)
    return products, new, changed


def refresh_all():
    diffs = {}
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as ex:
        futs = {ex.submit(refresh_company, c): c for c in list_companies()}
        for fut, c in futs.items():
            try:
                _, n, m = fut.result()
                diffs[c] = {"new": n, "changed": m}
            except Exception:
                diffs[c] = {"new": 0, "changed": 0}
    save_snapshot()
    return diffs


def get_product(rel_path: str):
    parts = Path(rel_path).parts
    if not parts:
        return None
    for p in scan_company(parts[0]):
        if p["path"] == rel_path:
            return p
    return None


def rescan_product(rel_path: str):
    """单产品改动后就地重扫并更新索引，避免整公司重扫。"""
    d = safe_dir(rel_path)
    if not d:
        return None
    prod = _build_product(rel_path, d, [x.name for x in d.iterdir() if x.is_file()])
    company = Path(rel_path).parts[0]
    with _index_lock:
        lst = _index.get(company)
        if lst is not None:
            for i, p in enumerate(lst):
                if p["path"] == rel_path:
                    lst[i] = prod
                    break
            else:
                lst.append(prod)
                lst.sort(key=lambda p: p["path"])
    return prod


def remove_product(rel_path: str):
    parts = Path(rel_path).parts
    if not parts:
        return
    with _index_lock:
        lst = _index.get(parts[0])
        if lst is not None:
            _index[parts[0]] = [p for p in lst if p["path"] != rel_path]


def remove_company(company: str):
    with _index_lock:
        _index.pop(company, None)
    invalidate_dirs()          # 归档/撤回把目录搬走了，目录状态缓存必须立刻作废


# ---------- 索引快照：让重启不再等几分钟 ----------
# 进程被 OOM killer 杀掉（本机内存吃紧时发生过多次）后 systemd 会重启它，
# 但内存索引是空的，108 家公司要重扫好几分钟，这期间界面全是"后台扫描中"。
# 快照只是产品结构的缓存，不含任何审核状态（状态永远现读 records），
# 删掉它最多让下次启动重扫一遍。全量约 9MB，写在原生盘上。

def save_snapshot():
    try:
        with _index_lock:
            data = {c: list(v) for c, v in _index.items()}
        # 不用 store.write_json：那个带 indent=2，22MB 的快照没人会去读，
        # 缩进白白多出近一半体积和写入时间。仍走 atomic_write_text 保证不半截。
        store.atomic_write_text(paths.INDEX_CACHE, json.dumps(
            {"version": 1, "companies": data}, ensure_ascii=False, separators=(",", ":")))
    except Exception as e:
        print(f"[warn] 索引快照写入失败（不影响使用）: {e}")


def load_snapshot() -> int:
    """启动时回填索引。只认当前仍存在的公司，否则会冒出一堆打不开的空卡片。"""
    try:
        raw = store.read_json(paths.INDEX_CACHE, None)
        if not isinstance(raw, dict) or raw.get("version") != 1:
            return 0
        known = set(known_companies())
        loaded = 0
        with _index_lock:
            for c, prods in (raw.get("companies") or {}).items():
                if c in known and isinstance(prods, list):
                    _index[c] = prods
                    loaded += 1
        return loaded
    except Exception as e:
        print(f"[warn] 索引快照读取失败，将重新扫描: {e}")
        return 0


# ---------- 路径安全 ----------

def _resolve(rel_path: str):
    parts = Path(rel_path).parts
    if not parts:
        return None
    root = company_root(parts[0])
    if root is None:
        return None
    root = root.resolve()
    full = root.joinpath(*parts[1:]).resolve() if len(parts) > 1 else root
    # 不能用 str.startswith 判断：公司「A」下的 ../AB/x 会解析成 …/AB/x，
    # 而 "…/AB/x".startswith("…/A") 为真，等于放行了同前缀的邻居公司。
    if full != root and root not in full.parents:
        return None
    return full


def safe_file(rel_path: str):
    f = _resolve(rel_path)
    return f if f and f.is_file() else None


def safe_dir(rel_path: str):
    d = _resolve(rel_path)
    return d if d and d.is_dir() else None


# ---------- 后台扫描 ----------

def _loop():
    n = load_snapshot()
    if n:
        print(f"[info] 已从索引快照恢复 {n} 家公司，界面可立即使用")
    while True:
        try:
            pending = [c for c in known_companies() if not company_ready(c)]
            if pending:
                with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as ex:
                    list(ex.map(scan_company, pending))
                save_snapshot()
        except Exception as e:
            print(f"[warn] 后台扫描出错: {e}")
        time.sleep(RESCAN_INTERVAL)
        try:
            refresh_all()          # 内部已保存快照
        except Exception as e:
            print(f"[warn] 后台重扫出错: {e}")


def start_background_scan():
    threading.Thread(target=_loop, daemon=True).start()
