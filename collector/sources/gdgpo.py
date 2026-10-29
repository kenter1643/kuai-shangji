"""广东省政府采购智慧云平台适配器（gdgpo.czt.gd.gov.cn）。

这是项目里第一个**省级 REST 接口**数据源，与 ccgp 的 HTML 抓取路子完全不同：

  接口基址  https://gdgpo.czt.gd.gov.cn/gpcms
  列表      GET /rest/web/v2/info/selectInfoMoreChannel
            参数 siteId / channel / regionCode / noticeType / title / pageSize
  详情      GET /rest/web/v2/info/getInfoById?id=<公告id>
  siteId    GET /rest/web/v2/index/getDeploymentSiteId?domain=gdgpo.czt.gd.gov.cn
  区划      GET /rest/web/v2/index/getRegionJsonByPid?regionCode=44

## 这个源最关键的一条：pageNo 翻页是失效的

实测证明服务端**忽略 pageNo**：pageNo=1/2/3/5/10 返回的是完全相同的最新 40 条
（按 noticeTime 倒序，精度到分钟），去重后只有 41 条。也就是说靠翻页拿历史
数据是走不通的。

能真正切分数据的维度是这三个，且可以叠加（每个切片最多回 40 条）：

  regionCode   区县代码，全省 215 个区县 + 22 市 + 省本级
  title        标题关键词，支持服务端模糊检索
  noticeType   公告类型编码

所以采集策略是**横向切片而不是纵向翻页**：遍历区县 × 关键词矩阵，
每个组合取最新 40 条。这也意味着 --pages 在这里的语义是「每个区县跑几个
关键词切片」，不是翻页数。

## 另外三点

1. **字段质量远高于 HTML 抓取** —— 列表接口直接给预算金额（`budget`，单位元）、
   采购人（`purchaser`）、代理机构（`agency`）、区划代码、公告类型编码。
   ccgp 必须抓详情页才有金额，这里列表就有。
2. **pageSize 上限 40** —— 传更大也只回 40。
3. **采购意向拿不到** —— 平台有「采购意向公开」栏目但 CMS 接口查它 total=0，
   意向数据走的是另一套业务系统（gpbs），不在这个接口里。
"""

from __future__ import annotations

import json
import os
import re

from collector.base import (BaseSource, clean_text, extract_amount,
                            extract_contact, extract_deadline, extract_winner,
                            guess_category)
from collector.extract import html_to_text

BASE = "https://gdgpo.czt.gd.gov.cn/gpcms"
PORTAL = "https://gdgpo.czt.gd.gov.cn/gpcms-center-web"
LIST_PATH = "/rest/web/v2/info/selectInfoMoreChannel"
DETAIL_PATH = "/rest/web/v2/info/getInfoById"
SITE_PATH = "/rest/web/v2/index/getDeploymentSiteId"
REGION_PATH = "/rest/web/v2/index/getRegionJsonByPid"
DOMAIN = "gdgpo.czt.gd.gov.cn"

PAGE_SIZE = 40          # 服务端硬上限

# 采集栏目。fca71be5「采购项目信息」是总库（千万级），各子栏目数据都汇总在这里。
CHANNEL = "fca71be5-fc0c-45db-96af-f513e9abda9d"

# 默认关键词切片。--pages N 时取前 N-1 个（第 1 个切片是无关键词的「最新」）。
KEYWORDS = ["智能化", "系统集成", "信息化", "运维", "医疗设备", "装修",
            "物业", "安防", "软件", "电梯"]


def _to_wan(v) -> float | None:
    """接口金额单位是元，库内统一存万元。"""
    try:
        f = float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if f <= 0:
        return None
    return round(f / 10000, 4)


def _wan_text(v) -> str:
    w = _to_wan(v)
    return f"{w} 万元" if w else ""


def _contacts_from_fields(d: dict) -> list[dict]:
    """从接口专用联系人字段组装联系人记录。

    平台把「采购人联系人」「代理联系人」「项目联系人」分成了独立字段，
    比从正文里正则捞准确得多，这是这个源最有价值的部分之一。
    """
    out = []

    def push(role, org, name, tel):
        name, tel = (name or "").strip(), (tel or "").strip()
        if not name and not tel:
            return
        # 手机号 / 座机号分流：1 开头 11 位算手机
        mob = tel if re.fullmatch(r"1[3-9]\d{9}", tel) else ""
        phone = "" if mob else tel
        out.append({"role": role, "org": org or "", "name": name,
                    "mobile": mob, "phone": phone, "email": ""})

    push("采购人联系人", d.get("purchaser") or "",
         d.get("purchaserLinkMan"), d.get("purchaserLinkPhone"))
    push("代理机构联系人", d.get("agency") or "",
         d.get("agentLinkMan") or d.get("agentManageName"),
         d.get("agentLinkPhone"))
    push("项目联系人", d.get("purchaser") or "",
         d.get("contactPerson"), d.get("contactNumber"))
    return out

_REGION_FILE = os.path.join(os.path.dirname(__file__), "gd_regions.json")

# noticeType 编码 → 阶段。编码是平台内部字典，靠抽样标题反推出来的。
#
# 两条容易搞错的：
#   "59"     是**真正的政府采购意向**（171 条全样本标题都带「意向」）——
#            这是国家级平台拿不到的立项前置信号，广东源的价值点之一。
#   "001101" 是电子卖场采购订单（复印纸、触控一体机这类直接选定采购），
#            不是前置预警。一开始误判成预警，导致 3000 多条噪音混进预警页。
NOTICE_STAGE = {
    "59": "前置预警",     # 政府采购意向公开
    "201111": "招标公告",   # 定点议价采购公告
    "00107D": "招标公告",   # 定点竞价采购公告
    "00107E": "招标公告",
    "202111": "中标结果",   # 定点议价成交公告
    "202112": "中标结果",
    "00105A": "其他",       # 电子卖场合同验收
    "001054": "其他",       # 合同公告
    "001101": "其他",       # 电子卖场采购订单（非前置信号）
}


def load_regions() -> list[tuple[str, str]]:
    """加载广东区县切片表。

    表由 tools/fetch_gd_regions.py 从平台区划接口拉下来固化，避免每次运行
    都去打接口。文件缺失时退化成「全省一个切片」，保证源不会直接挂掉。
    """
    if os.path.exists(_REGION_FILE):
        try:
            raw = json.load(open(_REGION_FILE, encoding="utf-8"))
            out = []
            for r in raw:
                code, name = r.get("code"), (r.get("name") or "").strip()
                parent = (r.get("parent") or "").strip()
                lvl = r.get("level") or 2
                if not code:
                    continue
                # 切片粒度是区县（level 3），但要按地级市口径打标签，
                # 否则前端地区筛选会被 215 个区县切成碎片。
                if lvl == 2:
                    label = name                      # 市级节点就用市名
                else:
                    label = parent or name            # 区县归到所属市
                if "本级" in name:
                    # 市本级 / 省本级：保留「XX市本级」而不是笼统的省本级
                    label = (parent if parent and parent != "广东省" else "广东省") + "本级"
                out.append((label or "广东省", code))
            seen, uniq = set(), []
            for nm, cd in out:
                if cd in seen:
                    continue
                seen.add(cd)
                uniq.append((nm, cd))
            return uniq
        except Exception:
            pass
    return [("广东省", "440000")]


REGIONS: list[tuple[str, str]] = load_regions()


class GdGpoSource(BaseSource):
    name = "广东省政府采购智慧云平台"
    scope = "广东省（省本级 + 21 地市，按区县切片采集）"

    def __init__(self, client):
        super().__init__(client)
        self.site_id: str | None = None

    def regions(self):
        return REGIONS

    # ---------------- 基础 ----------------
    def _site_id(self) -> str:
        """siteId 不写死，每次运行现取，避免平台换站点后整源失效。"""
        if self.site_id:
            return self.site_id
        txt = self.client.get(BASE + SITE_PATH, params={"domain": DOMAIN},
                              referer=PORTAL + "/")
        data = (json.loads(txt) or {}).get("data") or {}
        sid = data.get("id")
        if not sid:
            raise RuntimeError("取不到 siteId")
        self.site_id = sid
        return sid

    def _get_json(self, path: str, **params):
        txt = self.client.get(BASE + path, params=params, referer=PORTAL + "/")
        return json.loads(txt)

    # ---------------- 列表 ----------------
    def fetch(self, region, pages: int = 1, max_type_pages: int = 0,
              keyword: str = ""):
        """按「区县 × 关键词」切片采集。

        region = (地市名, 区县代码)。因为 pageNo 失效，这里不翻页，
        而是对当前区县跑若干关键词切片，每个切片最多 40 条。
        """
        kws = [k.strip() for k in keyword.split(",") if k.strip()]
        if not kws:
            kws = [""] + KEYWORDS[:max(0, pages - 1)]

        sid = self._site_id()
        _, code = region
        out: list[dict] = []
        for kw in kws:
            params = {"siteId": sid, "channel": CHANNEL,
                      "pageNo": 1, "pageSize": PAGE_SIZE}
            if code and code != "440000":
                params["regionCode"] = code
            if kw:
                params["title"] = kw
            try:
                j = self._get_json(LIST_PATH, **params)
            except Exception:
                continue
            for r in ((j or {}).get("data") or {}).get("rows") or []:
                rec = self._normalize(r, region[0])
                if rec:
                    out.append(rec)
        self.ok(slices=len(kws), rows=len(out))
        return out

    def _normalize(self, r: dict, default_region: str) -> dict | None:
        title = (r.get("title") or r.get("noticeName") or "").strip()
        rid = r.get("id")
        if not title or not rid:
            return None

        url = f"{PORTAL}/#/articleDetail?id={rid}"
        nt = (r.get("noticeType") or "").strip()
        stage = NOTICE_STAGE.get(nt) or guess_category(title)

        # budget 单位是元，统一换算成「万元」和 ccgp 口径一致
        amount = None
        try:
            b = r.get("budget")
            if b not in (None, "", "0", "0.00"):
                amount = round(float(b) / 10000.0, 4)
        except (TypeError, ValueError):
            amount = None

        return {
            "id": "gd-" + str(rid),
            "title": title,
            "url": url,
            "pub_time": (r.get("noticeTime") or "").strip(),
            "buyer": (r.get("purchaser") or r.get("author") or "").strip(),
            "agency": (r.get("agency") or "").strip(),
            "notice_type": nt,
            "category_path": (r.get("regionName") or "").strip(),
            "region": default_region,
            "stage": stage,
            "source": self.name,
            "source_type": "政府采购",
            "amount": amount,
            "winner": None,
            "deadline": None,
            "contact": None,
            "phone": None,
            "detail_flag": 0,
        }

    # ---------------- 详情 ----------------
    def fetch_detail(self, url: str) -> dict | None:
        """抓详情正文 + 抽取结构化信息。

        这个接口的详情响应**自带一套完整的业务字段**（purchaser / budget /
        supplierName / purchaserLinkMan / agentLinkPhone …），比从正文正则抽取
        准得多，所以这里是「接口字段为主 + 正文抽取兜底」双路并用。

        注意：电子卖场类公告（channelName=电子卖场公告）正文只是一个 PDF 附件
        链接，没有可读正文 —— 这类只存接口字段，不存正文。
        """
        m = re.search(r"id=([0-9a-zA-Z\-]+)", url)
        if not m:
            return None
        try:
            j = self._get_json(DETAIL_PATH, id=m.group(1))
        except Exception:
            return None
        data = (j or {}).get("data") or {}
        raw = data.get("content") or ""
        if not raw and not data.get("purchaser"):
            return None
        text = clean_text(raw)[:12000]

        rec = {
            "detail_flag": 1,
            "content": html_to_text(raw) if raw else "",
        }
        # --- 接口直给字段（优先采信）---
        amt = _to_wan(data.get("budget") or data.get("successfulMoney"))
        if amt:
            rec["amount"] = amt
        win = data.get("supplierName") or data.get("bidCompany")
        if win:
            rec["winner"] = win
        buyer = data.get("purchaser") or ""
        if buyer:
            rec["buyer"] = buyer
        agency = data.get("agency") or ""
        if agency:
            rec["agency"] = agency

        hint: dict[str, str] = {}
        if data.get("openTenderCode"):
            hint["项目编号"] = data["openTenderCode"]
        if data.get("purchaseMannerName"):
            hint["采购方式"] = data["purchaseMannerName"]
        if data.get("budget"):
            hint["预算金额"] = _wan_text(data["budget"])
        if data.get("successfulMoney"):
            hint["中标金额"] = _wan_text(data["successfulMoney"])
        if win:
            hint["中标单位"] = win
        if data.get("noticeEndTime"):
            hint["投标截止"] = data["noticeEndTime"]
            rec["deadline"] = data["noticeEndTime"]
        if data.get("openTenderTime"):
            hint["开标时间"] = data["openTenderTime"]
        if data.get("openTenderPlace"):
            hint["开标地点"] = data["openTenderPlace"]
        if buyer:
            hint["采购人"] = buyer
        if agency:
            hint["代理机构"] = agency
        if data.get("purchaserAddr"):
            hint["采购人地址"] = data["purchaserAddr"]
        if data.get("projectId"):
            hint["平台项目号"] = data["projectId"]
        rec["structured_hint"] = hint

        # --- 联系人：接口专用字段 + 正文抽取，两者合并 ---
        rec["contacts_hint"] = _contacts_from_fields(data)

        # --- 正文兜底（接口字段为空时才有意义）---
        if text:
            if not rec.get("amount"):
                rec["amount"] = extract_amount(text)
            if not rec.get("winner"):
                rec["winner"] = extract_winner(text)
            if not rec.get("deadline"):
                rec["deadline"] = extract_deadline(text)
            contact = extract_contact(text)
            if contact.get("contact") and not rec.get("contact"):
                rec["contact"] = contact["contact"]
            if contact.get("phone") and not rec.get("phone"):
                rec["phone"] = contact["phone"]
        return rec
