"""两份写法规范 SKILL.md 的结构检查：Agent Skills 头、必有章节、参数卡、不带脚本和联网指令。"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = {
    "cadre-speech": {
        "sections": ["称谓", "结构", "段落功能", "句式", "保真与译名", "禁忌", "参数卡", "工作顺序", "待定项"],
        "prefix": "S",
        "min_rules": 25,
        "must_mention": ["同志们：", "下面，我讲", "一、", "不编造", "不假托", "不冒充", "万", "亿"],
    },
    "soe-newswire": {
        "sections": ["电头", "标题", "导语", "机构名、人名、职务", "引语处理", "结构与段落功能", "句式与用词", "禁忌", "参数卡", "工作顺序", "待定项"],
        "prefix": "N",
        "min_rules": 30,
        "must_mention": ["日电", "全称", "简称", "新华社译名", "倒金字塔", "据", "不编造", "不冒充", "政见社"],
    },
}


def parse_frontmatter(text: str) -> dict:
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    assert m, "缺 YAML 头"
    meta = {}
    for line in m.group(1).splitlines():
        if line.startswith(" ") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return meta


class SkillFileTest(unittest.TestCase):
    def test_each_skill(self):
        for name, spec in SKILLS.items():
            with self.subTest(skill=name):
                path = ROOT / "shared" / "skills" / name / "SKILL.md"
                text = path.read_text(encoding="utf-8")
                meta = parse_frontmatter(text)
                self.assertEqual(meta["name"], name)
                self.assertTrue(meta["description"])
                self.assertLessEqual(len(meta["description"]), 1024)
                self.assertNotIn("占位", meta["description"])
                self.assertNotIn("暂不要使用", meta["description"])
                self.assertEqual(meta.get("license"), "CC-BY-SA-4.0")

                headings = re.findall(r"^## \d+ (.+)$", text, re.M)
                for section in spec["sections"]:
                    self.assertTrue(any(section in h for h in headings), f"{name} 缺章节 {section}；现有 {headings}")

                rows = re.findall(rf"^\| ({spec['prefix']}\d\d) \| (.+?) \| (.+?) \| (.+?) \| (.+?) \|$", text, re.M)
                self.assertGreaterEqual(len(rows), spec["min_rules"], f"{name} 参数卡条数不够")
                ids = [r[0] for r in rows]
                self.assertEqual(ids, sorted(ids))
                self.assertEqual(len(ids), len(set(ids)))
                for row in rows:
                    self.assertRegex(row[4], r"P[012]", f"{name} {row[0]} 缺级别")
                levels = {lvl for row in rows for lvl in re.findall(r"P[012]", row[4])}
                self.assertEqual(levels, {"P0", "P1", "P2"})

                for phrase in spec["must_mention"]:
                    self.assertIn(phrase, text, f"{name} 缺 {phrase}")

                # 写法规范不带脚本、不带联网指令（SECURITY.md）。
                self.assertNotIn("```", text)
                self.assertNotRegex(text, r"https?://")
                self.assertNotRegex(text, r"\b(curl|wget|pip install|npm install|python )\b")
                # 示例人物、机构须虚构：示例段里不出现译名表里的真实公司。
                example = text.split("示例片段")[-1]
                for real in ("英伟达", "微软", "谷歌", "OpenAI", "苹果", "亚马逊"):
                    self.assertNotIn(real, example, f"{name} 示例段用了真实机构 {real}")

    def test_placeholder_status_cleared(self):
        for name in SKILLS:
            text = (ROOT / "shared" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertNotIn("（占位）", text)

    def test_no_links_commands_or_scripts_in_any_skill(self):
        # 全部写法规范都不带网址、运行命令和脚本（SECURITY.md，docs/公开前检查.md 第八节）。
        skills = ROOT / "shared" / "skills"
        command = re.compile(r"(?:^|`)\s*(?:\$\s*)?(?:python3?|py|pip3?|npm|npx|node|curl|wget|bash|sh|powershell|pwsh|cmd)(?:\.exe)?\s+[-\w]", re.M)
        for path in sorted(skills.glob("*/SKILL.md")):
            with self.subTest(skill=path.parent.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotRegex(text, r"https?://")
                self.assertNotRegex(text, command)
        scripts = [p.name for p in skills.rglob("*") if p.is_file() and p.suffix != ".md"]
        self.assertEqual(scripts, [])


if __name__ == "__main__":
    unittest.main()
