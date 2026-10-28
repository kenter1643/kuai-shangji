"""快商机 · 采集器基础工具层

提供：带限速 / 重试 / 会话复用的 HTTP 客户端，以及中文正文清洗、金额、
金额单位、日期等招投标领域通用抽取函数。

所有数据源适配器均继承 BaseSource，实现 fetch(region) -> List[dict]。
"""
from __future__ import annotations

import html
import hashlib
import random
import re
import time
from typing import Iterable

import requests

try:
    import urllib3

    urllib3.disable_warnings()
except Exception:  # pragma: no cover
    pass

UA_POOL = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]


class HttpClient:
    """会话级 HTTP 客户端：首访预热拿 cookie，请求间随机抖动，失败退避重试。

    政务站点普遍按 IP 限次，`首次访问即返回拦截页` 是常态，
    因此在真实请求前先访问一次站点根路径建立会话。
    """

    def __init__(self, delay: float = 1.2, retries: int = 3, timeout: int = 25,
                 proxies: dict | None = None):
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self.proxies = proxies or {}
        self.session = requests.Session()
        self.session.trust_env = False  # 屏蔽系统/代理环境变量导致的连接抖动
        self._warm = set()

    def _headers(self, referer: str | None = None) -> dict:
        h = {
            "User-Agent": random.choice(UA_POOL),
            "Accept": "text/html,application/json,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
        }
        if referer:
            h["Referer"] = referer
        return h

    def warmup(self, root: str) -> None:
        """预热：访问站点根路径，拿到后续请求所需的会话 cookie。"""
        if root in self._warm:
            return
        self._warm.add(root)
        try:
            self.session.get(root, headers=self._headers(), timeout=self.timeout,
                             verify=False, proxies=self.proxies)
        except Exception:
            pass

    def get(self, url, *, params=None, referer=None, json_mode=False) -> str:
        last = None
        for i in range(self.retries):
            time.sleep(self.delay + random.uniform(0, 0.5))
            try:
                r = self.session.get(url, params=params, headers=self._headers(referer),
                                     timeout=self.timeout, verify=False,
                                     proxies=self.proxies)
                if r.status_code == 200:
                    txt = r.text
                    if self._blocked(txt):
                        last = RuntimeError("触发站点限流拦截")
                        time.sleep(3 * (i + 1))
                        continue
                    if json_mode:
                        return txt
                    enc = r.apparent_encoding or "utf-8"
                    r.encoding = enc if enc.lower() != "gb2312" else "gb18030"
                    return r.text
                last = RuntimeError(f"HTTP {r.status_code}")
            except Exception as e:
                last = e
            time.sleep(2 * (i + 1))
        raise RuntimeError(f"请求失败 {url}: {last}")

    @staticmethod
    def _blocked(txt: str) -> bool:
        head = txt[:3000]
        return any(k in head for k in ("频繁访问", "访问验证", "安全验证",
                                       "请输入验证码", "Access Denied"))

    def reset(self):
        self.session.close()
        self.session = requests.Session()
        self.session.trust_env = False
        self._warm = set()


# --------------------------------------------------------------------------
# 文本与字段抽取
# --------------------------------------------------------------------------

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[\s　\xa0]+")


def clean_text(s: str) -> str:
    """去标签、去实体、压缩空白。"""
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", s, flags=re.I)
    s = TAG_RE.sub("", s)
    s = html.unescape(s)
    return WS_RE.sub(" ", s).strip()


# 公告类型：命中顺序敏感，长词优先
CATEGORY_RULES = [
    ("采购意向", "前置预警"),
    ("意向公开", "前置预警"),
    ("需求公示", "前置预警"),
    ("资格预审", "招标公告"),
    ("公开招标", "招标公告"),
    ("邀请招标", "招标公告"),
    ("竞争性磋商", "招标公告"),
    ("竞争性谈判", "招标公告"),
    ("竞谈", "招标公告"),
    ("询价", "招标公告"),
    ("单一来源", "招标公告"),
    ("废标", "异常公告"),
    ("流标", "异常公告"),
    ("终止", "异常公告"),
    ("变更", "变更公告"),
    ("更正", "变更公告"),
    ("澄清", "变更公告"),
    ("答疑", "变更公告"),
    ("中标结果", "中标结果"),
    ("中标公告", "中标结果"),
    ("成交结果", "中标结果"),
    ("成交公告", "中标结果"),
    ("结果公告", "中标结果"),
    ("中标候选人", "中标结果"),
    ("候选人公示", "中标结果"),
    ("中标公示", "中标结果"),
]


def guess_category(text: str) -> str:
    """从标题/摘要推断公告阶段。这是商机分层的核心字段。"""
    for kw, cat in CATEGORY_RULES:
        if kw in text:
            return cat
    return "其他"


def parse_amount(s: str) -> float | None:
    """抽取金额并统一折算为「万元」。支持 xx万元 / xx元 / xx.xx元。"""
    if not s:
        return None
    s = s.replace(",", "").replace("，", "").replace(" ", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(元|万元|亿万元|亿元)?", s)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2) or ""
    if unit.startswith("亿万") or unit.startswith("亿"):
        v *= 10000
    elif unit == "元":
        v /= 10000
    return round(v, 4)


AMOUNT_KEYS = ("预算金额", "预算总金额", "采购预算", "项目预算", "最高限价",
               "预算金额（元）", "预算金额(元)", "预算价", "中标金额", "成交金额",
               "中标价", "成交价", "合同金额")


def extract_amount(text: str) -> float | None:
    """从正文抽取「最可信」的一处金额，单位折算为万元。"""
    for k in AMOUNT_KEYS:
        m = re.search(k + r"[^\d\n]{0,12}(\d[\d,\.]*\s*(?:元|万元|亿元)?)", text)
        if m:
            v = parse_amount(m.group(1))
            if v and 0 < v < 100_0000:
                return v
    # 兜底：正文里任意「xx万元」
    m = re.search(r"(\d[\d,\.]*)\s*万元", text)
    return parse_amount(m.group(1)) if m else None


WINNER_KEYS = ("中标供应商名称", "成交供应商名称", "中标单位名称", "中标人名称",
               "中标供应商", "成交供应商", "中标单位", "中标人", "成交单位",
               "成交人", "第一中标候选人", "第一成交候选人")

# 中标公告正文里「供应商名称」多为表格表头，后面紧跟序号和公司名，没有冒号，
# 因此另开一条表头型模式兜底。
_NAME = r"[\u4e00-\u9fa5（）()·\-A-Za-z0-9]{2,28}?(?:有限公司|有限责任公司|股份有限公司|集团|研究院|大学|医院|事务所|合作社|工厂|总公司|公司|中心)"
# 注意：正则里有 {2,28} 这类量词大括号，不能用 str.format 拼接，只做普通串接。
WINNER_TABLE_PAT = r"供应商名称\s*[:：]?\s*\d{0,3}\s*(" + _NAME + r")"

DEFUZZ = re.compile(r"[（(].*?[)）]")


def _tidy_name(s: str) -> str | None:
    s = DEFUZZ.sub("", s or "").strip()
    s = re.sub(r"^\d{1,3}", "", s).strip()
    if len(s) < 3 or len(s) > 30:
        return None
    if not re.search(r"(公司|中心|院|大学|医院|集团|厂|社|所|队|部|局)$", s):
        # 个人/联合体等非常规名只要长度合理也保留，但剔除纯地名
        if len(s) < 6:
            return None
    return s


def extract_winner(text: str) -> str | None:
    """抽取中标/成交单位名称，兼容「键名：值」与表格表头两种版式。"""
    for k in WINNER_KEYS:
        m = re.search(k + r"\s*[:：\s]{0,6}([\u4e00-\u9fa5（）()·\-A-Za-z0-9]{2,30}?"
                      r"(?:有限公司|有限责任公司|股份有限公司|集团|研究院|大学|医院|事务所|合作社|工厂|公司|中心|院|队|部))", text)
        if m:
            v = _tidy_name(m.group(1))
            if v:
                return v
    # 表格版式：正文清洗后表头与数据连成一片，形如
    # "供应商名称供应商地址中标金额评审总得分1甘肃某某科技发展有限公司..."
    # 因此先在「供应商名称」后开一个短窗口，剔掉表头词，再取序号后的公司名。
    idx = text.find("供应商名称")
    if idx >= 0:
        win = text[idx: idx + 100]
        win = re.sub(
            r"供应商地址|供应商名称|中标（成交）金额|中标金额|成交金额|评审总得分|"
            r"投标总价|序号|标段|包号|地址|金额|得分|备注|评审|主要标的|服务要求",
            "", win)
        m = re.search(r"\d{0,2}\s*(" + _NAME + r")", win)
        if m:
            v = _tidy_name(m.group(1))
            if v:
                return v
    return None


DEADLINE_KEYS = ("响应文件递交截止时间", "投标文件递交截止时间", "投标截止时间",
                 "响应截止时间", "递交响应文件截止时间", "开标时间", "报价截止时间")


def extract_deadline(text: str) -> str | None:
    for k in DEADLINE_KEYS:
        m = re.search(k + r"[^\d]{0,10}(\d{4}[-/年]\s?\d{1,2}[-/月]\s?\d{1,2}[日]?"
                      r"(?:\s*\d{1,2}[:：时]\s*\d{1,2})?)", text)
        if m:
            return m.group(1).replace("年", "-").replace("月", "-").replace("日", "").strip()
    return None


def extract_contact(text: str) -> dict:
    """抽取联系人与电话。"""
    out = {}
    m = re.search(r"(?:项目联系人|联系人|采购联系人|采购人联系人)\s*[:：\s]{1,4}([\u4e00-\u9fa5]{2,6})", text)
    if m:
        out["contact"] = m.group(1)
    m = re.search(r"(?:项目联系方式|联系方式|电话|联系电话)\s*[:：\s]{1,4}"
                  r"(1[3-9]\d{9}|0\d{2,3}-?\d{7,8}(-\d{1,4})?)", text)
    if m:
        out["phone"] = m.group(1)
    return out


def uid_of(url: str) -> str:
    """以 URL 为主键生成稳定短 ID（同一条公告跨源去重依赖它）。"""
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]


class BaseSource:
    """数据源适配器基类。

    子类需声明 name / scope / health，并实现 fetch(region) -> List[dict]。
    region 形如 ("北京市", "110000")，即 (名称, zoneId)。
    """

    name = "base"
    scope = "未知"
    health: dict = {}   # 运行时健康状态，前端数据源面板读取

    def __init__(self, client: HttpClient):
        self.client = client

    def regions(self) -> Iterable[tuple[str, str]]:
        raise NotImplementedError

    def fetch(self, region) -> list[dict]:
        raise NotImplementedError

    def ok(self, **kw):
        self.health.update({"status": "ok", **kw})

    def fail(self, msg, **kw):
        self.health.update({"status": "failed", "reason": str(msg)[:120], **kw})
