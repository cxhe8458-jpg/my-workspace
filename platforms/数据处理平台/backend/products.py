# -*- coding: utf-8 -*-
"""产品判定的唯一口径。改这里之前先读懂为什么要有这个模块。

以前每个模块各判各的：
  mapping    用 glob("**/info.json") 发现产品
  registry   用「叶子目录 且 含 .json」算指纹
  consolidate/audit 用「叶子目录 且 含 .json」
  stats      用 require_json 开关区分主流水线与额外来源
  variants   用「叶子目录 且 含任意文件」

后果有两层。浅的一层是同一批数据在不同页面上数出不同的产品数（`mapping.count_products`
数的是"任何含 info.json 的目录"、不检查有没有子目录，而 `registry.fingerprint` 只认叶子，
一个带子目录的产品在输入端算、在映射输出端不算，于是凭空多出「有更新」）。

深的一层是**没有 info.json 的产品从第②步就被静默丢掉**——`run_mapping` 唯一的产品入口
就是那句 glob。实测 `/mnt/d/已审核数据` 里有 9 个这样的产品（图片 + PDF、没有 json），
其中 8 个属于「杭州利珀科技股份有限公司」，而这家公司 info.json 数为 0，
`scan_companies` 那句 `if n:` 直接把它整家跳过 —— 它在界面上**根本不存在**。

现在统一为：

    产品 = 叶子目录（没有子目录） 且 含至少一个「产品性文件」

产品性文件 = json / pdf / 图片 / html。这么定而不是"叶子目录含任意文件"，是因为实测输入目录里
有 24 个叶子目录装的是 `.py/.pyc/.txt`（爬虫脚本残留，用户确认不是产品）、
29 个是空目录或只有 `.DS_Store`（那批数据来自 macOS）。

---

**html 源文件（2026-08-04 抓取规范变更后）**

每个产品目录会额外带一份 html（产品页原始快照）。三条规矩：

1. **html 算产品性文件**，所以「只有 html」的叶子目录也是一个产品。不这么定的话这类目录
   会被当成非产品目录整个跳过，界面上根本看不见 —— 与当年「杭州利珀 8 个产品因为没有
   info.json 而整家隐形」是同一类失效，而本模块存在的全部理由就是杜绝它。
2. **html 不参与任何判定**：映射看产品名与目录名、分类看参数载在哪、清洗合并图片与 PDF，
   三处都不读 html，也不因为有没有 html 而改变结论。
3. **html 全程零丢失**：②③⑤ 都是整目录复制，html 自然跟着走；清洗不删不改它；
   ③④⑤ 各有一道 html 数量校验兜底（见 consolidate / audit / idmap）。

所以 html 只出现在两处：`PRODUCT_EXT`（让含它的目录被认成产品）与 `HTML_EXT`
（让各步骤数得出、校验得了）。任何"按扩展名做业务判定"的地方都不该把它算进去。
"""
import os, json

# macOS/Windows 的目录元数据文件，不算内容
JUNK = {".DS_Store", "Thumbs.db", "desktop.ini"}
# 清洗过程中的临时文件前缀
TMP_PREFIX = ".__"

# ---------------- 爬虫工作目录：整棵剪掉，不是产品数据 ----------------
#
# **必须按目录名剔除，靠扩展名过滤是拦不住的。** 爬虫会把抓取过程中的页面缓存写进
# `<公司>/scripts/_cache/<产品名>/`，里面存的是和真产品**一模一样的 jpg**
# （实测「广州飒特红外股份有限公司」154 张）。于是每个缓存目录都是一个"叶子 + 含图片"，
# 完全符合产品判据 —— 实测该公司真实产品 25 个，缓存造出 24 个假产品，
# 分类结果 49 个，差不多翻倍，而且假产品还进了 AI 研判、花了钱、写进了交付物。
#
# 模块开头那段说的「24 个叶子装的是 .py/.pyc/.txt」是旧形态，那种靠扩展名就能滤掉；
# 现在缓存里是真图片，只能按目录名拦。
SKIP_DIRS = {
    "scripts", "script", "_cache", "__cache", ".cache",
    "__pycache__", "node_modules", ".git", ".idea", ".vscode",
    ".ipynb_checkpoints", "$RECYCLE.BIN", "System Volume Information",
}


def prune(dns):
    """os.walk 的 dirnames **原地**剪枝（`dns[:] = ...`，返回新列表是没用的）。

    同时剪掉隐藏目录。所有扫描产品的地方都该经过它 —— 直接用 `walk()` 更省事。
    """
    dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
    return dns


def walk(root):
    """os.walk + 自动剪掉爬虫工作目录。**扫描产品一律走这个，别直接 os.walk。**"""
    for dp, dns, fns in os.walk(root or ""):
        prune(dns)
        yield dp, dns, fns


def is_skipped(rel):
    """相对路径里是否踩到了被剪掉的目录（给"这条旧记录还算数吗"之类的判断用）。"""
    parts = str(rel).replace("\\", "/").split("/")
    return any(p in SKIP_DIRS or p.startswith(".") for p in parts if p)

# ⚠ 图片扩展名以这里为唯一口径，clean / classify3 / audit 一律从本模块导入，别各写一份 ——
# 曾经 classify3 含 .gif 而 clean 不含：一个 .gif 不会被合并却会被记成残留，
# verify_ok 永远为假 → 不写清洗记忆 → 这家公司每次重跑都白跑（旧缺陷 #3）。
IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif", ".svg")
DOC_EXT = (".pdf",)
JSON_EXT = (".json",)
HTML_EXT = (".html", ".htm")
PRODUCT_EXT = JSON_EXT + DOC_EXT + IMG_EXT + HTML_EXT
_PRODUCT_EXT_SET = frozenset(PRODUCT_EXT)


def real_files(fns):
    """滤掉目录元数据与临时文件后，真正算内容的文件名。"""
    return [f for f in fns if f not in JUNK and not f.startswith(TMP_PREFIX)]


def is_product_files(fns):
    """给定一层目录的文件名列表，判断它是否承载一个产品。

    这个函数会被调用几万次（每个叶子目录一次，统计扫描要遍历一万多个产品），
    所以写法要抠：**短路返回 + 集合查扩展名**，不要先建中间列表、也不要对每个文件
    做 `.lower().endswith(十元组)` —— 那样写实测把全量扫描从 94 秒拖到 346 秒。
    """
    for f in fns:
        if f in JUNK or f.startswith(TMP_PREFIX):
            continue
        i = f.rfind(".")
        if i > 0 and f[i:].lower() in _PRODUCT_EXT_SET:
            return True
    return False


def is_product_dir(dns, fns):
    """叶子目录（无子目录）且含产品性文件。os.walk 的 (dirnames, filenames) 直接传进来。"""
    return not dns and is_product_files(fns)


def iter_products(root):
    """遍历 root 下的产品目录，yield (绝对路径, 相对 root 的路径)。"""
    if not os.path.isdir(root):
        return
    for dp, dns, fns in walk(root):
        if is_product_dir(dns, fns):
            yield dp, os.path.relpath(dp, root)


def count_products(root):
    return sum(1 for _ in iter_products(root))


def json_name(dp, fns=None):
    """产品目录里的 json 文件名，没有返回 None。

    优先级：info.json（映射输出里的原始形态）→ <目录名>.json（清洗后被改成这个名字）
    → 任意一个 .json。没有 json 不是错误，这类产品的参数在图或 PDF 里。
    """
    fns = os.listdir(dp) if fns is None else fns
    if "info.json" in fns:
        return "info.json"
    own = os.path.basename(dp) + ".json"
    if own in fns:
        return own
    js = sorted(f for f in real_files(fns) if f.lower().endswith(".json"))
    return js[0] if js else None


def load_info(dp, fns=None):
    """读产品的 json，读不到或没有一律返回 {} —— 调用方不必区分这两种情况。"""
    n = json_name(dp, fns)
    if not n:
        return {}
    try:
        d = json.load(open(os.path.join(dp, n), encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def product_name(dp, fns=None):
    """产品名：json 里的「产品名」优先，回退目录叶子名。

    没有 json 的产品只能靠目录名——所幸目录名本来就是产品名（爬取时就是这么落盘的），
    映射判定链的「产品名命中」和「父级继承」两步照常可用。
    """
    return str(load_info(dp, fns).get("产品名") or "").strip() or os.path.basename(dp)


def has_pdf(fns):
    return any(f.lower().endswith(DOC_EXT) for f in real_files(fns))


def has_json(fns):
    return any(f.lower().endswith(JSON_EXT) for f in real_files(fns))


def has_image(fns):
    return any(f.lower().endswith(IMG_EXT) for f in real_files(fns))


# ---------------- html 源文件 ----------------
def html_files(fns):
    """一层目录里的 html 源文件名（已滤掉元数据与临时文件）。"""
    return [f for f in real_files(fns) if f.lower().endswith(HTML_EXT)]


def has_html(fns):
    return any(f.lower().endswith(HTML_EXT) for f in real_files(fns))


def count_html(root):
    """root 子树下**产品目录内**的 html 总数。

    只数产品目录里的，不是 `find -name '*.html'` —— 过程记录、报告之类的非产品目录
    不该被算进来，否则各步骤之间的 html 数量根本对不上，校验就成了噪声。
    """
    n = 0
    for dp, dns, fns in walk(root):
        if is_product_dir(dns, fns):
            n += len(html_files(fns))
    return n


def count_products_html(root, collect=None):
    """一趟 walk 同时数出 (产品数, 产品目录里的 html 数)。

    ② 映射落地后的校验要这两个数，以前是 `count_products(r) + count_html(r)` 分开调，
    **同一棵树被走了两遍**；而校验涉及两棵树（映射输出与未匹配），一共四趟。
    /mnt/d 上每趟都是几十秒起，且这段期间任务一声不吭。两个数来自同一个判定，
    本来就该一趟数完。

    collect：传一个 list 进来，把每个产品目录的路径追加进去。② 的落地校验要拿它跟
    本轮落地计划对账、找出**没人认领的历史孤儿**（见 mapping._report_unclaimed）——
    同一次 walk，不多花一秒。
    """
    n = h = 0
    for dp, dns, fns in walk(root):
        if is_product_dir(dns, fns):
            n += 1
            h += len(html_files(fns))
            if collect is not None:
                collect.append(dp)
    return n, h


def html_index(root):
    """root 子树下 {产品相对路径: html 文件名集合}。给"谁的 html 丢了"定位用。"""
    out = {}
    for dp, dns, fns in walk(root):
        if is_product_dir(dns, fns):
            h = html_files(fns)
            if h:
                out[os.path.relpath(dp, root)] = set(h)
    return out
