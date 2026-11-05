# -*- coding: utf-8 -*-
"""新闻稿 lint：照 shared/skills/soe-newswire/SKILL.md 第 9 节参数卡（N01–N35）检查成稿。

流水线（shared/backends/lint_hook）调用 ``lint_text(text, genre=, source=, document=, extra=)``：
document 是 {title, subtitle, paragraphs}（第一段是电头加导语），source 是英文原文，extra 里的 agency 是配置的虚构社名。
返回问题列表，每条 {level, rule, message, where}。

N10、N11、N13 译名写法由流水线的译名表检查做（shared/backends/glossary.py）；N16 引语保真、N23 时态要对照原文语义，
留给人工；N35 限定词保留用启发式比对 about、nearly、up to 等，报 P1。阈值是参数卡的经验值，待私有金标校准。

命令行::

    python -m tools.lint.newswire_lint 输出目录/原文.json --source 原文.txt [--agency 政见社] [--format json]

退出码：0 没有 P0；1 有 P0；2 读不了文件。
"""

from __future__ import annotations

import re
import sys
from typing import Any, Sequence

if __package__ in (None, ""):  # 直接按文件跑时补上仓库根目录
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.lint.genre_common import (  # noqa: E402
    INLINE_FORMAT_RE,
    MARKDOWN_LINE_RE,
    QUOTE_RE,
    approx_issues,
    cjk_len,
    cli,
    clip,
    draft_from,
    english_issues,
    issue,
    number_issues,
    sentences,
    strip_quotes,
    zh_numbers,
)

__all__ = ["lint_text", "main", "DEFAULT_AGENCY", "REAL_MEDIA"]

GENRE = "newswire"
DEFAULT_AGENCY = "政见社"
REAL_MEDIA = ("新华社", "中新社", "中国新闻社", "人民日报", "央视", "中央广播电视总台", "路透社", "美联社", "法新社", "彭博社")

DATELINE_RE = re.compile(r"^[一-鿿A-Za-z·]{4,22}\d{1,2}月\d{1,2}日电")
TIME_RE = re.compile(r"\d{1,2}日|\d{1,2}月\d{1,2}日|\d{4}年\d{1,2}月")
VAGUE_TIME = ("今天", "昨天", "今日", "昨日", "近日", "日前", "最近")
HONORIFIC_RE = re.compile(r"先生|女士|阁下")
SPEECH_VERB_RE = re.compile(r"说|表示|称|指出|强调|宣布|写道|认为|提到|透露")
ATTRIB_RE = re.compile(r"据[^，。；]{1,25}?(?:报道|透露|称)")
HEARSAY_RE = re.compile(r"据悉|据了解")
EN_UNIT_RE = re.compile(r"(?<![A-Za-z])(?:billion|million|trillion|bn)(?![A-Za-z])|\d\s?[KMB](?![A-Za-z])", re.I)
THOUSANDS_RE = re.compile(r"\d{1,3}(?:,\d{3})+")
FULLWIDTH_DIGIT_RE = re.compile(r"[０-９]")
RMB_RE = re.compile(r"人民币|\d(?:\.\d+)?\s*(?:万亿|亿|万)?元")
FIRST_PERSON_RE = re.compile(r"我们|我国|我(?![们国])")
SELF_REF_RE = re.compile(r"本报(?!告|道)|本台|笔者|记者了解到|记者获悉|本报讯")
FORBIDDEN_REFS = ("党中央", "中央精神", "总书记", "省委", "市委", "学习贯彻", "重要指示")
STATE_COUNCIL_RE = re.compile(r"(?<!美国)(?<!日本)(?<!韩国)国务院")
FAKE_ORG_RE = re.compile(r"(?<!民主)(?<!共和)(?<!工)党委|党组(?!织)|党支部|纪委")
PRAISE = ("意义重大", "里程碑", "标志着", "史上最", "重磅", "震惊", "刷屏")
PLACEHOLDER_RE = re.compile(r"待补|待核|待定|TODO|TBD|××|XX|［|］|\[|\]|未知(?=[，。；、）]|$)")
AI_MARK_RE = re.compile(r"AI\s?生成|人工智能生成|译者注|以下为|根据原文|本文由")
STYLE_WORDS = ("召开", "部署", "推进", "落实", "统筹", "推动", "深化", "强化", "加快", "确保", "全面", "扎实", "深入", "高质量", "提质增效")


def _subtitle_test(block: str) -> bool:
    return not DATELINE_RE.match(block)


def lint_text(
    text: str,
    *,
    genre: str | None = None,
    source: str | None = None,
    document: dict[str, Any] | None = None,
    extra: dict[str, str] | None = None,
    glossary_dir: str | None = None,
) -> list[dict[str, str]]:
    draft = draft_from(text, document=document, source=source, extra=extra, subtitle_test=_subtitle_test)
    paras = draft.paragraphs
    src = draft.source
    agency = (draft.extra.get("agency") or DEFAULT_AGENCY).strip()
    out: list[dict[str, str]] = []
    first = paras[0] if paras else ""

    # N01–N03 电头
    if not re.match(rf"^{re.escape(agency)}[一-鿿·]{{2,10}}\d{{1,2}}月\d{{1,2}}日电", first):
        out.append(issue("P0", "N01", f"第一段要以“{agency}＋城市＋M月D日电”开头", clip(first)))
    for media in REAL_MEDIA:
        if first.startswith(media):
            out.append(issue("P0", "N02", f"电头社名不得用真实媒体名：「{media}」"))
    end = first.find("电", 0, 30)
    dateline = first[: end + 1] if end >= 0 else ""
    if re.search(r"\d{4}年", dateline):
        out.append(issue("P1", "N03", f"电头不写年份：「{dateline}」"))
    lead = first[end + 1:].lstrip() if dateline and re.search(r"\d日电$", dateline) else first

    # N04 标题形态
    title_len = cjk_len(draft.title)
    if title_len > 25:
        out.append(issue("P1", "N04", f"主标题 {title_len} 字，不超过 25 字：「{draft.title}」"))
    if re.search(r"[。；！？!?…]$|[！？!?…]", draft.title):
        out.append(issue("P1", "N04", f"主标题不用句末标点、问号、叹号、省略号：「{draft.title}」"))
    if draft.subtitle and cjk_len(draft.subtitle) > 30:
        out.append(issue("P1", "N04", f"副标题不超过 30 字：「{draft.subtitle}」"))

    # N05 标题数字在正文出现
    body_values = [n.value for p in paras for n in zh_numbers(p)]
    for n in zh_numbers(draft.title + " " + draft.subtitle):
        if not any(abs(n.value - v) <= 1e-6 * max(1, abs(v)) for v in body_values):
            out.append(issue("P0", "N05", f"标题里的数字「{n.text}」正文没有"))

    # N06–N09 导语
    lead_len = cjk_len(lead)
    if lead_len > 120:
        out.append(issue("P1", "N06", f"导语 {lead_len} 字，不超过 120 字", clip(lead)))
    if len(sentences(lead)) > 2:
        out.append(issue("P1", "N06", f"导语 {len(sentences(lead))} 句，1 至 2 句", clip(lead)))
    if not TIME_RE.search(lead):
        out.append(issue("P1", "N07", "导语要有时间（×日或×月×日）", clip(lead)))
    if "“" in lead:
        out.append(issue("P1", "N08", "导语不用直接引语", clip(lead)))
    for w in VAGUE_TIME:
        if w in lead:
            out.append(issue("P1", "N09", f"导语不写模糊时间「{w}」"))

    body = "\n".join(paras)
    unquoted = strip_quotes(body)
    # N12 敬称
    for hm in HONORIFIC_RE.finditer(unquoted):
        out.append(issue("P1", "N12", f"不用敬称「{hm.group()}」"))
    # N14、N15 直接引语
    quotes = [(q, p) for p in paras for q in QUOTE_RE.findall(p) if len(q) >= 10]
    if len(quotes) > 3:
        out.append(issue("P2", "N14", f"直接引语 {len(quotes)} 处，不超过 3 处"))
    for q, p in quotes:
        if len(q) > 80:
            out.append(issue("P2", "N14", f"直接引语 {len(q)} 字，不超过 80 字", clip(q)))
        if not SPEECH_VERB_RE.search(QUOTE_RE.sub("", p)):
            out.append(issue("P1", "N15", "直接引语所在句要写明说话人和“说、表示、称”一类引述动词", clip(p)))
    # N17 消息来源
    attrib = ATTRIB_RE.findall(body)
    if len(attrib) > 2:
        out.append(issue("P2", "N17", f"“据……报道/透露/称”{len(attrib)} 处，不超过 2 处"))
    if len(HEARSAY_RE.findall(body)) > 1:
        out.append(issue("P2", "N17", "“据悉、据了解”不超过 1 处"))
    # N18 段落
    if not 3 <= len(paras) <= 12:
        out.append(issue("P2", "N18", f"正文 {len(paras)} 段，3 至 12 段"))
    for p in paras:
        n, s = cjk_len(p), len(sentences(p))
        if n > 200 or s > 3:
            out.append(issue("P2", "N18", f"段落 {n} 字、{s} 句：每段不超过 200 字、3 句", clip(p)))
    # N19 总字数
    total = cjk_len(draft.title) + cjk_len(draft.subtitle) + sum(cjk_len(p) for p in paras)
    if not 300 <= total <= 1500:
        out.append(issue("P1", "N19", f"全文 {total} 字，参数卡要求 300 至 1500 字"))
    # N20 数字保真（编造 P0，漏写 P1）；N35 限定词保留（启发式，P1）
    out += number_issues(paras, src, rule="N20", title=draft.title)
    out += approx_issues(paras, src, rule="N35")
    # N21 数字格式
    full = draft.all_text
    if EN_UNIT_RE.search(full):
        out.append(issue("P0", "N21", f"不出现 billion、million、K、M、B 一类英文计量词：「{EN_UNIT_RE.search(full).group()}」"))
    for tm in THOUSANDS_RE.finditer(full):
        out.append(issue("P1", "N21", f"数字不用千分位逗号：「{tm.group()}」"))
    if FULLWIDTH_DIGIT_RE.search(full):
        out.append(issue("P1", "N21", "数字用半角阿拉伯数字"))
    # N22 货币
    if RMB_RE.search(full) and not re.search(r"yuan|RMB|renminbi", src, re.I):
        out.append(issue("P1", "N22", f"原文没有人民币，成稿不写“人民币、元”：「{RMB_RE.search(full).group()}」"))
    # N24 第一人称
    for fm in FIRST_PERSON_RE.finditer(strip_quotes(full)):
        out.append(issue("P1", "N24", f"引语外不用第一人称「{fm.group()}」"))
        break
    # N25 记者自指
    for sm in SELF_REF_RE.finditer(full):
        out.append(issue("P0", "N25", f"不写记者自指「{sm.group()}」"))
    # N26 禁用指称、虚构组织
    for w in FORBIDDEN_REFS:
        if w in full:
            out.append(issue("P0", "N26", f"不得假托现实党政机关、领导人、文件：「{w}」"))
    if STATE_COUNCIL_RE.search(full):
        out.append(issue("P0", "N26", "「国务院」：外国政府部门写全“美国国务院”，不假托现实机关"))
    for om in FAKE_ORG_RE.finditer(full):
        out.append(issue("P0", "N26", f"不得虚构党委、党组、党支部、纪委：「{om.group()}」"))
    # N27 评价词
    for w in PRAISE:
        if w in strip_quotes(full):
            out.append(issue("P1", "N27", f"不加原文没有的定性「{w}」"))
    # N28 标点
    if "！" in full or "!" in full:
        out.append(issue("P1", "N28", "新闻稿不用叹号"))
    if "？" in strip_quotes(body) or "?" in strip_quotes(body):
        out.append(issue("P1", "N28", "新闻稿不用问号"))
    if full.count("……") > 1:
        out.append(issue("P2", "N28", "省略号不超过 1 处"))
    # N29 英文夹杂
    out += english_issues([draft.title, draft.subtitle, *paras], src, rule="N29", glossary_dir=glossary_dir)
    # N30 占位符
    for pm in PLACEHOLDER_RE.finditer(full):
        out.append(issue("P0", "N30", f"占位符或缺项字样「{pm.group()}」"))
    # N31 格式与链接
    for p in [draft.title, draft.subtitle, *paras]:
        if p and (MARKDOWN_LINE_RE.match(p) or INLINE_FORMAT_RE.search(p)):
            out.append(issue("P1", "N31", "正文不出现 Markdown、HTML 符号和链接", clip(p)))
    # N32 AI 标注与译注
    for am in AI_MARK_RE.finditer(full):
        out.append(issue("P1", "N32", f"正文不写 AI 标注、译注、过程说明：「{am.group()}」"))
    # N33 句长
    sents = [s for p in paras for s in sentences(p)]
    if sents:
        lengths = [cjk_len(s) for s in sents]
        avg = sum(lengths) / len(lengths)
        if avg > 40:
            out.append(issue("P2", "N33", f"平均句长 {avg:.0f} 字，不超过 40 字"))
        if max(lengths) > 70:
            s = sents[lengths.index(max(lengths))]
            out.append(issue("P2", "N33", f"最长一句 {max(lengths)} 字，不超过 70 字", clip(s)))
    # N34 风格词密度
    if total:
        density = sum(full.count(w) for w in STYLE_WORDS) * 1000 / total
        if not 5 <= density <= 20:
            out.append(issue("P2", "N34", f"风格词每千字 {density:.1f} 个，参数卡要求 5 至 20 个"))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    return cli(
        argv,
        prog="python -m tools.lint.newswire_lint",
        description="新闻稿 lint（soe-newswire 参数卡 N01–N35）。",
        lint_text=lint_text,
        extra_args=lambda p: p.add_argument("--agency", help=f"电头社名，默认 {DEFAULT_AGENCY}"),
        extra_from=lambda a: {"agency": a.agency or ""},
    )


if __name__ == "__main__":
    sys.exit(main())
