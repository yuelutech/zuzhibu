"""照片署名 zuzhibu/portraits.credit：只有 CC BY 照片要署名，依据登记表许可一栏；用临时数据目录和虚构作者测。"""

import csv
import tempfile
import unittest
from pathlib import Path

from zuzhibu import portraits


class CreditTest(unittest.TestCase):
    def test_credit_from_license(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "portraits")
            folder.mkdir()
            with (folder / "index.csv").open("w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh, lineterminator="\n")
                w.writerow(["person_id", "file", "license"])
                w.writerow(["a", "a.jpg", "CC BY 2.0：某摄影师（某网站），维基共享资源 File:a.jpg；本图已裁切、缩放"])
                w.writerow(["b", "b.jpg", "公有领域：美国政府作品"])
                w.writerow(["c", "c.jpg", "公有领域或 CC BY 3.0：白宫网站版权页"])
                w.writerow(["d", "d.jpg", "CC BY-SA 4.0：某作者，维基共享资源 File:d.jpg；改动：抠图、换蓝白渐变底，裁成 3:4 头肩像"])
            with_file = {"portrait": {"file": "x.jpg"}}
            self.assertEqual(portraits.credit(dict(with_file, id="a"), tmp), "照片：某摄影师（某网站），CC BY 2.0，有裁切")
            self.assertEqual(portraits.credit(dict(with_file, id="d"), tmp), "照片：某作者，CC BY-SA 4.0，已抠图换底、裁切")
            self.assertIsNone(portraits.credit(dict(with_file, id="b"), tmp))
            self.assertIsNone(portraits.credit(dict(with_file, id="c"), tmp))
            self.assertIsNone(portraits.credit({"id": "a", "portrait": {"url": "https://example.com/a.jpg"}}, tmp))
            self.assertIsNone(portraits.credit({"id": "z"}, tmp))

    def test_export_photo_only_from_repo(self):
        # 导出只内嵌仓库里的小图；没有小图的不放照片，不引用官网原图。
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "portraits")
            folder.mkdir()
            (folder / "a.jpg").write_bytes(b"jpeg")
            self.assertTrue(portraits.data_uri({"portrait": {"file": "a.jpg"}}, tmp).startswith("data:image/jpeg;base64,"))
            self.assertIsNone(portraits.data_uri({"portrait": {"url": "https://example.com/x.jpg"}}, tmp))


if __name__ == "__main__":
    unittest.main()
