"""译名表同步 tools/sync_glossary.py：母本换名、换行后写进副本，母本不动；用虚构姓名和临时目录测。"""

import tempfile
import unittest
from pathlib import Path

from tools import sync_glossary as sg


class SyncTest(unittest.TestCase):
    def test_sync_and_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp, "母本"), Path(tmp, "副本")
            source.mkdir()
            target.mkdir()
            rows = "﻿english_name,全称,decided_by\r\nTide Office,司潮署,某维护者\r\nReef Office,护礁署,待某维护者定\r\n".encode("utf-8")
            for table in sg.TABLES:
                (source / table).write_bytes(rows)
            self.assertEqual(sg.sync(source, "某维护者", target, check=True), list(sg.TABLES))
            self.assertEqual(sg.sync(source, "某维护者", target), list(sg.TABLES))
            copy = (target / sg.TABLES[0]).read_bytes()
            self.assertEqual(copy, "﻿english_name,全称,decided_by\nTide Office,司潮署,AI政见\nReef Office,护礁署,待AI政见定\n".encode("utf-8"))
            self.assertEqual((source / sg.TABLES[0]).read_bytes(), rows)
            self.assertEqual(sg.sync(source, "某维护者", target, check=True), [])
            with self.assertRaises(ValueError):
                sg.sync(source, " ", target)


if __name__ == "__main__":
    unittest.main()
