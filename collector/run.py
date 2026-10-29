"""快商机 · 采集入口

    python3 -m collector.run                     # 采集全部地区
    python3 -m collector.run --regions 北京市,广东省
    python3 -m collector.run --pages 3 --detail 60

分两阶段跑：
  1) 列表：遍历地区 × 分页，高速、低成本，覆盖全国；
  2) 详情：按配额补抓正文，抽取金额/中标人/截止时间（高成本，可控量）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from collector.base import HttpClient                       # noqa: E402
from collector.extract import extract_contacts, structure_of  # noqa: E402
from collector.store import Store                           # noqa: E402
from collector.sources.ccgp import CcgpSource, REGIONS      # noqa: E402
from collector.sources.gdgpo import GdGpoSource, REGIONS as GD_REGIONS  # noqa: E402

DB = os.path.join(ROOT, "data", "tenders.db")
OUT = os.path.join(ROOT, "data", "tenders.json")
DETAILS = os.path.join(ROOT, "data", "details.json")
STATUS = os.path.join(ROOT, "data", "status.json")

SOURCES = {
    "ccgp": (CcgpSource, REGIONS),
    "gdgpo": (GdGpoSource, GD_REGIONS),
}


def source_for_url(url: str, client: HttpClient):
    """按 URL 判断该用哪个适配器 —— 正文补录要跨源混跑，不能靠 --source 指定。"""
    return GdGpoSource(client) if "gdgpo.czt.gd.gov.cn" in url else CcgpSource(client)


def enrich(store: Store, src, item: dict) -> bool:
    """抓一条详情并落库：主表补字段 + 正文 / 结构化 / 联系人三件套。"""
    rec = src.fetch_detail(item["url"])
    if not rec:
        return False
    rec["id"] = item["id"]
    rec.setdefault("detail_flag", 1)      # 广东源适配器未设该标记，这里兜底
    content = rec.pop("content", "") or ""
    store.upsert(rec)
    if not content:
        return True
    base = store.get(item["id"]) or {}
    base = {**base, **{k: v for k, v in rec.items() if v not in (None, "")}}
    store.save_content(item["id"], content, structure_of(content, base))
    rows = extract_contacts(content, base.get("buyer") or "",
                            base.get("agency") or "")
    store.save_contacts(item["id"], rows, base)
    return True


def build_regions(names: str | None, default_regions):
    if not names or names == "all":
        return default_regions, [n for n, _ in default_regions]
    want = [w.strip() for w in names.split(",") if w.strip()]
    sel = []
    for w in want:
        for nm, zid in default_regions:
            if w == nm or nm.startswith(w) or w.startswith(nm[:2]) and nm.startswith(w):
                if (nm, zid) not in sel:
                    sel.append((nm, zid))
                break
    return sel or default_regions, [n for n, _ in sel or default_regions]


def main():
    ap = argparse.ArgumentParser(description="快商机 · 全国招投标商机采集器")
    ap.add_argument("--regions", default="all", help="逗号分隔地区名，或 all")
    ap.add_argument("--pages", type=int, default=2, help="每个地区列表翻页数")
    ap.add_argument("--detail", type=int, default=40, help="补抓详情的条数配额")
    ap.add_argument("--delay", type=float, default=1.1, help="请求间隔秒数")
    ap.add_argument("--types", type=int, default=1, help="是否额外遍历公告类型")
    ap.add_argument("--skip-list", action="store_true", help="跳过列表采集，只补详情")
    ap.add_argument("--reset-stage", default="",
                    help="把该阶段的详情标记重置为未抓，便于用新解析逻辑重抓")
    ap.add_argument("--source", default="ccgp", choices=list(SOURCES))
    ap.add_argument("--kw", default="", help="关键词（目前仅 gdgpo 源支持服务端检索）")
    ap.add_argument("--export-all", action="store_true",
                    help="导出时也包含 stage=其他（电子卖场合同/验收等已结束交易，默认排除）")
    ap.add_argument("--backfill-category", action="store_true",
                    help="只做行业分类补录（不联网），可用 --recat 全量重算")
    ap.add_argument("--recat", action="store_true",
                    help="配合 --backfill-category：覆盖已有分类全量重算")
    ap.add_argument("--rebuild-content", type=int, default=0,
                    help="补抓已采集详情的正文（存库 + 结构化 + 联系人）")
    ap.add_argument("--reparse", action="store_true",
                    help="不联网，用库内已有正文重算结构化信息与联系人（改抽取逻辑后用）")
    args = ap.parse_args()

    client = HttpClient(delay=args.delay)
    store = Store(DB)

    if args.backfill_category:
        n, dist = store.backfill_category(only_missing=not args.recat)
        out = store.export(OUT, keep_other=args.export_all)
        store.set_meta("last_classify", time.strftime("%Y-%m-%d %H:%M:%S"))
        store.close()
        print(f"[分类补录] 重算 {n} 条（{'全量' if args.recat else '仅空分类'}）")
        for k, v in sorted(dist.items(), key=lambda kv: -kv[1]):
            print(f"    {k:<12} {v:>6}")
        print(f"[导出] {OUT}（{out} 条）")
        return

    if args.reparse:
        rows = store.conn.execute(
            "SELECT c.id,c.content,t.title,t.buyer,t.agency,t.region,t.source,"
            "t.pub_time,t.amount,t.winner,t.deadline FROM tender_content c "
            "JOIN tenders t ON t.id=c.id").fetchall()
        base_keys = ("title", "buyer", "agency", "region", "source", "pub_time",
                     "amount", "winner", "deadline")
        for rid, content, *meta in rows:
            base = dict(zip(base_keys, meta))
            store.save_content(rid, content, structure_of(content, base))
            store.save_contacts(rid, extract_contacts(content, base.get("buyer") or "",
                                                       base.get("agency") or ""), base)
        store.conn.commit()
        nd = store.export_details(DETAILS)
        st = store.stats()
        store.set_meta("last_reparse", time.strftime("%Y-%m-%d %H:%M:%S"))
        store.close()
        print(f"[重解析] {len(rows)} 条正文 → 联系人 {st['contacts']} 条"
              f"（含手机号 {st['with_mobile']}）")
        print(f"[导出] {DETAILS}（{nd} 条）")
        return

    if args.rebuild_content > 0:
        pend = store.pending_content(args.rebuild_content)
        print(f"[正文补录] 待补 {len(pend)} 条（已抓详情但正文未入库）")
        done = 0
        for i, item in enumerate(pend, 1):
            try:
                if enrich(store, source_for_url(item["url"], client), item):
                    done += 1
            except Exception as e:
                print(f"    ✗ {item['id']} {type(e).__name__} {e}")
            if i % 20 == 0:
                store.conn.commit()
                print(f"    {i}/{len(pend)}…")
        store.conn.commit()
        nd = store.export_details(DETAILS)
        st = store.stats()
        store.set_meta("last_content", time.strftime("%Y-%m-%d %H:%M:%S"))
        store.close()
        print(f"[完成] 补录正文 {done} 条 · 库内正文 {st['with_content']} 条 · "
              f"联系人 {st['contacts']} 条（含手机号 {st['with_mobile']}）")
        print(f"[导出] {DETAILS}（{nd} 条）")
        return

    if args.reset_stage:
        store.conn.execute("UPDATE tenders SET detail_flag=0 WHERE stage=?",
                           (args.reset_stage,))
        store.conn.commit()
        print(f"[重置] 已将 stage={args.reset_stage} 的详情标记清空")
    src_cls, default_regions = SOURCES[args.source]

    regions, region_names = build_regions(args.regions, default_regions)

    src = src_cls(client)

    total_new, t0 = 0, time.time()
    if args.skip_list:
        print("[跳过] 列表采集，仅补详情")
    else:
        print(f"[源] {src.name}  ({src.scope})")
        print(f"[地区] 共 {len(regions)} 个：{'、'.join(n for n, _ in regions)[:120]}…")
        for idx, region in enumerate(regions, 1):
            try:
                kw = {"keyword": args.kw} if args.kw and args.source == "gdgpo" else {}
                rows = src.fetch(region, pages=args.pages,
                                 max_type_pages=args.types, **kw)
            except Exception as e:
                print(f"  ✗ {region[0]} 采集异常：{type(e).__name__} {e}")
                rows = []
            added = store.bulk(rows)
            total_new += added
            flag = "✓" if rows else "✗"
            print(f"  {flag} ({idx:>2}/{len(regions)}) {region[0]:<8} 抓 {len(rows):>3} 条，新增 {added:>3}")
            if not rows:
                time.sleep(args.delay * 2)
    list_sec = time.time() - t0

    # ---- 详情增量 ----
    detail_done = 0
    if args.detail > 0:
        pend = store.pending_detail(args.detail)
        print(f"\n[详情] 待补 {len(pend)} 条")
        for i, item in enumerate(pend, 1):
            try:
                if enrich(store, src, item):
                    detail_done += 1
            except Exception as e:
                print(f"    ✗ {item['id']} {type(e).__name__} {e}")
            if i % 10 == 0:
                store.conn.commit()
                print(f"    详情 {i}/{len(pend)}…")
        store.conn.commit()

    n = store.export(OUT, keep_other=args.export_all)
    nd = store.export_details(DETAILS)
    db_total = store.conn.execute("SELECT COUNT(*) FROM tenders").fetchone()[0]
    with_detail = store.conn.execute(
        "SELECT COUNT(*) FROM tenders WHERE detail_flag=1").fetchone()[0]
    cat_dist = dict(store.conn.execute(
        "SELECT category, COUNT(*) FROM tenders WHERE category IS NOT NULL "
        "AND stage<>'其他' GROUP BY category").fetchall())
    st = store.stats()
    store.set_meta("last_run", time.strftime("%Y-%m-%d %H:%M:%S"))
    store.close()

    status = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": src.name,
        "source_scope": src.scope,
        "regions_covered": region_names,
        "total_records": n,
        "db_total": db_total,
        "new_records": total_new,
        "detail_enriched": with_detail,
        "list_seconds": round(list_sec, 1),
        "detail_done": detail_done,
        "health": src.health,
        "categories": cat_dist,
        "with_content": st["with_content"],
        "contacts": st["contacts"],
        "contacts_with_mobile": st["with_mobile"],
    }
    with open(STATUS, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 52)
    print(f"采集完成 · 库内总量 {db_total} 条 · 本次新增 {total_new} 条")
    print(f"导出前端 {n} 条" + ("" if args.export_all else "（已排除「其他」类，加 --export-all 可全量导出）"))
    print(f"详情已补齐 {with_detail} 条 · 列表耗时 {list_sec:.1f}s")
    print(f"正文入库 {st['with_content']} 条 · 联系人 {st['contacts']} 条"
          f"（含手机号 {st['with_mobile']}） · 导出 {DETAILS}（{nd} 条）")
    print(f"产出：{OUT}")
    print("=" * 52)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断")
    except Exception:
        traceback.print_exc()
