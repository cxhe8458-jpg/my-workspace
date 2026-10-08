# -*- coding: utf-8 -*-
"""端到端自检：在临时目录里造一份假数据，跑完「审核 → 归档」全流程。

    python3 tools/selftest.py

全程不碰真实产品数据与真实审核状态：数据目录、状态目录、归档目录都指向 tmp。
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.infra import paths                                    # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="审核自检_"))
DATA, ARCH, STATE = TMP / "公司数据", TMP / "已审核", TMP / "state"

# 把所有落盘位置改到 tmp —— 必须在 store/scanner 被使用之前
paths.STATE_DIR = STATE
paths.RECORDS_DIR = STATE / "records"
paths.BACKUP_DIR = STATE / "backups"
paths.LEDGER_FILE = STATE / "归档台账.json"
paths.CONFIG_FILE = STATE / "config.json"
paths.ERROR_LOG = STATE / "errors.log"
paths.INDEX_CACHE = STATE / "索引缓存.json"
paths.DEFAULT_CONFIG = {"data_dir": str(DATA), "archive_dir": str(ARCH)}
paths.ensure_dirs()

from fastapi.testclient import TestClient                      # noqa: E402
from app.main import app                                       # noqa: E402
from app.infra import scanner, store                           # noqa: E402
from app.services import fixes                                 # noqa: E402

store.write_json(paths.CONFIG_FILE, paths.DEFAULT_CONFIG)
client = TestClient(app, raise_server_exceptions=False)

PASS = FAIL = 0


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✔ {label}")
    else:
        FAIL += 1
        print(f"  ✘ {label}  {extra}")


def png(color=(9, 9, 9)):
    import base64, io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (6, 6), color).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def build():
    """两个产品：A 有 HTML 有图有 PDF，B 无 HTML。"""
    a = DATA / "测试公司" / "系列一" / "产品A"
    b = DATA / "测试公司" / "系列一" / "产品B"
    for d in (a, b):
        d.mkdir(parents=True)
    (a / "info.json").write_text(json.dumps({
        "产品名": "产品A", "页面URL": "https://example.com/a",
        "参数信息": {"波长": "1064nm", "功率": "20W"}, "主图": "主图.jpg",
        # 以下是「产品信息」面板要展示的正文字段：既有正常值、也有"存在但为空"，
        # 还有一个 INFO_ORDER 里没列过的新键（爬虫将来加字段不该要改代码）
        "产品描述": "这是一段产品描述。" * 30,          # 够长，前端会折叠
        "产品应用": "",                                  # 存在但为空
        "产品特点": "特点一；特点二；特点三",
        "二级分类": "系列一",
        "将来新增的字段": "兜底展示"}, ensure_ascii=False), encoding="utf-8")
    (a / "产品A.html").write_text("<html>x</html>", encoding="utf-8")
    (a / "手册.pdf").write_bytes(b"%PDF-1.4\n%test\n")
    (b / "info.json").write_text(json.dumps({
        "产品名": "产品B", "页面URL": "", "参数信息": {}}, ensure_ascii=False), encoding="utf-8")
    scanner.remove_company("测试公司")


def main():
    print(f"临时目录：{TMP}\n")
    build()

    print("【登录鉴权】")
    r = client.get("/api/companies")
    check("未登录访问接口被拒", r.status_code == 401, r.status_code)
    r = client.get("/", follow_redirects=False)
    check("未登录打开页面跳登录页",
          r.status_code == 302 and r.headers.get("location") == "/login", r.status_code)
    r = client.get("/static/js/api.js", follow_redirects=False)
    check("未登录拿不到前端源码（跳登录页，不吐文件）",
          r.status_code == 302 and "FRONT_VERSION" not in r.text, r.status_code)
    r = client.get("/login")
    check("登录页本身放行", r.status_code == 200 and "登录" in r.text, r.status_code)
    r = client.post("/api/login", json={"user": "admin", "password": "口令不对"})
    check("口令错被拒", r.status_code == 401, r.text[:120])
    r = client.post("/api/login", json={"user": "root", "password": "123456"})
    check("用户名错被拒", r.status_code == 401, r.text[:120])
    r = client.post("/api/login", json={"user": "admin", "password": "123456"})
    check("正确账号可登录", r.status_code == 200 and bool(client.cookies.get("audit_session")), r.text[:150])
    check("配置里不存明文口令", "123456" not in paths.CONFIG_FILE.read_text(encoding="utf-8"))
    good = client.cookies.get("audit_session")
    client.cookies.set("audit_session", good[:-1] + ("0" if good[-1] != "0" else "1"))
    check("篡改会话 cookie 被拒", client.get("/api/companies").status_code == 401)
    client.cookies.set("audit_session", good)
    check("登录后接口放行", client.get("/api/companies").status_code == 200)

    print("\n【扫描与 HTML 检测】")
    r = client.get("/api/company/测试公司").json()
    check("公司有 2 个产品", len(r["products"]) == 2, r)
    byname = {p["name"]: p for p in r["products"]}
    check("产品A 检测到 HTML", byname["产品A"]["has_html"] is True)
    check("产品B 检测到缺 HTML", byname["产品B"]["has_html"] is False)
    check("初始都是未检", all(p["status"] == "未检" for p in r["products"]))

    print("\n【标记】")
    pa, pb = byname["产品A"]["path"], byname["产品B"]["path"]
    r = client.post("/api/mark", json={"path": pa, "status": "通过"})
    check("标记通过", r.status_code == 200 and r.json()["record"]["status"] == "通过", r.text[:200])
    check("返回下一个未检产品", r.json()["next"] == pb, r.json().get("next"))
    r = client.post("/api/mark", json={"path": pb, "status": "有问题"})
    check("有问题不填说明被拒", r.status_code == 400, r.text[:120])
    r = client.post("/api/mark", json={"path": pb, "status": "有问题", "note": "参数全空"})
    check("有问题带说明可标记", r.status_code == 200 and r.json()["record"]["note"] == "参数全空")

    print("\n【归档门槛】")
    e = client.get("/api/company/测试公司").json()["eligibility"]
    check("有问题时不可归档", e["ok"] is False and "有问题" in e["reason"], e)
    check("给出卡住的产品", any(b["path"] == pb for b in e["blockers"]), e["blockers"])
    r = client.post("/api/archive", json={"companies": ["测试公司"], "target": str(ARCH)})
    check("强行归档被拦下", r.json()["moved"] == 0, r.json())
    check("公司文件夹没被动", (DATA / "测试公司").is_dir())

    print("\n【数据修正】")
    r = client.post("/api/fix/upload", json={"path": pa, "kind": "param", "data": png(), "replace": ""})
    check("上传参数图", r.status_code == 200 and r.json()["name"] == "参数图_1.png", r.text[:200])
    info = json.loads((scanner.safe_dir(pa) / "info.json").read_text(encoding="utf-8"))
    check("info.json 登记了参数图（原参数信息非空也要建键）",
          info["参数信息"].get("参数图") == ["参数图_1.png"], info["参数信息"])
    r = client.post("/api/fix/reclassify", json={"path": pa, "filename": "参数图_1.png", "to": "main"})
    check("参数图改归类为主图", r.json().get("new_name") == "主图.png", r.text[:200])
    info = json.loads((scanner.safe_dir(pa) / "info.json").read_text(encoding="utf-8"))
    check("改归类后 info.json 同步", info["主图"] == "主图.png" and "参数图" not in info["参数信息"], info)
    r = client.post("/api/fix/delete_file", json={"path": pa, "filename": "主图.png"})
    bk = r.json().get("backup")
    check("删除图片并留备份", r.status_code == 200 and bk and Path(bk).is_file(), r.text[:200])
    check("文件确实没了", not (scanner.safe_dir(pa) / "主图.png").exists())
    r = client.post("/api/fix/restore_file", json={"path": pa, "filename": "主图.png", "backup": bk})
    check("撤销删除可恢复", r.status_code == 200 and (scanner.safe_dir(pa) / "主图.png").exists(), r.text[:200])
    r = client.put("/api/fix/field", json={"path": pa, "field": "页面URL", "value": "notaurl"})
    check("非法 URL 被拒", r.status_code == 400)
    r = client.put("/api/fix/field", json={"path": pa, "field": "页面URL", "value": "https://x.com/a"})
    check("合法 URL 可改", r.status_code == 200)

    print("\n【产品信息展示】")
    # 这一段刻意排在「数据修正」之后：fixes.py 是整份读出、改完再整份写回 info.json 的，
    # 顺手验证上面那一串改图/改URL没把描述、特点这些正文字段抹掉。
    info = client.get("/api/product", params={"path": pa}).json()["info"]
    got = {f["key"]: f for f in info["fields"]}
    order = [f["key"] for f in info["fields"]]
    check("产品描述经过多次数据修正仍在", got.get("产品描述", {}).get("value", "").startswith("这是一段产品描述"))
    check("产品特点被展示（scanner 旧字段契约不认这个键）", "产品特点" in got, order)
    check("空字段照样列出并标记为空",
          got.get("产品应用", {}).get("empty") is True and "产品应用" in order, got.get("产品应用"))
    check("INFO_ORDER 之外的新键兜底展示", got.get("将来新增的字段", {}).get("value") == "兜底展示", order)
    check("已在别处展示的字段不重复列出",
          not ({"页面URL", "参数信息", "主图", "手册文件", "手册URL"} & set(order)), order)
    check("产品名与目录名一致时不占一行", "产品名" not in order, order)
    check("正文字段排在分类字段之前",
          order.index("产品描述") < order.index("二级分类"), order)
    check("字段齐全时不报缺字段", info["missing"] == [], info["missing"])
    info_b = client.get("/api/product", params={"path": pb}).json()["info"]
    check("整个键都没有时报「缺字段」（与「为空」区分开）",
          info_b["missing"] == ["产品描述", "产品应用"], info_b["missing"])

    # 参数信息 整个是一串参数图文件名（凯普林等站点实测 12 个产品）：
    # 必须判成 images，落到 flat 的话前端会把数组下标当参数名，渲染出「0 | 参数图_1.jpg」的假参数表
    check("参数信息为图片数组时判为纯参数图",
          scanner.classify_params(["参数图_1.jpg", "参数图_2.png"]) == "images")
    check("参数信息为普通数组时不误判为参数图",
          scanner.classify_params(["波长 1064nm", "功率 20W"]) == "flat")

    # 记录式参数（company-product-records 新格式）：{型号名或表名: [{group,name,symbol,value,unit,conditions}]}
    # 值是一串"一条参数一条记录"的 dict —— 必须判成 records（前端按六列表渲染），
    # 落到 nested 的话前端会走 kvTable，把每条记录 JSON.stringify 成一坨
    REC = {"IRG 22": [{"group": None, "name": "Density", "symbol": None,
                       "value": "4.41 g/cm³", "unit": "g/cm³", "conditions": None}]}
    check("参数信息为记录式时判为 records", scanner.classify_params(REC) == "records")
    check("记录式 + 参数图混存时仍判 records（参数图键先剥离）",
          scanner.classify_params({**REC, "参数图": ["参数图_1.jpg"]}) == "records")
    check("普通 list[dict]（无 name/value）不误判为记录式",
          scanner.classify_params({"清单": [{"a": 1}, {"b": 2}]}) == "nested")
    check("记录式与老嵌套混存时判 records（前端两种块都会渲染）",
          scanner.classify_params({**REC, "其他": {"波长": "1064nm"}}) == "records")

    print("\n【一键通过】")
    # 再造 3 个未检产品
    for i in range(3):
        d = DATA / "测试公司" / "系列二" / f"产品C{i}"
        d.mkdir(parents=True)
        (d / "info.json").write_text('{"产品名":"C","参数信息":{}}', encoding="utf-8")
    scanner.refresh_company("测试公司")
    r = client.post("/api/company/pass_all", json={"company": "测试公司"}).json()
    check("一键通过只动未检的 3 个", r["passed"] == 3, r)
    check("有问题的被跳过", r["skipped_problem"] == 1, r)
    st = {p["name"]: p["status"] for p in client.get("/api/company/测试公司").json()["products"]}
    check("产品B 仍是有问题", st["产品B"] == "有问题", st)

    print("\n【归档 / 撤回】")
    client.post("/api/mark", json={"path": pb, "status": "通过"})
    e = client.get("/api/company/测试公司").json()["eligibility"]
    check("全部通过后可归档", e["ok"] is True, e)
    r = client.post("/api/archive", json={"companies": ["测试公司"], "target": str(ARCH)}).json()
    check("归档成功", r["moved"] == 1, r)
    check("源目录已清空", not (DATA / "测试公司").is_dir())
    check("目标目录已就位", (ARCH / "测试公司").is_dir())
    led = client.get("/api/archive/ledger").json()["entries"]
    check("台账记了一条", len(led) == 1 and led[0]["company"] == "测试公司", led)
    r = client.get("/api/company/测试公司").json()
    check("已归档仍可只读复查", r["archived"] is True and len(r["products"]) == 5, r.get("archived"))
    r = client.post("/api/mark", json={"path": pa, "status": "有问题", "note": "x"})
    check("已归档公司禁止修改", r.status_code == 400 and "只读" in r.json()["detail"], r.text[:150])
    r = client.post("/api/archive/revoke", json={"company": "测试公司"}).json()
    check("撤回归档", (DATA / "测试公司").is_dir() and not (ARCH / "测试公司").exists(), r)
    check("撤回后台账清空", client.get("/api/archive/ledger").json()["entries"] == [])
    r = client.post("/api/mark", json={"path": pa, "status": "通过"})
    check("撤回后可以再修改", r.status_code == 200, r.text[:150])

    # ---- 归档搬到一半的残局能被补完 ----
    # 复现 2026-08-12「杭州微影传感科技有限公司」：drvfs 上 rmtree 被 Windows 句柄挡住，
    # 数据已完整搬到归档区、源目录剩几个空目录、台账没写。这个状态两边都不算：
    # 不在台账所以不是「已归档」，源目录还在所以赖在审核列表上，
    # 而再点归档会撞上"目标目录已存在"，永远归档不掉。
    print("\n【归档残局补完】")
    import shutil as _sh
    _sh.copytree(DATA / "测试公司", ARCH / "测试公司")   # 数据已在归档区
    for f in (DATA / "测试公司").rglob("*"):             # 源目录只剩空目录壳
        if f.is_file():
            f.unlink()
    # ⚠ 这一句不能省。不刷新索引的话，内存里还缓存着搬走前的 5 个产品，
    # 归档门槛（eligibility）就被绕过去了，用例会以**假阳性**通过 ——
    # 而真实场景里索引是空的，门槛会以"该公司没有产品"把残局补完整个挡住。
    # 这个坑实测过：第一版修复自检全绿，拿真公司一跑照样归档不掉。
    client.post("/api/refresh", params={"company": "测试公司"})
    check("残局前置：台账是空的", client.get("/api/archive/ledger").json()["entries"] == [])
    from app.services import archive_svc as _asvc
    e = _asvc.eligibility("测试公司")
    check("残局前置：门槛本身不通过（证明补完必须排在门槛之前）",
          e["ok"] is False and "没有产品" in e["reason"], e)
    r = client.post("/api/archive", json={"companies": ["测试公司"], "target": str(ARCH)}).json()
    check("残局被补完而不是报“目标已存在”", r["moved"] == 1, r)
    check("补完时带回提示", "补完" in (r["results"][0]["msg"] if r["results"] else ""), r)
    led = client.get("/api/archive/ledger").json()["entries"]
    check("补写了台账", len(led) == 1 and led[0]["company"] == "测试公司", led)
    check("台账 total 取自磁盘而非空索引", led and led[0]["total"] == 5, led)
    check("源目录空壳被清掉", not (DATA / "测试公司").is_dir())

    # 两边都有真数据时绝不自作主张：必须拦下让人来判
    client.post("/api/archive/revoke", json={"company": "测试公司"})
    _sh.copytree(DATA / "测试公司", ARCH / "测试公司")
    r = client.post("/api/archive", json={"companies": ["测试公司"], "target": str(ARCH)}).json()
    check("两边都有数据时拒绝归档", r["moved"] == 0 and "已存在" in r["results"][0]["msg"], r)
    check("被拒后源数据原样还在", (DATA / "测试公司").is_dir())
    _sh.rmtree(ARCH / "测试公司")

    # ---- 归档后源目录留下删不掉的空壳 ----
    # drvfs 上 rmtree 常被 Windows 句柄挡住，留下一个文件都没有的空目录
    # （实测连手工 rmdir 都是 Permission denied，只能等对方放手）。
    # 那种残渣不该让公司赖在审核列表上，也不该在台账页报「库内又有同名文件夹」。
    print("\n【归档后残留空壳不算库内数据】")
    r = client.post("/api/archive", json={"companies": ["测试公司"], "target": str(ARCH)}).json()
    check("先正常归档一次", r["moved"] == 1, r)
    (DATA / "测试公司" / "某分类" / "某产品").mkdir(parents=True)   # 造出空壳
    scanner.invalidate_dirs()
    check("空壳不算库内公司", "测试公司" not in scanner.list_companies(), scanner.list_companies())
    check("空壳不影响已归档判定", scanner.is_archived("测试公司") is True)
    led = client.get("/api/archive/ledger").json()["entries"]
    check("台账不把空壳报成冲突", led and led[0]["conflict"] is False, led)
    comps = {c["name"]: c for c in client.get("/api/companies").json()}
    check("审核列表里是「已归档」而不是空卡片",
          comps.get("测试公司", {}).get("stage") == "已归档", comps.get("测试公司"))
    # 壳里一旦真有文件，就必须重新算作库内数据（冲突要报出来）
    (DATA / "测试公司" / "某分类" / "某产品" / "info.json").write_text("{}", encoding="utf-8")
    scanner.invalidate_dirs()
    check("壳里有文件就重新算库内", "测试公司" in scanner.list_companies())
    led = client.get("/api/archive/ledger").json()["entries"]
    check("有文件时台账照常报冲突", led and led[0]["conflict"] is True, led)
    # 还原现场：后面的用例要的是"公司在库内、未归档、5 个产品齐全"
    _sh.rmtree(DATA / "测试公司")
    client.post("/api/archive/revoke", json={"company": "测试公司"})
    client.post("/api/refresh", params={"company": "测试公司"})
    check("现场已还原（公司回到库内、台账清空）",
          (DATA / "测试公司").is_dir()
          and client.get("/api/archive/ledger").json()["entries"] == []
          and len(client.get("/api/company/测试公司").json()["products"]) == 5)

    print("\n【误提取删除 / 撤销】")
    r = client.post("/api/product/delete", json={"path": pb}).json()
    check("删除产品", not scanner.safe_dir(pb), r)
    r = client.post("/api/product/restore", json={"id": r["undo"]["id"]})
    check("撤销恢复产品", r.status_code == 200 and scanner.safe_dir(pb), r.text[:150])

    print("\n【路径安全】")
    check("穿越到上级被拦", scanner.safe_dir("测试公司/../../etc") is None)
    check("穿越到公司数据本身被拦", scanner.safe_dir("测试公司/..") is None)

    print("\n【写盘容错：replace 全程失败也不丢数据】")
    real = Path.replace
    Path.replace = lambda self, t: (_ for _ in ()).throw(PermissionError(13, "denied"))
    try:
        store.save_record("写盘测试", "写盘测试/x", {"status": "通过", "_event": "e"})
    finally:
        Path.replace = real
    recs = store.load_records("写盘测试")
    check("replace 失败仍写入成功", recs.get("写盘测试/x", {}).get("status") == "通过", recs)
    check("不留 .tmp 残留", not list(paths.RECORDS_DIR.glob("*.tmp")))

    print("\n【索引快照：重启后免重扫】")
    scanner.save_snapshot()
    check("快照已写入", paths.INDEX_CACHE.is_file())
    check("快照不含审核状态（状态只从 records 读）",
          "有问题" not in paths.INDEX_CACHE.read_text(encoding="utf-8"))
    scanner._index.clear()                       # 模拟进程被 OOM 杀掉后重启
    check("清空索引后公司变未就绪", not scanner.company_ready("测试公司"))
    n = scanner.load_snapshot()
    check("从快照恢复", n == 1 and scanner.company_ready("测试公司"), n)
    check("恢复后产品数正确", len(scanner.scan_company("测试公司")) == 5)
    # 快照里的公司如果已经不存在了，不能冒出打不开的空卡片
    scanner._index.clear()
    raw = store.read_json(paths.INDEX_CACHE, {})
    raw["companies"]["查无此公司"] = [{"path": "查无此公司/x", "name": "x"}]
    store.write_json(paths.INDEX_CACHE, raw)
    scanner.load_snapshot()
    check("快照里的幽灵公司被丢弃", not scanner.company_ready("查无此公司"))

    print("\n【目录状态缓存】")
    scanner.invalidate_dirs()
    check("库内公司可见", "测试公司" in scanner.list_companies())
    check("未归档时 is_archived 为假", scanner.is_archived("测试公司") is False)

    print("\n【对比窗口：策略探测与跳板页】")
    # 不联网：只验参数校验、缓存命中与跳板页可用（真探测要访问官网，自检不做）
    check("非法 URL 被拒", client.get("/api/pagecheck", params={"url": "javascript:alert(1)"}).status_code == 400)
    check("代理非法 URL 被拒", client.get("/api/proxy", params={"url": "file:///etc/passwd"}).status_code == 400)
    from app.api import files as files_api
    files_api._POLICY_CACHE["缓存站.example"] = (
        __import__("time").time(), {"host": "缓存站.example", "ok": True, "coop": True, "embeddable": False})
    r = client.get("/api/pagecheck", params={"url": "https://缓存站.example/p.html"}).json()
    check("探测结果按域名缓存（不重复请求官网）", r["coop"] is True and r["embeddable"] is False, r)
    r = client.get("/static/cmpwin.html")
    check("同源跳板窗口页可加载", r.status_code == 200 and "__cmpShow" in r.text, r.status_code)

    print("\n【坏文件不静默清零】")
    f = paths.RECORDS_DIR / "坏文件公司.json"
    f.write_text('{"a": 1, ', encoding="utf-8")
    check("坏文件读为空", store.load_records("坏文件公司") == {})
    check("坏文件被隔离留证", bool(list(paths.RECORDS_DIR.glob("坏文件公司.json.corrupt-*"))))

    print(f"\n{'='*54}\n通过 {PASS} 项，失败 {FAIL} 项\n{'='*54}")
    return FAIL


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if code else 0)
