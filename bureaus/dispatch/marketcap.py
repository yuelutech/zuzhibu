"""商界名单的市值：查名单里上市公司的收盘市值，写 data/rosters/market-caps.json；名单页据此把企业负责人分进“市值万亿美元以上”
“市值万亿美元以下”两层、层内按市值从大到小排（zuzhibu/rosters.py），市值文件变了名单页跟着变。未上市公司不在市值文件里。

    python -m bureaus.dispatch.marketcap --data <本机数据目录> --backend api            # 用使用者自己的 AI 接口，查最近一个交易日
    python -m bureaus.dispatch.marketcap --data data --as-of 2026-10-01               # 用本机登录的 Codex，查这一天的收盘市值
    python -m bureaus.dispatch.marketcap --data data --dry-run                        # 只写出提示词

“立即更新”跑完一局流水线以后调一次 refresh()（结果不影响更新本身是否成功）。一次会话查齐全部上市公司在同一个交易日的收盘市值，
每家都要有来源网址和含这个数的原文摘录；写之前再打开各家来源页，看摘录里的数在不在页上。有一家对不上（缺了、多了、交易日不一致、
数额离谱、和上次比变动过大、来源页上没有这个数）就整份不写，留着上次的；来源页打不开的不算对不上。
查哪几家（人物、公司、代码）看市值文件本身：名单换了企业，由维护者改市值文件里的这几行。一人兼几家上市公司的，每家一行
（如马斯克：特斯拉 TSLA、太空探索技术公司 SPCX），名单页按其中市值最大的一家算。只用标准库。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

from shared.backends import ai_api
from zuzhibu.store import REPO, data_dir, read_json, write_json

from . import research

CAPS_FILE = "market-caps.json"
MIN_USD, MAX_USD = 5e9, 5e13      # 收得进名单的美国上市公司，市值在这个范围以外就是查错了
MAX_RATIO = 2.5                   # 和上次比涨跌超过这个倍数，当作查错
MAX_LAG_DAYS = 10                 # 交易日最多比要查的日子早这么多天（遇上长假也够）

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "date": {"type": "string"},
        "companies": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"ticker": {"type": "string"}, "usd": {"type": "number"}, "url": {"type": "string"},
                           "publisher": {"type": "string"}, "excerpt": {"type": "string"}},
            "required": ["ticker", "usd", "url", "publisher", "excerpt"]}},
        "notes": {"type": "string"},
    },
    "required": ["date", "companies", "notes"],
}

Call = Callable[[str, dict, Any, Path, str], tuple[Any, dict, str | None]]


def caps_path(root: str | Path | None = None) -> Path:
    return data_dir(root) / "rosters" / CAPS_FILE


def load(path: Path) -> dict[str, Any]:
    try:
        data = read_json(path)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def prompt(entries: list[dict[str, Any]], *, as_of: str, today: str) -> str:
    lines = "\n".join(f"- {e['ticker']}：{e.get('company') or e['ticker']}" for e in entries)
    return f"""今天是 {today}。请联网查下列美国上市公司在 {as_of} 当天（不是交易日就取此前最近一个交易日）的收盘市值。

要求：
1. 所有公司用同一个交易日，写在 date（YYYY-MM-DD）。
2. 市值按公司全部已发行股份计，有几类股票的合在一起；单位美元，写完整数字（例如 4.51 万亿美元写 4510000000000）。
3. 每家给出能看到这个数的网页（交易所、公司投资者关系页或常用金融数据网站）：url、publisher（网站名），
   excerpt 照抄网页上含这个数的一小段原文（不超过 200 字）。查不到的不要估算，在 notes 里说明。
4. 只回 JSON，按 schema 填写，公司代码照下面写。

{lines}
"""


def check(answer: Any, entries: list[dict[str, Any]], *, as_of: str, previous: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """会话的回复 → (写进文件的各行, 不合格的地方)。有不合格就整份不写。previous 按代码（大写）给上次的那一条，
    一人兼几家上市公司的各家分开比，不拿这一家的新数去比另一家的旧数。"""
    problems: list[str] = []
    if not isinstance(answer, dict):
        return [], ["回复不是 JSON 对象"]
    try:
        day = dt.date.fromisoformat(str(answer.get("date") or ""))
    except ValueError:
        return [], [f"交易日写法不对：{answer.get('date')!r}"]
    target = dt.date.fromisoformat(as_of)
    if day > target or (target - day).days > MAX_LAG_DAYS:
        problems.append(f"交易日 {day} 不在 {as_of} 之前 {MAX_LAG_DAYS} 天以内")
    got: dict[str, dict[str, Any]] = {}
    for row in answer.get("companies") or []:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        if ticker in got:
            problems.append(f"{ticker} 出现两次")
        got[ticker] = row
    wanted = {e["ticker"].upper(): e for e in entries}
    for ticker in sorted(set(got) - set(wanted)):
        problems.append(f"多出名单外的 {ticker}")
    out = []
    for e in entries:
        ticker = e["ticker"].upper()
        row = got.get(ticker)
        if row is None:
            problems.append(f"缺 {ticker}")
            continue
        usd = row.get("usd")
        if not isinstance(usd, (int, float)) or isinstance(usd, bool) or not MIN_USD <= usd <= MAX_USD:
            problems.append(f"{ticker} 市值 {usd!r} 不像真的")
            continue
        url = str(row.get("url") or "").strip()
        if not url.startswith("https://") or not str(row.get("excerpt") or "").strip():
            problems.append(f"{ticker} 没有来源网址或原文摘录")
            continue
        old = (previous.get(ticker) or {}).get("usd")
        if isinstance(old, (int, float)) and old > 0 and not 1 / MAX_RATIO <= usd / old <= MAX_RATIO:
            problems.append(f"{ticker} 市值从 {old:.4g} 变到 {usd:.4g}，变动过大")
            continue
        out.append({"person": e["person"], "company": e.get("company") or "", "ticker": e["ticker"], "usd": int(round(usd)),
                    "date": day.isoformat(), "url": url, "publisher": str(row.get("publisher") or "").strip(),
                    "excerpt": str(row.get("excerpt") or "").strip()})
    return out, problems


def source_ok(row: dict[str, Any], fetch: Callable[[str], str | None] = research.fetch_page) -> bool | None:
    """来源页上找得到摘录里的数（如 5,563.73）：True 找到；False 页面打开了、没有这个数；None 页面打不开。"""
    raw = fetch(row["url"])
    if raw is None:
        return None
    text = research.page_parts(raw)[1]
    nums = re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+", row.get("excerpt") or "")
    return bool(nums) and all(research.fold(n) in text for n in nums)


def refresh(root: str | Path | None, config: Any, runs: Path, *, as_of: str | None = None, today: str | None = None,
            call: Call = research.call, fetch: Callable[[str], str | None] = research.fetch_page) -> dict[str, Any]:
    """查一次、核一遍，都对得上才写市值文件。返回 {ok, date, written, problems, unreachable}；没写时 problems 说明原因。"""
    today = today or dt.date.today().isoformat()
    as_of = as_of or today
    path = caps_path(root)
    current = load(path)
    entries = [c for c in current.get("companies") or [] if isinstance(c, dict) and c.get("person") and c.get("ticker")]
    if not entries:
        return {"ok": False, "date": None, "written": False, "problems": [f"{path} 里没有要查的公司"]}
    answer, _stats, error = call(prompt(entries, as_of=as_of, today=today), SCHEMA, config, runs, "marketcap")
    if error or answer is None:
        return {"ok": False, "date": None, "written": False, "problems": [error or "会话没有回复"]}
    rows, problems = check(answer, entries, as_of=as_of, previous={c["ticker"].upper(): c for c in entries})
    unreachable = []
    if not problems:
        for row in rows:
            seen = source_ok(row, fetch)
            if seen is None:
                unreachable.append(row["ticker"])
            elif not seen:
                problems.append(f"{row['ticker']} 的来源页上没有摘录里的数")
    if problems:
        return {"ok": False, "date": answer.get("date") if isinstance(answer, dict) else None, "written": False,
                "problems": problems, "unreachable": unreachable}
    write_json(path, {**{k: v for k, v in current.items() if k not in ("as_of", "companies")},
                      "as_of": rows[0]["date"], "companies": rows})
    return {"ok": True, "date": rows[0]["date"], "written": True, "problems": [], "unreachable": unreachable}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="查商界名单里上市公司的收盘市值，写 data/rosters/market-caps.json")
    ap.add_argument("--data", help="数据目录，默认仓库的 data/")
    ap.add_argument("--as-of", help="查哪一天的收盘市值（YYYY-MM-DD），默认今天（取最近一个交易日）")
    ap.add_argument("--backend", choices=("codex", "api"), default="codex")
    ap.add_argument("--settings", help="--backend api 时读哪个设置文件，默认“我的”页保存的位置")
    ap.add_argument("--codex"), ap.add_argument("--model", default=research.DEFAULT_MODEL)
    ap.add_argument("--effort", default=research.DEFAULT_EFFORT)
    ap.add_argument("--timeout", type=float, default=20, help="会话最长几分钟")
    ap.add_argument("--runs", default=str(REPO / "output" / "marketcap"), help="过程记录目录")
    ap.add_argument("--dry-run", action="store_true", help="只写出提示词，不开会话")
    args = ap.parse_args(argv)
    today = dt.date.today().isoformat()
    as_of = args.as_of or today
    if args.dry_run:
        entries = [c for c in load(caps_path(args.data)).get("companies") or [] if isinstance(c, dict) and c.get("ticker")]
        print(prompt(entries, as_of=as_of, today=today))
        return 0
    if args.backend == "api":
        from zuzhibu.server import settings_path  # 设置文件的位置与设置页保存的相同

        path = Path(args.settings) if args.settings else settings_path()
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
        config = ai_api.config_from_settings(settings.get("api") if isinstance(settings, dict) else None, deadline=args.timeout * 60)
    else:
        from .run import resolve_codex

        config = research.web_config(resolve_codex(args.codex), args.model, args.effort, args.timeout * 60)
    runs = Path(args.runs) / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    runs.mkdir(parents=True, exist_ok=True)
    result = refresh(args.data, config, runs, as_of=as_of, today=today)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
