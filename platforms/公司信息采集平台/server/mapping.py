# -*- coding: utf-8 -*-
"""字段映射：把 script.py 抓取的 25 字段记录，映射成 MySQL company 表 34 字段。

翻译规则（按皇上要求）：
  - 地址 location      ：英文；中文则译英
  - 概述 overview      ：英文；中文则译英      cnOverview：中文；英文则译中
  - 口号 slogan        ：英文；中文则译英      cnSlogan  ：中文；英文则译中
  - 标签 tags          ：中文；英文则译中
  - cnOriOverview      ：多余，不处理、不填

原始抓取字段完整保留一份（raw 字段），不因翻译/映射丢失任何信息。
"""
import sys
import os
import re
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from . import snowflake           # noqa: E402  （同包导入，server 作为包运行）
from . import translate as _tr    # noqa: E402

# MySQL company 表的字段白名单（仅这些字段允许写入，防误写/注入）
DB_COLUMNS = [
    "id", "name", "cnName", "location", "overview", "slogan", "logoExt",
    "cnOverview", "cnOriOverview", "cnSlogan", "email", "mobile", "phone",
    "fax", "memo", "website", "updatedBy", "updatedAt", "nation", "metaTitle",
    "metaKeywords", "metaDescription", "industryId", "isRecommended", "initial",
    "isEnabled", "source", "createdAt", "tags", "originUrl", "accessCount",
    "type", "company_alias", "check_status",
]

# DB 字段 -> 中文名（导出 Excel sheet1 表头用）
DB_FIELD_CN = {
    "id": "记录ID", "name": "英文名称", "cnName": "中文名称",
    "location": "地址(英)", "overview": "概述(英)", "slogan": "口号(英)",
    "logoExt": "Logo后缀", "cnOverview": "概述(中)", "cnOriOverview": "原始概述(中)",
    "cnSlogan": "口号(中)", "email": "邮箱", "mobile": "手机", "phone": "电话",
    "fax": "传真", "memo": "备注", "website": "官网", "updatedBy": "更新人",
    "updatedAt": "更新时间", "nation": "国别", "metaTitle": "页面标题",
    "metaKeywords": "页面关键词", "metaDescription": "页面描述", "industryId": "行业ID",
    "isRecommended": "是否推荐", "initial": "首字母", "isEnabled": "是否启用",
    "source": "来源", "createdAt": "创建时间", "tags": "标签", "originUrl": "原始网址",
    "accessCount": "访问次数", "type": "类型", "company_alias": "别名",
    "check_status": "审核状态",
}


# ---------------------------------------------------------------------------
# 审核界面用的字段元数据
# ---------------------------------------------------------------------------
# 分组展示：34 个字段平铺成一张长表没人看得下去，按「人要核什么」分组。
DB_FIELD_GROUPS = [
    ("基础信息", ["cnName", "name", "website", "nation", "company_alias", "initial", "source"]),
    ("联系方式", ["location", "email", "mobile", "phone", "fax"]),
    ("内容文案", ["cnOverview", "overview", "cnSlogan", "slogan", "tags", "memo"]),
    ("业务与状态", ["industryId", "type", "check_status", "isEnabled", "isRecommended", "accessCount"]),
    ("系统字段", ["id", "createdAt", "updatedAt", "updatedBy", "logoExt", "originUrl",
                  "cnOriOverview", "metaTitle", "metaKeywords", "metaDescription"]),
]

# 高风险字段：值全部由大模型从 HTML 里读出/改写，最容易张冠李戴或漏采，
# 界面上默认展开、加重点标记，提醒必须逐条对着官网看。
RISKY_COLUMNS = {
    "cnName", "name", "website", "nation", "location",
    "cnOverview", "overview", "cnSlogan", "slogan", "tags",
    "email", "mobile", "phone", "fax",
}

# 允许人工修正的字段。id 与两个时间戳由程序生成，改了只会制造混乱。
NON_EDITABLE = {"id", "createdAt", "updatedAt"}
EDITABLE_COLUMNS = [c for c in DB_COLUMNS if c not in NON_EDITABLE]

# 导出 Excel 时**默认不导出**的字段（用户 2026-09-04 指定）：
# 都是系统自动生成/维护的列，人工导库时不需要、也不该被覆盖。
# 用户可在「导出字段」里自行选回来。
EXPORT_DEFAULT_SKIP = {
    "updatedAt", "updatedBy", "source", "createdAt",
    "originUrl", "accessCount", "check_status", "type",
}


# 抓取的 25 个原始字段里，这几个虽然**不入库**，却是判断分类/属性对不对的关键，
# 审核时必须看得见（尤其 category_level1/2 是必填项，且要映射到固定 12 个一级分类）。
RAW_KEY_FIELDS = ["attribute", "category_level1", "category_level2", "industry",
                  "main_products", "company_nature", "factory_nation",
                  "cn_address", "address", "original_name"]


def _has_cjk(s):
    return any("\u4e00" <= c <= "\u9fff" for c in (s or ""))


def _wrap_html(text):
    """概述字段包一层 <p>，与表里既有数据格式一致。"""
    t = (text or "").strip()
    if not t:
        return None
    if t.startswith("<"):
        return t
    return "<p>%s</p>" % t


def _pick_en(cn, en, translate_fn):
    """保证有英文：优先取英文，其次中文译英。"""
    if en and str(en).strip():
        return str(en).strip()
    if cn and str(cn).strip():
        return translate_fn(str(cn).strip(), "en")
    return None


def _pick_zh(cn, en, translate_fn):
    """保证有中文：优先取中文，其次英文译中。"""
    if cn and str(cn).strip():
        return str(cn).strip()
    if en and str(en).strip():
        return translate_fn(str(en).strip(), "zh")
    return None


def build_db_record(record, defaults, origin_url=None):
    """把一条抓取记录 record（25字段 dict）转成待写入 MySQL 的 dict。

    translate_fn 由调用方注入（便于离线测试）；默认用 translate_text。
    """
    translate_fn = _tr.translate_text

    # 原始字段完整保留（绝不丢字段）
    raw = {k: record.get(k) for k in record} if isinstance(record, dict) else {}

    cn_name = (record.get("cn_name") or "").strip()
    en_name = (record.get("name") or "").strip()
    cn_address = (record.get("cn_address") or "").strip()
    en_address = (record.get("address") or "").strip()
    cn_overview = (record.get("cn_overview") or "").strip()
    en_overview = (record.get("overview") or "").strip()
    slogan_raw = (record.get("slogan") or "").strip()
    tags_raw = (record.get("tags") or "").strip()

    # --- 地址：英文为主，中文译英补齐 ---
    location = _pick_en(cn_address, en_address, translate_fn)

    # --- 概述：中英互补 ---
    overview_en = _pick_en(cn_overview, en_overview, translate_fn)
    overview_cn = _pick_zh(cn_overview, en_overview, translate_fn)

    # --- 口号：中英互补 ---
    slogan_en = slogan_cn = None
    if slogan_raw:
        if _has_cjk(slogan_raw):
            slogan_cn = slogan_raw
            slogan_en = translate_fn(slogan_raw, "en") or None
        else:
            slogan_en = slogan_raw
            slogan_cn = translate_fn(slogan_raw, "zh") or None

    # --- 标签：中文为主，英文译中补齐 ---
    tags = tags_raw or None
    if tags and not _has_cjk(tags):
        tags = translate_fn(tags, "zh") or tags

    # --- 自动生成字段 ---
    now = datetime.now()
    name_for_initial = en_name or cn_name
    initial = name_for_initial[0].upper() if name_for_initial else None

    row = {
        "id": snowflake.new_id(),
        "name": en_name or None,
        "cnName": cn_name or None,
        "location": location,
        "overview": _wrap_html(overview_en),
        "slogan": slogan_en,
        # logo 后缀**永远留空**：logo 要由人工上传到服务器之后才谈得上填后缀，
        # 抓取阶段猜一个 ".png" 只会让库里出现"有后缀却没有图"的假数据。（用户 2026-09-03 明确）
        "logoExt": None,
        "cnOverview": _wrap_html(overview_cn),
        "cnOriOverview": None,                       # 多余字段，不填
        "cnSlogan": slogan_cn,
        "email": record.get("email") or None,
        "mobile": record.get("mobile") or None,
        "phone": record.get("phone") or None,
        "fax": record.get("fax") or None,
        "memo": None,
        "website": record.get("website") or None,
        "updatedBy": defaults.get("updatedBy", "admin"),
        "updatedAt": now,
        "nation": record.get("nation") or None,
        "metaTitle": None,
        "metaKeywords": None,
        "metaDescription": None,
        "industryId": defaults.get("industryId", 0),
        "isRecommended": defaults.get("isRecommended", 0),
        "initial": initial,
        "isEnabled": defaults.get("isEnabled", 1),
        "source": record.get("data_source") or "official_website",
        "createdAt": now,
        "tags": tags,
        "originUrl": origin_url or record.get("website") or None,
        "accessCount": defaults.get("accessCount", 0),
        "type": defaults.get("type"),
        "company_alias": record.get("alias") or None,
        "check_status": defaults.get("checkStatus", 0),
    }
    # 附上原始抓取字段（供前端预览/留档，写入时会被剥离）
    row["_raw"] = raw
    return row
