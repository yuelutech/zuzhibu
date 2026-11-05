"""shared/render/article_html.py：公众号 HTML 的结构性检查。"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from shared.render import article_html
from shared.render.article_html import AFTERWORD_DEFAULT, AFTERWORD_TITLE, BOX_BG, digest_text, render_article_html, render_article_page
from tests.support_render import parse_html

DOC = {
    "title": "张三简历",
    "summary": "张三现任乙公司总裁。",
    "photo": "portrait.svg",
    "lead": "张三，男，1970年3月出生，美国人，1992年参加工作，甲大学电气工程理学硕士学位。",
    "current": "现任乙公司总裁、董事。",
    "careers": [
        {"period": "1988年9月－1992年7月", "organization": "甲大学", "role": "电气工程专业学习"},
        {"period": "1992年8月至今", "organization": "乙公司", "role": "总裁、董事", "note": "2020年起兼任丙学会理事"},
    ],
    "closing": "履历截至2026年10月。",
}


class FragmentStructureTests(unittest.TestCase):
    def setUp(self):
        self.html = render_article_html(DOC)
        self.parsed = parse_html(self.html)

    def test_tags_balanced(self):
        self.assertEqual(self.parsed.errors, [])

    def test_all_styles_inline(self):
        self.assertNotIn("<style", self.html)
        self.assertNotIn("class=", self.html)
        for tag, attrs in self.parsed.tags:
            if tag == "br":
                continue
            self.assertIn("style", attrs, f"<{tag}> 没有行内样式")
            self.assertTrue(attrs["style"], f"<{tag}> 行内样式为空")

    def test_outer_wrapper_is_single_section(self):
        self.assertTrue(self.html.startswith("<section "))
        self.assertTrue(self.html.rstrip().endswith("</section>"))

    def test_light_blue_digest_box(self):
        boxes = [attrs for tag, attrs in self.parsed.tags if tag == "section" and f"background-color:{BOX_BG}" in (attrs.get("style") or "")]
        self.assertEqual(len(boxes), 1)
        self.assertIn(f'>{DOC["summary"]}</section>', self.html)

    def test_centered_photo(self):
        imgs = [attrs for tag, attrs in self.parsed.tags if tag == "img"]
        self.assertEqual(len(imgs), 1)
        self.assertEqual(imgs[0]["src"], "portrait.svg")
        self.assertEqual(imgs[0]["alt"], "张三")
        wrappers = [attrs for tag, attrs in self.parsed.tags if tag == "section" and "text-align:center" in (attrs.get("style") or "")]
        self.assertTrue(wrappers)

    def test_lead_and_current_paragraphs_indented(self):
        paragraphs = [attrs for tag, attrs in self.parsed.tags if tag == "p" and "text-indent:2em" in (attrs.get("style") or "")]
        self.assertGreaterEqual(len(paragraphs), 3)  # 首段、现任段、尾段
        self.assertIn(f">{DOC['lead']}</p>", self.html)
        self.assertIn(f">{DOC['current']}</p>", self.html)
        self.assertIn(f">{DOC['closing']}</p>", self.html)

    def test_career_lines_indented_with_period_first(self):
        career_ps = [attrs for tag, attrs in self.parsed.tags if tag == "p" and "margin:0 0 8px 2em" in (attrs.get("style") or "")]
        self.assertEqual(len(career_ps), 2)
        self.assertIn(">1988年9月－1992年7月</span>甲大学电气工程专业学习</p>", self.html)
        self.assertIn(">1992年8月至今</span>乙公司总裁、董事（2020年起兼任丙学会理事）</p>", self.html)

    def test_no_afterword_by_default(self):
        # 导出物只留正文：默认不加“写在最后”，也没有 AI、戏仿一类说明（维护者 2026-10-21 定）。
        self.assertEqual(AFTERWORD_DEFAULT, [])
        self.assertNotIn(AFTERWORD_TITLE, self.html)
        for word in ("AI 演绎", "AI 生成", "演绎", "戏仿", "伪造", "冒充"):
            self.assertNotIn(word, self.html)

    def test_order_of_blocks(self):
        positions = [self.html.index(x) for x in (DOC["summary"], "<img", DOC["lead"], DOC["current"], "1988年9月", DOC["closing"])]
        self.assertEqual(positions, sorted(positions))


class FragmentVariantTests(unittest.TestCase):
    def test_digest_fallback_order(self):
        self.assertEqual(digest_text(article_html.load_doc(DOC)), DOC["summary"])
        without_summary = dict(DOC, summary=None)
        self.assertEqual(digest_text(article_html.load_doc(without_summary)), DOC["current"])
        lead_only = {"title": "x", "lead": "第一句。第二句。"}
        self.assertEqual(digest_text(article_html.load_doc(lead_only)), "第一句。")
        self.assertEqual(digest_text(article_html.load_doc({"title": "x"})), "")
        html = render_article_html({"title": "x", "paragraphs": ["正文"]})
        self.assertNotIn(BOX_BG, html)

    def test_no_photo_no_img(self):
        html = render_article_html({"title": "x", "lead": "首段。"})
        self.assertNotIn("<img", html)

    def test_afterword_override_and_removal(self):
        custom = render_article_html(dict(DOC, afterword="自定义结尾"))
        self.assertIn(">自定义结尾</p>", custom)
        self.assertIn(f">{AFTERWORD_TITLE}</section>", custom)
        self.assertLess(custom.index(DOC["closing"]), custom.index(AFTERWORD_TITLE))
        as_list = render_article_html(dict(DOC, afterword=["一", "二"]))
        self.assertIn(">一</p>", as_list)
        self.assertIn(">二</p>", as_list)
        removed = render_article_html(dict(DOC, afterword=""))
        self.assertNotIn(AFTERWORD_TITLE, removed)

    def test_subtitle_and_salutation(self):
        html = render_article_html({"title": "讲话", "subtitle": "（2026年10月16日）", "paragraphs": ["同志们：", "今天开会。"]})
        self.assertIn(">（2026年10月16日）</p>", html)
        parsed = parse_html(html)
        salutation = [attrs for tag, attrs in parsed.tags if tag == "p" and "text-align:left" in attrs["style"]]
        self.assertEqual(len(salutation), 1)
        self.assertNotIn("text-indent", salutation[0]["style"])
        self.assertIn(">同志们：</p>", html)

    def test_photo_credit_under_photo(self):
        html = render_article_html({"title": "x", "photo": "p.jpg", "photo_credit": "照片：某某，CC BY 2.0，有裁切"})
        self.assertIn("照片：某某，CC BY 2.0，有裁切</p></section>", html)
        self.assertNotIn("CC BY", render_article_html({"title": "x", "photo": "p.jpg"}))
        self.assertEqual(parse_html(html).errors, [])

    def test_escaping(self):
        html = render_article_html({"title": "A & B", "lead": "<b>粗体</b>", "photo": 'x".svg', "careers": [["2001年", "甲 & 乙"]]})
        self.assertNotIn("<b>", html)
        self.assertIn("&lt;b&gt;粗体&lt;/b&gt;", html)
        self.assertIn("甲 &amp; 乙", html)
        self.assertIn('src="x&quot;.svg"', html)
        self.assertEqual(parse_html(html).errors, [])


class PageAndCliTests(unittest.TestCase):
    def test_full_page_wraps_fragment(self):
        page = render_article_page(DOC)
        fragment = render_article_html(DOC)
        self.assertTrue(page.startswith("<!DOCTYPE html>"))
        self.assertIn("<title>张三简历</title>", page)
        self.assertIn(">张三简历</h1>", page)
        self.assertIn(fragment, page)
        self.assertNotIn("<style", page)
        self.assertEqual(parse_html(page).errors, [])

    def test_cli_writes_file_and_prints_fragment(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "doc.json"
            source.write_text(json.dumps(DOC, ensure_ascii=False), encoding="utf-8")
            output = Path(tmp) / "out.html"
            self.assertEqual(article_html.main([str(source), "-o", str(output)]), 0)
            self.assertEqual(output.read_text(encoding="utf-8"), render_article_page(DOC))
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(article_html.main([str(source), "--fragment"]), 0)
            self.assertEqual(buffer.getvalue(), render_article_html(DOC))

    def test_cli_reports_bad_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "doc.json"
            source.write_text(json.dumps({"lead": "没有标题"}), encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(article_html.main([str(source)]), 2)
            self.assertIn("title", err.getvalue())


if __name__ == "__main__":
    unittest.main()
