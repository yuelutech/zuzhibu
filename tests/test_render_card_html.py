"""shared/render/card_html.py 与 card.css：3:4 图文卡的结构性检查。"""

import json
import re
import tempfile
import unittest
from pathlib import Path

from shared.render import card_html
from shared.render.card_html import CSS_PATH, MAX_LINES, paginate, render_caption, render_card_html
from tests.support_render import parse_html

DOC = {
    "title": "张三简历",
    "photo": "portrait.svg",
    "lead": "张三，男，1970年3月出生，美国人，1992年参加工作，甲大学电气工程理学硕士学位。",
    "current": "现任乙公司总裁、董事。",
    "careers": [
        {"period": "1988年9月－1992年7月", "organization": "甲大学", "role": "电气工程专业学习"},
        {"period": "1992年8月至今", "organization": "超威半导体公司（AMD）", "role": "微处理器设计工程师、设计总监"},
    ],
    "closing": "履历截至2026年10月。",
    "tags": ["乙公司"],
}


def short_entries(count):
    return [{"period": f"{2000 + i}年", "organization": "甲公司", "role": "工程师"} for i in range(count)]


class CssTests(unittest.TestCase):
    def setUp(self):
        self.css = CSS_PATH.read_text(encoding="utf-8")

    def declaration(self, name):
        match = re.search(rf"{re.escape(name)}:\s*([^;]+);", self.css, re.S)
        self.assertIsNotNone(match, f"card.css 缺 {name}")
        return " ".join(match.group(1).split())

    def test_card_size_540_by_720(self):
        self.assertEqual(self.declaration("--card-width"), "540px")
        self.assertEqual(self.declaration("--card-height"), "720px")
        self.assertEqual(self.declaration("--card-bg"), "#3771BB")

    def test_body_font_fallback_times_before_fangsong(self):
        stack = self.declaration("--card-font-body")
        self.assertLess(stack.index("Times New Roman"), stack.index("仿宋_GB2312"))
        self.assertLess(stack.index("仿宋_GB2312"), stack.index("serif"))
        self.assertTrue(stack.startswith('"Times New Roman"'))

    def test_body_text_parameters(self):
        self.assertEqual(self.declaration("--card-body-size"), "20px")
        self.assertEqual(self.declaration("--card-title-size"), "44px")
        self.assertEqual(self.declaration("--card-line-height"), "30px")
        para = re.search(r"\.card-para\s*\{([^}]*)\}", self.css).group(1)
        self.assertIn("text-indent: 2em", para)
        self.assertIn("text-align: justify", para)

    def test_continuation_page_vertically_centered(self):
        cont = re.search(r"\.card-cont\s*\{([^}]*)\}", self.css).group(1)
        self.assertIn("justify-content: center", cont)
        self.assertIn("flex-direction: column", cont)
        centered = re.search(r"\.card-date,\s*\.card-text,\s*\.card-closing\s*\{([^}]*)\}", self.css).group(1)
        self.assertIn("text-align: center", centered)

    def test_no_font_files_referenced(self):
        self.assertNotIn("@font-face", self.css)
        self.assertNotIn("url(", self.css)


class PhotoCreditTests(unittest.TestCase):
    def test_credit_in_photo_frame(self):
        html = render_card_html({"title": "x", "photo": "p.jpg", "photo_credit": "照片：某某，CC BY 2.0，有裁切"})
        self.assertIn('<figcaption class="card-credit">照片：某某，CC BY 2.0，有裁切</figcaption></figure>', html)
        self.assertNotIn("card-credit\">", render_card_html({"title": "x", "photo": "p.jpg"}))
        self.assertEqual(parse_html(html).errors, [])


class PaginateTests(unittest.TestCase):
    def test_eight_short_entries_fill_one_page(self):
        pages = paginate({"title": "x", "careers": short_entries(8)})
        self.assertEqual(len(pages), 1)
        self.assertEqual(sum(b["height"] for b in pages[0]) + len(pages[0]) - 1, MAX_LINES)

    def test_ninth_entry_starts_a_new_page(self):
        pages = paginate({"title": "x", "careers": short_entries(9)})
        self.assertEqual([len(p) for p in pages], [8, 1])

    def test_long_entry_counts_extra_lines(self):
        pages = paginate(DOC)
        self.assertEqual(len(pages), 1)
        heights = [b["height"] for b in pages[0]]
        self.assertEqual(heights[:2], [2, 3])  # 第二条内容断成两行
        self.assertEqual(pages[0][1]["lines"], ["超威半导体公司（AMD）", "微处理器设计工程师、设计总监"])
        self.assertEqual(pages[0][-1]["kind"], "closing")

    def test_paragraphs_flow_into_continuation_pages(self):
        pages = paginate({"title": "x", "paragraphs": ["同志们：", "一" * 60]})
        self.assertEqual([b["kind"] for b in pages[0]], ["para", "para"])
        self.assertEqual([b["height"] for b in pages[0]], [1, 3])

    def test_custom_max_lines_and_empty_doc(self):
        pages = paginate({"title": "x", "careers": short_entries(4)}, max_lines=5)
        self.assertEqual([len(p) for p in pages], [2, 2])
        self.assertEqual(paginate({"title": "x", "lead": "只有首段。"}), [])


class HtmlStructureTests(unittest.TestCase):
    def setUp(self):
        self.html = render_card_html(DOC)
        self.parsed = parse_html(self.html)

    def test_well_formed_and_inline_css(self):
        self.assertEqual(self.parsed.errors, [])
        self.assertIn("<style>", self.html)
        self.assertIn("--card-width: 540px", self.html)
        self.assertIn('<meta name="viewport" content="width=540">', self.html)
        self.assertIn("<title>张三简历</title>", self.html)

    def test_cover_page_contents(self):
        cover = re.search(r'<section class="card card-cover" data-page="1">(.*?)</section>', self.html, re.S).group(1)
        self.assertIn('<h1 class="card-title">张三简历</h1>', cover)
        self.assertIn('<img src="portrait.svg" alt="">', cover)
        self.assertIn(f'<p class="card-para">{DOC["lead"]}</p>', cover)
        self.assertIn(f'<p class="card-para">{DOC["current"]}</p>', cover)
        self.assertNotIn("card-entry", cover)

    def test_continuation_page_contents(self):
        pages = re.findall(r'<section class="card card-cont" data-page="(\d+)">(.*?)</section>', self.html, re.S)
        self.assertEqual([number for number, _ in pages], ["2"])
        body = pages[0][1]
        self.assertEqual(body.count('<div class="card-block card-entry">'), 2)
        self.assertIn('<p class="card-date">1988年9月－1992年7月</p>', body)
        self.assertIn('<p class="card-text">超威半导体公司（AMD）<br>微处理器设计工程师、设计总监</p>', body)
        self.assertIn('<p class="card-block card-closing">履历截至2026年10月。</p>', body)

    def test_page_count_matches_paginate(self):
        doc = {"title": "x", "careers": short_entries(20)}
        html = render_card_html(doc)
        self.assertEqual(html.count('class="card card-cont"'), len(paginate(doc)))
        self.assertEqual(html.count('class="card card-cover"'), 1)

    def test_link_css_option(self):
        html = render_card_html(DOC, inline_css=False, css_href="styles/card.css")
        self.assertNotIn("<style>", html)
        self.assertIn('<link rel="stylesheet" href="styles/card.css">', html)

    def test_no_photo_no_figure_and_escaping(self):
        html = render_card_html({"title": "A<B", "lead": "x & y", "careers": [["2001年", "甲<乙"]]})
        self.assertNotIn("<figure", html)
        self.assertIn("A&lt;B", html)
        self.assertIn("x &amp; y", html)
        self.assertIn("甲&lt;乙", html)
        self.assertEqual(parse_html(html).errors, [])

    def test_salutation_paragraph_class(self):
        html = render_card_html({"title": "讲话", "paragraphs": ["同志们：", "正文。"]})
        self.assertIn('<p class="card-block card-para card-salutation">同志们：</p>', html)
        self.assertIn('<p class="card-block card-para">正文。</p>', html)


class CaptionTests(unittest.TestCase):
    def test_caption_format(self):
        caption = render_caption(DOC)
        self.assertEqual(caption, f"张三简历\n\n{DOC['lead']}\n\n#AI政见 #张三 #乙公司 #干部简历\n")

    def test_caption_without_lead_and_duplicate_tags(self):
        caption = render_caption({"title": "张三简历", "tags": ["张三", "AI政见", "丙"]})
        self.assertEqual(caption, "张三简历\n\n#AI政见 #张三 #丙 #干部简历\n")


class CliTests(unittest.TestCase):
    def test_cli_outputs_html_caption_and_copies_css(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "doc.json"
            source.write_text(json.dumps(DOC, ensure_ascii=False), encoding="utf-8")
            out_dir = Path(tmp) / "out"
            out_dir.mkdir()
            output = out_dir / "card.html"
            caption = out_dir / "caption.txt"
            code = card_html.main([str(source), "-o", str(output), "--caption", str(caption), "--link-css"])
            self.assertEqual(code, 0)
            html = output.read_text(encoding="utf-8")
            self.assertIn('<link rel="stylesheet" href="card.css">', html)
            self.assertEqual((out_dir / "card.css").read_text(encoding="utf-8"), CSS_PATH.read_text(encoding="utf-8"))
            self.assertEqual(caption.read_text(encoding="utf-8"), render_caption(DOC))

    def test_cli_max_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "doc.json"
            source.write_text(json.dumps({"title": "x", "careers": short_entries(4)}, ensure_ascii=False), encoding="utf-8")
            output = Path(tmp) / "card.html"
            self.assertEqual(card_html.main([str(source), "-o", str(output), "--max-lines", "5"]), 0)
            self.assertEqual(output.read_text(encoding="utf-8").count('class="card card-cont"'), 2)


if __name__ == "__main__":
    unittest.main()
