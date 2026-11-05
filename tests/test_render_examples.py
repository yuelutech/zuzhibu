"""shared/render/examples/：样例输入能读、生成样例与渲染器输出一致。"""

import json
import unittest

from shared.render import build_examples
from shared.render.build_examples import EXAMPLES_DIR, JOBS, docx_available
from shared.render.common import load_doc


class ExampleInputTests(unittest.TestCase):
    def test_inputs_load_and_are_marked_fictitious(self):
        for name in ("cadre.json", "politician.json", "speech.json"):
            raw = json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))
            self.assertIn("虚构", raw.get("_comment", ""), name)
            doc = load_doc(EXAMPLES_DIR / name)
            self.assertTrue(doc["title"], name)

    def test_photo_placeholder_exists_and_is_plain_svg(self):
        svg = (EXAMPLES_DIR / "portrait.svg").read_text(encoding="utf-8")
        self.assertTrue(svg.lstrip().startswith("<svg"))
        self.assertNotIn("<text", svg)
        self.assertNotIn("@font-face", svg)
        for name in ("cadre.json", "politician.json"):
            self.assertEqual(load_doc(EXAMPLES_DIR / name)["photo"], "portrait.svg")

    def test_politician_sample_has_separate_current_paragraph_and_two_continuation_pages(self):
        from shared.render.card_html import paginate

        doc = load_doc(EXAMPLES_DIR / "politician.json")
        self.assertTrue(doc["current"].startswith("现任"))
        self.assertEqual(len(paginate(doc)), 2)


class GeneratedSampleTests(unittest.TestCase):
    def test_every_job_has_an_output_file(self):
        for _, kind, output in JOBS:
            if kind == "docx" and not docx_available():
                continue
            self.assertTrue((EXAMPLES_DIR / output).exists(), output)

    def test_generated_samples_are_current(self):
        stale = build_examples.build(check=True)
        self.assertEqual(stale, [], "样例过期，运行 python -m shared.render.build_examples 重建：" + "、".join(stale))

    def test_check_mode_does_not_write(self):
        before = {path.name: path.stat().st_mtime_ns for path in EXAMPLES_DIR.iterdir()}
        build_examples.build(check=True)
        after = {path.name: path.stat().st_mtime_ns for path in EXAMPLES_DIR.iterdir()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
