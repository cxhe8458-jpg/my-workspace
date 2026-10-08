# -*- coding: utf-8 -*-
"""DOM 保真文本提取 + 键值切分 + 键值断裂自检（本 skill 所有取文本必须经过这里）。

解决的核心问题：<sup>/<sub> 上下标、&nbsp;、<br> 等把一段值拆成多个文本节点，
用 get_text(' ') 或按节点边界切 key/value 会产出：
  测量范围：1×10<sup>5</sup>～1×10<sup>-8</sup>Pa
  -> 错误: "测量范围：1×10": "5 ～1×10 -8 Pa"
  -> 正确: "测量范围": "1×10^5～1×10^-8Pa"

用法:
  from dom_text import node_text, split_kv, kv_red_flags
  python3 dom_text.py <html文件> [--selector css]      # 保真提取文本(调试用)
  python3 dom_text.py --check <info.json|目录>          # 扫描 参数信息 键值断裂 red flags
"""
import re, json, sys, argparse
from pathlib import Path

BLOCK_TAGS = {'p', 'div', 'li', 'tr', 'td', 'th', 'section', 'article', 'h1', 'h2',
              'h3', 'h4', 'h5', 'h6', 'dt', 'dd', 'table'}
SKIP_TAGS = {'script', 'style', 'noscript', 'template'}

# ══ 上下角标 Unicode 化（2026-09-28 用户口径，见 D:\SKILL.md 1.5.8）══════════════
# 能转就转 Unicode；不能转的保留标记形式（下标 `_x`、上标 `^x`），绝不臆造、绝不去掉标记。
# 下标：可转 a e h i j k l m n o p r s t u(+数字与 + - = ( ) )；不可转 b c d f g q v w x y z 及全部大写字母。
SUB_CONV = {'a': 'ₐ', 'e': 'ₑ', 'h': 'ₕ', 'i': 'ᵢ', 'j': 'ⱼ', 'k': 'ₖ', 'l': 'ₗ', 'm': 'ₘ',
            'n': 'ₙ', 'o': 'ₒ', 'p': 'ₚ', 'r': 'ᵣ', 's': 'ₛ', 't': 'ₜ', 'u': 'ᵤ',
            '0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄', '5': '₅', '6': '₆', '7': '₇',
            '8': '₈', '9': '₉', '+': '₊', '-': '₋', '=': '₌', '(': '₍', ')': '₎'}
# 上标：数字、+ - = ( )、小写拉丁字母可转；大写字母不转（用户示例 10^F 保留 ^F）
SUP_CONV = {'0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷',
            '8': '⁸', '9': '⁹', '+': '⁺', '-': '⁻', '=': '⁼', '(': '⁽', ')': '⁾',
            'a': 'ᵃ', 'b': 'ᵇ', 'c': 'ᶜ', 'd': 'ᵈ', 'e': 'ᵉ', 'f': 'ᶠ', 'g': 'ᵍ', 'h': 'ʰ',
            'i': 'ⁱ', 'j': 'ʲ', 'k': 'ᵏ', 'l': 'ˡ', 'm': 'ᵐ', 'n': 'ⁿ', 'o': 'ᵒ', 'p': 'ᵖ',
            'r': 'ʳ', 's': 'ˢ', 't': 'ᵗ', 'u': 'ᵘ', 'v': 'ᵛ', 'w': 'ʷ', 'x': 'ˣ', 'y': 'ʸ', 'z': 'ᶻ'}
# 正文里"裸写法"：`V_F` / `10^-8`（无标签）——只在整串可转时才转，且带护栏防误伤型号/单词
_LIT_SUB = re.compile(r'(?<![0-9A-Za-z])([0-9A-Za-z)\]}%])(_)([a-z]{1,4})(?![0-9A-Za-z])')
_LIT_SUP = re.compile(r'([0-9A-Za-z)\]}%])\^([0-9+\-=()a-z]{1,3})')


def to_sub(t):
    """下标串：全部字符可转才转，否则返回 None（保持原样）"""
    if t and all(c in SUB_CONV for c in t):
        return ''.join(SUB_CONV[c] for c in t)
    return None


def to_sup(t):
    if t and all(c in SUP_CONV for c in t):
        return ''.join(SUP_CONV[c] for c in t)
    return None


def _sub_mark(t):
    """<sub>x</sub> → 可转则 Unicode 下标，不可转则保留 `_x` 标记（信息不丢）"""
    return to_sub(t) or ('_' + t)


def _sup_mark(t):
    """<sup>x</sup> → 可转则 Unicode 上标，不可转则保留 `^x`"""
    return to_sup(t) or ('^' + t)


def lit_convert(text):
    """正文裸写法转换：只有整串可转才动，且要求下划线/尖号前是单个字母数字（防 `type_a`、`Laser_1000`）"""
    def rsub(m):
        u = to_sub(m.group(3))
        return (m.group(1) + u) if u else m.group(0)
    def rsup(m):
        u = to_sup(m.group(2))
        return (m.group(1) + u) if u else m.group(0)
    return _LIT_SUP.sub(rsup, _LIT_SUB.sub(rsub, text))


def _valign(n):
    st = (n.get('style') or '').lower().replace(' ', '')
    if 'vertical-align:sub' in st:
        return 'sub'
    if 'vertical-align:super' in st or 'vertical-align:sup' in st:
        return 'sup'
    return None


def _inline_text(el):
    return re.sub(r'\s+', '', el.get_text())


def node_text(el, lit=True):
    """保真提取一个元素的完整文本。

    规则（硬性，2026-09-28 更新）：
    - <sup>x</sup> / style=vertical-align:super → 可转则 Unicode 上标（1×10<sup>-8</sup>Pa → 1×10⁻⁸Pa），
      不可转保留 `^x`（10<sup>F</sup> → 10^F）
    - <sub>x</sub> / style=vertical-align:sub → 可转则 Unicode 下标（H<sub>2</sub>O → H₂O，I<sub>th</sub> → Iₜₕ），
      不可转保留 `_x`（V<sub>F</sub> → V_F，λ<sub>c</sub> → λ_c）
    - 源码里已经是 Unicode 上下标的（10⁻⁸、M²）原样保留，不再降级成 ASCII
    - 正文裸写法 `R_s`/`10^-8`（lit=True 时）按同一张表转换，带护栏防误伤型号/单词
    - <br> 与块级标签边界 → 单空格；相邻文本节点零宽拼接（禁止插空格，避免拆散科学计数法）
    - &nbsp;/全角空格 → 半角空格，连续空白折叠为一个
    """
    from bs4 import NavigableString, Tag
    out = []

    def rec(n):
        if isinstance(n, NavigableString):
            out.append(str(n))
            return
        if not isinstance(n, Tag) or n.name in SKIP_TAGS:
            return
        if n.name == 'br':
            out.append(' ')
            return
        if n.name == 'sup':
            t = _inline_text(n)
            if t:
                out.append(_sup_mark(t))
            return
        if n.name == 'sub':
            t = _inline_text(n)
            if t:
                out.append(_sub_mark(t))
            return
        va = _valign(n)
        if va:
            t = _inline_text(n)
            if t:
                out.append(_sup_mark(t) if va == 'sup' else _sub_mark(t))
            return
        for c in n.children:
            rec(c)
        if n.name in BLOCK_TAGS:
            out.append(' ')

    rec(el)
    text = ''.join(out).replace('\xa0', ' ').replace('　', ' ')
    if lit:
        text = lit_convert(text)
    return re.sub(r'\s+', ' ', text).strip()


def node_lines(el):
    """把元素拆成逻辑行（保真规则同 node_text）。

    - <br> 与块级标签边界 -> 换行
    - 行内连续空白(含 &nbsp; 串) -> 恰好 2 个空格（保留"多空格分隔键值"的信号）
    - 单个空白 -> 1 个空格
    返回非空行列表。
    """
    from bs4 import NavigableString, Tag
    out = []

    def rec(n):
        if isinstance(n, NavigableString):
            out.append(str(n))
            return
        if not isinstance(n, Tag) or n.name in SKIP_TAGS:
            return
        if n.name == 'br':
            out.append('\n')
            return
        if n.name == 'sup':
            t = _inline_text(n)
            if t:
                out.append(_sup_mark(t))
            return
        if n.name == 'sub':
            t = _inline_text(n)
            if t:
                out.append(_sub_mark(t))
            return
        va = _valign(n)
        if va:
            t = _inline_text(n)
            if t:
                out.append(_sup_mark(t) if va == 'sup' else _sub_mark(t))
            return
        for c in n.children:
            rec(c)
        if n.name in BLOCK_TAGS or n.name in ('dd', 'dt', 'li'):
            out.append('\n')

    rec(el)
    text = ''.join(out).replace('\xa0', ' ').replace('　', ' ')
    text = lit_convert(text)
    lines = []
    for line in text.split('\n'):
        line = re.sub(r'[ \t]{2,}', '  ', re.sub(r'[ \t]', ' ', line)).strip()
        if line:
            lines.append(line)
    return lines


def split_kv(text):
    """对'键：值'整段完整文本按第一个冒号切分。

    必须先 node_text() 拿到整段文本再调用本函数；
    严禁按 DOM 节点边界当 key/value 分界。
    返回 (key, value)；无冒号返回 None。
    """
    m = re.search(r'[：:]', text)
    if not m:
        return None
    return text[:m.start()].strip(), text[m.end():].strip()


def kv_red_flags(key, value):
    """键值断裂特征检测。命中任何一条 = 提取有错，必须回查页面，禁止直接交付。"""
    flags = []
    k, v = str(key), str(value)
    if re.search(r'[×xX]\s*10\s*$', k):
        flags.append('key以×10结尾：科学计数法被从中间截断')
    if re.search(r'[：:]', k):
        flags.append('key中含冒号：键值切分位置错误(值混入key)')
    if re.search(r'\d\s*$', k) and re.match(r'^\s*[-^]?\d', v):
        flags.append('key以数字结尾且value以数字/负号开头：疑似一个数值被拆到两边')
    if re.match(r'^\s*\^', v):
        flags.append('value以^开头：疑似上标丢失了底数')
    if re.match(r'^\s*-\d', v) and not re.match(r'^\s*-\d+(\.\d+)?\s*(℃|°C|[~～\-–]|Pa\b|V\b|dB)', v):
        flags.append('value以-数字开头且不是负数区间：疑似量级被拆断')
    # ★上下角标未 Unicode 化（2026-09-28 口径）：可转却留着 _x / ^x
    for s_ in (k, v):
        m = re.search(r'(?<![0-9A-Za-z])[0-9A-Za-z)\]}%]_([a-z]{1,4})(?![0-9A-Za-z])', str(s_))
        if m and all(c in SUB_CONV for c in m.group(1)):
            flags.append(f'下标未转 Unicode：{m.group(0)}')
        m = re.search(r'[0-9A-Za-z)\]}%]\^([0-9+\-=()a-z]{1,3})', str(s_))
        if m and all(c in SUP_CONV for c in m.group(1)):
            flags.append(f'上标未转 Unicode：{m.group(0)}')
    if re.search(r'\d\s+-?\d+\s*(Pa|pa|℃|%|V|A|W|Hz|mm|nm|μm|m)\b', v) \
            and not re.search(r'[~～\-–—至to]', v[:v.find(' ') if ' ' in v else len(v)]):
        flags.append('value中数字后跟游离的“空格+数字+单位”：疑似<sup>被拆成独立数字')
    return flags


def check_param_info(param):
    """递归检查 info.json 的 参数信息，返回 [(路径, key, value, flags)]。

    必须覆盖全部四种形态（含 list）：
      扁平 {k:v} / 型号字典 {型号:{k:v}} / **型号列表 {"型号列表":[{k:v},…]} / 参数图 {"参数图":[...]}
    ★ 历史 bug：只递归 dict，遇 list 直接跳过 —— 现行规范的 `型号列表` 数组形态
      整个逃过自检（多型号产品等于没检查）。list 必须递归。
    """
    hits = []

    def rec(node, path):
        if isinstance(node, dict):
            for kk, vv in node.items():
                if isinstance(vv, (dict, list)):
                    rec(vv, path + [str(kk)])
                else:
                    fl = kv_red_flags(kk, vv if isinstance(vv, str) else '')
                    if fl:
                        hits.append(('>'.join(path + [str(kk)]), kk, vv, fl))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                if isinstance(item, (dict, list)):
                    rec(item, path + [f'[{i}]'])
                # 纯字符串数组(如 参数图 文件名)无键值结构，不检
    rec(param, [])
    return hits


def _cmd_check(target):
    p = Path(target)
    files = sorted(p.rglob('*.json')) if p.is_dir() else [p]
    bad = 0
    for f in files:
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception as e:
            print(f'[无法解析] {f}: {e}')
            continue
        param = d.get('参数信息', d) if isinstance(d, dict) else {}
        hits = check_param_info(param) if isinstance(param, dict) else []
        if hits:
            bad += 1
            print(f'\n✗ {f}')
            for path, k, v, fl in hits:
                print(f'   {path} = {json.dumps(v, ensure_ascii=False)[:60]}')
                for x in fl:
                    print(f'     -> {x}')
    print(f'\n共检查 {len(files)} 个文件，{bad} 个存在键值断裂 red flag' if files else '无文件')
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('target', help='html文件，或 --check 时的 json 文件/目录')
    ap.add_argument('--selector', default=None, help='css选择器(仅html模式)')
    ap.add_argument('--check', action='store_true', help='扫描 json 的 参数信息 键值断裂')
    a = ap.parse_args()
    if a.check:
        sys.exit(_cmd_check(a.target))
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(open(a.target, encoding='utf-8').read(), 'html.parser')
    scope = soup.select_one(a.selector) if a.selector else soup.body or soup
    print(node_text(scope))


if __name__ == '__main__':
    main()
