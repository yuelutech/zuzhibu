"""七局定义与机构归属规则（docs/PRODUCT.md 第三、五节）。

人物的所属局按主要职务判定：现任职务里第一项非兼任、非委员会的职务。
机构到局的对照只看机构和职务文字（中文或英文），按 RULES 的顺序取第一个命中的局；
都不命中的企业、研究和投资机构，在 AI 与科技机构名单或关键词里的归二局，其余返回 None，由维护者定。

只用标准库。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

DISPATCH, AI, WHITEHOUSE, CONGRESS, MILITARY, JUSTICE, RETIRED = (
    "dispatch", "ai", "whitehouse", "congress", "military", "justice", "retired",
)


@dataclass(frozen=True)
class Bureau:
    id: str
    number: str
    name: str
    brief: str
    source_dir: str | None


BUREAUS: tuple[Bureau, ...] = (
    Bureau(DISPATCH, "一局", "干部调配局", "人事动态，维护者每月更新", "bureaus/dispatch"),
    Bureau(AI, "二局", "AI干部局", "人工智能与科技领袖", "bureaus/personnel-sv"),
    Bureau(WHITEHOUSE, "三局", "白宫干部局", "总统、白宫与内阁", "bureaus/personnel-whitehouse"),
    Bureau(CONGRESS, "四局", "国会干部局", "参众两院", "bureaus/personnel-congress"),
    Bureau(MILITARY, "五局", "美军干部局", "国防部与各军种", "bureaus/personnel-others"),
    Bureau(JUSTICE, "六局", "公检法干部局", "法院、司法部与执法机构", "bureaus/personnel-others"),
    Bureau(RETIRED, "七局", "老干部局", "离任无现职人员", "bureaus/retired"),
)
BY_ID = {b.id: b for b in BUREAUS}
#: 人物能归属的局（一局只管动态，不放人物）。
PERSON_BUREAUS = tuple(b.id for b in BUREAUS if b.id != DISPATCH)
#: 业务局：在任人物所在的局（不含一局、七局）。
ACTIVE_BUREAUS = tuple(b.id for b in BUREAUS if b.id not in (DISPATCH, RETIRED))


def label(bureau_id: str | None) -> str:
    """“四局·国会干部局”；未知编号原样返回。"""
    b = BY_ID.get(bureau_id or "")
    return f"{b.number}·{b.name}" if b else (bureau_id or "未定")


# ──────────────────────────────────────────────────────────────── 机构归属规则

# 每条：(局, 说明, 正则)。顺序要紧：国会在前（“参议院”不能被别的规则抢走），
# 军队、公检法在白宫之前（国防部、司法部虽是内阁部门，但归五局、六局）。
# 中文规则前面加 (?<!州)，排除州议会、州法院、州总检察长。
_RULES_SPEC: tuple[tuple[str, str, str], ...] = (
    (CONGRESS, "参众两院", r"(?<!州)(?<!州议会)(?:联邦参议员|联邦众议员|参议院|众议院|国会(?!图书馆)|众议院议长|参议院临时议长)"
                         r"|\bU\.?S\.? Senat|\bUnited States Senat|\bSenator\b|House of Representatives|\bU\.?S\.? Representative"
                         r"|\bCongress(?:man|woman|ional)?\b|Speaker of the House|Majority Leader|Minority Leader|\bWhip\b|Resident Commissioner"),
    (MILITARY, "国防部与军队", r"国防部|战争部|陆军|海军|空军|海军陆战队|太空军|海岸警卫队|参谋长联席会议|参联会|司令部|作战司令|国家安全局|国民警卫队|五角大楼"
                             r"|Department of (?:War|Defense)|Secretary of (?:War|Defense|the Army|the Navy|the Air Force)|Pentagon|\bArmy\b|\bNavy\b|Air Force"
                             r"|Marine Corps|Space Force|Coast Guard|Joint Chiefs|\bU\.?S\.? (?:[A-Z][A-Za-z-]+ )+Command\b|Combatant Command|Cyber Command|Space Command"
                             r"|Strategic Command|Pacific Command|National Security Agency|National Guard"),
    (JUSTICE, "法院、司法部与执法机构", r"(?<!州)最高法院|联邦法院|上诉法院|地区法院|(?<!州)大法官|联邦法官|司法部|(?<!州)总检察长|联邦检察官|联邦调查局|缉毒局"
                                     r"|烟酒枪炮及爆炸物管理局|法警局|移民和海关执法局|移民与海关执法局|海关和边境保护局|海关与边境保护局|特勤局"
                                     r"|Supreme Court|Court of Appeals|District Court|Chief Justice|Associate Justice|Department of Justice|Attorney General"
                                     r"|U\.?S\.? Attorney|Federal Bureau of Investigation|\bFBI\b|Drug Enforcement|\bDEA\b|Alcohol, Tobacco|\bATF\b"
                                     r"|Marshals Service|Immigration and Customs Enforcement|\bICE\b|Customs and Border Protection|\bCBP\b|Secret Service"),
    (WHITEHOUSE, "总统、白宫、内阁与独立机构", r"美国总统|美国副总统|^总统$|^副总统$|白宫|总统行政办公室|国务院|国务卿|财政部|商务部|能源部|内政部|农业部|劳工部"
                                          r"|卫生与公众服务部|卫生和公众服务部|住房和城市发展部|住房与城市发展部|交通部|教育部|退伍军人事务部|国土安全部"
                                          r"|环境保护局|环境保护署|管理和预算办公室|贸易代表|常驻联合国代表|驻联合国大使|小企业管理局|科学和技术政策办公室"
                                          r"|科技政策办公室|国家经济委员会|国家安全委员会|国家安全事务助理|国家情报总监|中央情报局|联邦通信委员会|联邦储备|美联储"
                                          r"|证券交易委员会|联邦贸易委员会|国家航空航天局|人事管理局|总务管理局|国家标准与技术研究院|内阁|驻.+大使"
                                          r"|商品期货交易委员会|联邦住房金融局|国家药物管制政策办公室|国家网络总监|常驻联合国|特使"
                                          # 2026-10-21 补：独立机构归三局（第五节），先补库里已有人物任职的和常见的
                                          r"|全国信用合作社管理局|国家档案和记录管理局|档案局|消费品安全委员会|全国劳资关系委员会|农业信贷管理局"
                                          r"|农业信贷系统保险公司|国家首都规划委员会|特别检察官办公室|千年挑战公司|非洲发展基金会|美洲基金会"
                                          r"|功绩制保护委员会|邮政监管委员会|地面运输委员会|公众公司会计监督委员会|全国调解委员会"
                                          r"|职业安全与健康审查委员会|国家人文基金会|国家艺术基金会|进出口银行|国家运输安全委员会|联邦存款保险公司"
                                          r"|联邦选举委员会|平等就业机会委员会|核管理委员会|联邦海事委员会|国际贸易委员会|消费者金融保护局"
                                          r"|国际开发金融公司|国际开发署|国家科学基金会|社会保障署|联邦能源管理委员会|和平队"
                                          r"|President of the United States|Vice President of the United States|White House|Executive Office of the President"
                                          r"|Secretary of State|Department of State|Treasury|Department of (?:Commerce|Energy|the Interior|Agriculture|Labor"
                                          r"|Health and Human Services|Housing and Urban Development|Transportation|Education|Veterans Affairs|Homeland Security)"
                                          r"|Environmental Protection Agency|Office of Management and Budget|Trade Representative|\bAmbassador\b"
                                          r"|Small Business Administration|Office of Science and Technology Policy|National Economic Council"
                                          r"|National Security Council|Director of National Intelligence|Central Intelligence Agency|\bCIA\b"
                                          r"|Federal Communications Commission|\bFCC\b|Federal Reserve|Securities and Exchange Commission|\bSEC\b"
                                          r"|Federal Trade Commission|\bFTC\b|\bNASA\b|Office of Personnel Management|General Services Administration|\bNIST\b"
                                          r"|National Aeronautics and Space Administration|Commodity Futures Trading Commission|\bCFTC\b"
                                          r"|Federal Housing Finance Agency|\bFHFA\b|Office of National Drug Control Policy|National Cyber Director"
                                          r"|(?:Mission|Representative) to the United Nations|Special Envoy"
                                          r"|National Credit Union Administration|\bNCUA\b|National Archives and Records Administration"
                                          r"|Archivist of the United States|Consumer Product Safety Commission|National Labor Relations Board"
                                          r"|Farm Credit (?:Administration|System Insurance Corporation)|National Capital Planning Commission"
                                          r"|Office of Special Counsel|Millennium Challenge Corporation|African Development Foundation"
                                          r"|Inter-American Foundation|Merit Systems Protection Board|Postal Regulatory Commission"
                                          r"|Surface Transportation Board|Public Company Accounting Oversight Board|National Mediation Board"
                                          r"|Occupational Safety and Health Review Commission|National Endowment for the (?:Humanities|Arts)"
                                          r"|Export-Import Bank|National Transportation Safety Board|Federal Deposit Insurance Corporation"
                                          r"|Federal Election Commission|Equal Employment Opportunity Commission|Nuclear Regulatory Commission"
                                          r"|Federal Maritime Commission|International Trade Commission|Consumer Financial Protection Bureau"
                                          r"|(?:U\.?S\.? )?International Development Finance Corporation|Agency for International Development"
                                          r"|National Science Foundation|Social Security Administration|Federal Energy Regulatory Commission"
                                          r"|Peace Corps"),
)
RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = tuple((b, why, re.compile(rx)) for b, why, rx in _RULES_SPEC)

#: 二局的机构：AI 与科技企业、研究机构、投资机构（名册 bureaus/personnel-sv/roster 的 34 组，加常见写法）。
AI_ORGS: tuple[str, ...] = (
    "OpenAI", "Anthropic", "Google", "DeepMind", "Alphabet", "Microsoft", "Meta", "Facebook", "Apple", "Amazon", "AWS",
    "NVIDIA", "Nvidia", "AMD", "Intel", "TSMC", "Oracle", "Tesla", "SpaceX", "xAI", "X Corp", "Palantir", "Broadcom",
    "Qualcomm", "Arm", "Samsung", "SK hynix", "Mistral", "Hugging Face", "Scale AI", "Databricks", "CoreWeave",
    "Perplexity", "Safe Superintelligence", "Thinking Machines", "Andreessen Horowitz", "a16z", "Sequoia",
    "Founders Fund", "Thrive", "Khosla", "IBM", "Salesforce", "Snowflake", "Cisco", "Adobe", "ByteDance", "TikTok",
    "Uber", "Neuralink", "Boring Company", "Cohere", "Inflection", "Character.AI", "Stability AI", "Midjourney",
    "开放人工智能研究中心", "谷歌", "微软", "苹果公司", "亚马逊", "英伟达", "超威半导体", "英特尔", "台积电", "台湾积体电路",
    "甲骨文", "特斯拉", "太空探索技术公司", "帕兰提尔", "博通", "高通", "安谋", "三星", "海力士", "抱抱脸", "元宇宙平台公司",
)
_AI_KEYWORDS = re.compile(r"人工智能|大模型|芯片|半导体|科技|技术公司|软件|云计算|实验室|风险投资|创投|资本|\bAI\b|Artificial Intelligence"
                          r"|Semiconductor|Technolog|Software|\bLabs?\b|Robotics|Quantum|Ventures"
                          r"|(?<!National )Capital\b")  # 风投的 Capital；National Capital Planning Commission（首都规划委员会）不算
_AI_ORG_RX = re.compile("|".join(
    # 英文名前后不能紧挨字母：Arm 不命中 Army、Armstrong，Intel 不命中 Intelligence
    rf"(?<![A-Za-z]){re.escape(o)}(?![A-Za-z])" if o.isascii() else re.escape(o)
    for o in sorted(AI_ORGS, key=len, reverse=True)
))

#: 维护者 2026-10-23 定：不收中国企业（总部在中国的企业，含港澳台地区）。这里列常见的，涉及这些企业职务的动态不写回、不公开
#: （bureaus/dispatch/run.py、zuzhibu/renmian.py），研究提示词里也写明（bureaus/dispatch/research.py 的 SCOPES）。
CHINESE_COMPANIES: tuple[str, ...] = (
    "华为", "中兴", "腾讯", "阿里巴巴", "阿里云", "蚂蚁集团", "百度", "字节跳动", "小米", "京东", "美团", "拼多多", "快手", "网易",
    "联想", "比亚迪", "宁德时代", "中芯国际", "大疆", "商汤", "旷视", "科大讯飞", "寒武纪", "深度求索", "月之暗面", "智谱", "稀宇",
    "百川智能", "阶跃星辰", "零一万物", "面壁智能", "摩尔线程", "壁仞", "宇树", "台积电", "台湾积体电路", "鸿海", "富士康", "联发科",
    "华硕", "宏碁", "广达电脑", "纬创", "和硕",
    "Huawei", "ZTE", "Tencent", "Alibaba", "Ant Group", "Baidu", "ByteDance", "Xiaomi", "JD.com", "Meituan", "Pinduoduo",
    "PDD Holdings", "Kuaishou", "NetEase", "Lenovo", "BYD", "CATL", "SMIC", "DJI", "SenseTime", "Megvii", "iFlytek", "Cambricon",
    "DeepSeek", "Moonshot AI", "Zhipu", "MiniMax", "Baichuan", "StepFun", "01.AI", "ModelBest", "Moore Threads", "Biren",
    "Unitree", "TSMC", "Taiwan Semiconductor", "Hon Hai", "Foxconn", "MediaTek", "ASUS", "Acer", "Quanta Computer", "Wistron",
    "Pegatron",
)
_CN_RX = re.compile("|".join(
    # 英文名前后不能紧挨字母：DJI 不命中 Djibouti，Acer 不命中 Racer
    rf"(?<![A-Za-z]){re.escape(o)}(?![A-Za-z])" if o.isascii() else re.escape(o)
    for o in sorted(CHINESE_COMPANIES, key=len, reverse=True)
))


def chinese_company(role: dict[str, Any]) -> bool:
    """这条职务的机构是不是中国企业（看中英文机构名）。"""
    return bool(_CN_RX.search(" ".join(str(role.get(k) or "") for k in ("organization", "organization_en"))))


def role_text(role: dict[str, Any]) -> str:
    """一条职务的全部文字：机构、职务、英文机构、英文职务。"""
    parts = [role.get("organization"), "、".join(role.get("roles") or []) or role.get("role"),
             role.get("organization_en"), role.get("role_en")]
    return " ".join(str(p) for p in parts if p)


def classify_role(role: dict[str, Any]) -> tuple[str | None, str]:
    """一条职务归哪个局。返回 (局编号或 None, 依据)。"""
    text = role_text(role)
    if not text.strip():
        return None, "职务为空"
    for bureau, why, rx in RULES:
        if rx.search(text):
            return bureau, why
    if _AI_ORG_RX.search(text):
        return AI, "AI 与科技机构名单"
    if _AI_KEYWORDS.search(text):
        return AI, "AI 与科技关键词"
    return None, "不在各局范围"


#: 还没到任的任职状态（rules/cadre.md 第 1 节第 4 条）：定所属局时排在已到任的职务后面。
PENDING_STATES = ("提名人选", "候任", "当选人", "拟任")


def primary_role(roles: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """主要职务：已到任的职务里第一项非兼任、非委员会的；没有就取第一项非委员会职务，再没有取第一项。
    只有提名、候任一类职务的，才从这些里取。"""
    roles = [r for r in roles if isinstance(r, dict)]
    seated = [r for r in roles if r.get("appointment_state") not in PENDING_STATES]
    for pool in (seated, roles):
        for r in pool:
            if not r.get("concurrent") and r.get("sector") != "committee":
                return r
        for r in pool:
            if r.get("sector") != "committee":
                return r
    return roles[0] if roles else None


def classify_person(current_roles: Iterable[dict[str, Any]]) -> tuple[str | None, str]:
    """按主要职务定所属局。没有现任职务返回 (None, "无现任职务")，由调用方决定是否转七局。"""
    role = primary_role(current_roles)
    if role is None:
        return None, "无现任职务"
    bureau, why = classify_role(role)
    return bureau, f"主要职务“{role_text(role)}”：{why}"


def as_dict(bureau: Bureau) -> dict[str, Any]:
    return {"id": bureau.id, "number": bureau.number, "name": bureau.name, "label": f"{bureau.number}·{bureau.name}",
            "brief": bureau.brief, "source_dir": bureau.source_dir}
