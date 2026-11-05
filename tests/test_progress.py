"""立即更新的进度：6 步提示语、结束提示语一字不差照审定的文案稿；进度条只往前走；停止文件。"""

import tempfile
import unittest
from pathlib import Path

from zuzhibu import progress


class ProgressTest(unittest.TestCase):
    def test_step_texts_follow_the_approved_copy(self):
        def text(step, **detail):
            return progress.text({"state": "running", "step": step, "detail": detail})

        self.assertEqual(text(1), "正在连接接口……")
        self.assertEqual(text(2), "正在查询公开信息……（白宫、国会、联邦公报、证券交易委员会等官方来源）")
        self.assertEqual(text(3, group="白宫", index=2, total=5), "正在深度思考……（逐项研判人事变动　白宫 2/5）")
        self.assertEqual(text(4, index=3, total=12), "正在逐条核实……（第 3 条，共 12 条）")
        self.assertEqual(text(5, people=2, index=1, total=2), "正在获取简历……（新任人员 2 人）")
        self.assertEqual(text(6, people=3), "正在更新简历……（3 人）")
        self.assertEqual(progress.text({"state": "done", "step": 6}), "", "结束后不显示步骤")

    def test_end_messages_follow_the_approved_copy(self):
        def message(state, events=0, resumes=0, error=None):
            return progress.message({"state": state, "result": {"events": events, "resumes": resumes}, "error": error})

        self.assertEqual(message("done", 5, 3), "更新完成。新增人事动态 5 条，更新简历 3 份。")
        self.assertEqual(message("done"), "更新完成。暂无新的人事动态。")
        self.assertEqual(message("stopped", 3), "更新已停止。已核实的 3 条人事动态已保存。")
        self.assertEqual(message("failed", error="接口额度不足"), "更新未完成：接口额度不足。已核实的结果已保存。")

    def test_percent_only_moves_forward(self):
        seen = []
        for step, detail in [(1, {}), (2, {}), (3, {"index": 1, "total": 5}), (3, {"index": 5, "total": 5}),
                             (4, {"index": 1, "total": 12}), (4, {"index": 12, "total": 12}), (5, {"index": 2, "total": 2}),
                             (6, {"people": 3})]:
            seen.append(progress.percent({"state": "running", "step": step, "detail": detail}))
        self.assertEqual(seen, sorted(seen))
        self.assertLess(seen[-1], 100)
        self.assertEqual(progress.percent({"state": "stopped", "step": 4}), 100)

    def test_progress_file_and_stop_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, stop = Path(tmp) / "update" / "progress.json", Path(tmp) / "update" / "stop"
            report = progress.Progress(path, stop)
            report.step(3, group="国会", index=3, total=5)
            report.result(events=2)
            self.assertFalse(report.stop_requested())
            stop.write_text("x", encoding="utf-8")
            self.assertTrue(report.stop_requested())
            report.finish("stopped")
            view = progress.view(progress.read(path))
            self.assertEqual((view["state"], view["step"], view["percent"]), ("stopped", 3, 100))
            self.assertEqual(view["message"], "更新已停止。已核实的 2 条人事动态已保存。")
            self.assertEqual(progress.read(Path(tmp) / "none.json"), {})


if __name__ == "__main__":
    unittest.main()
