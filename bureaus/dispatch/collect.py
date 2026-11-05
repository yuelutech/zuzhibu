"""一局线索采集：只用免费公开来源，程序抓取，不调模型（docs/PRODUCT.md 第七节）。

线索只是“可能有变动”的提示，不直接写回；写回前一律经过研究和核查两次会话。

- congress：unitedstates/congress-legislators 公开数据（legislators-current.json、legislators-historical.json），
  与人物库四局的 bioguide 编号对比：库里在任、数据里已卸任的 → 离任线索；数据里新就任、库里没有的 → 就任线索。
- roster：人物库里名册记录（还没有一局动态的）任职起始日期落在窗口内的 → 就任线索（顺带查前任去向）。

只用标准库。
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

from zuzhibu import bureaus
from zuzhibu.store import Store

LEGISLATORS = "https://unitedstates.github.io/congress-legislators/{name}.json"
USER_AGENT = "zuzhibu-dispatch/1.0 (+https://github.com/yuelutech/zuzhibu)"
STATE_ZH = {
    "AL": "阿拉巴马州", "AK": "阿拉斯加州", "AZ": "亚利桑那州", "AR": "阿肯色州", "CA": "加利福尼亚州", "CO": "科罗拉多州",
    "CT": "康涅狄格州", "DE": "特拉华州", "FL": "佛罗里达州", "GA": "佐治亚州", "HI": "夏威夷州", "ID": "爱达荷州",
    "IL": "伊利诺伊州", "IN": "印第安纳州", "IA": "爱荷华州", "KS": "堪萨斯州", "KY": "肯塔基州", "LA": "路易斯安那州",
    "ME": "缅因州", "MD": "马里兰州", "MA": "马萨诸塞州", "MI": "密歇根州", "MN": "明尼苏达州", "MS": "密西西比州",
    "MO": "密苏里州", "MT": "蒙大拿州", "NE": "内布拉斯加州", "NV": "内华达州", "NH": "新罕布什尔州", "NJ": "新泽西州",
    "NM": "新墨西哥州", "NY": "纽约州", "NC": "北卡罗来纳州", "ND": "北达科他州", "OH": "俄亥俄州", "OK": "俄克拉何马州",
    "OR": "俄勒冈州", "PA": "宾夕法尼亚州", "RI": "罗得岛州", "SC": "南卡罗来纳州", "SD": "南达科他州", "TN": "田纳西州",
    "TX": "得克萨斯州", "UT": "犹他州", "VT": "佛蒙特州", "VA": "弗吉尼亚州", "WA": "华盛顿州", "WV": "西弗吉尼亚州",
    "WI": "威斯康星州", "WY": "怀俄明州", "DC": "哥伦比亚特区", "PR": "波多黎各", "GU": "关岛", "VI": "美属维尔京群岛",
    "AS": "美属萨摩亚", "MP": "北马里亚纳群岛",
}


def fetch_json(url: str, cache: Path | None = None, timeout: float = 60.0) -> Any:
    """取 JSON；给了 cache 就把原文存一份（过程记录，不进仓库）。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(raw)
    return json.loads(raw.decode("utf-8"))


def seat_zh(term: dict[str, Any]) -> str:
    """数据集任期 → 简历写法：美国联邦参议员（南卡罗来纳州）、美国联邦众议员（佐治亚州第13选区）。"""
    state = STATE_ZH.get(term.get("state", ""), term.get("state", ""))
    if term.get("type") == "sen":
        return f"美国联邦参议员（{state}）"
    district = term.get("district")
    if district in (None, 0, "0"):
        return f"美国联邦众议员（{state}）"
    return f"美国联邦众议员（{state}第{district}选区）"


def seat_en(term: dict[str, Any]) -> str:
    if term.get("type") == "sen":
        return f"U.S. Senator from {term.get('state')}"
    return f"U.S. Representative, {term.get('state')}-{term.get('district')}"


def _full_name(member: dict[str, Any]) -> str:
    name = member.get("name") or {}
    return name.get("official_full") or " ".join(p for p in (name.get("first"), name.get("last"), name.get("suffix")) if p)


def congress_candidates(store: Store, current: list[dict[str, Any]], historical: list[dict[str, Any]],
                        since: str, until: str) -> list[dict[str, Any]]:
    """对比数据集与人物库四局，返回离任、就任线索（只取窗口内的日期；库里在任但数据已卸任的不论日期都报）。"""
    ours = {}
    for rec in store.people():
        bid = (rec.get("ids") or {}).get("bioguide")
        if bid:
            ours[bid.upper()] = rec
    cur = {m["id"]["bioguide"].upper(): m for m in current}
    hist = {m["id"]["bioguide"].upper(): m for m in historical}
    out = []
    for bid, rec in ours.items():
        if bid in cur or rec.get("bureau") != bureaus.CONGRESS:
            continue
        term = (hist.get(bid) or {}).get("terms", [{}])[-1]
        out.append({
            "id": f"congress-left-{bid.lower()}", "source": "congress-legislators", "kind_hint": "leave",
            "person_id": rec["id"], "name_en": rec.get("name_en"), "name_zh": rec.get("name_zh"),
            "role": {"organization": seat_zh(term) if term else "", "role": "", "organization_en": seat_en(term) if term else ""},
            "date": term.get("end"),
            "lead": f"congress-legislators 数据显示此人已不在任，最后一个任期止于 {term.get('end')}（{seat_en(term)}）。查明离任原因和日期，"
                    f"是否另有现职、是否已公布新任命。",
        })
    for bid, member in cur.items():
        if bid in ours:
            continue
        term = member["terms"][-1]
        start = term.get("start", "")
        if not (since <= start <= until):
            continue
        how = {"appointment": "获任命", "special-election": "特别选举当选"}.get(term.get("how") or "", "就任")
        out.append({
            "id": f"congress-new-{bid.lower()}", "source": "congress-legislators", "kind_hint": "appoint",
            "person_id": None, "name_en": _full_name(member), "name_zh": None, "ids": {"bioguide": bid},
            "role": {"organization": seat_zh(term), "role": "", "organization_en": seat_en(term)},
            "date": start,
            "lead": f"congress-legislators 数据显示此人 {start} 起任 {seat_en(term)}（{how}，{term.get('party')}）。"
                    f"核实就任日期和方式，查明前任是谁、前任去向。",
        })
    return out


def roster_candidates(store: Store, since: str, until: str) -> list[dict[str, Any]]:
    """名册记录里任职起始日期落在窗口内的。"""
    out = []
    for rec in store.people():
        if rec.get("tier") != "seed" or rec.get("events"):
            continue  # 只看名册导入的记录；已有一局动态的，职务就是核实过的，不再当线索
        for role in (rec.get("resume") or {}).get("current_roles") or []:
            start = ((role.get("start") or {}).get("value")) or ""
            if start and since <= start[:10] <= until:
                roster = (rec.get("roster_roles") or [{}])[0]
                out.append({
                    "id": f"roster-{rec['id']}", "source": "名册", "kind_hint": "appoint", "person_id": rec["id"],
                    "name_en": rec.get("name_en"), "name_zh": rec.get("name_zh"),
                    "role": {"organization": role.get("organization"), "role": role.get("role"),
                             "organization_en": roster.get("organization_en"), "role_en": roster.get("role_en")},
                    "date": start,
                    "lead": f"名册记此人 {start} 起任 {roster.get('organization_en')} {roster.get('role_en')}。"
                            f"核实任命和生效日期、原任职务；查明前任是谁、前任去向（另有现职、新任命，还是已无职务）。",
                })
                break
    return out


def planned_candidates(store: Store, until: str) -> list[dict[str, Any]]:
    """拟任、拟卸任到期的（生效日不晚于窗口末日）：交研究会话复核是否已到任，推迟或取消的照实写。"""
    out = []
    for rec in store.people():
        for p in rec.get("planned_roles") or []:
            if (p.get("date") or "9999")[:10] > until:
                continue
            action = p.get("action") or "任"
            out.append({
                "id": f"planned-{rec['id']}-{p.get('date')}", "source": "拟任到期", "kind_hint": "appoint" if action == "任" else "leave",
                "person_id": rec["id"], "name_en": rec.get("name_en"), "name_zh": rec.get("name_zh"),
                "role": {"organization": p.get("organization"), "role": p.get("role"),
                         "organization_en": p.get("organization_en"), "role_en": p.get("role_en")},
                "date": p.get("date"),
                "lead": f"原定 {p.get('date')} {action}{p.get('organization')}{p.get('role')}（{p.get('event')}），已到期："
                        f"核实是否已{'到任' if action == '任' else '卸任'}及实际日期；推迟或取消的照实写。",
            })
    return out


def collect(store: Store, since: str, until: str, cache_dir: Path | None = None) -> dict[str, Any]:
    """全部线索。网络取不到的来源记进 errors，不中断。"""
    leads: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        current = fetch_json(LEGISLATORS.format(name="legislators-current"), cache_dir / "legislators-current.json" if cache_dir else None)
        historical = fetch_json(LEGISLATORS.format(name="legislators-historical"), cache_dir / "legislators-historical.json" if cache_dir else None,
                                timeout=180)
        leads += congress_candidates(store, current, historical, since, until)
    except Exception as exc:  # 网络或数据格式问题：记下，继续
        errors.append(f"congress-legislators：{exc}")
    leads += roster_candidates(store, since, until)
    leads += planned_candidates(store, until)
    return {"since": since, "until": until, "leads": leads, "errors": errors}
