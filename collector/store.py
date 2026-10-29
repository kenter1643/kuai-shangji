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
    category      TEXT,
    categories    TEXT,
    created_at    INTEGER,
    updated_at    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_region ON tenders(region);
CREATE INDEX IF NOT EXISTS idx_stage  ON tenders(stage);
CREATE INDEX IF NOT EXISTS idx_time   ON tenders(pub_time);
CREATE TABLE IF NOT EXISTS meta(
    k TEXT PRIMARY KEY, v TEXT
);
/* 正文与结构化信息单独成表：正文体积大（单条数 KB ~ 数十 KB），
   与主表分离后列表查询、导出都不会被正文拖慢，H5 也是按需加载。 */
CREATE TABLE IF NOT EXISTS tender_content(
    id          TEXT PRIMARY KEY,
    content     TEXT,
    structured  TEXT,
    char_len    INTEGER DEFAULT 0,
    fetched_at  INTEGER
);
/* 联系人单独成表：一条公告可有多位联系人（采购人 / 代理机构 / 项目联系人），
   单独存才能按人检索、复用到「通讯录」，而不是淹没在正文里。 */
CREATE TABLE IF NOT EXISTS contacts(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id  TEXT,
    role       TEXT,
    org        TEXT,
    name       TEXT,
    mobile     TEXT,
    phone      TEXT,
    email      TEXT,
    title      TEXT,
    region     TEXT,
    source     TEXT,
    pub_time   TEXT,
    created_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ct_tender ON contacts(tender_id);
CREATE INDEX IF NOT EXISTS idx_ct_phone  ON contacts(mobile, phone);
"""

FIELDS = ["id", "title", "url", "pub_time", "buyer", "agency", "notice_type",
          "category_path", "region", "stage", "source", "source_type", "amount",
          "winner", "deadline", "contact", "phone", "detail_flag",
          "category", "categories"]

# 老库没有分类字段，启动即补列，避免手工删库重采
MIGRATIONS = [("category", "TEXT"), ("categories", "TEXT"),
              ("content_flag", "INTEGER DEFAULT 0")]


from collector.classify import classify_record            # noqa: E402


class Store:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(tenders)")}
        for col, typ in MIGRATIONS:
            if col not in have:
                self.conn.execute(f"ALTER TABLE tenders ADD COLUMN {col} {typ}")
        # 索引必须等列补上之后再建，否则老库建索引会报 no such column
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_cat ON tenders(category)")

    def upsert(self, rec: dict) -> bool:
        """插入或补全。返回 True 表示新增记录。"""
        now = int(time.time())
        # 入库即分类：列表阶段就能定类，详情补抓只影响金额/中标人等字段
        if not rec.get("category"):
            rec["category"], rec["categories"] = classify_record(rec)
        cur = self.conn.execute("SELECT id FROM tenders WHERE id=?", (rec["id"],))
        existed = cur.fetchone() is not None
        if existed:
            if not rec.get("detail_flag"):
                return False  # 列表数据不覆盖已抓过的详情数据
            sets, vals = [], []
            for k, v in rec.items():
                if k in ("id",) or k not in FIELDS:
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

    def save_content(self, tid: str, content: str, structured: dict) -> None:
        """保存正文与结构化信息（与 tenders 一对一）。"""
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO tender_content(id,content,structured,char_len,fetched_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET content=excluded.content,"
            " structured=excluded.structured, char_len=excluded.char_len,"
            " fetched_at=excluded.fetched_at",
            (tid, content, json.dumps(structured, ensure_ascii=False),
             len(content or ""), now))
        self.conn.execute("UPDATE tenders SET content_flag=1 WHERE id=?", (tid,))

    def save_contacts(self, tid: str, rows: list[dict], base: dict) -> int:
        """保存联系人。同一公告重抓时先清旧记录，避免累积重复。"""
        self.conn.execute("DELETE FROM contacts WHERE tender_id=?", (tid,))
        n = 0
        for r in rows:
            if not (r.get("name") or r.get("mobile") or r.get("phone")):
                continue
            self.conn.execute(
                "INSERT INTO contacts(tender_id,role,org,name,mobile,phone,email,"
                "title,region,source,pub_time,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, r.get("role") or "项目联系人", r.get("org") or "",
                 r.get("name") or "", r.get("mobile") or "", r.get("phone") or "",
                 r.get("email") or "", base.get("title") or "",
                 base.get("region") or "", base.get("source") or "",
                 base.get("pub_time") or "", int(time.time())))
            n += 1
        return n

    def pending_content(self, limit: int) -> list[dict]:
        """已抓详情但还没存正文的记录（正文补录用）。"""
        cur = self.conn.execute(
            "SELECT id,url,source FROM tenders WHERE detail_flag=1 AND "
            "(content_flag IS NULL OR content_flag=0) ORDER BY created_at DESC LIMIT ?",
            (limit,))
        return [{"id": r[0], "url": r[1], "source": r[2]} for r in cur.fetchall()]

    def get(self, tid: str) -> dict | None:
        cur = self.conn.execute("SELECT " + ",".join(FIELDS) + " FROM tenders WHERE id=?",
                                (tid,))
        row = cur.fetchone()
        return dict(zip(FIELDS, row)) if row else None

    def stats(self) -> dict:
        q = lambda s: self.conn.execute(s).fetchone()[0]          # noqa: E731
        return {
            "total": q("SELECT COUNT(*) FROM tenders"),
            "with_content": q("SELECT COUNT(*) FROM tender_content"),
            "contacts": q("SELECT COUNT(*) FROM contacts"),
            "with_mobile": q("SELECT COUNT(*) FROM contacts WHERE mobile<>''"),
        }

    def backfill_category(self, only_missing: bool = True) -> tuple[int, dict]:
        """分类补录：对历史数据重算行业分类。

        only_missing=True 只补空分类（词典小改后的增量场景）；
        False 则全量重算（词典大改后的整体刷新）。返回 (更新条数, 类目分布)。
        """
        sql = "SELECT id,title,buyer,agency,category FROM tenders"
        if only_missing:
            sql += " WHERE category IS NULL OR category=''"
        rows = self.conn.execute(sql).fetchall()
        dist: dict[str, int] = {}
        for rid, title, buyer, agency, _ in rows:
            cat, cats = classify_record({"title": title, "buyer": buyer,
                                         "agency": agency})
            self.conn.execute(
                "UPDATE tenders SET category=?, categories=? WHERE id=?",
                (cat, cats, rid))
            dist[cat] = dist.get(cat, 0) + 1
        self.conn.commit()
        return len(rows), dist

    def export_details(self, out_path: str) -> int:
        """导出详情包（正文 + 结构化信息 + 联系人），供 H5 点开详情时按需加载。

        独立文件而非并入 tenders.json：正文体积是列表数据的数倍，
        首屏只需要列表，详情等用户点开再拉。
        """
        cur = self.conn.execute(
            "SELECT c.id,c.content,c.structured,t.title,t.url,t.stage,t.region,"
            "t.source,t.pub_time FROM tender_content c JOIN tenders t ON t.id=c.id")
        items = {}
        for cid, content, structured, title, url, stage, region, src, pub in cur.fetchall():
            try:
                st = json.loads(structured) if structured else {}
            except ValueError:
                st = {}
            rows = self.conn.execute(
                "SELECT role,org,name,mobile,phone,email FROM contacts "
                "WHERE tender_id=? ORDER BY id", (cid,)).fetchall()
            items[cid] = {
                "t": title or "", "u": url or "", "s": stage or "",
                "r": region or "", "src": src or "", "p": pub or "",
                "c": content or "", "f": st,
                "ct": [{"role": a, "org": b, "name": c, "mobile": d, "phone": e,
                        "email": f} for a, b, c, d, e, f in rows],
            }
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "count": len(items), "items": items},
                      f, ensure_ascii=False, separators=(",", ":"))
        return len(items)

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
        # categories 以 JSON 文本存库，导出时还原成数组给前端做多选筛选
        for row in rows:
            cats = None
            if row.get("categories"):
                try:
                    cats = json.loads(row["categories"])
                except ValueError:
                    cats = None
            if not cats:
                cats = [row.get("category") or "其他"]
            # 单标签时不重复写数组，省体积（前端按 category 兜底）
            row["categories"] = [] if len(cats) <= 1 else cats
        # 空字段不落盘：绝大多数记录没有 winner/deadline/contact/phone，
        # 全量写出会让 JSON 平白大一倍（实测 7.9M → 4.3M）。
        # 前端 norm() 对缺失字段有默认值，读不到就当空串处理。
        rows = [{k: v for k, v in row.items()
                 if v not in (None, "") and not (k == "categories" and not v)}
                for row in rows]
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
        return len(rows)

    def close(self):
        self.conn.close()
