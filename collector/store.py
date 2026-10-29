"""存储层：SQLite 去重 + 结构化入库 + 导出前端 JSON。

去重键使用 URL 的 md5 短摘要（uid_of），保证跨源重复只会合并一次，
并且二次采集时对已存在记录做字段补全而非覆盖（详情页增量补齐依赖这点）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenders(
    id            TEXT PRIMARY KEY,
    title         TEXT,
    url           TEXT,
    pub_time      TEXT,
    buyer         TEXT,
    agency        TEXT,
    notice_type   TEXT,
    category_path TEXT,
    region        TEXT,
    stage         TEXT,
    source        TEXT,
    source_type   TEXT,
    amount        REAL,
    winner        TEXT,
    deadline      TEXT,
    contact       TEXT,
    phone         TEXT,
    detail_flag   INTEGER DEFAULT 0,
    created_at    INTEGER,
    updated_at    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_region ON tenders(region);
CREATE INDEX IF NOT EXISTS idx_stage  ON tenders(stage);
CREATE INDEX IF NOT EXISTS idx_time   ON tenders(pub_time);
CREATE TABLE IF NOT EXISTS meta(
    k TEXT PRIMARY KEY, v TEXT
);
"""

FIELDS = ["id", "title", "url", "pub_time", "buyer", "agency", "notice_type",
          "category_path", "region", "stage", "source", "source_type", "amount",
          "winner", "deadline", "contact", "phone", "detail_flag"]


class Store:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def upsert(self, rec: dict) -> bool:
        """插入或补全。返回 True 表示新增记录。"""
        now = int(time.time())
        cur = self.conn.execute("SELECT id FROM tenders WHERE id=?", (rec["id"],))
        existed = cur.fetchone() is not None
        if existed:
            if not rec.get("detail_flag"):
                return False  # 列表数据不覆盖已抓过的详情数据
            sets, vals = [], []
            for k, v in rec.items():
                if k in ("id",):
                    continue
                if v in (None, ""):
                    continue
                sets.append(f"{k}=?")
                vals.append(v)
            if not sets:
                return False
            vals += [now, rec["id"]]
            self.conn.execute(
                f"UPDATE tenders SET {','.join(sets)}, updated_at=? WHERE id=?", vals)
        else:
            cols = ["id"] + [k for k in FIELDS if k != "id"]
            vals = [rec["id"]] + [rec.get(k) for k in FIELDS if k != "id"]
            vals += [now, now]
            q = ",".join(["?"] * (len(cols) + 2))
            self.conn.execute(
                f"INSERT INTO tenders ({','.join(cols)},created_at,updated_at) VALUES ({q})",
                vals)
        return not existed

    def bulk(self, rows: Iterable[dict]) -> int:
        n = 0
        for r in rows:
            n += bool(self.upsert(r))
        self.conn.commit()
        return n

    def pending_detail(self, limit: int, prefer_stage: str | None = "中标结果") -> list[dict]:
        """取未抓详情的记录。中标结果类优先 —— 中标人/中标金额只存在于这类正文，
        详情配额有限时必须先喂给它，否则「对手分析」拿不到样本。"""
        if prefer_stage:
            cur = self.conn.execute(
                "SELECT id,url FROM tenders WHERE detail_flag=0 "
                "ORDER BY (stage=?) DESC, created_at DESC LIMIT ?",
                (prefer_stage, limit))
        else:
            cur = self.conn.execute(
                "SELECT id,url FROM tenders WHERE detail_flag=0 "
                "ORDER BY created_at DESC LIMIT ?", (limit,))
        return [{"id": r[0], "url": r[1]} for r in cur.fetchall()]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM tenders").fetchone()[0]

    def set_meta(self, k, v):
        self.conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, v))
        self.conn.commit()

    def export(self, out_path: str, keep_other: bool = False) -> int:
        """导出前端 JSON。

        默认**不导出 stage='其他'**。这一档在广东源里是电子卖场合同公告 /
        验收结果公告（占全库一半以上、7000+ 条），属于**已结束的交易记录**，
        不是商机 —— 全量导出既拖慢 H5 首屏（7MB），也让商机列表被噪音淹没。
        数据仍完整保留在 SQLite 里，需要时用 `--export-all` 导出即可。
        """
        sql = "SELECT " + ",".join(FIELDS) + ",created_at FROM tenders"
        if not keep_other:
            sql += " WHERE stage <> '其他'"
        sql += " ORDER BY created_at DESC"
        cur = self.conn.execute(sql)
        rows = [dict(zip(FIELDS + ["created_at"], r)) for r in cur.fetchall()]
        # 空字段不落盘：绝大多数记录没有 winner/deadline/contact/phone，
        # 全量写出会让 JSON 平白大一倍（实测 7.9M → 4.3M）。
        # 前端 norm() 对缺失字段有默认值，读不到就当空串处理。
        rows = [{k: v for k, v in row.items() if v not in (None, "")}
                for row in rows]
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
        return len(rows)

    def close(self):
        self.conn.close()
