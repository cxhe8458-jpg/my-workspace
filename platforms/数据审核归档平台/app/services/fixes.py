# -*- coding: utf-8 -*-
"""数据修正：图片、PDF、URL、误提取产品删除。

上一代最坑的一点是「文件已经改了、接口却返回失败」——顺序是先动文件、
再写 info.json，后半段一失败就只剩一个错位状态，用户照着提示重试就造出重复数据
（同一张参数图被存成三份，真实发生过）。

这里每个写操作都遵守：
    1. 先备份要动的文件
    2. 记下 info.json 原文，用于回滚
    3. 动文件
    4. 写 info.json；**任何一步失败都把前面做过的实际改动撤回**再抛错
"""
import base64
import io
import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from ..infra import paths, scanner, store
from . import review

DELETED_DIR = paths.BACKUP_DIR / "已删除产品"
DELETED_LEDGER = paths.STATE_DIR / "已删除产品.json"
_BAD_SEG = re.compile(r'[\\/:*?"<>|]')


class FixError(Exception):
    pass


# ---------- info.json 读写 ----------

def _load_info(rel_path: str):
    company = Path(rel_path).parts[0]
    review.assert_writable(company)          # 已归档公司一律拦下
    d = scanner.safe_dir(rel_path)
    if not d:
        raise FixError("产品目录不存在")
    f = d / "info.json"
    raw = f.read_text(encoding="utf-8") if f.exists() else None
    try:
        info = json.loads(raw) if raw else {}
    except Exception:
        info = {}
    return d, f, info, raw


def _sync_images(d: Path, info: dict):
    """按磁盘现状重算 info.json 里的图片/PDF 引用。

    比"增量改字段"稳得多：任何一次上传/删除/改归类之后重算一遍，
    引用永远和磁盘一致，不会出现指向已删文件的死引用。
    """
    names = sorted(x.name for x in d.iterdir() if x.is_file())
    mains = [n for n in names if n.startswith("主图") and Path(n).suffix.lower() in scanner.IMG_EXTS]
    params = [n for n in names if n.startswith("参数") and Path(n).suffix.lower() in scanner.IMG_EXTS]
    pdfs = [n for n in names if n.lower().endswith(".pdf")]

    if info.get("主图") not in mains:                 # 原值失效才改，别乱动用户数据
        info["主图"] = mains[0] if mains else ""
    if info.get("手册文件") not in pdfs:
        info["手册文件"] = pdfs[0] if pdfs else ""
    p = info.get("参数信息")
    if isinstance(p, dict):
        if params:
            p["参数图"] = params      # 键不存在就建：否则传了参数图 info.json 里查无此物
        else:
            p.pop("参数图", None)


def _commit(rel_path: str, d: Path, f: Path, info: dict, event: str):
    store.backup_file(f, rel_path)
    store.atomic_write_text(f, json.dumps(info, ensure_ascii=False, indent=2))
    store.save_record(Path(rel_path).parts[0], rel_path, {"_event": event})
    return scanner.rescan_product(rel_path)


def _rollback_info(f: Path, raw):
    try:
        if raw is None:
            f.unlink(missing_ok=True)
        else:
            store.atomic_write_text(f, raw)
    except OSError:
        pass


# ---------- 图片 / PDF ----------

def _decode_data_url(data: str):
    m = re.match(r"^data:([^;]+);base64,(.*)$", data or "", re.S)
    if not m:
        raise FixError("图片数据格式不对（需要 dataURL）")
    return m.group(1), base64.b64decode(m.group(2))


def _to_png(raw: bytes) -> bytes:
    """统一转 PNG。

    ⚠ 和旧的 _to_jpeg 有一处**行为差异**：透明通道一律保留，不再往白底上拍平。
    JPEG 没有 alpha，所以旧代码只能拍平；PNG 有，就不该毁掉原图信息 ——
    白色 logo/图标拍到白底上会直接变成一张空白图，而且不可逆。
    显示端觉得透明底不好看是显示端的事（加个棋盘格或底色即可），不该在存储时烧死。
    """
    from PIL import Image
    img = Image.open(io.BytesIO(raw))
    if img.mode == "P":
        # 调色板图：带 transparency 的转 RGBA，否则转 RGB，别无脑 RGBA 徒增体积
        img = img.convert("RGBA" if "transparency" in img.info else "RGB")
    elif img.mode not in ("RGB", "RGBA", "L", "LA"):
        img = img.convert("RGBA" if "A" in img.mode else "RGB")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _next_name(d: Path, kind: str) -> str:
    """取下一个可用文件名。

    ⚠ 占位判断必须看**所有图片扩展名**，不能只看 .png：历史数据里全是 .jpg，
    只查 .png 的话会算出"主图.png 没被占用"，于是 主图.jpg 和 主图.png 并存，
    _sync_images 取 sorted()[0] 就成了掷骰子。
    """
    def taken(stem: str) -> bool:
        return any((d / f"{stem}{e}").exists() for e in scanner.IMG_EXTS)

    if kind == "main":
        if not taken("主图"):
            return "主图.png"
        n = 2
        while taken(f"主图_{n}"):
            n += 1
        return f"主图_{n}.png"
    n = 1
    while taken(f"参数图_{n}"):
        n += 1
    return f"参数图_{n}.png"


def upload_file(rel_path: str, kind: str, data: str, replace: str = "") -> dict:
    if kind not in ("main", "param", "pdf"):
        raise FixError("kind 必须是 main/param/pdf")
    d, f, info, raw_info = _load_info(rel_path)
    _mt, raw = _decode_data_url(data)

    if kind == "pdf":
        if raw[:5] != b"%PDF-":
            raise FixError("上传的文件不是有效 PDF")
        name = replace or (info.get("手册文件") or f"{_BAD_SEG.sub('_', Path(rel_path).name)}.pdf")
        if not name.lower().endswith(".pdf"):
            name = name + ".pdf"          # 爬取数据里手册文件常常没后缀，不补就扫不到
    else:
        try:
            raw = _to_png(raw)
        except Exception as e:
            raise FixError(f"图片处理失败: {e}")
        if replace:
            if not (d / replace).exists():
                raise FixError("要替换的文件不存在")
            name = replace if replace.lower().endswith(".png") else Path(replace).stem + ".png"
        else:
            name = _next_name(d, kind)

    target = d / name
    old_bytes = target.read_bytes() if target.exists() else None
    if target.exists():
        store.backup_file(target, rel_path)
    # 替换时若扩展名变了（jpg→png），旧文件要清掉，否则两张图并存
    stale = d / replace if (replace and replace != name and (d / replace).exists()) else None
    stale_bytes = stale.read_bytes() if stale else None

    target.write_bytes(raw)
    if stale:
        stale.unlink()
    try:
        _sync_images(d, info)
        prod = _commit(rel_path, d, f, info,
                       f"{'替换' if replace else '新增'}{ {'main':'主图','param':'参数图','pdf':'PDF'}[kind] } {name}（原文件已备份）")
    except Exception:
        # 文件已经落地但 info.json 没写成——把文件系统恢复原样再抛，
        # 不留"磁盘变了、接口说失败"的错位状态
        if old_bytes is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(old_bytes)
        if stale and stale_bytes is not None:
            stale.write_bytes(stale_bytes)
        _rollback_info(f, raw_info)
        raise
    return {"name": name, "product": prod}


def delete_file(rel_path: str, filename: str) -> dict:
    d, f, info, raw_info = _load_info(rel_path)
    target = d / filename
    if target.name == "info.json" or not target.is_file() or target.parent != d:
        raise FixError("文件不存在或不允许删除")
    backup = store.backup_file(target, rel_path)
    content = target.read_bytes()
    target.unlink()
    try:
        _sync_images(d, info)
        prod = _commit(rel_path, d, f, info, f"删除文件 {filename}（已移入备份）")
    except Exception:
        target.write_bytes(content)       # 文件放回去，避免"说失败其实删了"
        _rollback_info(f, raw_info)
        raise
    return {"product": prod, "backup": backup, "filename": filename}


def restore_file(rel_path: str, filename: str, backup: str) -> dict:
    d, f, info, raw_info = _load_info(rel_path)
    bk = Path(backup)
    if not store.inside_backup(bk) or not bk.is_file():
        raise FixError("备份文件不存在，无法撤销")
    target = d / filename
    if target.exists():
        raise FixError(f"{filename} 已存在，无法恢复")
    shutil.copy2(bk, target)
    try:
        _sync_images(d, info)
        prod = _commit(rel_path, d, f, info, f"撤销删除：已从备份恢复 {filename}")
    except Exception:
        target.unlink(missing_ok=True)
        _rollback_info(f, raw_info)
        raise
    return {"product": prod}


def reclassify(rel_path: str, filename: str, to: str) -> dict:
    """主图 ⇄ 参数图：分类由文件名前缀决定，所以改归类＝重命名 + 同步引用。"""
    if to not in ("main", "param"):
        raise FixError("to 必须是 main/param")
    d, f, info, raw_info = _load_info(rel_path)
    src = d / filename
    if not src.is_file() or src.suffix.lower() not in scanner.IMG_EXTS or src.parent != d:
        raise FixError("图片不存在")
    cur = "param" if filename.startswith("参数") else "main"
    if cur == to:
        return {"unchanged": True, "new_name": filename,
                "product": scanner.get_product(rel_path)}
    ext = src.suffix.lower()
    if to == "main":
        cand, n = f"主图{ext}", 2
        while (d / cand).exists():
            cand, n = f"主图_{n}{ext}", n + 1
    else:
        n = 1
        while (d / f"参数图_{n}{ext}").exists():
            n += 1
        cand = f"参数图_{n}{ext}"
    store.backup_file(src, rel_path)
    src.rename(d / cand)
    try:
        _sync_images(d, info)
        label = "主图" if to == "main" else "参数图"
        prod = _commit(rel_path, d, f, info, f"{filename} 重新归类为{label}（→ {cand}）")
    except Exception:
        (d / cand).rename(src)            # 名字改回去
        _rollback_info(f, raw_info)
        raise
    return {"new_name": cand, "product": prod}


# ---------- URL ----------

FIELDS = {"页面URL": "url", "手册URL": "manual_url"}


def edit_field(rel_path: str, field: str, value: str) -> dict:
    if field not in FIELDS:
        raise FixError(f"只允许修改 {'/'.join(FIELDS)}")
    d, f, info, raw_info = _load_info(rel_path)
    value = (value or "").strip()
    if value and not re.match(r"^https?://", value):
        raise FixError("URL 需要以 http:// 或 https:// 开头")
    info[field] = value
    try:
        return {"product": _commit(rel_path, d, f, info, f"修改 {field} → {value or '（清空）'}")}
    except Exception:
        _rollback_info(f, raw_info)
        raise


# ---------- 误提取产品：删除 / 撤销 ----------

def delete_product(rel_path: str) -> dict:
    """整个产品文件夹移入备份区，审核记录一并收进台账，20 秒内可原样撤销。"""
    company = Path(rel_path).parts[0]
    review.assert_writable(company)
    d = scanner.safe_dir(rel_path)
    if not d:
        raise FixError("产品目录不存在")
    if d == scanner.company_root(company):
        raise FixError("不能删除公司根目录")
    DELETED_DIR.mkdir(parents=True, exist_ok=True)
    entry_id = uuid.uuid4().hex[:12]
    dest = DELETED_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{entry_id}"
    shutil.move(str(d), str(dest))
    rec = store.delete_record(company, rel_path)
    led = store.read_json(DELETED_LEDGER, [])
    led.insert(0, {"id": entry_id, "company": company, "path": rel_path,
                   "name": Path(rel_path).name, "moved_to": str(dest),
                   "record": rec, "time": store.now()})
    store.write_json(DELETED_LEDGER, led)
    scanner.remove_product(rel_path)
    return {"id": entry_id, "name": Path(rel_path).name}


def restore_product(entry_id: str) -> dict:
    led = store.read_json(DELETED_LEDGER, [])
    entry = next((e for e in led if e.get("id") == entry_id), None)
    if not entry:
        raise FixError("找不到该删除记录")
    src = Path(entry["moved_to"])
    if not src.is_dir():
        raise FixError("备份目录已不存在，无法恢复")
    rel = entry["path"]
    parts = Path(rel).parts
    root = scanner.company_root(parts[0])
    if root is None:
        raise FixError("公司目录不存在，无法恢复")
    dest = root.joinpath(*parts[1:])
    if dest.exists():
        raise FixError("原位置已存在同名目录，无法恢复")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    if entry.get("record"):
        store.put_record(entry["company"], rel, entry["record"])
    store.write_json(DELETED_LEDGER, [e for e in led if e.get("id") != entry_id])
    return {"product": scanner.rescan_product(rel)}


def create_product(company: str, category: str, name: str, url: str = "") -> dict:
    """补录漏提取的产品：建骨架目录 + info.json，随后用修正功能补齐。"""
    review.assert_writable(company)
    root = paths.data_dir() / company
    if not root.is_dir():
        raise FixError("公司不存在")
    name = _BAD_SEG.sub("_", (name or "").strip()).strip(". ")
    if not name:
        raise FixError("产品名不能为空")
    cats = [_BAD_SEG.sub("_", c.strip()).strip(". ")
            for c in (category or "").replace("\\", "/").split("/") if c.strip()]
    if any(c in ("", "..") for c in [*cats, name]):
        raise FixError("分类或产品名不合法")
    rel = "/".join([company, *cats, name])
    d = root.joinpath(*cats, name)
    # 路径级判断，不能用 str.startswith：公司「A」下的 ../AB/x 会被前缀匹配放行
    if root.resolve() not in d.resolve().parents:
        raise FixError("路径不合法")
    if d.exists():
        raise FixError(f"该位置已存在同名产品：{rel}")
    d.mkdir(parents=True)
    info = {"产品名": name, "所属大类": cats[-1] if cats else "", "页面URL": (url or "").strip(),
            "主图URL": "", "参数信息": {}, "手册URL": "", "手册文件": "", "主图": ""}
    store.atomic_write_text(d / "info.json", json.dumps(info, ensure_ascii=False, indent=2))
    store.save_record(company, rel, {"status": review.UNCHECKED, "_event": "新增产品（补录）"})
    return {"product": scanner.rescan_product(rel)}
