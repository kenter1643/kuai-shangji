"""中国政府采购网适配器 —— 快商机的主数据源。

为什么它能单源覆盖全国：
  bxsearch 支持 displayZone / zoneId 双参数分片检索，每个省级行政区一个
  zoneId，返回结构统一 —— 一份解析器即可吃下全国数据。返回的 <span> 摘要里
  自带采购人、代理机构、地区、品目分类，无需逐个抓详情页即可结构化。

已实测限制：裸 GET 首次请求必返「频繁访问」拦截页，必须先 warmup 建立会话，
且请求间隔不得低于 1s。
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from collector.base import (BaseSource, clean_text, extract_amount,
                            extract_contact, extract_deadline, extract_winner,
                            guess_category, uid_of)
from collector.extract import html_to_text

SEARCH = "http://search.ccgp.gov.cn/bxsearch"
ROOT = "http://www.ccgp.gov.cn/"

BASE_PARAMS = {
    "searchtype": "1", "page_index": "1", "bidSort": "0", "buyerName": "",
    "projectId": "", "pinMu": "0", "bidType": "0", "dbselect": "bidx",
    "start_time": "", "end_time": "", "timeType": "", "pppStatus": "0",
    "agentName": "",
}

# 34 个省级行政区 + 中央本级。zoneId 为 CCGP 内部地区编码。
REGIONS: list[tuple[str, str]] = [
    ("中央本级", "000000"), ("北京市", "110000"), ("天津市", "120000"),
    ("河北省", "130000"), ("山西省", "140000"), ("内蒙古自治区", "150000"),
    ("辽宁省", "210000"), ("吉林省", "220000"), ("黑龙江省", "230000"),
    ("上海市", "310000"), ("江苏省", "320000"), ("浙江省", "330000"),
    ("安徽省", "340000"), ("福建省", "350000"), ("江西省", "360000"),
    ("山东省", "370000"), ("河南省", "410000"), ("湖北省", "420000"),
    ("湖南省", "430000"), ("广东省", "440000"), ("广西壮族自治区", "450000"),
    ("海南省", "460000"), ("重庆市", "500000"), ("四川省", "510000"),
    ("贵州省", "520000"), ("云南省", "530000"), ("西藏自治区", "540000"),
    ("陕西省", "610000"), ("甘肃省", "620000"), ("青海省", "630000"),
    ("宁夏回族自治区", "640000"), ("新疆维吾尔自治区", "650000"),
]

# 短名映射：列表摘要里的「| 北京 |」会被归一到这里
SHORT = {"北京": "北京市", "天津": "天津市", "河北": "河北省", "山西": "山西省",
         "内蒙古": "内蒙古自治区", "辽宁": "辽宁省", "吉林": "吉林省",
         "黑龙江": "黑龙江省", "上海": "上海市", "江苏": "江苏省", "浙江": "浙江省",
         "安徽": "安徽省", "福建": "福建省", "江西": "江西省", "山东": "山东省",
         "河南": "河南省", "湖北": "湖北省", "湖南": "湖南省", "广东": "广东省",
         "广西": "广西壮族自治区", "海南": "海南省", "重庆": "重庆市",
         "四川": "四川省", "贵州": "贵州省", "云南": "云南省", "西藏": "西藏自治区",
         "陕西": "陕西省", "甘肃": "甘肃省", "青海": "青海省", "宁夏": "宁夏回族自治区",
         "新疆": "新疆维吾尔自治区", "中央": "中央本级"}


class CcgpSource(BaseSource):
    name = "中国政府采购网"
    scope = "全国 34 省级行政区 · 政府/事业单位采购与招标"

    def regions(self):
        return REGIONS

    # ---------------- 列表 ----------------
    def _params(self, zone_name: str, zone_id: str, page: int, kw: str = "",
                bid_type: str = "0"):
        p = dict(BASE_PARAMS)
        p.update({"page_index": str(page), "kw": kw, "bidType": bid_type,
                  "displayZone": zone_name if zone_id != "000000" else "",
                  "zoneId": zone_id})
        return p

    def fetch(self, region, pages: int = 2, max_type_pages: int = 0):
        """拉取单个地区的公告列表。

        pages            : 全类型翻页数
        max_type_pages   : >0 时额外遍历多种公告类型，扩充覆盖
        """
        zone_name, zone_id = region
        rows, self.rows = [], 0
        self.client.warmup(ROOT)
        combos = [("", "0")] if not max_type_pages else [
            ("", "0"), ("设备", "0"), ("系统", "0"), ("服务", "1"), ("工程", "0")]
        seen = set()
        for kw, bt in combos:
            for pg in range(1, pages + 1):
                try:
                    body = self.client.get(SEARCH, params=self._params(
                        zone_name, zone_id, pg, kw, bt), referer=SEARCH)
                except Exception as e:
                    if pg == 1 and kw == "":
                        self.fail(e, zone=zone_name)
                    break
                items = self.parse_list(body, zone_name)
                if not items:
                    break
                for it in items:
                    if it["url"] in seen:
                        continue
                    seen.add(it["url"])
                    rows.append(it)
        if rows:
            self.ok(zone_count=1, fetched=len(rows))
        return rows

    def parse_list(self, body: str, default_region: str) -> list[dict]:
        m = re.search(r'<ul class="vT-srch-result-list-bid">(.*?)</ul>', body, re.S)
        if not m:
            return []
        out = []
        for li in re.findall(r"<li>(.*?)</li>", m.group(1), re.S):
            rec = self._parse_li(li, default_region)
            if rec:
                out.append(rec)
        return out

    @staticmethod
    def _parse_li(li: str, default_region: str) -> dict | None:
        a = re.search(r'<a href="([^"]+)"[^>]*>\s*(.*?)\s*</a>', li, re.S)
        if not a:
            return None
        url = a.group(1).strip()
        title = clean_text(a.group(2))
        if not title:
            return None
        if url.startswith("//"):
            url = "http:" + url
        elif url.startswith("/"):
            url = urljoin(ROOT, url)

        sp = re.search(r"<span>(.*?)</span>", li, re.S)
        meta = {"buyer": "", "agency": "", "region": "", "category_path": "",
                "pub_time": "", "notice_type": ""}
        if sp:
            # <br> 与 </strong> 都是字段边界，必须先切成 "|" 再做文本清洗，
            # 否则 "...代理机构：XX公司 公开招标公告" 会粘连成一个字段。
            raw_html = re.sub(r"<br\s*/?>", "|", sp.group(1), flags=re.I)
            raw_html = re.sub(r"</strong>", "|", raw_html, flags=re.I)
            raw_html = re.sub(r"<strong[^>]*>", "", raw_html, flags=re.I)
            parts = [p.strip() for p in clean_text(raw_html).split("|") if p.strip()]
            if parts:
                dt = re.match(r"(\d{4}\.\d{1,2}\.\d{1,2}(?:\s+\d{1,2}:\d{2}(:\d{2})?)?)",
                              parts[0])
                if dt:
                    meta["pub_time"] = dt.group(1).replace(".", "-")
                    parts = parts[1:]
            for p in parts:
                if p.startswith("采购人："):
                    meta["buyer"] = p[4:].strip()
                elif p.startswith("代理机构："):
                    meta["agency"] = p[5:].strip()
                elif p in SHORT:
                    meta["region"] = SHORT[p]
                elif "/" in p or p.startswith("其他") or p.startswith("本级"):
                    meta["category_path"] = p.strip()
                elif p and not p.startswith(("本地=", "购买")):
                    meta["notice_type"] = p.strip()

        cat = guess_category(title + meta["notice_type"])
        return {
            "id": uid_of(url),
            "title": title,
            "url": url,
            "pub_time": meta["pub_time"],
            "buyer": meta["buyer"],
            "agency": meta["agency"],
            "notice_type": meta["notice_type"],
            "category_path": meta["category_path"],
            "region": meta["region"] or default_region,
            "stage": cat,
            "source": "中国政府采购网",
            "source_type": "政府采购",
            "detail_flag": 0,
        }

    # ---------------- 详情 ----------------
    def fetch_detail(self, url: str) -> dict | None:
        """抓公告正文，补齐金额 / 中标人 / 截止时间 / 联系方式。

        详情页请求成本远高于列表（1 条 1 次请求），因此仅由 run.py 的
        --detail 参数按配额增量调用，不放在常规采集路径里。
        """
        try:
            body = self.client.get(url, referer=SEARCH)
        except Exception:
            return None
        # 正文没有稳定的容器 class（自由排版 / 表格 / 附件型都有），
        # 用「剔除 script+style 后全文清洗」最稳，抽取函数本身有足够锚点。
        body = re.sub(r"<script.*?</script>", "", body, flags=re.S)
        body = re.sub(r"<style.*?</style>", "", body, flags=re.S)
        text = clean_text(body)
        if len(text) < 200:
            return None
        contact = extract_contact(text)
        rec = {
            "amount": extract_amount(text),
            "winner": extract_winner(text),
            "deadline": extract_deadline(text),
            "contact": contact.get("contact", ""),
            "phone": contact.get("phone", ""),
            "detail_flag": 1,
            # 正文一并带回（store 会分离入库，不进 tenders 主表）
            "content": html_to_text(body),
        }
        return rec
