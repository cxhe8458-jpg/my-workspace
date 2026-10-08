# -*- coding: utf-8 -*-
"""上传名单文件解析：Excel / CSV / TXT -> [(url, name), ...]

- Excel：复用 script._urls_from_xlsx（按列名自动识别「网址/官网」列与「公司名称」列）
- CSV  ：按列名定位 + 网址形态过滤
- TXT  ：每行一个，支持「网址,公司名」逗号分隔
"""
import os
import sys
import csv
import io
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import script  # noqa: E402  复用 _urls_from_xlsx / _URLISH / normalize_url

# 网址列候选名（与 script._urls_from_xlsx 保持一致，另补充小写/英文变体）
URL_COLS = ("网址", "官网", "URL", "url", "公司网址", "官网地址", "网站", "website", "Website")
NAME_COLS = ("公司名称", "名称", "企业名称", "厂家名称", "公司名", "name", "Name")


def _parse_excel(content):
    """content: 文件字节。写临时文件后复用 script 的解析（含 _URLISH 过滤）。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.write(content)
    tmp.close()
    try:
        return script._urls_from_xlsx(tmp.name)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _parse_csv(content):
    """CSV：第一行表头，按列名定位网址列与名称列，再按网址形态过滤。"""
    text = content.decode("utf-8-sig", errors="ignore")
    reader = csv.reader(io.StringIO(text))
    try:
        header = [str(c).strip() if c is not None else "" for c in next(reader)]
    except StopIteration:
        return []

    i_url = 0
    for col in URL_COLS:
        if col in header:
            i_url = header.index(col)
            break
    i_name = None
    for col in NAME_COLS:
        if col in header:
            i_name = header.index(col)
            break

    out = []
    for row in reader:
        if i_url >= len(row):
            continue
        v = str(row[i_url] or "").strip()
        if not v or v.lower() in ("none", "null") or v.startswith("#"):
            continue
        if not script._URLISH.match(v):
            continue
        name = ""
        if i_name is not None and i_name < len(row) and row[i_name] not in (None, ""):
            name = str(row[i_name]).strip()
        out.append((v, name))
    return out


def _parse_txt(content):
    """TXT：每行一个，支持「网址,公司名」。"""
    text = content.decode("utf-8-sig", errors="ignore")
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        u = parts[0]
        name = parts[1] if len(parts) > 1 else ""
        if u and script._URLISH.match(u):
            out.append((u, name))
    return out


def parse_upload(content, filename):
    """解析上传名单。返回 (items, error)。
    items: [(url, name), ...]；error: 非空表示失败原因。
    """
    if not content:
        return [], "文件为空"
    ext = os.path.splitext(filename or "")[1].lower()
    try:
        if ext in (".xlsx", ".xls"):
            items = _parse_excel(content)
        elif ext == ".csv":
            items = _parse_csv(content)
        elif ext == ".txt":
            items = _parse_txt(content)
        else:
            return [], "不支持的文件类型：%s（支持 .xlsx / .xls / .csv / .txt）" % ext
    except Exception as e:
        return [], "解析失败：%s" % e

    if not items:
        return [], "未识别到有效网址（请确认表格里有「网址」或「官网」列）"
    return items, None
