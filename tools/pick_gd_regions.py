# -*- coding: utf-8 -*-
"""按天轮换挑选广东区划切片，让每日采集能在固定时限内跑完。

广东源的列表接口忽略 pageNo，只能横向切片，切片维度之一是 regionCode，
237 个区划节点全跑一遍要 40 分钟以上 —— 定时任务会被中途掐断（实测跑到
138/237 就被杀），结果既没采完也没发布。

所以按天切块：
  - level 2（省本级 + 21 个地级市）每天都跑，量最大也最重要；
  - level 3（区县 / 市本级）按「第 N 天取第 N 份」轮换，PARTS 天覆盖一轮。

用法：
    python3 tools/pick_gd_regions.py            # 今天该采的切片，逗号分隔
    python3 tools/pick_gd_regions.py 7          # 7 天一轮
    python3 tools/pick_gd_regions.py 7 --date 2026-11-01   # 指定日期（调试用）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REGIONS = os.path.join(HERE, "..", "collector", "sources", "gd_regions.json")


def pick(parts: int, day: dt.date) -> list[str]:
    with open(REGIONS, encoding="utf-8") as f:
        nodes = json.load(f)
    lv2 = [n["name"] for n in nodes if n.get("level") == 2]
    lv3 = [n["name"] for n in nodes if n.get("level") == 3]
    idx = day.toordinal() % parts          # 日期决定今天取第几份，稳定可复现
    return lv2 + lv3[idx::parts]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("parts", nargs="?", type=int, default=7, help="几天覆盖一轮")
    ap.add_argument("--date", default="", help="YYYY-MM-DD，默认今天（调试用）")
    a = ap.parse_args()
    day = (dt.date.fromisoformat(a.date) if a.date else dt.date.today())
    names = pick(max(1, a.parts), day)
    print(",".join(names), end="")
    print(f"\n# {day} 共 {len(names)} 个切片（{a.parts} 天一轮）", file=sys.stderr)


if __name__ == "__main__":
    main()
