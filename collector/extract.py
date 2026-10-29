"""快商机 · 正文清洗与结构化抽取

详情页正文是「半结构化」的：同一份信息在不同平台、不同代理机构排版里写法不同
（`名 称：` / `名称：` / `采购人名称：`、`联系方式：` / `项目联系方式：`），
因此这里按「锚点 + 就近取值」的方式抽字段，抽不到就留空，宁缺勿错。

产出三样东西，全部落 SQLite 统一管理：
  1. `content`    —— 清洗后的正文纯文本（保留段落，可直接在 H5 里阅读）
  2. `structured` —— 结构化字段 JSON（项目编号 / 采购方式 / 预算 / 中标价 …）
  3. `contacts`   —— 联系人记录（采购人 / 代理机构 / 项目联系人，含手机与座机）
"""

from __future__ import annotations

import html
import re

# --------------------------------------------------------------------------
# 正文清洗
# --------------------------------------------------------------------------

NOISE_LINES = (
    "当前位置", "首页", "返回顶部", "打印本页", "打印此页", "关闭窗口", "关闭本页",
    "上一篇", "下一篇", "版权所有", "网站标识码", "京ICP", "ICP备", "公安备案",
    "技术支持", "您访问的", "网站地图", "联系我们", "友情链接", "主办单位",
    "网站编辑", "联系方式：本站", "浏览次", "访问量", "分享到", "扫一扫",
    "中国政府采购网", "广东省政府采购", "登录", "注册", "搜索", "高级搜索",
)

# 平台页脚的全国统一客服电话，会混进正文并被当成项目联系人电话
FOOTER_PHONES = ("010-63819289", "010-63819281", "010-63819282", "010-63819283",
                 "010-55603617", "010-83084960")


def html_to_text(raw: str, limit: int = 20000) -> str:
    """把详情页 HTML 清洗成可读纯文本。

    政务详情页没有稳定的正文容器 class（自由排版 / 表格 / 附件型都有），
    所以走「剔 script+style → 块级标签转换行 → 逐行去噪」这条路。
    """
    if not raw:
        return ""
    s = re.sub(r"<script.*?</script>", "", raw, flags=re.S | re.I)
    s = re.sub(r"<style.*?</style>", "", s, flags=re.S | re.I)
    s = re.sub(r"<(br|/p|/div|/tr|/li|/h\d)\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    for p in FOOTER_PHONES:
        s = s.replace(p, " ")
    s = s.replace("\u3000", " ").replace("\xa0", " ")

    out, seen = [], set()
    for line in s.split("\n"):
        t = re.sub(r"[ \t]+", " ", line).strip()
        if not t:
            continue
        # 去导航栏 / 页脚噪声行
        if any(k in t for k in NOISE_LINES) and len(t) < 80:
            continue
        if len(t) < 3 and not re.search(r"[\u4e00-\u9fa5]", t):
            continue
        if t in seen:          # 页眉页脚常重复出现
            continue
        seen.add(t)
        out.append(t)
    text = "\n".join(out)
    return text[:limit]


# --------------------------------------------------------------------------
# 字段抽取
# --------------------------------------------------------------------------

MOBILE = r"1[3-9]\d{9}"
TEL = r"0\d{2,3}-?\d{7,8}(?:-\d{1,5})?"
MAIL = r"[\w.\-]+@[\w\-]+\.[a-zA-Z]{2,}"

# 键值之间可能带空格（`名 称：`），统一容忍
_SEP = r"\s*[:：]\s*"

PROJECT_NO_KEYS = ("采购项目编号", "项目编号", "采购计划编号", "计划编号",
                   "招标编号", "公告编号", "采购编号")
METHODS = ("公开招标", "邀请招标", "竞争性磋商", "竞争性谈判", "竞争性磋商采购",
           "询价", "单一来源", "定点议价", "电子卖场", "网上竞价", "框架协议",
           "批量集中采购", "协议供货")
AMOUNT_WIN_KEYS = ("中标（成交）金额", "中标(成交)金额", "中标金额", "成交金额",
                   "中标价", "成交价", "中标总价", "合同金额")
OPEN_TIME_KEYS = ("开标时间", "响应文件开启时间", "开启时间", "谈判时间",
                  "磋商时间", "询价时间", "报价开启时间")
PACKAGE_KEYS = ("包号", "标段编号", "标段名称", "采购包")


def _first(pattern: str, text: str) -> str:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else ""


def extract_project_no(text: str) -> str:
    for k in PROJECT_NO_KEYS:
        v = _first(k + _SEP + r"([A-Za-z0-9\-\[\]（）()·／/]{4,40})", text)
        if v:
            return v
    return ""


def extract_method(text: str, title: str = "") -> str:
    """采购方式。优先级：正文「采购方式：」键 → 标题关键词 → 正文开头。

    不能只看正文：正文里常有「评审专家（单一来源采购人员）名单」这类
    反向出现，直接全文匹配会把公开招标误判成单一来源。
    """
    v = _first(r"采购方式" + _SEP + r"([\u4e00-\u9fa5]{2,10})", text)
    if v:
        return v
    for m in METHODS:
        if title and m in title:
            return m
    head = text[:600]
    for m in METHODS:
        if m in head:
            return m
    return ""


def _to_wan(s: str) -> float | None:
    """金额字符串 → 万元。"""
    s = (s or "").replace(",", "").replace("，", "").replace(" ", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(元|万元|亿元)?", s)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2) or ""
    if unit.startswith("亿"):
        v *= 10000
    elif unit == "元":
        v /= 10000
    return round(v, 4)


def extract_win_amount(text: str) -> float | None:
    for k in AMOUNT_WIN_KEYS:
        m = re.search(k + r"[^\d\n]{0,12}(\d[\d,\.]*\s*(?:元|万元|亿元)?)", text)
        if m:
            v = _to_wan(m.group(1))
            if v and 0 < v < 1_000_000:
                return v
    return None


def extract_open_time(text: str) -> str:
    for k in OPEN_TIME_KEYS:
        m = re.search(k + r"[^\d]{0,10}(\d{4}[-/年]\s?\d{1,2}[-/月]\s?\d{1,2}[日]?"
                      r"(?:\s*\d{1,2}[:：时]\s*\d{1,2}(?:分)?)?)", text)
        if m:
            return m.group(1).replace("年", "-").replace("月", "-").replace("日", "").strip()
    return ""


def extract_package(text: str) -> str:
    for k in PACKAGE_KEYS:
        v = _first(k + _SEP + r"([^\s，,；;。]{1,30})", text)
        if v:
            return v
    return ""


# --------------------------------------------------------------------------
# 采购人 / 代理机构 / 联系人
# --------------------------------------------------------------------------

# 分块锚点：正文里这三段信息通常各占一块，按锚点切开可避免张冠李戴
SECTIONS = [
    ("采购人", r"采购人信息|采购人名称|采购人|采购单位|招标人|采购 人"),
    ("代理机构", r"代理机构信息|采购代理机构|招标代理机构|代理机构|代理 机构"),
    ("项目联系人", r"项目联系人|项目联系方式|联系人信息"),
]


def _slice_blocks(text: str) -> list[tuple[str, str]]:
    """按锚点把正文切成 (角色, 块文本)。

    同一角色允许出现多次（表头一处、正文一处），靠 push() 去重，
    不能只保留第一次 —— 表格版式里第一次命中常常是没有值的表头。
    """
    marks = []
    for role, pat in SECTIONS:
        for m in re.finditer(pat, text):
            marks.append((m.start(), role))
    marks.sort()
    blocks = []
    for i, (pos, role) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else min(len(text), pos + 500)
        if pos >= len(text):
            continue
        blocks.append((role, text[pos:min(end, pos + 600)]))
    return blocks[:12]


NAME_KEY = r"(?:项目联系人|联系人|采购人联系人|经办人|联系人姓名|项目联系电话)"
# 表格版式里「项目联系人」后常常没有冒号（表头与值分处两个 td），冒号需可选
NAME_SEP = r"\s*[:：]?\s{0,2}"
NAME_TAIL = r"([\u4e00-\u9fa5·]{2,4}(?:[、，,／/][\u4e00-\u9fa5·]{2,4})*)"


def _split_names(s: str) -> list[str]:
    """「玄志伟、李兆益、海丽斯」→ 三人各一条记录。"""
    return [x.strip() for x in re.split(r"[、，,／/]", s or "") if len(x.strip()) >= 2]


# 有些公告「项目联系人」栏填的是单位名而非人名，不能当姓名存
BAD_NAME = re.compile(
    r"(公司|中心|有限|责任|事务所|大学|学院|学校|医院|研究院|集团|工厂|"
    r"招标|咨询|管理|服务|项目部|采购|代理|银行|社|队|局|部|委|办|处|站|所)$")


def _clean_names(names: list[str]) -> list[str]:
    return [n for n in names if not BAD_NAME.search(n)]


# 单位名要避开「项目名称」「包名称」—— 用负向后视排除
ORG_KEYS = (r"代理机构名称", r"采购代理机构名称", r"采购人名称", r"单位名称",
            r"机构名称", r"(?<![项目包标段])名\s*称")


def _pick_org(block: str) -> str:
    for k in ORG_KEYS:
        v = _pick(block, k)
        if v:
            v = v.strip()
            if 3 <= len(v) <= 40 and not v.endswith(("项目", "公告")):
                return v
    return ""


def _pick(block: str, keys: str, tail: str = r"([^\n，,；;。]{1,30})") -> str:
    m = re.search(keys + _SEP + tail, block)
    return m.group(1).strip() if m else ""


def _phones(block: str) -> tuple[str, str]:
    """返回 (手机, 座机)。"""
    mob = re.search(r"(" + MOBILE + r")", block)
    tel = re.search(r"(" + TEL + r")", block)
    return (mob.group(1) if mob else "", tel.group(1) if tel else "")


def extract_contacts(text: str, buyer: str = "", agency: str = "") -> list[dict]:
    """抽取联系人：一个角色最多出一组（单位 + 电话），多人用顿号分条。

    先按角色块聚合「单位 / 电话」，再回填姓名 —— 反过来做会出现
    「有电话没姓名」和「有姓名没电话」两条重复记录。
    """
    text = text or ""
    blocks = _slice_blocks(text) or [("项目联系人", text)]
    info: dict[str, dict] = {}

    for role, blk in blocks:
        cur = info.setdefault(role, {"org": "", "mobile": "", "phone": "",
                                     "email": "", "names": []})
        org = _pick_org(blk)
        if org and len(org) > len(cur["org"]):
            cur["org"] = org
        near = re.search(r"(?:联系方式|电\s*话|联系电话|手机)[^\d\n]{0,8}("
                         + MOBILE + "|" + TEL + ")", blk)
        mob, tel = _phones(blk)
        if near:
            v = near.group(1)
            if re.fullmatch(MOBILE, v):
                mob, tel = v, tel or mob
            else:
                tel, mob = v, mob
        if tel and not cur["phone"]:
            cur["phone"] = tel
        if mob and not cur["mobile"]:
            cur["mobile"] = mob
        mail = re.search(r"(" + MAIL + r")", blk)
        if mail and not cur["email"]:
            cur["email"] = mail.group(1)
        for n in _clean_names(_split_names(_pick(blk, NAME_KEY, NAME_TAIL))):
            if n not in cur["names"]:
                cur["names"].append(n)

    for role, cur in info.items():
        if not cur["org"]:
            # 项目联系人多数由代理机构人员担任，找不到单位时优先挂代理机构
            cur["org"] = ({"采购人": buyer, "代理机构": agency}.get(role)
                          or (agency if role == "项目联系人" else buyer) or "")

    # 兜底：块里都没抓到姓名时，用全文再试一次（表格版式常见）
    if not any(c["names"] for c in info.values()):
        nm = _pick(text, NAME_KEY, NAME_TAIL)
        names = _clean_names(_split_names(nm))
        if names:
            tgt = info.get("项目联系人") or info.setdefault(
                "项目联系人", {"org": buyer or "", "mobile": "", "phone": "",
                               "email": "", "names": []})
            tgt["names"] = names
            if not tgt["org"]:
                tgt["org"] = buyer or ""

    out: list[dict] = []
    for role, cur in info.items():
        if not (cur["names"] or cur["mobile"] or cur["phone"]):
            continue
        for n in (cur["names"] or [""]):
            out.append({"role": role, "org": cur["org"], "name": n,
                        "mobile": cur["mobile"], "phone": cur["phone"],
                        "email": cur["email"]})
    return out


def structure_of(text: str, rec: dict | None = None) -> dict:
    """把正文 + 已有字段汇总成结构化信息 dict（值全为字符串，便于前端直接展示）。"""
    rec = rec or {}
    s: dict[str, str] = {}
    no = extract_project_no(text)
    method = extract_method(text, rec.get("title") or "")
    win = extract_win_amount(text)
    open_t = extract_open_time(text)
    pkg = extract_package(text)

    if no:
        s["项目编号"] = no
    if method:
        s["采购方式"] = method
    if pkg:
        s["包段"] = pkg
    if rec.get("amount"):
        s["预算金额"] = f"{rec['amount']} 万元"
    if win:
        s["中标金额"] = f"{win} 万元"
    if rec.get("winner"):
        s["中标单位"] = rec["winner"]
    if rec.get("deadline"):
        s["投标截止"] = rec["deadline"]
    if open_t and open_t != rec.get("deadline"):
        s["开标时间"] = open_t
    if rec.get("buyer"):
        s["采购人"] = rec["buyer"]
    if rec.get("agency"):
        s["代理机构"] = rec["agency"]
    return s
