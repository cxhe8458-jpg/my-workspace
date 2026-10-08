# -*- coding: utf-8 -*-
"""通用参数表提取器（rowspan/colspan 网格重建 + 表方向判定 + 结构体检）。

输出本 skill 规定的 参数信息 格式：
  单行表        -> {列:值, ...}                          扁平
  行=型号(rows) -> {型号: {该行其余列…}, …}                型号字典
  列=型号(cols) -> {型号(变体): {公共参数… + 逐变体参数…}, …}  型号字典(公共参数复制进每个型号)

★ 硬性原则（2026-07 修订，源自湖畔光电实测事故）：
  1. **方向必须判定，不许默认"一行=一个型号"**。转置表(参数名在首列、型号在表头列)
     用行式逻辑解析 = 把 N 个参数当成 N 个型号，全表报废。
  2. **colspan 展开出来的重复值，绝不能当作"每列各自的真值"输出**。网格重建的
     重复填充只用于"读格子"；某行的值若是一个跨列合并单元格，它是公共参数，
     写成"每个型号都等于这个值"就是造假数据（红线1）。
  3. 判不出方向/表头异常 -> **拒绝输出，打结构报告**，由人 `--orient` 指定后再跑。
  4. **多型号一律 `{型号:{…}}` 字典**（2026-07-28 用户指令，取代 `型号列表` 数组，便于入库解析）；
     **公共参数复制进每个型号**，让每条型号自成完整记录；型号名有空/重复时**拒绝输出**（压行会丢数据）。

用法:
  python3 table_extract.py <html文件> [--container "css选择器"] [--json 出.json]
                           [--orient auto|rows|cols] [--keycols N]
  --orient rows  每行一个型号（型号列在表头里）
  --orient cols  转置表：参数名在最左 --keycols 列(默认2)，型号/变体在表头列
"""
import re, json, sys, argparse
from pathlib import Path
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from dom_text import node_text   # 保真提取: sup->^x, sub并入, 文本节点零宽拼接

MODEL_KW = ['型号', '料号', 'Part Number', 'PartNumber', 'Part No', 'Model', 'PN', '规格型号']


def clean(s):
    return re.sub(r'\s+', ' ', str(s).replace('\xa0', ' ')).strip()


def grid(table):
    """展开 rowspan/colspan 为规整二维数组。

    返回 (值网格, 归属网格)。归属网格存每格来源单元格的 id()，
    用于区分"这一格是本来就有的" vs "被合并单元格跨越填充出来的"——
    没有这个信息就无法执行硬性原则 2。
    """
    rows = table.find_all('tr')
    g, own = [], []
    occ = {}
    for ri, tr in enumerate(rows):
        while len(g) <= ri:
            g.append([]); own.append([])
        ci = 0
        for cell in tr.find_all(['td', 'th']):
            while occ.get((ri, ci)) is not None:
                ci += 1
            rs = int(cell.get('rowspan', 1) or 1)
            cs = int(cell.get('colspan', 1) or 1)
            val, oid = node_text(cell), id(cell)
            for dr in range(rs):
                for dc in range(cs):
                    occ[(ri + dr, ci + dc)] = (val, oid)
            ci += cs
    if not occ:
        return [], []
    nr = max(r for r, _ in occ) + 1
    nc = max(c for _, c in occ) + 1
    g = [['' for _ in range(nc)] for _ in range(nr)]
    own = [[None for _ in range(nc)] for _ in range(nr)]
    for (r, c), (v, oid) in occ.items():
        g[r][c] = v
        own[r][c] = oid
    return g, own


def is_title_row(tr):
    cells = tr.find_all(['td', 'th'])
    return len(cells) == 1 and int(cells[0].get('colspan', 1) or 1) > 1


def _kw_idx(cells):
    for kw in MODEL_KW:
        for i, h in enumerate(cells):
            if kw.lower() in str(h).lower():
                return i
    return None


def audit(header, g, ti):
    """表头/结构体检，返回警告列表。命中即不可盲信输出。"""
    w = []
    if not header:
        return ['表头为空']
    if any(not clean(h) for h in header):
        w.append(f'表头存在空单元格(第{[i for i,h in enumerate(header) if not clean(h)]}列)'
                 '：按列位对齐，勿按"过滤空表头后的顺序"取值')
    dup = {h for h in header if h and header.count(h) > 1}
    # colspan 展开会天然产生重复表头，只有跨不同来源的重复才是问题，这里一并提示
    if dup:
        w.append(f'表头有重复列名 {sorted(dup)}：同名列写进同一个 dict 会互相覆盖丢数据')
    if len(g) - ti - 1 <= 0:
        w.append('无数据行')
    return w


def detect_orient(header, g, ti):
    """判定表方向。判不出返回 None —— 由人指定，不猜。"""
    if _kw_idx(header) is not None:
        return 'rows'
    first_col = [row[0] for row in g[ti + 1:] if row]
    if _kw_idx([header[0] if header else '']) is not None:
        return 'cols'
    if _kw_idx(first_col) is not None:
        return 'cols'
    return None


def parse_rows(header, g, own, ti, warn):
    """每行一个型号 -> {型号: {该行其余列}}。"""
    ki = _kw_idx(header)
    data = [row for row in g[ti + 1:] if any(clean(x) for x in row)]
    rows = []
    for row in data:
        d = {}
        for i in range(min(len(row), len(header))):   # 按列位对齐，不过滤空表头
            h = clean(header[i])
            if h and h not in d:
                d[h] = row[i]
        rows.append(d)
    if len(rows) == 1:
        return rows[0]                                 # 单行 -> 扁平
    mk = clean(header[ki]) if ki is not None else None
    names = [clean(r.get(mk, '')) for r in rows] if mk else []
    if not mk or not all(names) or len(set(names)) != len(names):
        warn.append(f'★型号键 {mk!r} 的值有空/重复 {names[:4]}：转成字典会把多行压成一条丢数据。'
                    '拒绝输出——请确认型号列选对了没有，或回官网核对该表是否漏抓型号列')
        return {}
    return {n: {k: v for k, v in r.items() if k != mk} for n, r in zip(names, rows)}


def parse_cols(header, g, own, ti, keycols=2):
    """转置表：参数名在最左 keycols 列，型号/变体在表头列。

    值单元格跨越全部型号列(同一个来源 id) -> 公共参数；逐列不同来源 -> 逐变体参数。
    """
    variants, seen = [], set()
    for c in range(keycols, len(header)):
        v = clean(header[c])
        if v and v not in seen:
            variants.append((v, c)); seen.add(v)
    label = clean(header[0]) or '型号'
    common, per = {}, {v: {} for v, _ in variants}

    for r in range(ti + 1, len(g)):
        row, orow = g[r], own[r]
        if not any(clean(x) for x in row):
            continue
        parts, seen_id = [], set()
        for c in range(min(keycols, len(row))):        # 键区：父键+子键(去重合并单元格)
            if orow[c] not in seen_id and clean(row[c]):
                parts.append(clean(row[c])); seen_id.add(orow[c])
        if not parts:
            continue
        key = parts[0] if len(parts) == 1 else f"{parts[0]}（{'/'.join(parts[1:])}）"
        ids = {orow[c] for _, c in variants if c < len(orow)}
        if len(ids) == 1:                              # 同一来源跨全部型号列 = 公共参数
            common[key] = row[variants[0][1]]
        else:
            for v, c in variants:
                if c < len(row):
                    per[v][key] = row[c]
    # 公共参数复制进每个型号，使每个型号自成完整记录
    models = {v: {**common, **per[v]} for v, _ in variants if per[v]}
    return models if models else dict(common)


def table_to_param(table, orient='auto', keycols=2):
    rows = table.find_all('tr')
    ti, title = 0, ""
    if rows and is_title_row(rows[0]):
        title = node_text(rows[0]); ti = 1
    g, own = grid(table)
    if not g or len(g) <= ti:
        return dict(标题=title, 表头=[], 方向=None, 警告=['空表'], 参数信息={})
    header = g[ti]
    warn = audit(header, g, ti)
    o = orient if orient != 'auto' else detect_orient(header, g, ti)
    ndata = len([r for r in g[ti + 1:] if any(clean(x) for x in r)])

    if o is None:
        warn.append('★方向判不出（表头无型号/料号类关键字）：拒绝输出。'
                    '请看下方结构预览，确认后用 --orient rows|cols 指定')
        return dict(标题=title, 表头=header, 方向=None, 数据行数=ndata,
                    首列=[r[0] for r in g[ti + 1:]], 警告=warn, 参数信息={})
    if ndata == 1 and o == 'rows':
        param = parse_rows(header, g, own, ti, warn)
    elif o == 'rows':
        param = parse_rows(header, g, own, ti, warn)
    else:
        param = parse_cols(header, g, own, ti, keycols)
    return dict(标题=title, 表头=header, 方向=o, 数据行数=ndata, 警告=warn, 参数信息=param)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("html"); ap.add_argument("--container", default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--orient", default="auto", choices=["auto", "rows", "cols"])
    ap.add_argument("--keycols", type=int, default=2, help="--orient cols 时参数名占几列")
    a = ap.parse_args()
    soup = BeautifulSoup(open(a.html, encoding="utf-8").read(), "html.parser")
    scope = soup.select_one(a.container) if a.container else soup
    tables = scope.find_all('table') if scope else []
    out, need_review = [], 0
    for n, t in enumerate(tables, 1):
        r = table_to_param(t, a.orient, a.keycols)
        out.append(r)
        pv = r["参数信息"]
        nmodel = sum(1 for v in pv.values() if isinstance(v, dict)) if isinstance(pv, dict) else 0
        print(f"\n表{n}『{r['标题'] or '(无标题)'}』 方向={r['方向'] or '★未判定'} "
              f"数据行{r.get('数据行数')} 型号数={nmodel or ('扁平' if pv else 0)}")
        print(f"   表头: {r['表头']}")
        for w in r["警告"]:
            print(f"   ⚠ {w}")
        if r["方向"] is None:
            need_review += 1
            print(f"   首列预览: {r.get('首列', [])[:8]}")
    if a.json:
        json.dump(out, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("\n写出:", a.json)
    if need_review:
        print(f"\n★ {need_review} 张表方向未判定，未产出参数——必须人工确认后指定 --orient 重跑")
    sys.exit(2 if need_review else 0)


if __name__ == "__main__":
    main()
