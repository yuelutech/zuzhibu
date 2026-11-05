"""shared/render/common.py：稿件对象规范化、履历条目写法、字宽估算与断行。"""

import json
import tempfile
import unittest
from pathlib import Path

from shared.render.common import (
    LINE_LIMIT,
    RenderError,
    derive_name,
    estimate_lines,
    first_sentence,
    is_salutation,
    join_org_role,
    load_doc,
    normalize_career,
    normalize_doc,
    text_width,
    wrap_content,
)

RAW = {
    "title": "张三简历",
    "lead": "张三，男，1970年3月出生，美国人，1992年参加工作，甲大学电气工程理学硕士学位。",
    "current": "现任乙公司总裁。",
    "careers": [
        {"period": "1988年9月－1992年7月", "organization": "甲大学", "role": "电气工程专业学习"},
        ["1992年8月－2000年1月", "丙公司", "工程师"],
        "2000年2月至今 乙公司总裁",
    ],
    "closing": "履历截至2026年10月。",
    "tags": ["#乙公司"],
    "_comment": "测试用",
}


class NormalizeDocTests(unittest.TestCase):
    def test_minimal_doc_gets_defaults(self):
        doc = normalize_doc({"title": "张三简历"})
        self.assertEqual(doc["title"], "张三简历")
        self.assertEqual(doc["name"], "张三")
        self.assertEqual(doc["careers"], [])
        self.assertEqual(doc["paragraphs"], [])
        self.assertEqual(doc["tags"], [])
        self.assertIsNone(doc["afterword"])
        self.assertEqual(doc["redhead"], {"agency": "", "doc_number": "", "recipient": "", "signoff": "", "date": ""})
        for key in ("subtitle", "summary", "photo", "lead", "current", "closing"):
            self.assertEqual(doc[key], "")

    def test_title_required(self):
        with self.assertRaises(RenderError):
            normalize_doc({"lead": "没有标题"})
        with self.assertRaises(RenderError):
            normalize_doc({"title": "   "})

    def test_unknown_field_rejected_but_underscore_keys_ignored(self):
        with self.assertRaises(RenderError) as ctx:
            normalize_doc({"title": "x", "carrers": []})
        self.assertIn("carrers", str(ctx.exception))
        doc = normalize_doc({"title": "x", "_comment": "随便写"})
        self.assertNotIn("_comment", doc)

    def test_not_a_dict_rejected(self):
        with self.assertRaises(RenderError):
            normalize_doc(["title"])

    def test_wrong_types_rejected(self):
        with self.assertRaises(RenderError):
            normalize_doc({"title": "x", "careers": "不是列表"})
        with self.assertRaises(RenderError):
            normalize_doc({"title": "x", "lead": ["不是字符串"]})
        with self.assertRaises(RenderError):
            normalize_doc({"title": "x", "redhead": "不是对象"})
        with self.assertRaises(RenderError):
            normalize_doc({"title": "x", "redhead": {"agency": "甲", "stamp": "乙"}})

    def test_careers_accept_three_spellings(self):
        doc = normalize_doc(RAW)
        self.assertEqual([e["content"] for e in doc["careers"]], ["甲大学电气工程专业学习", "丙公司工程师", "乙公司总裁"])
        self.assertEqual(doc["careers"][2]["period"], "2000年2月至今")

    def test_tags_strip_hash_and_name_derived(self):
        doc = normalize_doc(RAW)
        self.assertEqual(doc["tags"], ["乙公司"])
        self.assertEqual(doc["name"], "张三")

    def test_explicit_name_kept(self):
        self.assertEqual(normalize_doc({"title": "干部任前公示", "name": "张三"})["name"], "张三")

    def test_afterword_variants(self):
        self.assertIsNone(normalize_doc({"title": "x"})["afterword"])
        self.assertIsNone(normalize_doc({"title": "x", "afterword": None})["afterword"])
        self.assertEqual(normalize_doc({"title": "x", "afterword": ""})["afterword"], "")
        self.assertEqual(normalize_doc({"title": "x", "afterword": "自定义"})["afterword"], "自定义")
        self.assertEqual(normalize_doc({"title": "x", "afterword": ["一", " 二 "]})["afterword"], ["一", "二"])

    def test_normalize_is_idempotent(self):
        once = normalize_doc(RAW)
        self.assertEqual(normalize_doc(once), once)

    def test_load_doc_from_json_file_and_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "doc.json"
            path.write_text(json.dumps(RAW, ensure_ascii=False), encoding="utf-8")
            from_file = load_doc(path)
            from_str = load_doc(str(path))
        self.assertEqual(from_file, normalize_doc(RAW))
        self.assertEqual(from_str, from_file)
        self.assertEqual(load_doc(RAW), from_file)

    def test_load_doc_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RenderError):
                load_doc(Path(tmp) / "missing.json")
            bad = Path(tmp) / "bad.json"
            bad.write_text("{不是 json", encoding="utf-8")
            with self.assertRaises(RenderError):
                load_doc(bad)


class NormalizeCareerTests(unittest.TestCase):
    def test_dict_with_org_and_role(self):
        entry = normalize_career({"period": "2001年1月至今", "organization": "甲公司", "role": "总裁、董事"})
        self.assertEqual(entry["text"], "甲公司总裁、董事")
        self.assertEqual(entry["content"], "甲公司总裁、董事")
        self.assertEqual(entry["note"], "")

    def test_dict_with_text_only(self):
        entry = normalize_career({"period": "2001年至今", "text": "甲公司总裁"})
        self.assertEqual(entry["organization"], "")
        self.assertEqual(entry["content"], "甲公司总裁")

    def test_list_two_and_three_items(self):
        self.assertEqual(normalize_career(["2001年至今", "甲公司总裁"])["content"], "甲公司总裁")
        three = normalize_career(["2001年至今", "甲公司", "总裁"])
        self.assertEqual((three["organization"], three["role"], three["content"]), ("甲公司", "总裁", "甲公司总裁"))
        with self.assertRaises(RenderError):
            normalize_career(["只有一项"])

    def test_string_split_at_first_whitespace(self):
        entry = normalize_career("1984年－1992年　英伟达公司 工程师")
        self.assertEqual(entry["period"], "1984年－1992年")
        self.assertEqual(entry["content"], "英伟达公司 工程师")
        with self.assertRaises(RenderError):
            normalize_career("1984年－1992年英伟达公司工程师")

    def test_note_wrapped_in_fullwidth_parentheses(self):
        entry = normalize_career({"period": "2001年至今", "organization": "甲公司", "role": "总裁", "note": "2020年起兼任乙学会理事"})
        self.assertEqual(entry["note"], "（2020年起兼任乙学会理事）")
        self.assertEqual(entry["content"], "甲公司总裁（2020年起兼任乙学会理事）")
        already = normalize_career({"period": "2001年至今", "text": "甲公司总裁", "note": "（其间：在职学习）"})
        self.assertEqual(already["note"], "（其间：在职学习）")

    def test_conflicting_or_missing_fields_rejected(self):
        with self.assertRaises(RenderError):
            normalize_career({"period": "2001年至今", "organization": "甲公司", "role": "总裁", "text": "别的写法"})
        with self.assertRaises(RenderError):
            normalize_career({"organization": "甲公司", "role": "总裁"})
        with self.assertRaises(RenderError):
            normalize_career({"period": "2001年至今"})
        with self.assertRaises(RenderError):
            normalize_career({"period": "2001年至今", "organization": "甲公司", "where": "x"})
        with self.assertRaises(RenderError):
            normalize_career(42)

    def test_normalized_entry_can_be_normalized_again(self):
        entry = normalize_career({"period": "2001年至今", "organization": "甲公司", "role": "总裁"})
        self.assertEqual(normalize_career(entry), entry)

    def test_space_between_english_and_chinese(self):
        self.assertEqual(join_org_role("DST Global", "合伙人"), "DST Global 合伙人")
        self.assertEqual(join_org_role("星川科技", "Starbrook 产品负责人"), "星川科技 Starbrook 产品负责人")
        self.assertEqual(join_org_role("LSI Logic公司", "工程师"), "LSI Logic公司工程师")
        self.assertEqual(join_org_role("超威半导体公司（AMD）", "工程师"), "超威半导体公司（AMD）工程师")
        self.assertEqual(join_org_role("", "工程师"), "工程师")
        self.assertEqual(join_org_role("甲公司", ""), "甲公司")


class WidthAndWrapTests(unittest.TestCase):
    def test_text_width(self):
        self.assertEqual(text_width("汉字"), 2.0)
        self.assertEqual(text_width("1984"), 2.0)
        self.assertEqual(text_width("1984年"), 3.0)
        self.assertEqual(text_width("（AMD）"), 3.5)
        self.assertEqual(text_width(""), 0.0)

    def test_short_line_not_broken(self):
        entry = normalize_career({"period": "x", "organization": "英伟达公司", "role": "总裁、首席执行官、董事"})
        self.assertEqual(wrap_content(entry), ["英伟达公司总裁、首席执行官、董事"])

    def test_break_after_organization(self):
        entry = normalize_career({"period": "x", "organization": "超威半导体公司（AMD）", "role": "微处理器设计工程师、设计总监"})
        self.assertGreater(text_width(entry["content"]), LINE_LIMIT)
        self.assertEqual(wrap_content(entry), ["超威半导体公司（AMD）", "微处理器设计工程师、设计总监"])

    def test_break_before_note_and_after_comma(self):
        entry = normalize_career(
            {
                "period": "x",
                "organization": "英伟达公司",
                "role": "工程师",
                "note": "其间：1984年－1992年在斯坦福大学电气工程专业在职研究生学习，获理学硕士学位",
            }
        )
        lines = wrap_content(entry)
        self.assertEqual(lines[0], "英伟达公司工程师")
        self.assertTrue(lines[1].startswith("（其间："))
        self.assertTrue(lines[1].endswith("，"))
        self.assertEqual(lines[-1], "获理学硕士学位）")
        self.assertEqual("".join(lines), entry["content"])

    def test_greedy_packing_keeps_short_pieces_together(self):
        entry = normalize_career({"period": "x", "organization": "甲公司", "role": "总裁", "note": "2024年3月起兼任远望大学工程学院顾问委员会委员"})
        self.assertEqual(wrap_content(entry), ["甲公司总裁", "（2024年3月起兼任远望大学工程学院顾问委员会委员）"])

    def test_oversized_piece_kept_whole(self):
        long_role = "一" * 30
        entry = normalize_career({"period": "x", "organization": "甲公司", "role": long_role})
        self.assertEqual(wrap_content(entry), ["甲公司", long_role])

    def test_text_only_entry_breaks_after_comma(self):
        entry = normalize_career({"period": "x", "text": "一" * 12 + "，" + "二" * 12})
        self.assertEqual(wrap_content(entry), ["一" * 12 + "，", "二" * 12])

    def test_custom_limit(self):
        entry = normalize_career({"period": "x", "organization": "甲公司", "role": "总裁"})
        self.assertEqual(wrap_content(entry, limit=3), ["甲公司", "总裁"])

    def test_estimate_lines(self):
        self.assertEqual(estimate_lines(""), 1)
        self.assertEqual(estimate_lines("一" * 22), 1)
        self.assertEqual(estimate_lines("一" * 23), 2)
        self.assertEqual(estimate_lines("一" * 10, limit=4), 3)


class SmallHelperTests(unittest.TestCase):
    def test_derive_name(self):
        self.assertEqual(derive_name("张三简历"), "张三")
        self.assertEqual(derive_name("张三同志简历"), "张三")
        self.assertEqual(derive_name("在座谈会上的讲话"), "在座谈会上的讲话")
        self.assertEqual(derive_name("简历"), "简历")

    def test_first_sentence(self):
        self.assertEqual(first_sentence("一句。两句。"), "一句。")
        self.assertEqual(first_sentence("没有句号"), "没有句号")
        self.assertEqual(first_sentence(""), "")

    def test_is_salutation(self):
        self.assertTrue(is_salutation("同志们："))
        self.assertTrue(is_salutation("各位来宾、各位朋友："))
        self.assertFalse(is_salutation("今天的会议主要有三项议程："))
        self.assertFalse(is_salutation("同志们，大家好。"))
        self.assertTrue(is_salutation("各位元首、各位公职人员、各位企业家："))
        self.assertTrue(is_salutation("尊敬的各位嘉宾，女士们、先生们，朋友们："))
        self.assertFalse(is_salutation("具体如下："))


if __name__ == "__main__":
    unittest.main()
