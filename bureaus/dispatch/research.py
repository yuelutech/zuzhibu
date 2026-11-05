"""一局的研究和核查：两次联网会话，加程序放行（fail-closed）。

会话两种走法，提示词、输出 schema 和放行规则相同：维护者本机已登录的 Codex（shared/backends/codex_cli.py），
或使用者在“我的”页填的自己的 AI 接口（shared/backends/ai_api.py，用使用者自己的额度）。

做法照 reverify.py：研究会话逐条取证输出结构化动态；另开核查会话重新打开来源逐字段核对；
放行规则在程序里：核查没联网、没明确通过、有不过的字段、没有核实的来源，一律搁置。
离任类的“没有其他现职”“没有新任命”两项，核查确认了才保留，否则改成“未查清”，写回时就不会转入七局。

补采的动态（run.py --inbox）先过程序核查 precheck：逐条取来源网页，一级来源的摘录逐字找到、
人物、职务、类别、日期都在来源里的直接放行；过不了的再开核查会话，一次一条。

只用标准库。
"""

from __future__ import annotations

import copy
import html
import json
import re
import unicodedata
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from shared.backends import ai_api, codex_cli
from zuzhibu import bureaus, personnel, sources

DEFAULT_MODEL = "gpt-6.1-sol"
DEFAULT_EFFORT = "medium"
# 会话隔离与 reverify.py 相同：不读本机 config.toml，只读沙箱，只留联网检索（code_mode_host 不能关）。
ISOLATION_ARGS = ("--ignore-user-config", "--ignore-rules")
ISOLATION_OVERRIDES = (
    'approval_policy="never"',
    "project_doc_max_bytes=0",
    *(f"features.{name}=false" for name in (
        "shell_tool", "unified_exec", "apps", "plugins", "remote_plugin", "hooks", "memories", "goals",
        "multi_agent", "browser_use", "browser_use_external", "computer_use", "image_generation")),
)

_ROLE = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "organization": {"type": "string"}, "role": {"type": "string"},
        "organization_en": {"type": "string"}, "role_en": {"type": "string"},
        "start": {"type": ["string", "null"]}, "end": {"type": ["string", "null"]},
        "concurrent": {"type": "boolean"},
        "sector": {"type": "string", "enum": ["enterprise", "advisory", "politics", "committee"]},
        "source_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["organization", "role", "organization_en", "role_en", "start", "end", "concurrent", "sector", "source_ids"],
}
_SOURCE = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "id": {"type": "string"}, "url": {"type": "string"}, "title": {"type": "string"}, "publisher": {"type": "string"},
        "published_at": {"type": ["string", "null"]}, "excerpt": {"type": "string"},
    },
    "required": ["id", "url", "title", "publisher", "published_at", "excerpt"],
}
EVENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "candidate_id": {"type": ["string", "null"]},
        "kind": {"type": "string", "enum": list(personnel.KINDS)},
        "person_id": {"type": ["string", "null"]},
        "name_en": {"type": "string"}, "name_zh": {"type": "string"},
        "announced_at": {"type": ["string", "null"]}, "effective_at": {"type": ["string", "null"]},
        "ended_roles": {"type": "array", "items": _ROLE},
        "new_roles": {"type": "array", "items": _ROLE},
        "other_current_roles": {"anyOf": [{"type": "array", "items": _ROLE}, {"type": "null"}]},
        "new_appointment": {"anyOf": [_ROLE, {"type": "null"}]},
        "new_appointment_checked": {"type": "boolean"},
        "headline_zh": {"type": "string"}, "summary_zh": {"type": "string"},
        "sources": {"type": "array", "items": _SOURCE},
        "bureau_hint": {"type": "string", "enum": [*bureaus.ACTIVE_BUREAUS, "none"]},
    },
    "required": ["candidate_id", "kind", "person_id", "name_en", "name_zh", "announced_at", "effective_at", "ended_roles",
                 "new_roles", "other_current_roles", "new_appointment", "new_appointment_checked", "headline_zh",
                 "summary_zh", "sources", "bureau_hint"],
}
RESEARCH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "events": {"type": "array", "items": EVENT_SCHEMA},
        "checked_candidates": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"candidate_id": {"type": "string"},
                           "outcome": {"type": "string", "enum": ["event", "no_change", "unverifiable"]},
                           "note": {"type": "string"}},
            "required": ["candidate_id", "outcome", "note"]}},
        "notes": {"type": "string"},
    },
    "required": ["events", "checked_candidates", "notes"],
}
VERIFY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"reviews": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "index": {"type": "integer"},
            "decision": {"type": "string", "enum": ["approve", "hold"]},
            "failed_claims": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {"field": {"type": "string"}, "problem": {"type": "string"}},
                "required": ["field", "problem"]}},
            "corrections": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {"field": {"type": "string"}, "value": {"type": "string"},
                               "source_url": {"type": "string"}, "source_title": {"type": "string"},
                               "source_excerpt": {"type": "string"}},
                "required": ["field", "value", "source_url", "source_title", "source_excerpt"]}},
            "verified_source_ids": {"type": "array", "items": {"type": "string"}},
            "other_current_roles_confirmed": {"type": "boolean"},
            "new_appointment_confirmed": {"type": "boolean"},
            "notes": {"type": "string"},
        },
        "required": ["index", "decision", "failed_claims", "corrections", "verified_source_ids",
                     "other_current_roles_confirmed", "new_appointment_confirmed", "notes"]}}},
    "required": ["reviews"],
}

SCOPES = {
    bureaus.AI: "二局（AI干部局）：人工智能和科技企业、研究机构、投资机构的领导人物。下面在库人物的任何职务变动都要查；"
                "在库人物所在机构的首席执行官、总裁、董事长、首席技术官、首席科学家、联合创始人、研究负责人和董事变动也收。"
                "库外机构只收全球主要人工智能公司（大模型、算力芯片、云计算、头部人工智能应用，估值或市值居行业前列）"
                "和顶级风险投资机构的首席执行官、总裁、董事长、联合创始人、首席技术官、首席科学家一级的任免；"
                "库外中小公司的董事任免、高校临时院长和所长、副总裁以下职务不收。"
                "中国企业（总部在中国的企业，含港澳台地区）的人事一律不收。",
    bureaus.WHITEHOUSE: "三局（白宫干部局）：总统、副总统、白宫办公厅与总统行政办公室（幕僚长、国家安全事务助理、国家经济委员会、"
                        "科技政策办公室、白宫人工智能事务主管等），内阁各部部长和副部长（国防部、司法部除外），国家情报总监、"
                        "中央情报局局长，联邦通信委员会、美联储理事会、证券交易委员会、联邦贸易委员会等独立机构负责人，驻外大使。",
    bureaus.CONGRESS: "四局（国会干部局）：参众两院议员的就任、辞职、逝世、特别选举、任命补缺，议长、多数党和少数党领袖、党鞭等领导职务，"
                      "常设委员会主席的更替。",
    bureaus.MILITARY: "五局（美军干部局）：国防部（战争部）部长、副部长、各军种部长，参谋长联席会议主席和副主席，陆军、海军、空军、"
                      "海军陆战队、太空军、海岸警卫队的军种首长，各作战司令部司令，国家安全局局长。",
    bureaus.JUSTICE: "六局（公检法干部局）：最高法院大法官和联邦上诉法院法官，司法部长、常务副部长、副部长、首席政府律师，"
                     "联邦调查局、缉毒局、烟酒枪炮及爆炸物管理局、法警局局长，移民和海关执法局、海关和边境保护局、特勤局负责人。",
}
#: 研究会话分组：按资料库五栏（内部局名），每组一次研究会话；立即更新按组报进度（“白宫 2/5”）。
GROUPS = {
    "whitehouse": (bureaus.WHITEHOUSE,),
    "ai": (bureaus.AI,),
    "congress": (bureaus.CONGRESS,),
    "military": (bureaus.MILITARY,),
    "justice": (bureaus.JUSTICE,),
}
GROUP_LABELS = {"whitehouse": "白宫", "ai": "商界", "congress": "国会", "military": "国防", "justice": "司法"}
#: 旧的组名：gov 是三局、五局、六局合一组（2026-10-21 前）。
GROUP_ALIASES = {"gov": ("whitehouse", "military", "justice")}


def web_config(codex: str, model: str, effort: str, timeout_s: float) -> codex_cli.CodexConfig:
    return codex_cli.CodexConfig(
        command=(codex,), model=model, sandbox="read-only", timeout=timeout_s, retries=1, web_search="live",
        config_overrides=(f'model_reasoning_effort="{effort}"', *ISOLATION_OVERRIDES), extra_args=ISOLATION_ARGS,
        ignore_user_config=False, disabled_features=(),
    )


def _role_line(rec: dict[str, Any]) -> str:
    roles = (rec.get("resume") or {}).get("current_roles") or []
    zh = "、".join(bureaus.role_text(r) for r in roles[:3]) or "（无现任职务）"
    en = "; ".join(f"{r.get('organization_en')} {r.get('role_en')}" for r in rec.get("roster_roles") or [])
    return zh + (f"〔{en}〕" if en else "")


def watch_list(people: list[dict[str, Any]], scope: tuple[str, ...]) -> str:
    """在库人物：二局、三局、五局、六局全部列出；四局只列有领导职务的（议员变动靠线索）。"""
    lines = []
    for rec in sorted(people, key=lambda r: (r.get("bureau") or "", r["id"])):
        b = rec.get("bureau")
        if b not in scope:
            continue
        if b == bureaus.CONGRESS:
            roles = (rec.get("resume") or {}).get("current_roles") or []
            if not any(re.search(r"议长|领袖|党鞭|主席", bureaus.role_text(r)) and r.get("sector") == "politics" for r in roles):
                continue
        lines.append(f"- {rec['id']}｜{rec.get('name_zh') or '（待译）'}｜{rec.get('name_en')}｜{bureaus.label(b)}｜{_role_line(rec)}")
    return "\n".join(lines) or "（无）"


def roster_block(roster: str) -> str:
    """名单上的职务（资料库五栏）：这些位置有人离任、换人、到任的都要查明，接任者不在库里也要写出英文全名。"""
    if not roster.strip():
        return ""
    return ("\n【名单职务】软件资料库名单上的下列职务（编号｜姓名｜英文名｜职务），窗口内有人离任、换人、到任或代理的，"
            "都要查明并输出动态；接任者不在库里的 person_id 填 null，写明英文全名。\n" + roster.strip() + "\n")


def research_prompt(*, today: str, since: str, until: str, scope: tuple[str, ...], leads: list[dict[str, Any]],
                    people: list[dict[str, Any]], min_searches: int, roster: str = "") -> str:
    lead_lines = "\n".join(
        f"- {c['id']}｜{c.get('name_zh') or ''} {c.get('name_en')}｜{c['role'].get('organization')}{c['role'].get('role') or ''}"
        f"〔{c['role'].get('organization_en') or ''} {c['role'].get('role_en') or ''}〕｜{c.get('date')}｜{c['lead']}"
        for c in leads) or "（无）"
    scope_lines = "\n".join(f"- {SCOPES[b]}" for b in scope)
    return f"""你是“AI政见·组织部”一局（干部调配局）的人事动态核实员。任务：查明 {since} 至 {until} 期间下列范围的美国人事变动，逐条取证，输出结构化动态。今天是 {today}。

【范围】
{scope_lines}

【线索】程序从免费公开数据里发现的，逐条核实；每条都在 checked_candidates 里给出结论（event、no_change 或 unverifiable）。
{lead_lines}

【在库人物】（编号｜中文名｜英文名｜所属局｜现任职务）。变动涉及这些人时 person_id 填编号；不在库里的填 null。
{watch_list(people, scope)}
{roster_block(roster)}
【主动检索】线索之外，再按上面的范围检索窗口内的任命、离任、调任、代理、逝世、提名和参议院确认，至少 {min_searches} 次不同检索。只保留发布或生效日期在窗口内、能打开原文核实的。

【取证纪律】
1. 每条来源都要真正打开网页。excerpt 照抄网页原文（英文照录，不翻译，300 个字符以内）；原文没写的细节不写。
2. 来源优先：机构官方公告和官网任职页，政府官方发布（白宫、国会、参议院、各部、各军种、法院），联邦公报，证券交易委员会申报（8-K 第 5.02 项）；其次是路透社、美联社、彭博社等权威媒体。维基百科只作线索，不作来源。
3. 日期写 YYYY-MM-DD，只知道月份写 YYYY-MM。announced_at 是宣布日，effective_at 是生效日（就职、宣誓、离任、逝世之日）；不知道填 null，不推测。
4. 离任（leave）、逝世（death）：查明此人之后是否还有其他现任职务——查过确实没有填 []，没查清填 null；是否已公布新任命——有就写进 new_appointment，没有填 null；new_appointment_checked 只有确实查过才填 true。
5. 调任（transfer）同时填原职务 ended_roles 和新职务 new_roles；任命（appoint）、代理（acting）只填 new_roles。接替某人时，前任的去向单独写一条动态。提名（nominate）、确认（confirm）尚未就职的，把提名职务写进 new_roles。
6. 中文写法：人名用在库人物表或新华社译名；表外人名按新华社译名规则音译，并在 notes 里列出“表外专名”。机构和职务：
   - 国会议员：organization 写“美国联邦参议员（南卡罗来纳州）”“美国联邦众议员（佐治亚州第13选区）”，role 写空字符串，sector 填 politics；
   - 企业：organization 写公司中文名（如“苹果公司”），role 写职务（如“首席执行官”），sector 填 enterprise；
   - 政府、军队、法院：organization 写机构名（如“美国国家情报总监办公室”“美国太空军”），role 写职务（如“国家情报总监”“太空作战部长”），军衔不写进 role，sector 填 politics；
   - organization_en、role_en 写英文原文；兼任的 concurrent 填 true。
7. headline_zh、summary_zh 只作内部记录，不面向读者（界面和输出的标题由程序按字段生成）：headline_zh 用一句话概括这条变动；summary_zh 记取证要点（依据哪条来源、哪些没查清），供核查和维护者参考。
8. 不编造。查不到的不写进 events，在 checked_candidates 里写 unverifiable 和原因。
9. 不运行命令，不读写文件，只用联网检索和打开网页。

【输出】只输出符合 schema 的 JSON。每条动态的 sources 用 e1、e2……编号，各职务的 source_ids 引用这些编号。bureau_hint 填这条动态主要涉及的局（{"、".join(bureaus.ACTIVE_BUREAUS)}，都不是填 none）。
"""


def verify_prompt(*, today: str, events: list[dict[str, Any]]) -> str:
    listing = json.dumps([{"index": n, **{k: v for k, v in e.items() if k not in ("candidate_id", "bureau_hint")}}
                          for n, e in enumerate(events)], ensure_ascii=False, indent=1)
    return f"""你是“AI政见·组织部”一局的独立核查员。下面是另一次会话整理的人事动态。重新打开每条来源，逐字段核对，给出每条动态的结论。今天是 {today}。

【核对要求】
1. 每条来源都重新打开：网页能打开、确属此人此事、excerpt 与原文一致。不一致或打不开的，不列入 verified_source_ids。
2. 逐字段核对：人物、类别、原职务、新职务、宣布日期、生效日期，中文职务与英文原文是否对应。能确定正确值的，写进 corrections（field 写字段路径，如 effective_at、new_roles[0].start，value 写正确值），这一项就不再写进 failed_claims；确有错误又定不了正确值的，才写进 failed_claims。每条更正都要附所依据的网页：source_url 写本次打开过的网页链接，source_title 写网页标题，source_excerpt 照抄能看出正确值的原文（英文照录，300 个字符以内）；没附来源的更正不采纳。职务名称（role、role_en、organization、organization_en）的更正只收一级来源（机构官网、政府网站、证券交易委员会申报），媒体报道不能用来改职务名。来源打不开、摘录对不上的，只是不列入 verified_source_ids，不写进 failed_claims。
3. 离任、逝世类：另行检索此人是否还有其他现职、是否已公布新任命。other_current_roles 的说法成立，other_current_roles_confirmed 填 true；new_appointment 的说法（包括“没有新任命”）成立，new_appointment_confirmed 填 true；没查清都填 false。
4. 各字段都有核实的来源支持（更正后的值也算）、至少一条来源核实，decision 填 approve；否则填 hold，在 notes 写原因。
5. headline_zh、summary_zh 只作内部记录，不用核对，也不用更正。
6. 不运行命令，不读写文件，只用联网检索和打开网页。

【待核动态】
{listing}

【输出】只输出符合 schema 的 JSON；reviews 按 index 一一对应，每条动态都要有一条 review。
"""


# ──────────────────────────────────────────────────────────────── Codex 调用与统计


_VIEWED = re.compile(r"view\d+$")  # 检索结果里整页读过的网页：ref_id 形如 turn0view0


def web_stats(attempts) -> dict[str, Any]:
    """从 JSONL 事件流统计联网检索次数、打开的网页和用量。

    打开的网页：open_page、find_in_page 动作的链接，加上检索结果里整页读过的（ref_id 是 turnNviewM、摘要是“Total lines”），
    检索动作有时直接把网页整页读进来，不另记 open_page。
    """
    stats: dict[str, Any] = {"searches": 0, "opened": [], "input_tokens": 0, "output_tokens": 0, "errors": []}
    for attempt in attempts or []:
        path = Path(getattr(attempt, "stdout_path", "") or "")
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "turn.completed":
                usage = event.get("usage") or {}
                stats["input_tokens"] += int(usage.get("input_tokens") or 0)
                stats["output_tokens"] += int(usage.get("output_tokens") or 0)
            elif event.get("type") in ("error", "turn.failed"):
                stats["errors"].append(str(event.get("message") or event.get("error"))[:300])
            elif event.get("type") == "item.completed":
                item = event.get("item") or {}
                if item.get("type") in ("web_search", "web_search_call") and item.get("status") not in ("failed", "error"):
                    stats["searches"] += 1
                    action = item.get("action") or {}
                    if action.get("type") in ("open_page", "find_in_page") and action.get("url"):
                        stats["opened"].append(action["url"])
                    stats["opened"] += [r["url"] for r in item.get("results") or []
                                        if isinstance(r, dict) and r.get("url") and _VIEWED.search(str(r.get("ref_id") or ""))]
    stats["opened"] = list(dict.fromkeys(stats["opened"]))
    return stats


def call(prompt: str, schema: dict[str, Any], config: codex_cli.CodexConfig | ai_api.ApiConfig, runs: Path,
         label: str) -> tuple[Any, dict[str, Any], str | None]:
    """开一次会话：codex exec，或使用者自己的 AI 接口。返回 (结果 JSON 或 None, 统计, 错误说明或 None)。
    Codex 未登录、找不到程序，AI 接口密钥或额度有问题、不能联网检索，直接抛出（整次更新停下）。"""
    if isinstance(config, ai_api.ApiConfig):
        try:
            result = ai_api.run(prompt, schema, config, runs, label=label)
        except (ai_api.ApiAuthError, ai_api.ApiNoWebSearch, ai_api.ApiCancelled):
            raise
        except ai_api.ApiError as exc:
            return None, {**exc.stats, "reason": ai_api.reason_of(exc)[1]}, f"{type(exc).__name__}：{exc}"
        return result.data, result.stats, None
    try:
        result = codex_cli.run(prompt, schema, config, runs, label=label)
    except (codex_cli.CodexAuthError, codex_cli.CodexNotFound):
        raise
    except codex_cli.CodexError as exc:
        return None, web_stats(exc.attempts), f"{type(exc).__name__}：{exc}"
    return result.data, web_stats(result.attempts), None


# ──────────────────────────────────────────────────────────────── 放行


_PATH = re.compile(r"^(\w+)(?:\[(\d+)\])?(?:\.(\w+))?$")


def _text_value(spec: dict[str, Any], value: str) -> tuple[bool, str | None]:
    """schema 里是文字的字段才能用一句文字更正：返回 (能不能改, 改成什么)。"""
    types = spec.get("type") if isinstance(spec.get("type"), list) else [spec.get("type")]
    if "string" not in types:
        return False, None   # 列表、对象、是非项（含眼下是空值的 other_current_roles、new_appointment）不能用一句文字顶替
    if value in ("", "null") and "null" in types:
        return True, None
    if spec.get("enum") and value not in spec["enum"]:
        return False, None
    return True, value


def _apply_correction(event: dict[str, Any], field: str, value: str) -> bool:
    m = _PATH.match(field.strip())
    if not m:
        return False
    key, index, sub = m.group(1), m.group(2), m.group(3)
    if key not in event:
        return False
    if index is None and sub is None:
        ok, new = _text_value(EVENT_SCHEMA["properties"].get(key) or {}, value)
        if ok:
            event[key] = new
        return ok
    if index is not None and key in ("ended_roles", "new_roles") and isinstance(event[key], list) \
            and int(index) < len(event[key]) and sub and isinstance(event[key][int(index)], dict):
        ok, new = _text_value(_ROLE["properties"].get(sub) or {}, value)
        if ok:
            event[key][int(index)][sub] = new
        return ok
    return False


_ROLE_NAME_FIELD = re.compile(r"^(?:ended_roles|new_roles)\[\d+\]\.(?:role|role_en|organization|organization_en)$")


def _correction_source_problem(c: dict[str, Any], seen: set[str] | None) -> str | None:
    """核查会话的一条更正能不能采纳：要附来源链接和原文摘录；给了 seen 时，来源要是会话里打开过的；
    改职务名称的，来源要是一级来源。能采纳返回 None，否则返回原因。"""
    url, excerpt = (c.get("source_url") or "").strip(), (c.get("source_excerpt") or "").strip()
    if not url or not excerpt:
        return "没附来源"
    if seen is not None and _url_key(url) not in seen:
        return "所据网页本次会话没打开过"
    if _ROLE_NAME_FIELD.match((c.get("field") or "").strip()) and sources.tier_of(url) != 1:
        return "职务名称的更正只收一级来源"
    return None


def _cite_correction(event: dict[str, Any], field: str, c: dict[str, Any]) -> str:
    """把更正所附的来源记进动态的来源表（同一链接、同一摘录只记一次），并挂到受影响的职务上。返回来源编号。"""
    url, excerpt = c["source_url"].strip(), c["source_excerpt"].strip()
    srcs = event.setdefault("sources", [])
    src = next((s for s in srcs if s.get("url") == url and s.get("excerpt") == excerpt), None)
    if src is None:
        used, n = {s.get("id") for s in srcs}, 1
        while f"c{n}" in used:
            n += 1
        src = {"id": f"c{n}", "url": url, "title": (c.get("source_title") or "").strip() or url, "publisher": "",
               "published_at": None, "excerpt": excerpt, "supports": []}
        srcs.append(src)
    src.setdefault("supports", []).append(field)
    m = _PATH.match(field)
    key, index = (m.group(1), m.group(2)) if m else (field, None)
    if key in ("ended_roles", "new_roles") and index is not None and int(index) < len(event.get(key) or []):
        targets = [event[key][int(index)]]
    elif key == "effective_at":
        targets = (event.get("ended_roles") or []) + (event.get("new_roles") or [])  # 生效日就是职务起止日
    else:
        targets = []
    for role in targets:
        if src["id"] not in (role.get("source_ids") or []):
            role.setdefault("source_ids", []).append(src["id"])
    return src["id"]


def _url_key(url: str) -> str:
    """比对用的链接：不分 http、https 和 www.，去掉片段和末尾斜杠。"""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return ""
    host = (parts.hostname or "").lower().removeprefix("www.")
    return host + parts.path.rstrip("/") + (f"?{parts.query}" if parts.query else "")


def gate(events: list[dict[str, Any]], review: dict[str, Any] | None, verify_searches: int,
         opened: list[str] | None = None) -> list[dict[str, Any]]:
    """逐条放行。返回每条：{event（改过的副本）, status: verified|held, reasons, corrections}。

    给了 opened（核查会话打开过的网页）时，核查说核实了、会话里却没打开过的来源不算核实（补采动态一次一条地核）。
    """
    reviews = {r.get("index"): r for r in (review or {}).get("reviews") or []}
    seen = {_url_key(u) for u in opened or []}
    out = []
    for n, original in enumerate(events):
        event = copy.deepcopy(original)
        reasons: list[str] = []
        applied: list[str] = []
        r = reviews.get(n)
        if verify_searches <= 0:
            reasons.append("核查会话没有联网检索记录")
        if r is None:
            reasons.append("核查没有给出结论")
            out.append({"event": event, "status": "held", "reasons": reasons, "corrections": applied})
            continue
        corrected, cited = set(), set()
        for c in r.get("corrections") or []:
            field, value = (c.get("field") or "").strip(), c.get("value", "")
            why = _correction_source_problem(c, seen if opened is not None else None)
            if why:
                applied.append(f"更正不采纳：{field} → {value}（{why}）")
                continue
            if _apply_correction(event, field, value):
                sid = _cite_correction(event, field, c)
                cited.add(sid)
                applied.append(f"{field} → {value}（来源 {sid}）")
                corrected.add(field)
        # 来源记录问题（sources[i]…）不交模型裁量，照 reverify.py：这条来源不算核实；另有核实来源撑住各字段的，删去它，不整条搁置
        source_only = True
        for fail in r.get("failed_claims") or []:
            field = (fail.get("field") or "").strip()
            if field.startswith("sources") or field in corrected:
                continue
            source_only = False
            reasons.append(f"{field}：{fail.get('problem')}")
        if r.get("decision") != "approve" and not source_only:
            reasons.append("核查结论为搁置：" + (r.get("notes") or ""))
        elif r.get("decision") != "approve":
            applied.append("核查只因来源记录问题搁置：删去未核实的来源，其余字段已核实")
        verified = set(r.get("verified_source_ids") or []) | cited  # 更正所附的来源也算核实来源
        if opened is not None:
            unopened = [s.get("id") for s in event.get("sources") or []
                        if s.get("id") in verified - cited and _url_key(s.get("url") or "") not in seen]
            if unopened:
                verified -= set(unopened)
                applied.append(f"核查会话没有打开过的来源不算核实：{'、'.join(unopened)}")
        event["sources"] = [s for s in event.get("sources") or [] if s.get("id") in verified]
        if not event["sources"]:
            reasons.append("没有核实的来源")
        kept = {s["id"] for s in event["sources"]}
        for key in ("ended_roles", "new_roles"):
            for role in event.get(key) or []:
                ids = [i for i in role.get("source_ids") or [] if i in kept]
                if role.get("source_ids") and not ids:
                    reasons.append(f"{key} 的“{role.get('organization')}{role.get('role')}”只靠未核实的来源")
                role["source_ids"] = ids or sorted(kept)
        if event.get("kind") in ("leave", "death"):
            if not r.get("other_current_roles_confirmed"):
                event["other_current_roles"] = None
            if not r.get("new_appointment_confirmed"):
                event["new_appointment_checked"] = False
        status = "verified" if not reasons else "held"
        out.append({"event": event, "status": status, "reasons": reasons, "corrections": applied})
    return out


# ──────────────────────────────────────────────────────────────── 程序核查：取网页核对摘录


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) zuzhibu-dispatch/1.0"
Fetch = Callable[[str], "str | None"]


def fetch_page(url: str, timeout: float = 20.0) -> str | None:
    """取网页原文（最多 3MB）。取不到（403、超时、证书错误等）或不是网页文本（PDF 等）返回 None。"""
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = resp.headers.get_content_type()
            charset = resp.headers.get_content_charset() or "utf-8"
            raw = resp.read(3_000_000)
    except Exception:
        return None
    if raw[:5] == b"%PDF-" or ctype == "application/pdf":
        return None  # PDF 是压缩流，程序不解析，交核查会话
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


_FOLD = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "′": "'", "“": '"', "”": '"', "″": '"',
                       "–": "-", "—": "-", "‐": "-", "‑": "-", "−": "-", "­": None, "​": None})


def fold(text: str) -> str:
    """比对用的写法：还原字符实体，全角半角、弯引号、破折号、空白统一，去掉重音符号，小写。"""
    text = unicodedata.normalize("NFKD", html.unescape(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).translate(_FOLD)
    return re.sub(r"\s+", " ", text).strip().lower()


def page_parts(raw: str) -> tuple[str, str]:
    """(标题, 正文)，都已 fold。标题取 <title>、<h1> 和 og:title；正文是去掉标签后的全部文字。"""
    titles = re.findall(r"<title[^>]*>(.*?)</title>|<h1[^>]*>(.*?)</h1>", raw, re.S | re.I)
    titles = [a or b for a, b in titles]
    titles += re.findall(r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"']([^\"']*)", raw, re.I)
    strip = lambda s: re.sub(r"<[^>]+>", " ", s)
    return fold(" | ".join(strip(t) for t in titles)), fold(strip(raw))


def excerpt_in(excerpt: str, text: str) -> bool:
    """摘录逐字出现在网页正文（已 fold）里：摘录按省略号分段，各段依次出现；不到 12 个字符的摘录不算。"""
    parts = [p.strip(" .,;:\"'") for p in re.split(r"\[?\s*\.\.\.\s*\]?", fold(excerpt))]
    parts = [p for p in parts if p]
    if sum(len(p) for p in parts) < 12:
        return False
    pos = 0
    for part in parts:
        found = text.find(part, pos)
        if found < 0:
            return False
        pos = found + len(part)
    return True


def excerpt_check(url: str, excerpt: str, timeout: float = 20.0, fetch: Fetch | None = None) -> bool | None:
    """取网页原文，看摘录是否逐字出现。True 出现；False 没出现；None 取不到网页（403、超时、PDF 等）。"""
    if not url or not excerpt.strip():
        return None
    raw = fetch(url) if fetch else fetch_page(url, timeout)
    if raw is None:
        return None
    return excerpt_in(excerpt, page_parts(raw)[1])


_NAME_SUFFIX = {"jr", "sr", "ii", "iii", "iv", "v", "phd", "md", "esq"}
_STOP_WORDS = {"a", "an", "and", "the", "of", "to", "for", "in", "on", "at", "by", "with", "as", "from",
               "united", "states", "america", "american", "us", "rank"}
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
#: 类别要在来源里看得到的字样（摘录或网页标题里）。
_ARRIVED = (r"sworn|swore|appointed|appoints|appointment of|assumed|installed|investiture|took office|takes office|took over"
            r"|takes over|began|begins|joined|joins|named|elected|promoted|hired|welcom|now (?:a|an|the|serves|leads)|became"
            r"|becomes|serv(?:es|ed|ing) as|started|starts|received (?:his|her|their) (?:presidential )?commission")
_KIND_WORDS = {
    "nominate": r"nominat",
    "confirm": r"confirm",
    "appoint": _ARRIVED,
    "transfer": _ARRIVED,
    "acting": r"acting|interim|temporar|perform(?:s|ed|ing)? the duties",
    "leave": r"resign|depart|step(?:s|ped|ping)? (?:down|aside)|leav|left|retir|until|former|ended|ending|expire|exit"
             r"|transition|no longer|conclud|vacat|fired|dismiss|remov|succeed|replac",
    "death": r"died|dies|death|passed away|obituar|in memoriam",
    "announce": r"announc|will|to become|to serve|to lead|to join|named|appoint|select|intend|designat|tapped|picked"
                r"|chose|elected|succeed",
}
#: 任命、调任、代理：来源只有“将任”“提名”一类说法、没有到任说法的，不算到任。
_FUTURE = (r"intent to|intends to|(?<![a-z])will (?:be|become|join|serve|take|assume|succeed|lead)|to become|nominat"
           r"|designate(?![a-z])|for appointment")
_SURE = r"sworn|swore|appointed|appoints|investiture|took office|assumed|installed|took over|began|joined|became|now serves|serves as"


def kind_in(kind: str, text: str) -> bool:
    words = _KIND_WORDS.get(kind or "")
    if not words or not re.search(words, text):
        return False
    if kind in ("appoint", "transfer", "acting") and re.search(_FUTURE, text):
        return re.search(_SURE, text) is not None
    return True


def surname(name_en: str) -> str:
    """英文名的姓：最后一个词，去掉 Jr.、III、Ph.D. 一类后缀。"""
    words = fold(name_en).replace(",", " ").split()
    while words and words[-1].replace(".", "") in _NAME_SUFFIX:
        words.pop()
    return words[-1].strip(".") if words else ""


def _has(word: str, text: str, whole: bool = False) -> bool:
    """词出现在文字里：词首对齐（director 能对上 directors）；whole 时整词对齐（姓 He 对不上 help）。"""
    tail = r"(?![a-z0-9])" if whole else ""
    return bool(word) and re.search(r"(?<![a-z0-9])" + re.escape(word) + tail, text) is not None


def role_words(role: dict[str, Any]) -> list[str]:
    """职务的英文实词：取 role_en，空的取 organization_en；虚词、United States 一类不算。"""
    for text in (role.get("role_en") or "", role.get("organization_en") or ""):
        words = list(dict.fromkeys(w for w in re.findall(r"[a-z0-9]+", fold(text)) if len(w) > 1 and w not in _STOP_WORDS))
        if words:
            return words
    return []


def role_in(role: dict[str, Any], text: str) -> bool:
    """职务的英文实词至少三分之二出现在文字里。"""
    words = role_words(role)
    return bool(words) and 3 * sum(_has(w, text) for w in words) >= 2 * len(words)


def date_in(value: str, text: str) -> bool:
    """日期（YYYY-MM-DD、YYYY-MM、YYYY）以常见英文写法出现在文字里：September 14, 2026、Sept. 14、14 September 2026、
    2026-09-14、9/14/2026；只到月的认 September 2026 和这个月里的具体日子。"""
    m = re.fullmatch(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", (value or "").strip())
    if not m:
        return False
    year, month, day = m.group(1), m.group(2), m.group(3)
    if not month:
        return _has(year, text)
    name = _MONTHS[int(month) - 1]
    names = "(?<![a-z])(?:" + "|".join(dict.fromkeys((name, name[:4], name[:3]))) + ")"
    if not day:
        pattern = rf"{names}\.?,? (?:\d{{1,2}}(?:st|nd|rd|th)?,? )?{year}|{year}-{month}"
    else:
        d = int(day)
        pattern = (rf"{names}\.? {d}(?:st|nd|rd|th)?(?![0-9])|(?<![0-9]){d} {names}(?![a-z])"
                   rf"|{year}-{month}-{day}|(?<![0-9])0?{int(month)}/0?{d}/{year}")
    return re.search(pattern, text) is not None


def _dates(event: dict[str, Any]) -> list[tuple[str, str]]:
    found = [(k, event.get(k)) for k in ("announced_at", "effective_at")]
    for key in ("ended_roles", "new_roles"):
        for n, role in enumerate(event.get(key) or []):
            found += [(f"{key}[{n}].{k}", role.get(k)) for k in ("start", "end")]
    return [(k, v) for k, v in found if v]


def precheck(event: dict[str, Any], fetch: Fetch = fetch_page) -> dict[str, Any]:
    """程序核查一条动态（不调模型）。返回和 gate 一样的 {event, status, reasons, corrections}，另加 method、notes。

    来源要过三关才算核实：一级来源（zuzhibu/sources.py tier_of）、网页取得到、摘录逐字找到。
    再逐项看核实来源能不能撑住：人物的姓、各职务的英文实词、类别字样在摘录或网页标题里；每个日期在网页上。
    全部撑住才 verified；否则 held，reasons 写明哪项没据，交核查会话。放行时只留核实来源，职务来源改指撑住它的来源；
    离任、逝世类“没有其他现职”“没有新任命”两项程序核不了，照 gate 改成未查清（写回时不转七局）。
    """
    ev = copy.deepcopy(event)
    notes: list[str] = []
    checked: list[tuple[dict[str, Any], str, str]] = []  # (来源, 摘录加标题, 正文)
    for src in ev.get("sources") or []:
        sid, url = src.get("id"), src.get("url") or ""
        if sources.tier_of(url) != 1:
            notes.append(f"{sid}：不是一级来源")
            continue
        raw = fetch(url)
        if raw is None:
            notes.append(f"{sid}：网页取不到（403、超时、PDF 等）")
            continue
        title, body = page_parts(raw)
        if not excerpt_in(src.get("excerpt") or "", body):
            notes.append(f"{sid}：摘录在网页上没有逐字找到")
            continue
        checked.append((src, fold(src.get("excerpt") or "") + " | " + title, body))
    reasons: list[str] = []
    if not checked:
        reasons.append("没有核实的一级来源（" + "；".join(notes) + "）")
    else:
        name = surname(ev.get("name_en") or "")
        if not any(_has(name, ctx, whole=True) for _, ctx, _ in checked):
            reasons.append(f"人物：核实来源的摘录和网页标题里没有“{name or ev.get('name_en')}”")
        joined = " | ".join(ctx for _, ctx, _ in checked)  # 同一网页摘了几段的，合起来看
        for key in ("ended_roles", "new_roles"):
            for n, role in enumerate(ev.get(key) or []):
                if not role_in(role, joined):
                    reasons.append(f"{key}[{n}]：“{role.get('role_en') or role.get('organization_en')}”不在核实来源的摘录和网页标题里")
                cited = [src["id"] for src, _, _ in checked if src["id"] in (role.get("source_ids") or [])]
                role["source_ids"] = cited or [src["id"] for src, ctx, _ in checked if any(_has(w, ctx) for w in role_words(role))]
        if not ev.get("ended_roles") and not ev.get("new_roles"):
            reasons.append("没有原职务也没有新职务")
        if not any(kind_in(ev.get("kind") or "", ctx) for _, ctx, _ in checked):
            reasons.append(f"类别：核实来源的摘录和网页标题里没有表示“{personnel.KIND_LABEL.get(ev.get('kind'), ev.get('kind'))}”的字样")
        for field, value in _dates(ev):
            if not any(date_in(value, body) for _, _, body in checked):
                reasons.append(f"{field}：{value} 不在核实来源的网页上")
    kept = {src["id"] for src, _, _ in checked}
    ev["sources"] = [s for s in ev.get("sources") or [] if s.get("id") in kept]
    if ev.get("kind") in ("leave", "death"):
        if ev.get("other_current_roles") is not None or ev.get("new_appointment_checked"):
            notes.append("离任后有无其他现职、新任命，程序核不了，按未查清写回（不转七局）")
        ev["other_current_roles"] = None
        ev["new_appointment_checked"] = False
    return {"event": ev, "status": "held" if reasons else "verified", "reasons": reasons, "corrections": [],
            "method": "excerpt", "notes": [f"已核：{'、'.join(sorted(kept))}"] * bool(kept) + notes}
