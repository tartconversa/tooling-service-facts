"""核对源交付的结构和原件是否完整随附。"""
import json
from pathlib import Path
import unittest

from source_inventory import iter_deliveries


ROOT = Path(__file__).resolve().parents[1]


class SourceDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.deliveries = list(iter_deliveries(ROOT / "sources"))

    def test_partition_keys(self):
        expected = {path.stem for path in (ROOT / "sources").glob("*.json")}
        actual = {f"{row['intake']:02d}-{row['table']}" for row in self.deliveries}
        self.assertEqual(actual, expected)
        self.assertGreater(len({row["intake"] for row in self.deliveries}), 1)

    def test_records_match_declared_fields(self):
        schema = json.loads((ROOT / "source-fields.json").read_text(encoding="utf-8"))
        self.assertEqual({row["table"] for row in self.deliveries}, set(schema))
        seen = set()
        for delivery in self.deliveries:
            table = delivery["table"]
            for record in delivery["records"]:
                key = (table, record["id"])
                self.assertNotIn(key, seen)
                seen.add(key)
                self.assertEqual(set(record), set(schema[table]))
                for name, kind in schema[table].items():
                    value = record[name]
                    if value is None:
                        continue
                    if kind in {"range", "file_ticks"}:
                        self.assertEqual(len(value), 2)
                        self.assertLess(value[0], value[1])
                    elif kind == "int":
                        self.assertIs(type(value), int)
                    elif kind == "text":
                        self.assertIsInstance(value, str)
                    elif kind in {"items", "ints"}:
                        self.assertIsInstance(value, list)
