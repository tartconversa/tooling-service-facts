"""源数据加载与结构/引用校验。"""
import json
import unittest
from pathlib import Path

from toolhistory.loader import DataError, load_dataset

ROOT = Path(__file__).resolve().parents[1]


class LoaderTests(unittest.TestCase):
    def test_loads_all_three_deliveries(self):
        dataset = load_dataset(ROOT / "sources")
        self.assertEqual(dataset.intakes, [1, 2, 3])
        self.assertEqual(len(dataset.parts), 7)
        self.assertTrue(dataset.legs and dataset.cycles and dataset.repairs)

    def test_upto_isolation(self):
        dataset = load_dataset(ROOT / "sources")
        first = dataset.upto(1)
        self.assertEqual(first.intakes, [1])
        self.assertEqual(first.repairs, [])
        self.assertEqual({r.id for r in first.cycles}, {"west1", "east1", "shift1"})

    def test_unknown_part_reference_is_rejected(self, tmp_path=None):
        import tempfile
        from toolhistory.loader import load_dataset
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            schema = json.loads((ROOT / "source-fields.json").read_text(encoding="utf-8"))
            (tmp / "source-fields.json").write_text(json.dumps(schema), encoding="utf-8")
            src = tmp / "sources"
            src.mkdir()
            (src / "01-parts.json").write_text(json.dumps({
                "intake": 1, "table": "parts",
                "records": [{"id": "p1", "role": "base", "model": "M",
                             "design": 10, "owner": "x"}]}, ensure_ascii=False),
                encoding="utf-8")
            (src / "01-legs.json").write_text(json.dumps({
                "intake": 1, "table": "legs",
                "records": [{"id": "l1", "line": "x", "tool": "t", "span": [0, 5],
                             "base": "ghost", "jaw": "p1", "grade": "high"}]},
                ensure_ascii=False), encoding="utf-8")
            for table, records in {
                "cycle": [], "rates": [], "repairs": [],
                "work_orders": [], "loans": [], "acks": [],
            }.items():
                (src / f"01-{table}.json").write_text(
                    json.dumps({"intake": 1, "table": table, "records": records}),
                    encoding="utf-8")
            with self.assertRaises(DataError):
                load_dataset(src)

    def test_bad_range_is_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            schema = json.loads((ROOT / "source-fields.json").read_text(encoding="utf-8"))
            (tmp / "source-fields.json").write_text(json.dumps(schema), encoding="utf-8")
            src = tmp / "sources"
            src.mkdir()
            (src / "01-parts.json").write_text(json.dumps({
                "intake": 1, "table": "parts", "records": []}), encoding="utf-8")
            (src / "01-legs.json").write_text(json.dumps({
                "intake": 1, "table": "legs",
                "records": [{"id": "l1", "line": "x", "tool": "t", "span": [9, 5],
                             "base": "b", "jaw": "j", "grade": "high"}]},
                ensure_ascii=False), encoding="utf-8")
            for table, records in {
                "cycle": [], "rates": [], "repairs": [],
                "work_orders": [], "loans": [], "acks": [],
            }.items():
                (src / f"01-{table}.json").write_text(
                    json.dumps({"intake": 1, "table": table, "records": records}),
                    encoding="utf-8")
            with self.assertRaises(DataError):
                load_dataset(src)


if __name__ == "__main__":
    unittest.main()
