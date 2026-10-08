# -*- coding: utf-8 -*-
"""MySQL 连接与安全写入。

安全原则（皇上强调：库里数据多，误操作丢数据很危险）：
  - 只做 INSERT（新增），绝不 UPDATE / DELETE / TRUNCATE。
  - 字段白名单：只写 mapping.DB_COLUMNS 与表实际字段的交集。
  - 参数化查询（防注入）。
  - 按 website 去重：已存在则跳过，不覆盖。
  - 单条失败不影响其余。
"""
import pymysql

from server import mapping


def _conn(cfg):
    d = cfg["db"]
    return pymysql.connect(
        host=d.get("host", "127.0.0.1"),
        port=int(d.get("port", 3306)),
        user=d.get("username", ""),
        password=d.get("password", ""),
        database=d.get("database", ""),
        charset="utf8mb4",
        connect_timeout=10,
    )


def test_connection(cfg):
    """测试连接。返回 (ok, message, columns)。columns 为表实际字段列表。"""
    try:
        conn = _conn(cfg)
        try:
            with conn.cursor() as cur:
                table = cfg["db"].get("table", "company")
                cur.execute("SHOW COLUMNS FROM `%s`" % table)
                columns = [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
        return True, "连接成功，表「%s」共 %d 个字段" % (cfg["db"].get("table"), len(columns)), columns
    except Exception as e:
        return False, "连接失败：%s" % e, []


def _existing_websites(conn, table):
    """读出表里已有的 website 集合，用于去重。"""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT `website` FROM `%s` WHERE `website` IS NOT NULL" % table)
            return {r[0] for r in cur.fetchall() if r[0]}
    except Exception:
        return set()


def insert_records(cfg, records, columns=None):
    """把映射后的记录安全写入。返回 (inserted, skipped, errors)。

    records: build_db_record 产出的 dict 列表（含 _raw，写入时会剥离）。
    columns: 表实际字段（test_connection 拿到），缺省则现场查一次。
    """
    if not records:
        return 0, 0, []
    d = cfg["db"]
    table = d.get("table", "company")

    conn = _conn(cfg)
    inserted = skipped = 0
    errors = []
    try:
        if columns is None:
            with conn.cursor() as cur:
                cur.execute("SHOW COLUMNS FROM `%s`" % table)
                columns = [r[0] for r in cur.fetchall()]
        cols = [c for c in mapping.DB_COLUMNS if c in columns]  # 白名单 ∩ 实际字段

        existing = _existing_websites(conn, table)

        for rec in records:
            rec = {k: v for k, v in rec.items() if not k.startswith("_")}
            website = rec.get("website")
            if website and website in existing:
                skipped += 1
                continue
            row_keys = [c for c in cols if c in rec and rec[c] is not None]
            if not row_keys:
                skipped += 1
                continue
            placeholders = ", ".join(["%s"] * len(row_keys))
            sql = "INSERT INTO `%s` (`%s`) VALUES (%s)" % (
                table, "`, `".join(row_keys), placeholders)
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, [rec[k] for k in row_keys])
                conn.commit()
                inserted += 1
                if website:
                    existing.add(website)
            except Exception as e:
                conn.rollback()
                errors.append("「%s」写入失败：%s" % (rec.get("cnName") or rec.get("name") or website, e))
    finally:
        conn.close()
    return inserted, skipped, errors
