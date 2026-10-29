# -*- coding: utf-8 -*-
"""刷新广东省区划切片表 → collector/sources/gd_regions.json

gdgpo 的列表接口忽略 pageNo，只能横向切片，切片维度之一就是 regionCode。
所以必须有一份准确的区县代码表。这份表从平台自己的区划接口拉，不要手抄：

    GET /gpcms/rest/web/v2/index/getRegionJsonByPid?regionCode=44
    → 返回广东省全部 239 个区划节点（省 / 市 / 区县）

用法：python3 tools/fetch_gd_regions.py
"""
import json
import os
import sys
import warnings

import requests

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "https://gdgpo.czt.gd.gov.cn/gpcms"
PORTAL = "https://gdgpo.czt.gd.gov.cn/gpcms-center-web"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "collector", "sources", "gd_regions.json")


def main():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json, */*",
                      "X-Requested-With": "XMLHttpRequest", "Referer": PORTAL + "/"})
    s.trust_env = False

    j = s.get(BASE + "/rest/web/v2/index/getRegionJsonByPid",
              params={"regionCode": "44"}, timeout=30, verify=False).json()
    nodes = j.get("data") or []
    if not nodes:
        raise SystemExit("区划接口返回空，可能接口变了")

    # 只保留市级(2) 和 区县级(3)；区县级是主要切片粒度
    by_id = {str(n.get("id")): (n.get("regionFullName") or "").strip() for n in nodes}
    out = []
    for n in nodes:
        lvl = n.get("levelNum")
        if lvl not in ("2", "3"):
            continue
        out.append({
            "code": n.get("regionCode"),
            "name": (n.get("regionFullName") or "").strip(),
            "parent": by_id.get(str(n.get("parentId")), ""),
            "level": int(lvl),
        })

    out.sort(key=lambda x: x["code"])
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    lv2 = sum(1 for x in out if x["level"] == 2)
    lv3 = sum(1 for x in out if x["level"] == 3)
    print(f"已写入 {OUT}")
    print(f"  市级 {lv2} 个 · 区县级 {lv3} 个 · 合计 {len(out)} 个切片")


if __name__ == "__main__":
    main()
