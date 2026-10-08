# -*- coding: utf-8 -*-
"""三类分类的判定规则（被 consolidate.py 复用）。

**分类判的是"产品参数载在哪里"**，不是"目录里有什么文件"：
  - 有原始 PDF        → 参数在 PDF（说明书）里
  - 有参数图           → 参数在图片里
  - 以上都没有         → 参数只可能在 json 里，再看 json 参数是否为空

判定顺序（2026-07-23 定，2026-07-29 补一条，勿再改动）：
  有 PDF 或 有参数图 → 类别3_有pdf或参数图
  **压根没有 json**   → 类别3_有pdf或参数图   ← 2026-07-29 新增
  否则按 json 中的产品参数：
    参数非空          → 类别1_json_非空
    参数为空          → 类别2_json_空

新增那一条的道理：类别3 的语义是「参数不在 json 里」。一个连 json 都没有的产品
（实测有 9 个，形态是"一张图 + 一份 PDF"的方案类产品页），参数只可能在图或 PDF 里，
归类别3 是唯一说得通的落点 —— 归类别2 等于声称"它的 json 参数为空"，那是假话。
这不与下面那条"产品展示图不参与判定"冲突：那条针对的是**有 json 的**产品。

⚠ 产品展示图（主图/图片_N/产品图_N）**不参与判定** —— 它不承载参数。
旧口径把"有任何图片"都判进类别3，导致"只有产品照片、参数全在 json 里"的产品被误分；
实测 742 个产品中有 229 个（31%）因此归错，旧口径下 707/742 全挤在类别3、分类近乎失效。
注意这与清洗行为无关：清洗仍会把主图/产品图/参数图全部合并进 PDF（那是产出交付物，
与"参数载在哪"是两回事）。

参数图判据 = 文件名含"参数"（实测该命名覆盖全部 726 张参数图）。
参数字段 = 产品参数 / 参数信息 / 产品规格 任一非空（空 = {} / [] / 空串）。
输出：<输出根>/<类别X>/<公司及原有相对路径>/<产品>/

⚠ **html 源文件不参与判定**：它是产品页快照，不承载"参数在哪"这个信息。
一个「只有 html」的产品会走到 `not has_json` 那条 → 类别3，这是对的：它的参数
确实不在 json 里。html 全程原样保留，见 products.py 顶部。
"""
import os, json
from . import products as P

CAT1 = "类别1_json_非空"
CAT2 = "类别2_json_空"
CAT3 = "类别3_有pdf或参数图"
PARAM_FIELDS = ["产品参数", "参数信息", "产品规格"]
# 口径统一到 products.IMG_EXT（含 .gif/.svg）。以前这里单独写一份且与 clean.py 不一致，
# 导致 .gif 被记成残留却永远不会被合并 —— 见 products.py 顶部注释（旧缺陷 #3）。
IMG_EXT = P.IMG_EXT


def is_param_image(fname):
    """参数图 = 图片且文件名含"参数"（参数图_1.jpg 等）。产品展示图不算。"""
    return fname.lower().endswith(IMG_EXT) and "参数" in fname


def _nonempty(v):
    if v is None: return False
    if isinstance(v, (dict, list)): return len(v) > 0
    if isinstance(v, str): return bool(v.strip())
    return True


def _has_params(d):
    for f in PARAM_FIELDS:
        if _nonempty(d.get(f)):
            return True
    pp = d.get("产品参数")
    if isinstance(pp, dict) and any(_nonempty(v) for v in pp.values()):
        return True
    return False


def classify_product(dp, fns):
    """返回类别名，或 None（不是产品目录）。

    产品判定走 products.is_product_files（叶子 + 含 json/pdf/图片），
    不再要求必须有 json —— 以前 `if not js: return None` 会让"只有图片和 PDF"的产品
    被当成非产品目录整个跳过。
    """
    if not P.is_product_files(fns):
        return None
    if any(f.lower().endswith(".pdf") or is_param_image(f) for f in fns):
        return CAT3
    if not P.has_json(fns):
        return CAT3          # 没有 json，参数只可能在图里 —— 见模块开头
    d = P.load_info(dp, fns)
    return CAT1 if _has_params(d) else CAT2

# 注：曾有 collect() / _company_of() / run_classify() 一组旧的「全量分类」入口，
# 已无任何调用方（分类现在由 consolidate 逐产品调 classify_product 完成），且其 apply 参数
# 容易被误读成本模块支持 dry-run。已删除。
