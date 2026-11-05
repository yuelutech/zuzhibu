"""shared/render/docx_redhead.py：红头件 .docx 的结构性检查（直接读 zip 里的 XML）。渲染器只用标准库，测试不跳过。"""

import contextlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from shared.render import docx_redhead
from shared.render.common import RenderError
from shared.render.docx_redhead import DEFAULT_AGENCY, DEFAULT_FONTS, DEFAULT_FOOTER_NOTE, LOCAL_FONTS, resolve_fonts
from tests.support_render import body_paragraphs, first_run_props, paragraph_text, read_docx, w, wattr

DOC = {
    "title": "测试标题",
    "subtitle": "（2026年1月1日）",
    "lead": "张三，男，1970年3月出生，美国人，1992年参加工作，甲大学电气工程理学硕士学位。",
    "current": "现任乙公司总裁。",
    "careers": [{"period": "2001年1月－2002年2月", "organization": "甲公司", "role": "工程师"}],
    "paragraphs": ["同志们：", "正文第一段。"],
    "closing": "谢谢大家。",
    "redhead": {
        "agency": "虚构机关文件",
        "doc_number": "虚发〔2026〕1号",
        "recipient": "各部门：",
        "signoff": "虚构机关",
        "date": "2026年1月1日",
    },
}


def build(source, **kwargs):
    buffer = io.BytesIO()
    docx_redhead.build_document(source, **kwargs).save(buffer)
    return read_docx(buffer.getvalue())


class FontConfigTests(unittest.TestCase):
    def test_defaults_are_free_fonts(self):
        for value in DEFAULT_FONTS.values():
            self.assertNotIn("方正", value)
            self.assertNotIn("Times New Roman", value)
        self.assertEqual(set(DEFAULT_FONTS), {"header", "title", "body", "latin", "heading", "kai", "page"})
        self.assertEqual(set(LOCAL_FONTS), set(DEFAULT_FONTS))

    def test_resolve_merges_partial_config(self):
        fonts = resolve_fonts({"body": "仿宋_GB2312", "latin": ""})
        self.assertEqual(fonts["body"], "仿宋_GB2312")
        self.assertEqual(fonts["latin"], DEFAULT_FONTS["latin"])
        self.assertEqual(resolve_fonts(None), DEFAULT_FONTS)
        with self.assertRaises(RenderError):
            resolve_fonts({"caption": "x"})

    def test_body_paragraph_plan(self):
        kinds = [kind for kind, _ in docx_redhead.body_paragraphs(docx_redhead.load_doc(DOC))]
        self.assertEqual(kinds, ["lead", "current", "career", "salutation", "para", "closing"])
        speech = {"title": "讲话", "paragraphs": ["同志们：", "一、要抓落实", "（一）抓住重点。正文。", "正文。"]}
        kinds = [kind for kind, _ in docx_redhead.body_paragraphs(docx_redhead.load_doc(speech))]
        self.assertEqual(kinds, ["salutation", "heading", "para", "para"])


class DocumentStructureTests(unittest.TestCase):
    def paragraph(self, text):
        return self.paragraphs[self.texts.index(text)]

    def test_paragraph_order(self):
        expected = [
            "虚构机关文件",
            "虚发〔2026〕1号",
            "",
            "测试标题",
            "（2026年1月1日）",
            "各部门：",
            DOC["lead"],
            DOC["current"],
            "2001年1月－2002年2月　甲公司工程师",
            "同志们：",
            "正文第一段。",
            "谢谢大家。",
            "虚构机关",
            "2026年1月1日",
        ]
        self.assertEqual(self.texts, expected)

    def test_red_header(self):
        header = self.paragraph("虚构机关文件")
        self.assertEqual(wattr(header.find(f"{w('pPr')}/{w('jc')}"), "val"), "center")
        props = first_run_props(header)
        self.assertEqual(wattr(props.find(w("color")), "val"), "FF0000")
        self.assertEqual(wattr(props.find(w("sz")), "val"), "72")
        rfonts = props.find(w("rFonts"))
        self.assertEqual(wattr(rfonts, "eastAsia"), DEFAULT_FONTS["header"])
        self.assertEqual(wattr(rfonts, "ascii"), DEFAULT_FONTS["latin"])
        self.assertEqual(wattr(rfonts, "hint"), "eastAsia")

    def test_red_rule_paragraph(self):
        rule = self.paragraphs[2]
        bottom = rule.find(f"{w('pPr')}/{w('pBdr')}/{w('bottom')}")
        self.assertIsNotNone(bottom)
        self.assertEqual(wattr(bottom, "color"), "FF0000")
        self.assertEqual(wattr(bottom, "val"), "single")
        self.assertEqual(wattr(bottom, "sz"), "12")
        self.assertEqual(len([p for p in self.paragraphs if p.find(f"{w('pPr')}/{w('pBdr')}") is not None]), 1)

    def test_doc_number_centered_body_size(self):
        number = self.paragraph("虚发〔2026〕1号")
        self.assertEqual(wattr(number.find(f"{w('pPr')}/{w('jc')}"), "val"), "center")
        self.assertEqual(wattr(first_run_props(number).find(w("sz")), "val"), "32")

    def test_title(self):
        title = self.paragraph("测试标题")
        self.assertEqual(wattr(title.find(f"{w('pPr')}/{w('jc')}"), "val"), "center")
        props = first_run_props(title)
        self.assertEqual(wattr(props.find(w("sz")), "val"), "44")
        self.assertEqual(wattr(props.find(w("rFonts")), "eastAsia"), DEFAULT_FONTS["title"])
        self.assertIsNone(title.find(f"{w('pPr')}/{w('ind')}"))

    def test_body_paragraph_indent_spacing_fonts(self):
        for text in (DOC["lead"], DOC["current"], "正文第一段。", "谢谢大家。", "2001年1月－2002年2月　甲公司工程师"):
            paragraph = self.paragraph(text)
            ppr = paragraph.find(w("pPr"))
            ind = ppr.find(w("ind"))
            self.assertEqual(wattr(ind, "firstLineChars"), "200", text)
            self.assertEqual(wattr(ind, "firstLine"), "640", text)
            spacing = ppr.find(w("spacing"))
            self.assertEqual(wattr(spacing, "line"), "560", text)
            self.assertEqual(wattr(spacing, "lineRule"), "exact", text)
            self.assertEqual(wattr(ppr.find(w("jc")), "val"), "both", text)
            rfonts = first_run_props(paragraph).find(w("rFonts"))
            self.assertEqual(wattr(rfonts, "eastAsia"), DEFAULT_FONTS["body"])
            self.assertEqual(wattr(rfonts, "ascii"), DEFAULT_FONTS["latin"])
            self.assertEqual(wattr(rfonts, "hAnsi"), DEFAULT_FONTS["latin"])
            self.assertEqual(wattr(first_run_props(paragraph).find(w("sz")), "val"), "32")

    def test_salutation_and_recipient_flush_left(self):
        for text in ("同志们：", "各部门："):
            ppr = self.paragraph(text).find(w("pPr"))
            self.assertIsNone(ppr.find(w("ind")), text)
            jc = ppr.find(w("jc"))
            self.assertIn(None if jc is None else wattr(jc, "val"), (None, "left"), text)

    def test_signoff_right_aligned(self):
        signoff = self.paragraph("虚构机关")
        self.assertEqual(wattr(signoff.find(f"{w('pPr')}/{w('jc')}"), "val"), "right")
        self.assertEqual(wattr(signoff.find(f"{w('pPr')}/{w('ind')}"), "rightChars"), "200")
        date = self.paragraph("2026年1月1日")
        self.assertEqual(wattr(date.find(f"{w('pPr')}/{w('jc')}"), "val"), "right")
        self.assertEqual(wattr(date.find(f"{w('pPr')}/{w('ind')}"), "rightChars"), "400")

    def test_page_setup_a4_with_official_margins(self):
        sect = self.root.find(f"{w('body')}/{w('sectPr')}")
        size = sect.find(w("pgSz"))
        self.assertAlmostEqual(int(wattr(size, "w")), 11906, delta=1)
        self.assertAlmostEqual(int(wattr(size, "h")), 16838, delta=1)
        margins = sect.find(w("pgMar"))
        self.assertAlmostEqual(int(wattr(margins, "top")), 2098, delta=1)
        self.assertAlmostEqual(int(wattr(margins, "bottom")), 1984, delta=1)
        self.assertAlmostEqual(int(wattr(margins, "left")), 1588, delta=1)
        self.assertAlmostEqual(int(wattr(margins, "right")), 1474, delta=1)

    def test_footer_has_page_numbers_only(self):
        # 默认页脚只有页码，不加说明文字；文件属性也不写说明（维护者 2026-10-21 定）。
        self.assertIn("word/footer1.xml", self.names)
        self.assertEqual(DEFAULT_FOOTER_NOTE, "")
        core = zipfile.ZipFile(io.BytesIO(docx_redhead.build_document(DOC).to_bytes())).read("docProps/core.xml").decode("utf-8")
        for text in (self.footer, core):
            for word in ("AI 演绎", "AI 生成", "演绎", "戏仿", "伪造", "冒充"):
                self.assertNotIn(word, text)

    def test_page_numbers_odd_right_even_left(self):
        # 页码“—　1　—”：单页（default）居右空一字，双页（even）居左空一字，settings 打开奇偶页不同。
        self.assertIn("word/footer2.xml", self.names)
        self.assertIn('w:type="default"', self.document_xml)
        self.assertIn('w:type="even"', self.document_xml)
        self.assertIn("<w:evenAndOddHeaders/>", self.settings_xml)
        self.assertIn("PAGE", self.footer)
        self.assertIn("—　", self.footer)
        self.assertIn('<w:jc w:val="right"/>', self.footer)
        self.assertIn('w:rightChars="100"', self.footer)
        self.assertIn('<w:jc w:val="left"/>', self.footer2)
        self.assertIn('w:leftChars="100"', self.footer2)

    def test_document_grid_28_chars_22_lines(self):
        grid = self.root.find(f"{w('body')}/{w('sectPr')}/{w('docGrid')}")
        self.assertEqual(wattr(grid, "type"), "linesAndChars")
        self.assertAlmostEqual(int(wattr(grid, "charSpace")), -849, delta=8)
        self.assertAlmostEqual(int(wattr(grid, "linePitch")), 579, delta=2)

    @classmethod
    def setUpClass(cls):
        cls.root, cls.footer, cls.names = build(DOC)
        cls.paragraphs = body_paragraphs(cls.root)
        cls.texts = [paragraph_text(p) for p in cls.paragraphs]
        data = io.BytesIO()
        docx_redhead.build_document(DOC).save(data)
        with zipfile.ZipFile(data) as archive:
            cls.document_xml = archive.read("word/document.xml").decode("utf-8")
            cls.settings_xml = archive.read("word/settings.xml").decode("utf-8")
            cls.footer2 = archive.read("word/footer2.xml").decode("utf-8")


class SpeechLayoutTests(unittest.TestCase):
    """讲话稿：署名行居中楷体，“一、”黑体，“（一）”标题句楷体、句后正文仍是正文字体。"""

    @classmethod
    def setUpClass(cls):
        doc = {
            "title": "在年度大会上的讲话",
            "byline": ["（2026年1月12日）", "张三"],
            "paragraphs": ["同志们：", "一、要抓好落实", "（一）抓住重点。这是正文。"],
        }
        cls.root, _, _ = build(doc, fonts=LOCAL_FONTS)
        cls.paragraphs = body_paragraphs(cls.root)
        cls.texts = [paragraph_text(p) for p in cls.paragraphs]

    def paragraph(self, text):
        return self.paragraphs[self.texts.index(text)]

    def test_byline_centered_kai(self):
        for text in ("（2026年1月12日）", "张三"):
            p = self.paragraph(text)
            self.assertEqual(wattr(p.find(f"{w('pPr')}/{w('jc')}"), "val"), "center")
            self.assertEqual(wattr(first_run_props(p).find(w("rFonts")), "eastAsia"), "楷体_GB2312")
        self.assertEqual(self.texts.index("（2026年1月12日）"), self.texts.index("在年度大会上的讲话") + 1)

    def test_heading_fonts(self):
        heading = self.paragraph("一、要抓好落实")
        self.assertEqual(wattr(first_run_props(heading).find(w("rFonts")), "eastAsia"), "黑体")
        self.assertEqual(wattr(heading.find(f"{w('pPr')}/{w('ind')}"), "firstLineChars"), "200")
        sub = self.paragraph("（一）抓住重点。这是正文。")
        runs = sub.findall(w("r"))
        self.assertEqual(len(runs), 2)
        self.assertEqual(wattr(runs[0].find(f"{w('rPr')}/{w('rFonts')}"), "eastAsia"), "楷体_GB2312")
        self.assertEqual(wattr(runs[1].find(f"{w('rPr')}/{w('rFonts')}"), "eastAsia"), "仿宋_GB2312")


class DocumentVariantTests(unittest.TestCase):
    def test_font_override_applies_everywhere(self):
        fonts = {"header": "方正小标宋简体", "title": "方正小标宋简体", "body": "仿宋_GB2312", "latin": "Times New Roman"}
        root, footer, _ = build(DOC, fonts=fonts, footer_note="页脚说明")
        paragraphs = body_paragraphs(root)
        texts = [paragraph_text(p) for p in paragraphs]
        header_fonts = first_run_props(paragraphs[texts.index("虚构机关文件")]).find(w("rFonts"))
        self.assertEqual(wattr(header_fonts, "eastAsia"), "方正小标宋简体")
        body_fonts = first_run_props(paragraphs[texts.index(DOC["lead"])]).find(w("rFonts"))
        self.assertEqual(wattr(body_fonts, "eastAsia"), "仿宋_GB2312")
        self.assertEqual(wattr(body_fonts, "ascii"), "Times New Roman")
        self.assertEqual(wattr(body_fonts, "hAnsi"), "Times New Roman")
        self.assertIn('w:eastAsia="仿宋_GB2312"', footer)

    def test_default_agency_and_optional_parts(self):
        root, footer, names = build({"title": "只有标题", "paragraphs": ["一段。"]}, footer_note=None, page_numbers=False)
        texts = [paragraph_text(p) for p in body_paragraphs(root)]
        self.assertEqual(texts, [DEFAULT_AGENCY, "", "只有标题", "一段。"])
        self.assertNotIn("word/footer1.xml", names)
        self.assertIsNone(footer)

    def test_same_input_same_bytes(self):
        self.assertEqual(docx_redhead.build_document(DOC).to_bytes(), docx_redhead.build_document(DOC).to_bytes())

    def test_custom_footer_note_and_layout(self):
        root, footer, _ = build(DOC, footer_note="自定义页脚", layout={"body_size": 14, "line_pitch": 24})
        self.assertIn("自定义页脚", footer)
        paragraphs = body_paragraphs(root)
        texts = [paragraph_text(p) for p in paragraphs]
        lead = paragraphs[texts.index(DOC["lead"])]
        self.assertEqual(wattr(lead.find(f"{w('pPr')}/{w('spacing')}"), "line"), "480")
        self.assertEqual(wattr(lead.find(f"{w('pPr')}/{w('ind')}"), "firstLine"), "560")
        self.assertEqual(wattr(first_run_props(lead).find(w("sz")), "val"), "28")

    def test_render_docx_writes_file_and_creates_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "nested" / "out.docx"
            result = docx_redhead.render_docx(DOC, output)
            self.assertEqual(result, output)
            root, _, _ = read_docx(output.read_bytes())
            self.assertIn("测试标题", [paragraph_text(p) for p in body_paragraphs(root)])

    def test_cli_local_fonts(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "doc.json"
            source.write_text(json.dumps(DOC, ensure_ascii=False), encoding="utf-8")
            output = Path(tmp) / "out.docx"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = docx_redhead.main([str(source), "-o", str(output), "--local-fonts", "--font-latin", "Liberation Serif", "--footer-note", "", "--no-page-numbers"])
            self.assertEqual(code, 0)
            self.assertIn(str(output), out.getvalue())
            root, _, names = read_docx(output.read_bytes())
            paragraphs = body_paragraphs(root)
            texts = [paragraph_text(p) for p in paragraphs]
            fonts = first_run_props(paragraphs[texts.index(DOC["lead"])]).find(w("rFonts"))
            self.assertEqual(wattr(fonts, "eastAsia"), LOCAL_FONTS["body"])
            self.assertEqual(wattr(fonts, "ascii"), "Liberation Serif")
            self.assertNotIn("word/footer1.xml", names)

    def test_cli_reports_bad_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "doc.json"
            source.write_text(json.dumps({"title": "x", "bogus": 1}), encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(docx_redhead.main([str(source), "-o", str(Path(tmp) / "o.docx")]), 2)
            self.assertIn("bogus", err.getvalue())


if __name__ == "__main__":
    unittest.main()
