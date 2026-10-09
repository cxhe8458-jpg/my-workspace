# -*- coding: utf-8 -*-
"""页面2：产品分类映射引擎（移植自 product-data-pipeline/engine.py，规则零改动）
- 判定顺序：Part-2 硬规则 → 产品名 → 父级逐级退回继承 → AI研判(LLM兜底) → 未分类
- 复制不移动、产品叶子数据零改动；输出 公司/cat1/cat2/产品（只含匹配成功的产品）
- 未匹配产品（含 AI 研判也失败的）按原目录结构复制到 未匹配目录/<公司>/…，供人工匹配页处理
- 映射报告.md / 研判归类.json 等过程记录统一放 过程记录目录/<公司名>/，与产品数据分离
- 支持公司级多线程（1-5，默认1）
"""
import os, re, json, glob, time, shutil, threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from .paths import CONFIG_DIR
from .jobs import fmt_duration
from . import llm as llmmod
from . import history
from . import registry
from . import keywords as kwlib
from . import products as P

AI_WORKERS_DEFAULT = 8      # AI 研判的产品级并发度（见 _ai_batch 的注释）

APP_FILES = ("mapping_application.json", "mapping_service.json")
UNIFY = {"光纤检测工具": "测试和测量", "光纤测试与测量": "测试和测量"}

GENERIC_EN = {"adapter","mount","base","post","clamp","housing","package","tool","tools",
              "accessory","accessories","cover","connector","module","cable","sleeve",
              "holder","bracket","plate","spacer","coupler","kit","board","card","unit"}

PART2 = [
 (["ase source","ase module","broadband ase source","broadband ase","sled","superluminescent diode","superluminescent led","low coherence light source","超辐射发光二极管"], "激光器和发光二极管","超辐射发光二极管"),
 (["3d scanner","structured light scanner","blue light scanner","portable scanner","handheld scanner","三维扫描仪","3d扫描仪"], "测试和测量","三维扫描仪"),
 (["otdr","optical time domain reflectometer","光时域反射仪"], "测试和测量","光纤测试与测量"),
 (["inspection probe","inspection microscope","endface inspection","fiber scope","光纤检测","端面检测"], "测试和测量","光纤检测工具"),
 (["doe","diffractive","cgh","hoe","phase mask","binary optics","衍射光学","衍射元件"], "光学元件","衍射光学元件"),
 (["confocal microscope","fluorescence microscope","digital microscope","stereo microscope","polarizing microscope","laser scanning microscope","共聚焦显微镜","荧光显微镜","数码显微镜","体视显微镜","偏光显微镜"], "测试和测量","显微镜"),
]
GAIN_CRYSTAL = ["nd:yag","cr:yag","er:yag","yb:yag","nd:yvo4","ti:sapphire","ti sapphire","ruby","laser crystal","gain crystal","激光晶体","增益晶体","钇铝石榴石","钒酸钇","钛宝石"]
NONLIN_CRYSTAL = ["bbo","lbo","ktp","ppktp","ppln","linbo3","kdp","dkdp","nonlinear crystal","optical crystal","electro-optic crystal","acousto-optic crystal","非线性晶体"]


def eng_norm(s):
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " " + re.sub(r"\s+", " ", s).strip() + " "

def nospace(s):
    return re.sub(r"\s+", "", s)


# ---------- 繁体归一 ----------
# 词库里的 `zh` 关键词一律是简体，而 `zh_hit()` 做的是**直接子串比对**，
# 于是繁体公司一个都匹配不上：实测「高明鐵企業股份有限公司」980 个产品里
# 148 个未匹配、825 个全落进 AI 待复核 —— 它的分类名是「傳動元件」「壓電模組系列」
# 「馬達_驅動器」，而词库里写的是「传动元件」「压电模组系列」「马达_驱动器」。
#
# 修法是**在匹配层把繁体折成简体**，而不是往 13 个 mapping_*.json 里再手写一份
# 繁体词条：那份要永远和简体那份同步，迟早分叉，而分叉的表现是"某些词只在一种字形下命中"。
# 归一只用于**比对**，输出的分类名、报告里的原始文本都不受影响。
#
# 表是单字级 繁→简（`config/zh_hant2hans.json`，4300 组，由 zhconv 离线生成后固化）。
# **固化而不是运行时 import zhconv**：deploy/start.sh 不装任何依赖，模块缺失时
# 映射会静默地只对简体生效 —— 这正是最难发现的一类退化。
def _load_hant_table():
    try:
        with open(os.path.join(CONFIG_DIR, "zh_hant2hans.json"), encoding="utf-8") as f:
            return str.maketrans(json.load(f))
    except Exception as e:
        print(f"[warn] 繁简对照表读取失败，繁体产品将匹配不上：{e}")
        return {}


_HANT2HANS = _load_hant_table()


def zh_norm(s):
    """把文本里的繁体字折成简体，供关键词比对用（只影响比对，不改写任何数据）。"""
    return s.translate(_HANT2HANS) if s else s

def _plural_variants(kw):
    k = kw.strip(); vs = {k}
    if len(k) >= 3:
        if k.endswith("y"): vs.add(k[:-1] + "ies")
        if k.endswith(("s","x","z","ch","sh")): vs.add(k + "es")
        else: vs.add(k + "s")
        if k.endswith("s"): vs.add(k[:-1])
    return vs

def eng_hit(kw, text_norm):
    if len(kw.strip()) < 2:
        return False
    return any(eng_norm(v) in text_norm for v in _plural_variants(kw))

def zh_hit(zh, text_ns):
    return bool(zh) and nospace(zh) in text_ns

def _flag(kw):
    return len(kw) <= 2 or kw.strip().lower() in GENERIC_EN


def _custom_cat_pairs():
    """人工新增分类的 (category1, category2) 集合 —— 只读 data/custom_categories.json。

    **为什么合法性判定要分两套**：`MappingEngine.VALID` 是「标准分类树」的守门员，AI 研判
    只能落进它（第 14 节第 5 条：AI 不允许新建/改名分类）；但人工匹配页的【新增分类】是有意
    允许的，`manual.rematch` 会把新分类连同关键词一起归档进关键词库。

    关键词装进了 MAPS、却没进 VALID，`_best_over` 那句 `c2 not in self.VALID` 就把它们
    **静默丢掉**：同一个产品靠「人工映射记录」下次还能沿用，但同类**新产品**永远不认 ——
    学习闭环恰好对"新增分类"这个最需要它的场景失效（实测：标准分类关键词 stage=name 命中；
    新增分类关键词 stage=none 落进未匹配）。

    所以规则命中这条路额外认自定义分类，AI 那条路（`_ai_judge` / `_apply_ai` 里的
    `engine.VALID`）**一个字不动**，分类树本身也绝不写回 config/。
    """
    try:
        from .manual import _custom
        out = set()
        for c1, v in (_custom() or {}).items():
            for c2 in (v.get("children") or {}):
                out.add((c1, c2))
        return out
    except Exception:
        return set()          # 读不到就当没有：退化成旧行为，不会误判


class MappingEngine:
    """配置驱动的分类引擎；config_dir 默认用平台内置双语 _config。"""

    def __init__(self, config_dir=None):
        conf = config_dir or CONFIG_DIR
        cats = json.load(open(os.path.join(conf, "company_categories.json"), encoding="utf-8"))["categories"]
        self.CATS = cats
        self.VALID = {}
        for c1, v in cats.items():
            for c2 in v["children"]:
                self.VALID.setdefault(c2.strip(), set()).add(c1)
        self.CUSTOM = _custom_cat_pairs()
        self.MAPS = []
        for f in glob.glob(os.path.join(conf, "mapping_*.json")):
            if os.path.basename(f) in APP_FILES:
                continue
            d = json.load(open(f, encoding="utf-8"))
            for eng, v in d.items():
                if not v.get("enable", True):
                    continue
                self.MAPS.append({"eng": eng, "zh": v.get("zh", ""), "c1": v["category1"],
                                  "c2": v["category2"], "conf": v.get("confidence", 1.0)})
        # 自定义关键词库（人工添加 / 人工已确认_ai研判），运行时实时加载
        self.MAPS.extend(kwlib.custom_engine_entries())
        # 中文关键词在**装载时**折一次简体，比对时就不必逐条转换 ——
        # MAPS 有数千条、每个产品都要整表扫一遍，放进 zh_hit 里等于每产品转几千次。
        # 人工关键词库里若有人写了繁体，这里一并归一，两边字形从此不再影响命中。
        for m in self.MAPS:
            m["zh"] = zh_norm(m["zh"])

    def unify(self, c1, c2):
        if c2 in UNIFY:
            return UNIFY[c2], c2
        return c1, c2

    def _legal(self, c1, c2):
        """规则命中的合法落点 = 标准分类树 ∪ 人工新增分类（见 _custom_cat_pairs）。
        AI 不走这里 —— 它仍然只认 VALID。"""
        return bool(c2) and ((c2 in self.VALID and c1 in self.VALID[c2])
                             or (c1, c2) in self.CUSTOM)

    def taxonomy_text(self):
        lines = []
        for c1, v in self.CATS.items():
            lines.append(f"- {c1}: " + "、".join(c.strip() for c in v["children"]))
        return "\n".join(lines)

    # ---------- Part-2 ----------
    def part2(self, tn, tns):
        def any_hit(kws):
            for kw in kws:
                if re.search(r"[一-鿿]", kw):
                    if zh_norm(nospace(kw)) in tns: return kw
                else:
                    if eng_norm(kw) in tn: return kw
            return None
        if any_hit(["thz","terahertz","太赫兹"]):
            if any_hit(["imaging","camera","成像","相机"]):
                return "测试和测量","太赫兹成像",1.0,any_hit(["thz","terahertz","太赫兹"])
            if any_hit(["tds","time domain","时域"]):
                return "测试和测量","太赫兹时域",1.0,any_hit(["tds","time domain","时域"])
            return "测试和测量","太赫兹",0.97,any_hit(["thz","terahertz","太赫兹"])
        for kws, c1, c2 in PART2:
            h = any_hit(kws)
            if h:
                return c1, c2, 1.0, h
        return None

    # ---------- mapping 匹配 ----------
    def _best_over(self, fields):
        best = None
        for text, w in fields:
            fn = eng_norm(text); fns = zh_norm(nospace(text))
            for m in self.MAPS:
                kwlen = 0; matched = None
                if m["eng"] and eng_hit(m["eng"], fn):
                    kwlen = len(m["eng"]); matched = m["eng"]
                if m["zh"] and zh_hit(m["zh"], fns):
                    if len(m["zh"]) * 2 > kwlen:
                        kwlen = len(m["zh"]) * 2; matched = m["zh"]
                if not matched:
                    continue
                c1, c2 = self.unify(m["c1"], m["c2"])
                if not self._legal(c1, c2):
                    continue
                if c2 in ("晶体", "激光晶体"):
                    # 晶体二次分流：中文关键词用 nospace 子串、英文用词边界；
                    # 不能对中文关键词用 eng_norm（会塌缩为空串导致误命中）
                    def _cin(x):
                        if re.search(r"[一-鿿]", x):
                            return zh_norm(nospace(x)) in fns
                        return eng_hit(x, fn)
                    if any(_cin(x) for x in GAIN_CRYSTAL):
                        c2 = "激光晶体"
                    elif any(_cin(x) for x in NONLIN_CRYSTAL):
                        c2 = "晶体"
                score = w * 10000 + kwlen * 10 + m["conf"]
                cand = {"category1": c1, "category2": c2, "confidence": m["conf"],
                        "matched_keyword": matched, "_s": score}
                if best is None or score > best["_s"]:
                    best = cand
        return best

    def classify(self, product_text, ancestors):
        pn_text = " ".join(ancestors) + " " + product_text
        tn = eng_norm(pn_text); tns = zh_norm(nospace(pn_text))
        p = self.part2(tn, tns)
        if p:
            c1, c2, conf, kw = p
            c1, c2 = self.unify(c1, c2)
            if c2 in self.VALID and c1 in self.VALID[c2]:
                return {"category1": c1, "category2": c2, "confidence": conf,
                        "matched_keyword": kw, "reason": "Part-2 硬规则命中", "stage": "part2"}
        b = self._best_over([(product_text, 1)])
        if b and b["confidence"] >= 0.90:
            b.pop("_s", None)
            b["reason"] = "产品名 mapping 命中(" + b["matched_keyword"] + ")"
            b["stage"] = "name"
            if _flag(b["matched_keyword"]):
                b["reason"] += "[泛词命中,建议人工确认]"
            return b
        for anc in ancestors:
            bb = self._best_over([(anc, 1)])
            if bb and bb["confidence"] >= 0.90:
                bb.pop("_s", None)
                bb["reason"] = f"继承自父级目录《{anc}》命中《{bb['matched_keyword']}》(置信度取父级值)"
                bb["stage"] = "parent"
                if _flag(bb["matched_keyword"]):
                    bb["reason"] += "[泛词命中,建议人工确认]"
                return bb
        return {"category1": "", "category2": "", "confidence": 0.0,
                "matched_keyword": "", "reason": "无 mapping 命中", "stage": "none"}


# ---------- AI 研判 ----------
AI_JUDGE_INSTRUCTION = """你是光电产品分类专家。下面是一个经关键词规则未能分类的产品，请依据其完整路径、
产品信息和你的光电专业知识，研判它到底是什么产品，并归入公司标准分类树中【已存在】的分类。

严格要求：
1. category1/category2 必须从下方分类树中原样选取，禁止新建、改名、翻译分类。
2. 必须基于产品实际信息研判，禁止臆测；把研判依据写清楚（该产品实际是什么、为何归此类）。
3. 若分类树中确实没有合适的子类，category1/category2 返回空字符串，并在 依据 中据实说明
   该产品实际是什么、为何无法归类（缺什么样的子类、最接近的候选是什么）。
4. 只返回一个 JSON 对象，不要多余解释：
   {"category1":"…","category2":"…","依据":"…"}

# 公司标准分类树（category1: category2 列表）
"""


SKIP_DIRS = ("产品映射", "产品已映射", "产品未映射", "二次分类", "待分类")


def list_company_dirs(root):
    """输入路径下的候选公司目录名（只 listdir，不下钻）。

    单独抽出来是因为**数产品要全树 walk**：输入目录实测 87 家 / 10573 个产品要 97 秒，
    而总览的秒开路径只需要知道"有哪些公司"，不需要知道每家几个产品。
    """
    if not os.path.isdir(root):
        return None
    return [c for c in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, c))
            and not c.startswith(("_", ".")) and c not in SKIP_DIRS]


def count_products(root, company):
    """某一家公司在输入目录下的产品数。口径见 products.py（叶子 + 含 json/pdf/图片）。

    以前数的是"任何含 info.json 的目录"（且不检查有没有子目录），与 registry.fingerprint
    的"叶子且含 .json"对不上，一个带子目录的产品在输入端算、在映射输出端不算，
    凭空多出「有更新」。现在两端同一口径。"""
    return P.count_products(os.path.join(root, company))


def scan_companies(root):
    """扫描输入路径下的公司（一级子目录，含产品后代的即公司）。

    注意：以前判据是"含 info.json 后代"，于是整整一家「杭州利珀科技股份有限公司」
    （8 个产品全是 图片+PDF、无 json）因为 n=0 被这里的 `if n:` 整家跳过，
    在界面上根本不存在。

    **返回值带 mtime**（2026-08-05）：`registry.status_of` 要靠它识别"数量没变但内容动过"
    （补 html / 改 json / 换图）。用 `registry.fingerprint` 一次拿到 (数量, 最大 mtime)，
    与原先的 `count_products` 是同一遍 walk，**不额外花时间**。"""
    names = list_company_dirs(root)
    if names is None:
        return None
    out = []
    for c in names:
        n, mt = registry.fingerprint(root, c)
        if n:
            out.append({"name": c, "products": n, "mtime": mt})
    return out


def _load_info(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def _leaf_files(d):
    fns = P.real_files(os.listdir(d))
    imgs = [f for f in fns if f.lower().endswith(P.IMG_EXT)]
    pdfs = [f for f in fns if f.lower().endswith(".pdf")]
    htmls = P.html_files(fns)
    tag = []
    if "info.json" in fns: tag.append("info.json")
    elif not P.has_json(fns): tag.append("无json")
    if imgs: tag.append(f"图片x{len(imgs)}")
    if pdfs: tag.append(f"PDFx{len(pdfs)}")
    # html 写进映射报告的「携带文件」列：新规范下每个产品都该带一份，
    # 报告里一眼看得出哪个产品没带，比等到 ④ 复查才发现早得多。
    if htmls: tag.append(f"htmlx{len(htmls)}")
    return "、".join(tag) or "（空）"


def _ai_judge(engine, model_cfg, company, orig, info, job):
    """LLM 研判一个产品，返回 (result_dict or None, 依据/原因)。"""
    feat = str(info.get("产品特性", ""))[:800]
    params = info.get("参数信息") or info.get("产品参数") or {}
    pkeys = "、".join(list(params.keys())[:15]) if isinstance(params, dict) else ""
    prompt = (f"公司：{company}\n产品完整路径：{orig}\n"
              f"产品名：{info.get('产品名','')}\n上级名称：{info.get('上级名称','')}\n"
              f"页面URL：{info.get('页面URL','')}\n产品特性(截断)：{feat}\n参数型号：{pkeys}\n")
    sys_extra = AI_JUDGE_INSTRUCTION + engine.taxonomy_text()
    text, err = llmmod.chat(model_cfg, prompt, system_extra=sys_extra)
    if err:
        job.log(f"  ⚠ AI 研判调用失败 [{orig}]: {err}")
        return None, "AI 调用失败：" + err
    obj = llmmod.extract_json(text)
    if not obj:
        return None, "AI 返回无法解析：" + (text or "")[:150]
    c1, c2 = engine.unify(obj.get("category1", "").strip(), obj.get("category2", "").strip())
    basis = str(obj.get("依据", "")).strip()
    if c2 and c2 in engine.VALID and c1 in engine.VALID[c2]:
        return {"category1": c1, "category2": c2, "依据": basis}, basis
    return None, basis or "AI 判定分类树中无合适子类"


STAGE_STAT = {"part2": "规则命中", "name": "规则命中", "parent": "规则命中",
              "manual": "沿用人工归类", "ai": "AI研判", "none": "未匹配"}


def _tick(job, prog):
    """推进全局进度计数（prog=(lock, [done])，跨公司线程共享）。"""
    with prog[0]:
        prog[1][0] += 1
        job.step(prog[1][0])


# ---------- 落地清单：重跑时的差集清场（旧缺陷 #1） ----------
#
# 问题：以前落地只 `rmtree(dst)`（**新**位置），从不清理该公司上一轮写在**别处**的产物。
# 于是一个产品从"未匹配"变成"规则命中"之后，`_未匹配` 里的旧副本永远留着；
# 换过分类的在映射输出里留两份。实测 2026-07-31 `_未匹配` 与映射输出重叠 58 个产品
# （6 家公司，其中 52 个同 URL 确认是同一个产品），约占「待人工匹配」总数的 6% ——
# 用户对着一份早就归好类的产品反复做人工匹配。而 `verify_ok = (got >= len(rows))`
# 用 `>=` 显式容忍了这种叠加，闸门根本看不见。
#
# 修法：每轮把「产品 → 落地路径」存进过程记录，下一轮只删**本轮扫到的产品**的旧位置。
# 关键是不能整家清场：CLAUDE.md 第六节写明「原始输入目录会被用户处理完就挪走，
# 映射输出才是累积的」——一家公司分两批进入输入目录时，整家 rmtree 会把上一批
# 已落地的产品一起删掉。本轮没扫到的产品一律不碰，累积性就保住了。
MANIFEST = "落地清单.json"


def _load_manifest(proc_dir):
    p = os.path.join(proc_dir, MANIFEST)
    if not os.path.exists(p):
        return {}
    try:
        d = json.load(open(p, encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}          # 读不出来就当没有：退化成旧行为，不会误删


def _manifest_entry(v):
    """清单里的一条记录 → (dst, url, leaf)。

    有两种形态，都要认：
      · 新版 `{"dst":…, "url":…, "leaf":…}` —— 带**产品身份**，_purge_stale 靠它认出
        "产品在输入树里换了位置"（见那里的长注释）；
      · 老版 `"<落地路径>"` —— 没有身份信息，url/leaf 返回空串，
        于是 _purge_stale 退化成旧行为（不删）。**老清单不会误删**，只是那一轮还认不出换位置，
        写回新版之后自然生效。
    """
    if isinstance(v, dict):
        return (str(v.get("dst") or ""), str(v.get("url") or "").strip(),
                str(v.get("leaf") or ""))
    return str(v or ""), "", ""


def _save_manifest(proc_dir, now):
    """原子写 —— 与 研判归类.json 同理，截断的清单会让下一轮清场打空或误判。"""
    p = os.path.join(proc_dir, MANIFEST)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(now, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def _under(path, root):
    """path 是否在 root 内部（已归一化，防 `..` 逃逸）。"""
    if not root:
        return False
    a, b = os.path.abspath(path), os.path.abspath(root)
    return a == b or a.startswith(b + os.sep)


def _cleanup_empty(path, stop_root):
    """删除空目录，向上直到 stop_root（不含）。清场后别留一堆空壳分类目录。"""
    p = path
    while p and not _same(p, stop_root) and _under(p, stop_root):
        try:
            if os.path.isdir(p) and not os.listdir(p):
                os.rmdir(p)
            else:
                break
        except OSError:
            break
        p = os.path.dirname(p)


def _ci_key(path):
    """落点比较用的键：**大小写不敏感**。

    /mnt/d 是 NTFS，`电池/扣式电池logo激光打标自动线` 与 `电池/扣式电池LOGO激光打标自动线`
    是两个不同字符串、却是**同一个目录**。按字符串判重会漏掉它们，两条落地计划指向同一个
    物理位置，复制循环里后一条把前一条刚写好的整棵 rmtree 掉 —— 实测「武汉赛斐尔激光」
    扫描 55 个产品只落地 54 个，`verify_ok` 不过，**整条全流程在 ② 末尾中止**；又因为校验
    失败不写映射记忆，这家公司每次全流程都会重演（同一家公司连挂三次）。
    口径与 clean.py 那条"NTFS 大小写不敏感的删除守卫"一致（那里用 `.lower()` 比路径）。
    """
    return os.path.normpath(path).lower()


def _same(a, b):
    """同一个落点吗 —— **大小写不敏感**（NTFS）。位置没变就别白删一遍再复制一遍。"""
    return _ci_key(a) == _ci_key(b)


def _purge_stale(job, comp, plan, croot, proc_dir, roots, rows=None):
    """按落地清单清掉该公司上一轮留下的、现在已经不该存在的产物。

    返回 (换分类数, 作废数, 换位置数)。三类，判据不同：

    ① **本轮也扫到，但落到了别处** —— 换过分类、或从「未匹配」变成了命中。删旧位置。

    ② **本轮没扫到，而源目录还在** —— 它不再被认成产品了（判定口径变严），上一轮的
       产物是垃圾。实测「广州飒特红外」：爬虫把页面缓存写进 `scripts/_cache/<产品名>/`，
       里面是和真产品一样的 jpg，于是 24 个缓存目录被当成产品映射、分类、还进了 AI 研判，
       25 个真产品被算成 49 个。加了目录剪枝之后，这些旧产物只能靠这一条清掉。

    ③ **源目录已经不在了，且本轮没在别处扫到同一个产品** —— 一律不碰：那是输入目录被
       用户处理完挪走的正常情形，映射输出是累积的（CLAUDE.md 第六节）。这是整个差集清场
       设计的底线 —— 分不清 ①②③ 就会误删几个月前的成果。

    ⚠ **「源路径不在了」不等于 ③，这里以前就栽在这上面。** 清单以**源相对路径**为键
      （`{orig: 落地路径}`），而产品在输入树里换个位置（人工整理、爬虫重跑换了分类目录）
      时 `orig` 也一起变了：旧键从新清单里消失，`src=croot/旧orig` 又确实不存在，
      于是被判成 ③「输入被挪走」—— 上一轮的产物**永远没人认领**。
      实测：把 `公司甲/光学元件/光学透镜/Z9` 移到 `公司甲/成像/内窥镜/Z9` 后重跑 ②，
      旧位置原样留着、新位置也生成了，②③④⑤ 一路 ✓通过（`verify_ok = got >= len(rows)`
      用 `>=` 容忍了叠加），最终交付里凭空多出一个按**旧分类**摆放的重复产品。
      所以源路径不在时，再按**产品身份**认一次：本轮在别处扫到同一个产品 → 是"换位置"，
      删旧位置；认不出才落到 ③。

      身份用页面URL（强证据）；无 json 的产品没有 URL，退回用叶子名（弱证据）。
      **弱证据是安全的**：它要求本轮确实扫到过同名产品 —— 若输入被整批挪走，rows 是空的，
      一个都认不出，全部按 ③ 保留。

    旧路径必须落在平台自己产出的两棵树里才动手：清单是盘上的一个 json，
    可能过期、可能被手工改过，绝不能凭它去 rmtree 任意路径。
    """
    prev = _load_manifest(proc_dir)
    if not prev:
        return 0, 0, 0
    now = {r["orig"]: dst for r, dst in plan}
    seen_urls = {str(r.get("url") or "").strip() for r in (rows or [])}
    seen_urls.discard("")
    seen_leaves = {r["prod"] for r in (rows or [])}
    n_moved = n_dead = n_shift = 0
    for orig, val in prev.items():
        old, o_url, o_leaf = _manifest_entry(val)
        if not old or not os.path.isdir(old):
            continue
        dst = now.get(orig)
        dead = moved = False
        if dst is not None:
            if _same(old, dst):
                continue                      # 位置没变
        else:
            src = os.path.join(croot, orig.replace("/", os.sep))
            if not os.path.isdir(src):
                # 源路径不在 —— 可能是"输入被挪走"（③），也可能是"产品在输入里换了位置"
                moved = bool((o_url and o_url in seen_urls)
                             or (not o_url and o_leaf in seen_leaves))
                if not moved:
                    continue                  # ③ 源已挪走 → 累积保留，绝不碰
            dead = not moved                  # ② 源还在却没被扫到 → 已不算产品
        if not any(_under(old, x) for x in roots):
            job.log(f"  ⚠ 落地清单里的旧路径不在映射输出/未匹配目录内，已跳过不删：{old}")
            continue
        try:
            shutil.rmtree(old)
            for x in roots:
                if _under(old, x):
                    _cleanup_empty(os.path.dirname(old), x)
                    break
            if moved:
                n_shift += 1
                if n_shift <= 5:
                    job.log(f"  ↪ 产品在输入目录里换了位置（{orig}），旧落地位置已清掉")
            elif dead:
                n_dead += 1
                if n_dead <= 5:
                    job.log(f"  ✗ 作废（已不再是产品，多半是爬虫缓存目录）：{orig}")
            else:
                n_moved += 1
        except OSError as e:
            job.log(f"  ⚠ 清理旧落地位置失败（不影响本次落地）：{old} —— {e}")
    return n_moved, n_dead, n_shift


def _report_unclaimed(job, comp, seen, plan, proc_dir, roots):
    """交付树里既不在本轮落地计划、也不在上一轮清单里的产品 —— **只报告，不删除**。

    这是"孤儿"唯一暴露得出来的地方，补的是 `verify_ok = got >= len(rows)` 那个盲区：
    映射输出是累积的，`>=` 是必须的（早先批次的产品也在树里），于是这个多出来的数字
    没有任何人会去细看。而落地清单只记当前这一轮 —— 产品一旦变成孤儿就从清单里消失，
    `_purge_stale` 再也看不到它，②③④⑤ 全程无感，最后以"多一个产品"的形态进交付物。

    只报不删，与 `_strays` 同一套理由：删未知目录有丢数据的风险，而计数对不上本来就是要
    人工看一眼的信号。**这条也是修复前的历史孤儿唯一的出口**（修复只能阻止新的孤儿产生，
    已经躺在树里的那些清单里没有记录，只能靠这里点出来）。
    """
    if not seen:
        return
    claimed = {os.path.abspath(d) for _r, d in plan}
    for v in _load_manifest(proc_dir).values():
        d, _u, _l = _manifest_entry(v)
        if d:
            claimed.add(os.path.abspath(d))
    orphans = [p for p in seen if os.path.abspath(p) not in claimed]

    def where(p):
        for r in roots:
            if _under(p, r):
                return os.path.relpath(p, r)
        return p

    if orphans:
        job.bump("疑似孤儿", len(orphans))
        job.log(f"  ⚠ {comp}：映射输出/未匹配里有 {len(orphans)} 个产品既不在本轮落地计划、"
                f"也不在上一轮落地清单里 —— 多半是历史遗留的旧副本（例如修复前"
                f"「产品在输入目录里换了位置」留下的那一份）。**平台不自动删**，请人工核对：")
        for p in orphans[:5]:
            job.log(f"      {where(p)}")
        if len(orphans) > 5:
            job.log(f"      …共 {len(orphans)} 个")


# review 写在同一条记录上的字段（复核状态、复核时间、归档关键词、改成哪个分类）。
# 它们属于人工复核，不属于本次研判结论，见 _save_yanpan。
REVIEW_FIELDS = ("复核", "复核时间", "归档关键词", "修改为")
_yp_lock = threading.Lock()
_yp_seen = {}          # {路径: (mtime, size)} —— 我们上次落盘时那份文件的指纹


def _save_yanpan(path, cache):
    """原子写研判缓存 + 保住 review 写进去的复核标记。

    **原子写**：以前是 `json.dump(cache, open(path, "w"))` —— 进程在写到一半时被杀，
    盘上就留下一个截断的 json，下次 `load` 失败、缓存整个作废，那一批 LLM 调用的钱白花。
    这个缓存是断点续跑唯一的凭据（实测一次中断保住了 1071 次调用），不能这么脆。

    **保住复核标记**：复核接口是同步 HTTP、**不入队列**，用户完全可以在某家公司正跑着 ②
    的时候去确认它的研判。而这里的 cache 是开跑时读进内存的，结束时整体覆盖 —— 那几分钟
    里写进去的 `复核=已确认` 会被无声抹掉，用户得再确认一遍（而且是"点了没反应"那种）。
    所以落盘前先看盘上的文件有没有被我们之外的人动过（mtime+size 变了就回读），把复核
    字段并回来。**只在研判结论没变时并**：分类被改过就说明这条记录该重看，标记不该留。
    正常单写者情况下这个 stat 发现指纹没变，一次回读都不做。
    """
    with _yp_lock:
        prev_fp = _yp_seen.get(path)
        try:
            cur_fp = (os.path.getmtime(path), os.path.getsize(path))
        except OSError:
            cur_fp = None
        # prev_fp 为 None（本次进程还没写过它）也要回读一次：那次写可能发生在
        # 我们读进内存之后、第一次落盘之前，是最容易丢标记的窗口。
        if cur_fp is not None and cur_fp != prev_fp:
            try:
                with open(path, encoding="utf-8") as f:
                    disk = json.load(f)
            except Exception:
                disk = None
            if isinstance(disk, dict):
                for k, fresh in cache.items():
                    old = disk.get(k)
                    if not isinstance(old, dict) or not isinstance(fresh, dict):
                        continue
                    if old.get("category1") != fresh.get("category1") \
                            or old.get("category2") != fresh.get("category2"):
                        continue          # 结论变了 → 复核标记作废，不并
                    for fld in REVIEW_FIELDS:
                        if fld in old and fld not in fresh:
                            fresh[fld] = old[fld]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        try:
            _yp_seen[path] = (os.path.getmtime(path), os.path.getsize(path))
        except OSError:
            _yp_seen.pop(path, None)


def _apply_ai(engine, job, row, y, comp, orig):
    """把一条研判结果落到 row 上。合法性仍由 engine.VALID 守门 —— AI 不能新建分类。"""
    c1, c2 = engine.unify(y.get("category1", "") or "", y.get("category2", "") or "")
    if c2 and c2 in engine.VALID and c1 in engine.VALID[c2]:
        row.update({"category1": c1, "category2": c2, "confidence": 0.90,
                    "matched_keyword": "(研判)", "stage": "ai",
                    "reason": "研判归类(AI研判)：" + y.get("依据", "")})
        job.bump("AI研判")
        job.log(f"  🤖 AI研判: {comp}/{orig} → {c1}/{c2}")
    else:
        row["stage"] = "none"
        row["reason"] = "未匹配原因：" + (y.get("依据") or "无 mapping 命中，AI 亦无合适子类")
        job.bump("未匹配")
        job.log(f"  ⊘ AI研判无合适子类: {comp}/{orig}")


class _AIRunner:
    """AI 研判的**流式**执行器 —— 并发落在产品这一层，且边挑边投。

    两条都是踩出来的，别改回去：

    1. **并发必须是产品级**。以前 AI 在产品循环里同步调，而"线程数"是**公司级**的。
       2026-07-30 实测一批 14 家公司，深圳市志奋领一家 2611 个产品占全批 83%，
       其余 13 家 23 分钟跑完后 4 个线程全部闲置，剩 1 个线程扛 83% 的量，
       单次 LLM 11~20 秒，3 小时 16 分只推进 1017 个。公司级并行在这种分布下等于没有。

    2. **必须边挑边投，不能"先全部判定完再批量投递"**。改成两遍之后曾经是后者：
       第一遍要把整家公司的 info.json 读完才开始调 AI，2611 个产品在 /mnt/d(NTFS) 上
       实测 **4 分 11 秒**（14:39:17 → 14:43:28），这段时间**一条日志都不发**、
       进度条只为规则命中的产品跳动，界面看起来就是卡死；随后 1071 条缓存命中
       又在同一秒里一起结算，日志"哗"地涌出来。现在第一条 AI 结果几秒内就回来。

    缓存命中的不占并发额度（`研判归类.json` 按产品 orig 记账，重跑不重复付费）。
    """

    def __init__(self, engine, job, comp, rows, cache, yp_path, model_cfg, pool, prog):
        self.e, self.job, self.comp, self.rows = engine, job, comp, rows
        self.cache, self.yp, self.cfg = cache, yp_path, model_cfg
        self.pool, self.prog = pool, prog
        self.lock = threading.Lock()
        self.futs = []
        self.n_sub = self.n_done = self.n_cached = 0
        self.scanning = True      # 第一遍还没结束 → 分母还在变，别假装知道总数
        self.t0 = None

    @property
    def taken(self):
        return self.n_sub + self.n_cached

    def add(self, idx, orig, info):
        """第一遍挑出一个要问 AI 的产品：命中缓存就地结算，否则立刻投进池子。"""
        if self.job.cancelled:
            return
        y = self.cache.get(orig)
        if y is not None:
            self.n_cached += 1
            _apply_ai(self.e, self.job, self.rows[idx], y, self.comp, orig)
            _tick(self.job, self.prog)
            return
        with self.lock:
            if self.t0 is None:
                self.t0 = time.time()
            self.n_sub += 1
        if self.pool:
            self.futs.append(self.pool.submit(self._one, idx, orig, info))
        else:
            self._one(idx, orig, info)

    def _one(self, idx, orig, info):
        if self.job.cancelled:
            return
        res, basis = _ai_judge(self.e, self.cfg, self.comp, orig, info, self.job)
        y = res if res else {"category1": "", "category2": "", "依据": basis}
        with self.lock:
            self.cache[orig] = y
            try:
                _save_yanpan(self.yp, self.cache)
            except Exception as ex:
                self.job.log(f"  ⚠ 研判缓存写盘失败（不影响本次结果）: {ex!r}")
        _apply_ai(self.e, self.job, self.rows[idx], y, self.comp, orig)
        _tick(self.job, self.prog)
        with self.lock:
            self.n_done += 1
            k, sub, scanning = self.n_done, self.n_sub, self.scanning
        if k % 20 == 0 or (not scanning and k == sub):
            self._report(k, sub, scanning)

    def _report(self, k, sub, scanning):
        """报吞吐与 ETA。没有这个，界面上就是「进度条几小时不动、日志里产品名一条条滚」，
        分不出是慢还是卡死 —— 那正是 7-30 那次三个多小时里没人能判断该等还是该砍的原因。"""
        el = time.time() - (self.t0 or time.time())
        rate = k / el if el > 0 else 0
        if scanning:
            self.job.log(f"  ⏱ {self.comp} AI 研判 已回 {k} / 已投 {sub}"
                         f"（产品仍在挑选中）· {rate * 60:.1f} 个/分钟")
            return          # 挑选阶段由主线程占着 current，别两边抢着刷
        left = (sub - k) / rate if rate > 0 else None
        self.job.log(f"  ⏱ {self.comp} AI 研判 {k}/{sub} · {rate * 60:.1f} 个/分钟"
                     f" · 预计还需 {fmt_duration(left) or '—'}")
        self.job.set_current(f"{self.comp} · AI 研判 {k}/{sub}"
                             + (f"（预计还需 {fmt_duration(left)}）" if left else ""))

    def finish(self):
        """第一遍扫完后，等剩下的调用回来。"""
        with self.lock:
            self.scanning = False
            sub, cached, done = self.n_sub, self.n_cached, self.n_done
        if cached:
            self.job.log(f"── {self.comp}：{cached} 个产品命中既有研判缓存，未重复调用 AI")
        if not self.futs:
            return
        self.job.log(f"── {self.comp}：共投递 {sub} 次 AI 研判，已回 {done}，等待剩余 …")
        for f in as_completed(self.futs):
            try:
                f.result()
            except Exception as ex:
                self.job.log(f"  ✗ AI 研判任务异常: {ex!r}")
            if self.job.cancelled:
                for g in self.futs:   # 还没开跑的直接撤掉；已在飞的那几个会自己返回
                    g.cancel()
                break


def _map_company(engine, job, comp, croot, prods, output_root, unmatched_root,
                 process_root, model_cfg, use_ai, prog, ai_pool=None):
    """处理一家公司；返回汇总 dict。prog=(lock, [done]) 共享进度。

    分两遍：① 规则判定（快，纯本地）顺便挑出要问 AI 的；② AI 研判批次，产品级并发。
    ai_pool = 全局共享的研判线程池（None = 不并发，逐个调）。
    """
    # 输入侧指纹**在处理之前取**：它代表"这一轮映射的是哪一版数据"。
    # 放到最后取的话，跑了几小时期间用户又动了输入目录，那次改动会被这次的记录吞掉，
    # 下一轮再也发现不了（③ 清洗的 fp_n/fp_mt 也是同样的时序，两边保持一致）。
    in_n, in_mt = registry.fingerprint_dir(croot)
    proc_dir = os.path.join(process_root, comp)
    os.makedirs(proc_dir, exist_ok=True)
    yp_path = os.path.join(proc_dir, "研判归类.json")
    yanpan_cache = {}
    if os.path.exists(yp_path):
        try:
            yanpan_cache = json.load(open(yp_path, encoding="utf-8"))
        except Exception:
            yanpan_cache = {}
    # 人工归类记忆：人工匹配 / AI复核修改 定过的产品，重跑时直接沿用，不再走规则和 AI。
    # 没有这一步，重跑会让已人工归好的产品再次落进 _未匹配，逼用户重做一遍。
    manual_rec = {}
    mr_path = os.path.join(proc_dir, "人工映射记录.json")
    if os.path.exists(mr_path):
        try:
            manual_rec = json.load(open(mr_path, encoding="utf-8"))
        except Exception:
            manual_rec = {}
    # ---- 扫描判定：沿用人工归类 / 规则命中；没命中的**当场投给 AI**（不攒批） ----
    rows = []
    runner = _AIRunner(engine, job, comp, rows, yanpan_cache, yp_path,
                       model_cfg, ai_pool, prog) if (use_ai and model_cfg) else None
    n_prod = len(prods)
    n_hit = 0
    t_scan = time.time()
    for i, d in enumerate(prods, 1):
        if job.cancelled:
            break
        rel = os.path.relpath(d, croot)
        parts = rel.split(os.sep)
        if len(parts) < 2:
            parts = [os.path.basename(croot)] + parts
        prod = parts[-1]
        orig = "/".join(rel.split(os.sep))
        job.set_current(f"{comp} · 扫描判定 {i}/{n_prod} · {orig}")
        info = P.load_info(d)          # 没有 json 就是 {}，产品名回退目录名
        name = str(info.get("产品名") or "").strip() or prod
        parent = info.get("上级名称", "")
        ancestors = list(rel.split(os.sep)[:-1][::-1])
        if parent and parent not in ancestors:
            ancestors = [parent] + ancestors
        mr = manual_rec.get(orig) or {}
        if mr.get("category1") and mr.get("category2"):
            r = {"category1": mr["category1"], "category2": mr["category2"],
                 "confidence": 1.0, "matched_keyword": "(人工)", "stage": "manual",
                 "reason": "沿用人工归类" + (f"（{mr.get('时间', '')}）" if mr.get("时间") else "")
                           + ("[新增分类]" if mr.get("新增分类") else "")}
        else:
            r = engine.classify(name + " " + prod, ancestors)
        row = {"orig": orig, "parts": rel.split(os.sep), "prod": prod, "src": d,
               "files": _leaf_files(d), "url": info.get("页面URL", ""), **r}
        rows.append(row)
        if not row["category2"] and runner:
            runner.add(len(rows) - 1, orig, info)   # 立刻投递，不等扫描结束
        else:
            if not row["category2"]:
                row["reason"] = "未匹配（无 mapping 命中，AI 研判未启用）"
            job.bump(STAGE_STAT[row["stage"]])
            n_hit += 1
            _tick(job, prog)
        # 扫描本身也要出声：2611 份 info.json 在 NTFS 上要读 4 分钟，
        # 一条日志不发的话界面看起来就是卡死（7-30 实测静默 251 秒）。
        if i % 200 == 0 or i == n_prod:
            job.log(f"  · {comp} 扫描判定 {i}/{n_prod} "
                    f"（规则/人工命中 {n_hit}，交给 AI {runner.taken if runner else 0}）"
                    f" · 已用 {fmt_duration(time.time() - t_scan)}")
    if job.cancelled:
        return None
    job.log(f"── {comp}：扫描判定完毕 {len(rows)} 个产品，规则/人工命中 {n_hit}，"
            f"交给 AI {runner.taken if runner else 0}")
    if runner:
        runner.finish()
    if job.cancelled:
        return None
    # ---- 落地计划 + 清场（真正的复制在下面那段） ----
    job.set_current(f"{comp} · 计算落地计划…")
    cdst = os.path.join(output_root, comp)
    # 先把每个产品这一轮的目标路径全部算出来（含同名消歧），再清场，最后才复制。
    # **顺序不能反**：清场必须在复制之前 —— 产品 A 上一轮的旧位置，可能正好是
    # 产品 B 这一轮的新位置（消歧后缀腾挪时会发生），清场放在复制之后会把刚写好的 B 删掉。
    #
    # ⚠ 冲突判定走 `_ci_key`（**大小写不敏感**），不能拿裸字符串比 —— 见那里的长注释：
    #   仅差大小写的两个产品名在 NTFS 上是**同一个目录**，按字符串判重会漏掉这一对，
    #   两条计划指向同一个物理位置，后一条把前一条刚写好的整棵 rmtree 掉，落地数少一个
    #   → ② 校验不过 → 整条全流程中止，而且这家公司每次都会重演。
    used = {}
    plan = []
    for r in rows:
        if r["category2"]:
            dst = os.path.join(cdst, r["category1"], r["category2"], r["prod"])
        else:
            # 未匹配：按原目录结构复制到 未匹配目录/<公司>/原大类/[原子类]/产品
            dst = os.path.join(unmatched_root, comp, *r["parts"])
        if _ci_key(dst) in used:  # 同名叶子冲突消歧（含仅大小写不同）
            tag = r["parts"][0]
            cand = f"{dst}（源_{tag}）"; k = 2
            while _ci_key(cand) in used:
                cand = f"{dst}（源_{tag}_{k}）"; k += 1
            dst = cand
        used[_ci_key(dst)] = r["orig"]
        plan.append((r, dst))

    ucomp = os.path.join(unmatched_root, comp)
    n_purged, n_dead, n_shift = _purge_stale(job, comp, plan, croot, proc_dir,
                                             (cdst, ucomp), rows)
    if n_purged:
        job.log(f"── {comp}：清掉 {n_purged} 个产品的上一轮旧落地位置"
                f"（换了分类、或从「未匹配」变成了命中）")
    if n_dead:
        job.log(f"── {comp}：清掉 {n_dead} 个**已不再算产品**的旧产物"
                f"（源目录还在，但已被判定口径排除 —— 多为爬虫缓存目录）")
    if n_shift:
        job.log(f"── {comp}：清掉 {n_shift} 个产品的**旧落地位置**"
                f"（它们在本轮输入目录里换了位置 —— 不认这一条，旧副本会一路复制进交付物）")

    # ---- 落地复制 ----
    # ⚠ **这个循环必须出声。** 它是整个 ② 里最长的一段（整目录 copytree，实测
    # 东莞市蓝宇激光 309 个产品在 drvfs 上约 1.5 MB/s、跑了好几分钟），而它以前
    # 既不写日志也不动进度条 —— 界面上就是「AI 研判 122/122 跑完了，然后彻底不动」，
    # 和卡死完全分不出来。这与 ② 当年那条教训是同一件事：
    # 任何超过几十秒还不出声的阶段，用户都只能理解为卡死。
    n_html_src = 0
    n_plan = len(plan)
    t_copy = time.time()
    for i, (r, dst) in enumerate(plan, 1):
        if os.path.exists(dst):
            shutil.rmtree(dst)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copytree(r["src"], dst)
        try:
            n_html_src += len(P.html_files(os.listdir(r["src"])))
        except OSError:
            pass
        job.set_current(f"{comp} · 复制落地 {i}/{n_plan} · {r['orig']}")
        # 每 20 个报一次吞吐与 ETA（与 _AIRunner._report 同口径）。逐个报会把日志刷爆，
        # 而大公司几千个产品时，用户需要的正是"还要多久"这个数。
        if i % 20 == 0 or i == n_plan:
            el = time.time() - t_copy
            rate = i / el * 60 if el > 0 else 0
            eta = (n_plan - i) / (i / el) if el > 0 and i else 0
            job.log(f"  📁 {comp} 复制落地 {i}/{n_plan} · {rate:.1f} 个/分钟"
                    f" · 预计还需 {fmt_duration(eta)}")
    # 清单带上**产品身份**（页面URL + 叶子名）：下一轮靠它认出"产品在输入树里换了位置"，
    # 少了它，旧落地位置就永远没人认领（见 _purge_stale 的长注释）。
    _save_manifest(proc_dir, {r["orig"]: {"dst": dst,
                                          "url": str(r.get("url") or "").strip(),
                                          "leaf": r["prod"]}
                              for r, dst in plan})
    # ---- 完整性校验：已映射 + 未匹配 的产品目录计数 >= 本次产品数 ----
    # 数产品目录而不是数 info.json：无 json 的产品照样要被校验覆盖，
    # 否则它们复制丢了也不会报警。
    # html 保全：② 是整目录 copytree，源里有多少份 html 就该落地多少份。
    # 用 >= 而不是 ==：映射输出是累积的，上一批（输入目录已被挪走）的 html 也在里面。
    #
    # 产品数与 html 数**一趟走完**：以前是 count_products ×2 + count_html ×2 = 同样两棵树
    # 各走两遍，共四趟全树 walk，drvfs 上每趟几十秒，而且照样一声不吭。
    job.set_current(f"{comp} · 校验落地结果…")
    t_vf = time.time()
    seen_paths = []          # 顺路收集产品路径 → 交给 _report_unclaimed 找孤儿，不多走一趟
    n_map, h_map = P.count_products_html(cdst, collect=seen_paths)
    n_un, h_un = P.count_products_html(ucomp, collect=seen_paths)
    got, got_html = n_map + n_un, h_map + h_un
    job.log(f"  ✓ {comp} 落地校验：产品 {got}（映射 {n_map} + 未匹配 {n_un}）"
            f"，html {got_html}，耗时 {time.time() - t_vf:.0f}s")
    ok = [r for r in rows if r["category2"]]
    un = [r for r in rows if not r["category2"]]
    yp = [r for r in ok if r["stage"] == "ai"]
    mn = [r for r in ok if r["stage"] == "manual"]
    rev = [r for r in ok if "人工确认" in r["reason"]]
    _write_report(comp, rows, proc_dir, unmatched_root)
    dist = Counter((r["category1"], r["category2"]) for r in ok)
    html_ok = (got_html >= n_html_src)
    # got 仍用 >=：映射输出是累积的，早先批次（输入目录已被挪走）的产品也在树里。
    # 差集清场只保证"同一个产品不会同时留在两处"，不保证 got == 本轮 rows。
    verify_ok = (got >= len(rows)) and html_ok
    job.log(f"── {comp}：规则命中 {len(ok)-len(yp)-len(mn)}，沿用人工 {len(mn)}，"
            f"AI研判 {len(yp)}，未匹配 {len(un)}（已入 _未匹配）"
            + (f"，html {got_html}/{n_html_src}" if n_html_src else "")
            + f"，完整性校验 {'✓通过' if verify_ok else '✗不一致!'}")
    if not html_ok:
        job.log(f"  ✗✗ {comp}：html 源文件落地数 {got_html} < 源 {n_html_src}，"
                f"不写映射记忆，下次会重做这家")
    _report_unclaimed(job, comp, seen_paths, plan, proc_dir, (cdst, ucomp))
    if verify_ok:  # 映射记忆：记录已完成公司（供增量扫描与统计分析）
        # in_mtime 是输入侧指纹的另一半，`registry.status_of` 靠它识别
        # "产品数没变但内容动过"。没有它，补进已有产品目录的 html 会被静默忽略。
        registry.update(comp, {"products": len(rows), "total": len(rows),
                               "in_mtime": in_mt,
                               "mapped": len(ok) - len(yp) - len(mn), "ai": len(yp),
                               "manual": len(mn), "unmatched": len(un),
                               "dist": {f"{c1}/{c2}": n for (c1, c2), n in dist.items()}})
    return {"company": comp, "total": len(rows), "mapped": len(ok) - len(yp) - len(mn),
            "ai": len(yp), "manual": len(mn), "unmatched": len(un), "review": len(rev),
            "copied": len(rows), "verify_ok": verify_ok,
            "purged": n_purged, "dead": n_dead, "shift": n_shift,
            "html_src": n_html_src, "html_out": got_html,
            "dist": [{"c1": c1, "c2": c2, "n": n} for (c1, c2), n in dist.most_common()],
            "rows": [{k: r[k] for k in ("orig", "files", "category1", "category2",
                                        "confidence", "matched_keyword", "reason", "stage")}
                     for r in rows],
            "report": os.path.join(proc_dir, "映射报告.md")}


def run_mapping(job, input_root, output_root, companies, model_cfg=None, use_ai=True,
                threads=1, unmatched_root="", process_root="", ai_workers=None):
    """执行映射。

    两个并发度，别混淆：
      threads     公司级并行(1-5) —— 管的是本地文件遍历与复制
      ai_workers  **产品级** AI 研判并发(1-32，默认 8) —— 管的是 LLM 调用

    ai_pool 是**全局共享**的一个池子，所有公司线程都往里投递。这样即使 threads=5，
    在飞的 LLM 调用总数也恒定不超过 ai_workers —— 不会因为公司并行而把并发翻 5 倍
    去撞供应商的限流。
    """
    engine = MappingEngine()
    threads = max(1, min(5, int(threads or 1)))
    ai_workers = max(1, min(32, int(ai_workers or AI_WORKERS_DEFAULT)))
    if not unmatched_root:
        unmatched_root = os.path.join(os.path.dirname(output_root.rstrip("/")), "公司产品数据_未匹配")
    if not process_root:
        process_root = os.path.join(os.path.dirname(output_root.rstrip("/")), "公司产品数据_产品映射_过程记录")
    os.makedirs(output_root, exist_ok=True)
    os.makedirs(unmatched_root, exist_ok=True)
    os.makedirs(process_root, exist_ok=True)
    tasks = []
    for comp in companies:
        croot = os.path.join(input_root, comp)
        # 产品发现的唯一入口。曾经是 glob("**/info.json") —— 没有 info.json 的产品
        # 因此从第②步就被静默丢掉，既不映射也不落地，界面上完全看不出来。
        prods = sorted(dp for dp, _ in P.iter_products(croot))
        tasks.append((comp, croot, prods))
    total = sum(len(t[2]) for t in tasks)
    job.step(0, total)
    ai_on = bool(use_ai and model_cfg)
    job.log(f"共 {len(companies)} 家公司、{total} 个产品，公司级线程 {threads}，"
            + (f"AI研判：开（产品级并发 {ai_workers}）" if ai_on else "AI研判：关"))
    if ai_on:
        big = sorted(((len(p), c) for c, _r, p in tasks), reverse=True)[:3]
        job.log("  产品数最多的公司：" + "、".join(f"{c}({n})" for n, c in big)
                + " —— 整批耗时由最大的一家决定")
    prog = (threading.Lock(), [0])
    summary = []
    ai_pool = ThreadPoolExecutor(max_workers=ai_workers,
                                 thread_name_prefix="aijudge") if ai_on else None
    try:
        if threads == 1:
            for comp, croot, prods in tasks:
                if job.cancelled:
                    break
                s = _map_company(engine, job, comp, croot, prods, output_root,
                                 unmatched_root, process_root, model_cfg, use_ai,
                                 prog, ai_pool)
                if s:
                    summary.append(s)
        else:
            with ThreadPoolExecutor(max_workers=threads) as ex:
                futs = {ex.submit(_map_company, engine, job, comp, croot, prods,
                                  output_root, unmatched_root, process_root,
                                  model_cfg, use_ai, prog, ai_pool): comp
                        for comp, croot, prods in tasks}
                for f in as_completed(futs):
                    try:
                        s = f.result()
                        if s:
                            summary.append(s)
                    except Exception as e:
                        job.log(f"  ✗ 公司 {futs[f]} 处理失败: {e!r}")
            summary.sort(key=lambda x: companies.index(x["company"]))
    finally:
        if ai_pool:
            ai_pool.shutdown(wait=True, cancel_futures=True)
    if summary:
        n_purged = sum(s.get("purged", 0) for s in summary)
        n_shift = sum(s.get("shift", 0) for s in summary)
        if n_purged:
            job.log(f"共清理 {n_purged} 个产品的旧落地位置 —— "
                    f"同一个产品不会再同时留在「未匹配」和映射输出里")
        if n_shift:
            job.log(f"共清理 {n_shift} 个产品的旧落地位置 —— 它们在输入目录里换了位置"
                    f"（换分类/换文件夹），旧副本不清掉就会一路复制进交付物")
        history.add("mapping", f"产品映射 × {len(summary)} 家公司",
                    [s["company"] for s in summary],
                    {"产品": sum(s["total"] for s in summary),
                     "规则命中": sum(s["mapped"] for s in summary),
                     "沿用人工": sum(s.get("manual", 0) for s in summary),
                     "AI研判": sum(s["ai"] for s in summary),
                     "未匹配": sum(s["unmatched"] for s in summary),
                     "html": sum(s.get("html_src", 0) for s in summary),
                     "清理旧位置": n_purged + n_shift},
                    {"输入": input_root, "输出": output_root,
                     "未匹配": unmatched_root, "过程记录": process_root},
                    all(s["verify_ok"] for s in summary))
    return {"companies": summary, "unmatched_root": unmatched_root, "process_root": process_root}


def _write_report(company, rows, proc_dir, unmatched_root):
    ok = [r for r in rows if r["category2"]]
    un = [r for r in rows if not r["category2"]]
    yp = [r for r in ok if r["stage"] == "ai"]
    mn = [r for r in ok if r["stage"] == "manual"]
    hit = [r for r in ok if r["stage"] not in ("ai", "manual")]
    rev = [r for r in hit if "人工确认" in r["reason"]]
    cnt = Counter((r["category1"], r["category2"]) for r in ok)
    L = [f"# {company} 产品映射报告\n",
         "> 生成工具：自动化数据处理平台（product-folder-standardizer 规则）",
         f"> 说明：仅调整分类层级并**复制**落地，产品叶子目录内 info.json / 图片 / PDF **原样保留、零改动**；叶子产品名不变。\n",
         "## 一、总览",
         f"- 产品总数：**{len(rows)}**",
         f"- mapping/规则命中：**{len(hit)}**（其中泛词命中、建议人工确认：{len(rev)}）",
         f"- 沿用既往人工归类（人工匹配/AI复核已定，本次直接继承）：**{len(mn)}**",
         f"- 研判归类（AI 研判兜底，需复核）：**{len(yp)}**",
         f"- 未匹配（已复制到 未匹配目录/{company}/，待人工匹配页处理）：**{len(un)}**",
         "- 目标结构：`公司/category1/category2/产品`（映射输出只含匹配成功的产品）\n",
         "## 二、映射分布（category1 / category2 → 数量）"]
    for (c1, c2), n in cnt.most_common():
        L.append(f"- {c1} / {c2}：{n}")
    L += ["", "## 三、逐产品映射明细",
          "| 原分类路径（大类/[子类/]产品） | 携带文件 | → 现分类（cat1/cat2） | 命中关键词 | 置信度 | 备注 |",
          "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: (not bool(x["category2"]), x["orig"])):
        now = f"{r['category1']}/{r['category2']}" if r["category2"] else "未匹配（已入 _未匹配，待人工处理）"
        L.append(f"| {r['orig']} | {r['files']} | {now} | {r['matched_keyword'] or '—'} | "
                 f"{r['confidence']:.2f} | {r['reason']} |")
    L.append("")
    if yp:
        L.append("## 三.5、研判归类（AI 研判兜底，已进标准树，需复核）")
        L.append("下列产品经关键词规则未命中，由 AI 依据产品资料+专业知识**研判**归入合法分类（置信度 0.90）：")
        for r in sorted(yp, key=lambda x: x["orig"]):
            L.append(f"- `{r['orig']}` → **{r['category1']}/{r['category2']}** —— "
                     + r["reason"].replace("研判归类(AI研判)：", "依据："))
        L.append("")
    if un:
        L.append("## 四、未匹配产品及原因（已按原目录结构复制到 未匹配目录，供人工匹配页处理）")
        for r in un:
            L.append(f"- `{r['orig']}` —— {r['reason'].replace('未匹配原因：', '')}")
        L.append("")
    if rev:
        L.append("## 五、建议人工确认（泛词命中）")
        for r in rev:
            L.append(f"- `{r['orig']}` → {r['category1']}/{r['category2']}"
                     f"（命中泛词“{r['matched_keyword']}”，请确认）")
        L.append("")
    os.makedirs(proc_dir, exist_ok=True)
    open(os.path.join(proc_dir, "映射报告.md"), "w", encoding="utf-8").write("\n".join(L))
