# -*- coding: utf-8 -*-
"""记录式参数构建器（company-product-records skill 专用）

输出结构（用户 2026-09-29 定稿）：
    参数信息 = { 型号名 or 表名: [ {group,name,symbol,value,unit,conditions}, ... ] }

字段规则：
    group      参数类别分组（通栏组标题 / rowspan 分组列）；无分组 → None
    name       参数主字段原文（去尾脚注角标 *；name 后的括号条件/符号拆出去）
    symbol     符号：独立 symbol 列 或 写在 name 后的符号；无 → None
    value      值原文（**单位保留在值里不删**；值尾括号条件拆出去）
    unit       单位：独立单位列 > name 括号 > value 里识别；**只进 unit，不塞 conditions**
    conditions 测试/适用/环境条件及备注等限定信息，统一进这里

两条结构性规则：
    ① 一个格里多值（各自带限定）→ 拆成多条记录
    ② 表里有 最小值/典型值/最大值 列 → 存成一条，value = "min:x、typical:y、max:z"（空档保留原样如 `–`）

角标规则复用 company-product-scraper 的 dom_text（能转就转 Unicode，整串可转才转；转不了保留 _x / ^x）。
"""
import os
import re
import sys

_sc = os.path.expanduser('~/.claude/skills/company-product-scraper/scripts')
if _sc not in sys.path:
    sys.path.insert(0, _sc)
from dom_text import lit_convert, to_sub, to_sup, SUB_CONV, SUP_CONV  # noqa: E402

# ══ 中英划分 + 表头前缀（2026-09-29 用户口径）══════════════════════════════
# ① 表头（组）不再单独成字段，而是**拼进 name 做父级**：光学参数 + 波长 → 「光学参数-波长」
# ② 中英文都划分，但**原文只有一种语言时不臆造另一种**：波长 Wavelength → 波长(Wavelength)
# ③ 中文与中文拼、英文与英文拼：光学参数 Specifications + 波长 Wavelength
#     → 光学参数-波长(Specifications-Wavelength)
_CJK = r'\u3400-\u4dbf\u4e00-\u9fff'
_HAS_CJK = re.compile(f'[{_CJK}]')
# 切口：最后一个"汉字结尾的中文段" + 空白 + 到行尾的全英文段（英文不在开头也照切：
# 「TTL 调制频率 Modulation Frequency」→ 中文段 TTL 调制频率 / 英文段 Modulation Frequency）
_SPLIT_CN_EN = re.compile(f'^(.+?[{_CJK}])\\s+([A-Za-z][^{_CJK}]*)$')


def split_cn_en(s):
    """→ (中文段 or None, 英文段 or None)。只有一种语言时另一侧为 None（不臆造）。

    拆不开（中英交错且切不干净，如 `15℃ (不结露) 15℃ (No condensation)`）→ 整串算中文段。
    """
    s = (s or '').strip()
    if not s:
        return None, None
    has_cjk, has_lat = bool(_HAS_CJK.search(s)), bool(re.search(r'[A-Za-z]', s))
    if not (has_cjk and has_lat):
        return (s, None) if has_cjk else (None, s)
    m = _SPLIT_CN_EN.match(s)
    if not m:
        return s, None
    return m.group(1).strip(), m.group(2).strip()


def bilingual(s):
    """中英并存 → 「中文(English)」半角括号；只有一种语言 → 原文不动"""
    if s is None:
        return None
    cn, en = split_cn_en(s)
    if cn is None or en is None:
        return str(s).strip()
    return f'{cn}({en})'


def merge_name(group, name):
    """表头（父级）拼进字段名：中文拼中文、英文拼英文，英文段进半角括号"""
    g_cn, g_en = split_cn_en(group) if group else (None, None)
    n_cn, n_en = split_cn_en(name)
    name = (name or '').strip()
    if n_cn is None:                        # 字段纯英文：英文段 = 英文表头-字段
        left = g_cn or ''
        right = '-'.join([x for x in (g_en, name) if x])
    elif n_en is None:                      # 字段纯中文（或拆不开）
        left = '-'.join([x for x in (g_cn, name) if x])
        right = g_en or ''
    else:                                   # 中英并存：各自拼各自
        left = '-'.join([x for x in (g_cn, n_cn) if x])
        right = '-'.join([x for x in (g_en, n_en) if x])
    if not left:                      # 没有中文侧（纯英文名、无中文表头）→ 原文返回，不加括号
        return right or name
    return left + (f'({right})' if right else '')


# ── 单位识别 ────────────────────────────────────────────────────────────────
_CJK = re.compile(r'[　-〿一-鿿＀-￯]')
_UNIT_CHARS = re.compile(r'^[A-Za-zµμΩÅ°℃℉%‰·⋅*/^²³⁻⁺⁰-⁹0-9\s.\-–—]+$')
_SEP_HEAD = re.compile(r'^[\s/·⋅*、,，\-–—]+')


_DEGREE_BROKEN = re.compile(r'(\d)\s*(?:\^\s*[oᵒ°]|[oᵒ])\s*C(?![a-zA-Z])')


def norm_units(t):
    """度符号归一：只修**破损写法** `368 ^°C` / `368 ^oC` / `368ᵒC` → `368 °C`；
    本来就正确的 `20 – 100°C` 一律不动。"""
    return _DEGREE_BROKEN.sub(r'\1 °C', str(t or ''))


def prep(t):
    """入口预处理：先做角标 Unicode 化、再度符号归一 —— 顺序不能反（否则 `g/cm^3` 识不出单位）"""
    if t is None:
        return None
    return norm_units(lit_convert(str(t)))


# 常见单位白名单（小写比较；词尾数字/上标先剥掉：cm³→cm、m2→m）
UNIT_WHITELIST = set('''m km dm cm mm pm fm nm um μm kg g mg ug μg t l ml ul μl pa kpa mpa gpa hpa bar mbar torr atm
s ms us μs ns ps fs min h hr hrs d day y hz khz mhz ghz thz a ma ua μa na pa ka v mv kv uv μv w mw kw
mw gw uw μw j mj uj μj kj wh kwh ev kev mev ohm kohm mohm f pf nf uf μf h mh uh μh c ah mah n kn mn
n·m pa·s cst s mho t mt g oe rpm r/min rps db dbm dbc dbμv % ‰ ppm ppb ppt lm lx sr rad mrad urad μrad
deg r k °c ℃ °f ℉ mol mmol mmol/l mol/l m s m2 m3 cm2 cm3 mm2 mm3 g/cm3 kg/m3 w/m2 w/m·k j/kg·k
w/(m·k) j/(kg·k) °c/w lm/w cd/m2 nit nit cd cd/m² r iu aq nm/℃ %/℃ ppm/℃ mv/℃ ma/v v/w w/a
hz/v v/hz pa·s mpa·s m/s m/s2 mm/s rpm kv/mm mv/m v/m a/mm2 w/cm2 mw/cm2 w/mm2 j/cm2 mj/cm2
cps cp ksi mpa·m1/2 μj/cm2 mj/cm2 w/mk mk kb kbit mb gb tb byte bit dpi lpi tpi mmhg inhg inh2o
mmh2o kg/cm2 lb psi ksi n/mm2 kgf nm·m mn·m g·cm kg·m oz lb gsm'''.split())


def _unit_token_ok(t):
    t = re.sub(r'[0-9²³⁻⁺⁰-⁹]+$', '', t).strip().lower()
    if t in UNIT_WHITELIST or t in ('°', '℃', '℉', '%', '‰'):
        return True
    return t.endswith('s') and t[:-1] in UNIT_WHITELIST   # mins→min、hrs→hr


def looks_like_unit(s):
    """整体像不像单位：不含汉字、符合单位字符集、且每个复合分量都在单位白名单里
    （`nm`/`g/cm³`/`W/m·K`/`°C` → True；`bare`/`typical`/`Line Pair` → False，不许把英文词当单位）"""
    s = (s or '').strip().strip('·⋅*/^-–—')
    if not s or len(s) > 24 or _CJK.search(s):
        return False
    if not _UNIT_CHARS.match(s):
        return False
    toks = [t for t in re.split(r'[·⋅*/^\s]+', s) if t]
    return bool(toks) and all(_unit_token_ok(t) for t in toks)


def unit_from_value(v):
    """从值里识别单位：取**首个数量**（含小数）后面紧跟的单位串

    `>10W@500kHz` → W（不是 kHz）；`< 13ps@1064nm,…` → ps；`～1.5mm` → mm；`<1%RMS` → %；
    `<20 μ rad` → μrad（去空格）；`<40mins` → min（复数收缩）。整串不像单位 → None。
    """
    v = str(v or '')
    m = re.search(r'\d+(?:[.,]\d+)?', v)
    if not m:
        return None
    tail = _SEP_HEAD.sub('', v[m.end():])
    # 找尾部里第一个"字母开头"的单位串（区间写法 1-20Hz / 10- 0.5 kHz 也能命中）
    run = re.search(r'[A-Za-zµμΩÅ%‰°℃℉][A-Za-zµμΩÅ%‰°℃℉·⋅*/^²³⁻⁰-⁹]{0,13}',
                    re.sub(r'\s+', '', tail))
    if not run:
        return None
    run = run.group(0)
    for i in range(len(run), 0, -1):
        cand = run[:i].strip('·⋅*/^-–—')
        if cand and looks_like_unit(cand):
            return cand
    return None


# ── name / value 的括号与符号拆分 ───────────────────────────────────────────
_PAREN_TAIL = re.compile(r'[（(]\s*([^（()）]{1,40}?)\s*[）)]\s*$')
# 末尾符号：只认"明确像物理符号"的写法（保守，不强行拆）
_SYMBOL_TAIL = re.compile(r'\s+(d[A-Za-z]\s*/\s*d[A-Za-z]|[λΔ][A-Za-z0-9]{0,2}|[A-Za-z]{1,2}[0-9]{1,2})$')


def split_tail(s):
    """把字符串尾部的括号拆出来 → (主文, symbol, unit, conditions, 未拆部分说明)

    · 括号内容像单位 → unit
    · 括号内容含数字（温度/波长/频率/时间…）→ conditions
    · 其它（别名/说明性括号，如 (Line Pair)、(Typical)）→ **留在主文里不拆**（还原原文，不强拆）
    """
    base = str(s or '').strip()
    symbol = unit = conditions = None
    while True:
        m = _PAREN_TAIL.search(base)
        if not m:
            break
        inner = m.group(1).strip()
        if re.search(r'\d', inner):
            conditions = inner          # 含数字的括号 = 限定条件（(20 – 100°C)、(1 μm)），**不是单位**
        elif looks_like_unit(inner):
            unit = inner                # 纯单位括号（(nm)）
        else:
            break                       # 别名/说明性括号 → 留在 name 里不拆
        base = base[:m.start()].strip()
    return base, symbol, unit, conditions


def split_symbol(name):
    """name 末尾的符号（如 `Thermal change dn/dT` 的 dn/dT）→ 保守拆分，拆不出就保留原名"""
    base = str(name or '').rstrip().rstrip('*').strip()
    m = _SYMBOL_TAIL.search(base)
    if m:
        return base[:m.start()].strip(), m.group(1).replace(' ', '')
    return base, None


def split_multivalue(v):
    """一个格里多值 → [(值, 条件), ...]

    仅当按 `；`/`;`/换行 切出的 ≥2 段，且每段都自带括号限定（或条件列已给出）时才拆；
    否则整串保持一条（不做无依据的切分）。
    """
    v = str(v or '')
    parts = [p.strip() for p in re.split(r'[；;\n]', v) if p.strip()]
    if len(parts) < 2:
        return [(v.strip(), None)]
    out = []
    for p in parts:
        base, _, _, cond, = split_tail(p)
        if cond is None:
            return [(v.strip(), None)]
        out.append((base.strip(), cond))
    return out


# ── min / typical / max ────────────────────────────────────────────────────
MTM_LABEL = {'min': ('最小',), 'typical': ('典型',), 'max': ('最大',)}


def join_mtm(values):
    """{min:原文, typical:原文, max:原文} → 'min:x、typical:y、max:z'

    ★ 用户 2026-09-29 答复：**空档也保留档位**（如博高很多行只有典型值、最小/最大是 `–`）→
      写成 `min:–、typical:300mw、max:–`，不省略。
    """
    order = ('min', 'typical', 'max')
    return '、'.join(f'{k}:{bilingual(str(values.get(k, "") or "").strip()) or ""}' for k in order)


# ── 记录构造 ────────────────────────────────────────────────────────────────
def make_record(group=None, name='', symbol=None, value='', unit=None, conditions=None):
    """构造一条记录（5 字段，缺省 null）；表头 group 拼进 name，各文本过角标 + 中英划分

    ★2026-09-29：**不再有 group 字段** —— 表头字样作为父级拼在 name 前（光学参数-波长）。
    """
    def t(x):
        return prep(str(x).strip()) if x is not None else None

    return {
        'name': merge_name(group, t(name) or ''),
        'symbol': t(symbol) if symbol else None,
        'value': bilingual(t(value)) if value else None,
        'unit': bilingual(t(unit)) if unit else None,
        'conditions': bilingual(t(conditions)) if conditions else None,
    }


def record_from_row(name, value, group=None, symbol=None, unit=None, conditions=None,
                    symbol_from_name=True):
    """行式表一行 → 记录/记录列表：拆 name 尾括号、拆 value 尾括号、多值拆分、单位识别"""
    name, value = prep(name), prep(value)
    unit, conditions = prep(unit), prep(conditions)
    base, s1, u1, c1 = split_tail(name)
    if symbol_from_name and not symbol:
        base, s2 = split_symbol(base)
    else:
        s2 = None
    symbol = symbol or s2 or s1
    unit = unit or u1
    conditions = conditions or c1

    unit = unit or unit_from_value(value)
    recs = []
    for v, cond in split_multivalue(value):
        b, _, u2, c2 = split_tail(v)
        v_unit = unit or u2 or unit_from_value(b)
        recs.append(make_record(group=group, name=base, symbol=symbol,
                                value=b, unit=v_unit,
                                conditions=cond or c2 or conditions))
    return recs


def row_of_mtm(name, cells, group=None, symbol=None, unit=None, conditions=None,
               labels=('min', 'typical', 'max'), symbol_from_name=True):
    """一行含 最小值/典型值/最大值 → 一条记录，value='min:…、typical:…、max:…'"""
    vals = {}
    for lab, cell in zip(labels, cells):
        vals[lab] = prep(cell) or ''
    name, unit, conditions = prep(name), prep(unit), prep(conditions)
    base, s1, u1, c1 = split_tail(name)
    if symbol_from_name and not symbol:
        base, s2 = split_symbol(base)
    else:
        s2 = None
    return make_record(group=group, name=base, symbol=symbol or s2 or s1,
                       value=join_mtm(vals), unit=unit or u1,
                       conditions=conditions or c1)


# ── 自检 ────────────────────────────────────────────────────────────────────
FIELD_ORDER = ('name', 'symbol', 'value', 'unit', 'conditions')
_MTM = re.compile(r'^min:.*、typical:.*、max:.*$')


def check_records(params, strict=True):
    """交付前自检：字段齐全 / 值非空 / min-typ-max 格式 / 角标未转 / name 尾星号"""
    bad = []

    def walk(x, path=()):
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, path + (str(k),))
        elif isinstance(x, list):
            for i, r in enumerate(x):
                p = ' > '.join(path + (f'[{i}]',))
                if not isinstance(r, dict):
                    bad.append((p, '记录不是对象')); continue
                miss = [f for f in FIELD_ORDER if f not in r]
                if miss:
                    bad.append((p, f'缺字段 {miss}'))
                if not (r.get('value') or '').strip():
                    bad.append((p, 'value 为空'))
                if re.search(r'min:|typical:|max:', str(r.get('value', ''))) and not _MTM.match(str(r['value'])):
                    bad.append((p, f"min/typ/max 格式异常: {r['value'][:40]}"))
                if str(r.get('name', '')).endswith('*'):
                    bad.append((p, 'name 尾部残留脚注星号'))
                if 'group' in r:
                    bad.append((p, '残留 group 字段（表头应已拼进 name）'))
                for f in ('name', 'symbol', 'value', 'unit', 'conditions'):
                    t = str(r.get(f) or '')
                    if re.search(r'(?<![0-9A-Za-z])[0-9A-Za-z)\]%]_([a-z]{1,4})(?![0-9A-Za-z])', t) \
                            and all(c in SUB_CONV for c in re.search(r'_([a-z]{1,4})', t).group(1)):
                        bad.append((p, f'下标未转 Unicode: {f}={t[:30]}'))
                    m = re.search(r'[0-9A-Za-z)\]%]\^([0-9+\-=()a-z]{1,3})', t)
                    if m and all(c in SUP_CONV for c in m.group(1)):
                        bad.append((p, f'上标未转 Unicode: {f}={t[:30]}'))
        else:
            pass

    walk(params)
    return bad


# ── 自测：用户 2026-09-29 给的 IRG 22 样例 ──────────────────────────────────
def _selftest():
    src = [
        # (name, value, unit列, symbol列, conditions列)
        ('Composition', 'Ge₃₃As₁₂Se₅₅', None, None, None),   # 页面用 <sub> 标记时 node_text 已给出 Unicode 下标
        ('Composition(bare)', 'Ge33As12Se55', None, None, None),  # 页面无标记 → 照原文保留（不推断）
        ('Density', '4.41 g/cm^3', None, None, None),
        ('Thermal expansion (20 – 100°C)', '12.5 · 10^–6/K', None, None, None),
        ('Transition temperature', '368 ^°C', None, None, None),
        ('Thermal change dn/dT*',
         '94.8 · 10^–6/K (1 μm)；67.7 · 10^–6/K (5 μm)；67.1 · 10^–6/K (10 μm)', None, None, None),
        ('Refractive index', '2.5971', None, None, '1 μm'),
        ('Refractive index', '2.5104', None, None, '5 μm'),
        ('Refractive index', '2.4968', None, None, '10 μm'),
    ]
    recs = []
    for n, v, u, s, c in src:
        recs += record_from_row(n, v, unit=u, symbol=s, conditions=c)
    out = {'IRG 22': recs}
    import json
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print('\n-- min/typ/max --')
    print(json.dumps(row_of_mtm('功率稳定 Power Stability',
                                ['–', '300mw', '–']), ensure_ascii=False))
    bad = check_records(out)
    print('\n自检:', bad if bad else '通过')
    return 0 if not bad else 1


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(_selftest())
    print(__doc__)
