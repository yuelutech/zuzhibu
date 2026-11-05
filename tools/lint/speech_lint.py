# -*- coding: utf-8 -*-
"""讲话稿 lint：照 shared/skills/cadre-speech/SKILL.md 第 7 节参数卡（S01–S30）检查成稿。

流水线（shared/backends/lint_hook）调用 ``lint_text(text, genre=, source=, document=, extra=)``：
document 是 {title, subtitle, paragraphs}，source 是英文原文。返回问题列表，每条 {level, rule, message, where}。

程序能判的条目都在这里；S11 专名保真、S12 译名一致由流水线的译名表检查做（shared/backends/glossary.py），
S15 四字格密度没有词表、S29 外来引语要对照原文语义，留给人工。阈值是参数卡的经验值，待私有金标校准。

命令行::

    python -m tools.lint.speech_lint 输出目录/原文.json --source 原文.txt [--format json]

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
    HAN_NUM,
    HEADING1_RE,
    INLINE_FORMAT_RE,
    MARKDOWN_LINE_RE,
    approx_issues,
    cjk_len,
    cli,
    clip,
    draft_from,
    english_issues,
    issue,
    number_issues,
    parallel_runs,
    sentences,
)

__all__ = ["lint_text", "main"]

GENRE = "speech"

DATE_LINE_RE = re.compile(r"^（\d{4}年\d{1,2}月\d{1,2}日）$")
DATE_LIKE_RE = re.compile(r"^[（(]?\s*\d{4}\s*年.*日\s*[）)]?$")
SALUTATION_RE = re.compile(r"^[一-鿿、，]{2,30}：$")
TRANSITION_RE = re.compile(rf"(几|[两{HAN_NUM}\d]+)(?:点|个方面|方面|个问题|个字|条)")
THANKS_RE = re.compile(r"^谢谢(?:大家|各位|你们)?[！!。]?$")
CALL_RE = re.compile(r"让我们|努力|奋斗|贡献")
YAO_RE = re.compile(r"要|必须|坚持|着力|加快|确保|切实|深入")

FILLERS = ("我觉得", "你知道", "嗯", "呃", "好吧", "对吧", "然后呢", "哈哈", "OK", "嘿", "太棒了", "疯狂", "不可思议")
#: S18：现实党政机关、领导人、文件作指示来源。“美国国务院”等外国机构不算。
FORBIDDEN_REFS = ("党中央", "中央精神", "总书记", "省委", "市委", "部党组", "讲话精神", "重要指示", "上级要求", "学习贯彻")
STATE_COUNCIL_RE = re.compile(r"(?<!美国)(?<!日本)(?<!韩国)国务院")
#: S19：给讲话人单位虚构的党组织、纪检和组织部门。
FAKE_ORG_RE = re.compile(r"(?<!民主)(?<!共和)(?<!工)党委|党组(?!织)|党支部|纪委|组织部门|(?<!总)书记(?!员)")
PLACEHOLDER_RE = re.compile(r"待补|待核|待定|TODO|TBD|××|XX|［|］|\[|\]|未知(?=[，。；、）]|$)")
AI_MARK_RE = re.compile(r"AI\s?生成|人工智能生成|译者注|以下为|本文由|改写自")
PRAISE = {"伟大": ("great",), "英明": (), "历史性": ("historic",), "划时代": ("epoch",), "史上最": (), "震撼": ("stunning", "shock")}
CERTAIN_RE = re.compile(r"必将|一定|毫无疑问")
VOCATIVE_RE = re.compile(r"同志们[！，]")
BAD_NUMBERING_RE = re.compile(r"^\s*(?:\d+、|[•·●▪◆\-–—]\s*)")


def _subtitle_test(block: str) -> bool:
    return block.startswith("——")


def _structure(paragraphs: Sequence[str]) -> dict[str, Any]:
    """找出日期行、讲话人行、称谓、一级标题、致谢行、结尾段的位置。"""
    info: dict[str, Any] = {"date": None, "speaker": None, "salutation": None, "headings": [], "thanks": None, "closing": None}
    for i, p in enumerate(paragraphs[:3]):
        if DATE_LINE_RE.match(p) or DATE_LIKE_RE.match(p):
            info["date"] = i
            break
    for i, p in enumerate(paragraphs[:5]):
        if p.endswith("：") and len(p) <= 32:
            info["salutation"] = i
            break
    if info["date"] is not None and info["salutation"] is not None and info["salutation"] - info["date"] == 2:
        info["speaker"] = info["date"] + 1
    elif info["date"] is not None and info["date"] + 1 < len(paragraphs) and len(paragraphs[info["date"] + 1]) <= 40 \
            and not paragraphs[info["date"] + 1].endswith(("：", "。")):
        info["speaker"] = info["date"] + 1
    info["headings"] = [i for i, p in enumerate(paragraphs) if HEADING1_RE.match(p) and len(p) <= 45 and not p.endswith("。")]
    last = len(paragraphs) - 1
    if paragraphs and THANKS_RE.match(paragraphs[-1]):
        info["thanks"] = last
        last -= 1
    if last >= 0 and (not info["headings"] or last > info["headings"][-1]):
        info["closing"] = last
    return info


def _han_numeral(text: str) -> int | None:
    values = {"两": 2, **{c: i + 1 for i, c in enumerate(HAN_NUM)}}
    if text.isdigit():
        return int(text)
    if text in values:
        return values[text]
    return None


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
    out: list[dict[str, str]] = []
    st = _structure(paras)
    special = {st["date"], st["speaker"], st["salutation"], st["thanks"], *st["headings"]}

    # S01 开头称谓
    sal = st["salutation"]
    if sal is None:
        out.append(issue("P0", "S01", "正文开头没有独立成段的称谓（如“同志们：”）"))
    elif not SALUTATION_RE.match(paras[sal]):
        out.append(issue("P0", "S01", f"称谓「{paras[sal]}」只能含汉字、顿号、逗号，以全角冒号结尾", clip(paras[sal])))

    # S02 标题
    if draft.subtitle:
        if not (draft.subtitle.startswith("——在") and re.search("讲话|发言|致辞", draft.subtitle)):
            out.append(issue("P1", "S02", f"副标题应写“——在××上的讲话”：「{draft.subtitle}」"))
        if len(draft.title) > 20 or re.search(r"[，。！？；：、]", draft.title):
            out.append(issue("P1", "S02", f"双标题的主标题不超过 20 字、不带标点：「{draft.title}」"))
    elif not re.search("讲话|发言|致辞", draft.title):
        out.append(issue("P1", "S02", f"单标题应写“在××上的讲话”：「{draft.title}」"))

    # S03 日期行
    if st["date"] is None:
        out.append(issue("P1", "S03", "标题下没有日期行“（YYYY年M月D日）”"))
    else:
        line = paras[st["date"]]
        if not DATE_LINE_RE.match(line) or re.search(r"(?<!\d)0\d[月日]", line):
            out.append(issue("P1", "S03", f"日期行写作“（2026年1月12日）”，全角括号，月日不补零：「{line}」"))

    # S04 一级标题数与编号
    heads = st["headings"]
    if not heads:
        out.append(issue("P0", "S04", "没有“一、二、三”一级标题"))
    else:
        if not 3 <= len(heads) <= 5:
            out.append(issue("P1", "S04", f"一级标题 {len(heads)} 个，应为 3 至 5 个"))
        numbers = [HEADING1_RE.match(paras[i]).group()[:-1] for i in heads]
        expected = [HAN_NUM[k] if k < 10 else "" for k in range(len(numbers))]
        if numbers != expected:
            out.append(issue("P1", "S04", f"一级标题编号不连续：{'、'.join(numbers)}"))
        # S05 一级标题形态
        for i in heads:
            body = HEADING1_RE.sub("", paras[i])
            if len(body) > 25 or re.search(r"[。；：！？，]$", body):
                out.append(issue("P2", "S05", f"一级标题不超过 25 字、无句末标点：「{paras[i]}」"))

    # S06 过渡句
    if heads:
        before = paras[(sal + 1 if sal is not None else 0):heads[0]]
        found = None
        for p in reversed(before):
            m = TRANSITION_RE.search(p)
            if m:
                found = m
                break
        if found is None:
            out.append(issue("P1", "S06", "分点之前没有“下面，我讲几点意见”一类过渡句"))
        else:
            n = _han_numeral(found.group(1))
            if n is not None and n != len(heads):
                out.append(issue("P1", "S06", f"过渡句说“{found.group(0)}”，一级标题有 {len(heads)} 个"))

    # S07 段落长度
    for i, p in enumerate(paras):
        if i in special:
            continue
        n = cjk_len(p)
        if n > 400:
            out.append(issue("P1", "S07", f"段落 {n} 字，超过 400 字要拆", clip(p)))
        elif n < 20 and not TRANSITION_RE.search(p):
            out.append(issue("P2", "S07", f"段落只有 {n} 字", clip(p)))

    # S08 结尾号召
    if st["closing"] is None:
        out.append(issue("P1", "S08", "没有结尾段"))
    else:
        close = paras[st["closing"]]
        if not CALL_RE.search(close):
            out.append(issue("P1", "S08", "结尾段要有“让我们……”或“努力、奋斗、贡献”一类号召", clip(close)))
        if cjk_len(close) > 120:
            out.append(issue("P1", "S08", f"结尾段 {cjk_len(close)} 字，不超过 120 字", clip(close)))

    # S09 篇幅比
    body_paras = [p for i, p in enumerate(paras) if i not in (st["date"], st["speaker"], st["salutation"])]
    body_chars = sum(cjk_len(p) for p in body_paras)
    if src.strip():
        words = len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-]*", src))
        low, high = (0.5, 1.2) if words > 6000 else (0.8, 1.6)
        ratio = body_chars / words if words else 0
        if words and not low <= ratio <= high:
            out.append(issue("P1", "S09", f"正文 {body_chars} 字，原文 {words} 词，比值 {ratio:.2f}，应在 {low} 至 {high}"))

    # S10 数字保真（编造 P0，漏写 P1）；限定词保留并入 S10
    out += number_issues(paras, src, rule="S10", skip_paragraph=lambda i, p: i == st["date"], title=draft.title)
    out += approx_issues(body_paras, src, rule="S10")

    # S13 “要”字句占比（主体段落）
    if heads:
        main = [p for i, p in enumerate(paras) if heads[0] < i and i not in special and (st["closing"] is None or i < st["closing"])]
        sents = [s for p in main for s in sentences(p)]
        if sents:
            share = sum(1 for s in sents if YAO_RE.search(s)) / len(sents)
            if share < 0.4:
                out.append(issue("P2", "S13", f"主体段落“要、必须、坚持……”句占 {share:.0%}，参数卡要求不少于 40%"))

    # S14 排比：同一字起头的分句连续 3 个以上，或“一是……二是……三是”
    runs = parallel_runs(paras)
    if runs < 2:
        out.append(issue("P2", "S14", f"排比 {runs} 处，参数卡要求至少 2 处"))

    # S16 句长
    sents = [s for i, p in enumerate(paras) if i not in special for s in sentences(p)]
    if sents:
        lengths = [cjk_len(s) for s in sents]
        avg = sum(lengths) / len(lengths)
        longest = max(lengths)
        if not 20 <= avg <= 45:
            out.append(issue("P2", "S16", f"平均句长 {avg:.0f} 字，参数卡要求 20 至 45 字"))
        if longest > 80:
            s = sents[lengths.index(longest)]
            out.append(issue("P2", "S16", f"最长一句 {longest} 字，超过 80 字要拆", clip(s)))

    body = draft.body_text
    full = draft.all_text
    # S17 口语填充词
    for w in FILLERS:
        if w in body:
            out.append(issue("P1", "S17", f"口语词「{w}」"))
    # S18 禁用指称
    for w in FORBIDDEN_REFS:
        if w in full:
            out.append(issue("P0", "S18", f"不得假托现实党政机关、领导人、文件：「{w}」"))
    if STATE_COUNCIL_RE.search(full):
        out.append(issue("P0", "S18", "不得假托现实党政机关：「国务院」（美国国务院写全）"))
    # S19 虚构组织
    for m in FAKE_ORG_RE.finditer(full):
        out.append(issue("P0", "S19", f"不得虚构党组织、纪检和组织部门或党内职务：「{m.group()}」"))
    # S20 英文夹杂
    out += english_issues([draft.title, draft.subtitle, *paras], src, rule="S20", glossary_dir=glossary_dir)
    # S21 占位符
    for m in PLACEHOLDER_RE.finditer(full):
        out.append(issue("P0", "S21", f"占位符或缺项字样「{m.group()}」"))
    # S22 格式符号
    for p in [draft.title, draft.subtitle, *paras]:
        if p and (MARKDOWN_LINE_RE.match(p) or INLINE_FORMAT_RE.search(p)):
            out.append(issue("P1", "S22", "正文不出现 Markdown、HTML 符号和链接", clip(p)))
    # S23 问号与叹号
    q, e = body.count("？") + body.count("?"), body.count("！") + body.count("!")
    if q > 2:
        out.append(issue("P2", "S23", f"问号 {q} 个，不超过 2 个"))
    if e > 3:
        out.append(issue("P2", "S23", f"叹号 {e} 个，不超过 3 个"))
    # S24 第二人称
    if body.count("你们") > 2:
        out.append(issue("P2", "S24", f"“你们”{body.count('你们')} 次，不超过 2 次，对听众用“大家”“同志们”"))
    # S25 AI 标注与译注
    for m in AI_MARK_RE.finditer(full):
        out.append(issue("P1", "S25", f"正文不写 AI 标注、译注：「{m.group()}」"))
    # S26 评价词
    low_src = src.lower()
    for w, en in PRAISE.items():
        if w in body:
            level = "P2" if any(x in low_src for x in en) else "P1"
            out.append(issue(level, "S26", f"评价词「{w}」" + ("（原文有对应词，人工看是否过度）" if level == "P2" else "，原文没有对应词")))
    # S27 确定性
    certain = CERTAIN_RE.findall(body)
    if len(certain) > 2:
        out.append(issue("P1", "S27", f"“必将、一定、毫无疑问”共 {len(certain)} 次，不超过 2 次"))
    # S28 呼语次数
    voc = len(VOCATIVE_RE.findall(body))
    if voc > 3:
        out.append(issue("P2", "S28", f"“同志们！/同志们，”{voc} 次，不超过 3 次"))
    # S30 编号体系
    for p in paras:
        if BAD_NUMBERING_RE.match(p):
            out.append(issue("P1", "S30", "编号只用“一、（一）1.（1）”和“一是”，不用“1、”、圆点、短横线", clip(p)))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    return cli(argv, prog="python -m tools.lint.speech_lint", description="讲话稿 lint（cadre-speech 参数卡 S01–S30）。", lint_text=lint_text)


if __name__ == "__main__":
    sys.exit(main())
