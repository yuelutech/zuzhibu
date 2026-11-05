"""首发基准导入：从仓库里的各局原料导入到临时数据目录，查人数、归局、分级和幂等。不取 aipolis（私有，测试不依赖）。"""

import tempfile
import unittest
from pathlib import Path

from zuzhibu import bureaus, importer
from zuzhibu.store import Store


def _rows(path):
    return len(importer._csv(path)) if path.exists() else 0


N_SV, N_OTHERS, N_WH = _rows(importer.SV_ROSTER), _rows(importer.OTHERS_ROSTER), _rows(importer.WH_ROSTER)
N_ALL = 538 + N_SV + N_OTHERS + N_WH


class ImporterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.tmp.name)).load()
        cls.report = importer.build(cls.store, "2026-10-17", None)
        cls.store.load()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_counts_and_bureaus(self):
        self.assertEqual(self.report["notes"], [])
        self.assertEqual(self.report["written"], N_ALL)
        by = self.report["by_bureau"]
        self.assertEqual(by[bureaus.CONGRESS], 536)
        self.assertEqual(by[bureaus.AI], N_SV)
        self.assertEqual(by[bureaus.MILITARY] + by[bureaus.JUSTICE] + by[bureaus.WHITEHOUSE], N_OTHERS + N_WH + 2)

    def test_president_and_vice_president_go_to_whitehouse(self):
        for name in ("Donald Trump", "JD Vance"):
            rec = self.store.find_by_name(name)[0]
            self.assertEqual(rec["bureau"], bureaus.WHITEHOUSE, name)
            self.assertIn(rec["tier"], ("gold", "reverified"), name)   # Care 旧金标按现行规则重新取证后记 reverified

    def test_independent_agencies_assignment(self):
        expected = {"Joshua M. Rudd": bureaus.MILITARY, "Kash Patel": bureaus.JUSTICE, "Sonia Sotomayor": bureaus.JUSTICE,
                    "Jay Clayton": bureaus.WHITEHOUSE, "John L. Ratcliffe": bureaus.WHITEHOUSE, "Kevin Warsh": bureaus.WHITEHOUSE,
                    "Brendan Carr": bureaus.WHITEHOUSE}
        for name, bureau in expected.items():
            self.assertEqual(self.store.find_by_name(name)[0]["bureau"], bureau, name)

    def test_ambiguous_government_titles_stay_english(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roster.csv"
            path.write_text("english_name,position_en,organization_en,since,official_bio_url,official_portrait_url,notes\n"
                            "Lan Yu,Governor,Board of Governors of the Federal Reserve System,,https://www.federalreserve.gov/x,,\n",
                            encoding="utf-8")
            rec = importer.roster_records(path, {}, importer.glossary_orgs(), importer.glossary_titles())[0]
        self.assertEqual(rec["_roles"][0]["role"], "Governor", "美联储的 Governor 不能按职务表译成州长")
        self.assertTrue(any("待译" in u for u in rec["_unknowns"]))

    def test_reverified_congress_records(self):
        rec = self.store.find_by_external("bioguide", "A000055")
        self.assertEqual(rec["tier"], "reverified")
        self.assertGreaterEqual(rec["data_cutoff"], "2026-10-17")

    def test_second_run_writes_nothing(self):
        again = importer.build(self.store, "2026-10-18", None)
        self.assertEqual(again["written"], 0)
        self.assertEqual(again["unchanged"], N_ALL)


if __name__ == "__main__":
    unittest.main()
