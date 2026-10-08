# -*- coding: utf-8 -*-
"""抓取调度：复用 script.py 的完整抓取流程（内部抓取并发 = FETCH_WORKERS = 8）。

直接构造 tasks 调 process_chunk，绕开 load_urls 的名单合并逻辑，
避免误抓 PS_厂家积累表里的历史名单。

另外负责**把证据链带出来**（人工审核的依据，见 _evidence_of）。
"""
import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import script  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rel(path):
    """落盘文件路径 -> 相对项目根目录的 posix 路径（供 /api/evidence 安全取用）。"""
    if not path:
        return None
    try:
        return os.path.relpath(os.path.abspath(path), BASE_DIR).replace(os.sep, "/")
    except ValueError:          # 跨盘符（Windows）
        return None


def _evidence_of(task):
    """一条记录的「AI 是从哪儿得出这些字段的」。

    这 25 个字段全是大模型从 HTML 里读出来的，没有任何人核对过。人要复核，
    就必须能当场翻到原始依据——所以把抓取阶段落盘的三个 HTML、logo 图片、
    以及关于页/联系页的真实 URL 一并带出来。

    两个 fallback 标记尤其重要，它们直接说明这条数据的可信度：
      about_fallback ：没找到「关于我们」，25 个字段其实全是从首页猜的；
      contact_missing：没抓到「联系我们」，联系方式只能来自关于页。
    """
    return {
        "url": task.get("url"),
        "site": task.get("site"),
        "domain": task.get("domain"),
        # 协议写反（http/https）被自动纠正过：名单里的原始网址与实际生效网址不一致
        "url_corrected_from": task.get("url_corrected_from"),
        "about_url": task.get("about_url"),
        "contact_url": task.get("contact_url"),
        "home_html": _rel(task.get("home_html_path")),
        "about_html": _rel(task.get("about_html_path")),
        "contact_html": _rel(task.get("contact_html_path")),
        "logo": _rel(task.get("logo_path")),
        "products": task.get("products") or [],       # 首页解析出的产品大类（分类的依据）
        "provisional_name": task.get("provisional_name"),
        "ai_name": task.get("ai_name"),
        "about_fallback": not task.get("about_url"),
        "contact_missing": not task.get("contact_url"),
        "run_tag": script.RUN_TAG,
    }


def _collect_evidence(tasks, records):
    """把证据按 records 的顺序对齐。

    ⚠ 依据的是一个实现事实：`run_stage` 返回的就是**传进去的那批 task 对象本身**
    （每个阶段函数都是原地改写 task 再 return task），所以 process_chunk 跑完之后，
    这里的 tasks 仍然握着全部阶段产物；而 records 里的每个元素就是 task["record"]
    这个**同一个对象**，因此可以按对象身份（id）精确配对，不必靠域名去猜。
    """
    by_id = {}
    for t in tasks:
        rec = t.get("record")
        if rec is not None:
            by_id[id(rec)] = t
    out = []
    for r in records:
        t = by_id.get(id(r))
        out.append(_evidence_of(t) if t else {})
    return out


def crawl_urls(urls, run_name="web采集"):
    """抓取一批 URL，返回 (records, failed, evidence)。

    records : 成功记录列表，每条为 script 的 25 字段 dict（含 website 等）。
    failed  : 失败任务列表（含 url / status 供前端提示）。
    evidence: 与 records 一一对应的证据（HTML/logo 落盘路径、来源 URL、兜底标记）。
    抓取结果会留档到 runs/<时间戳>_<run_name>/（JSON + Excel），原始字段不丢。
    """
    urls = [u for u in (urls or []) if u and str(u).strip()]
    if not urls:
        return [], [], []

    # 1) 新建运行周期目录（HTML/logo/缓存/导出都落在这里）
    script.init_run_env(run_name=run_name)

    # 2) 构造 tasks（与 run_pipeline 内部一致的初始结构）
    tasks = []
    for i, u in enumerate(urls):
        nu = script.normalize_url(u)
        site, netloc = script.base_site(nu)
        dom = script.domain_of(netloc)
        tasks.append({"idx": i, "url": nu, "site": site, "domain": dom,
                      "original_name": None, "status": "init"})

    # 3) 抓取（process_chunk 内部用 ThreadPoolExecutor，抓取并发 8）
    try:
        records, failed = script.process_chunk(tasks)
    except Exception:
        import traceback
        script.log.error("抓取整体异常：\n%s", traceback.format_exc())
        records, failed = [], tasks

    # 4) 给记录补 _domain，方便留档去重
    for r in records:
        if not r.get("_domain"):
            try:
                r["_domain"] = script.domain_of(urlparse(r.get("website") or "").netloc)
            except Exception:
                pass

    # 5) 留档：JSON（完整 25 字段） + Excel（原始字段表，一个不丢）
    #    ⚠ 证据在留档之后才收集：留档产物保持与改造前完全一致，不掺进新字段
    if records:
        try:
            script.merge_append(script.OUTPUT_JSON, records,
                                lambda r: r.get("_domain") or r.get("domain"))
        except Exception:
            pass
        try:
            script.export_data(records, fmt="excel")
        except Exception:
            pass
    try:
        script.mark_processed([t["domain"] for t in tasks])
    except Exception:
        pass

    return records, failed, _collect_evidence(tasks, records)
