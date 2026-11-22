"""计数覆盖证明、镜像去重、重置与跨装配界限的核算验证。"""
import unittest

from toolhistory.loader import load_dataset
from toolhistory.reconcile import reconcile_cycles

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


class ReconcileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(ROOT / "sources")
        cls.entries, cls.runs, cls.conflicts, cls.mirrors = reconcile_cycles(
            cls.dataset.cycles, cls.dataset.legs, cls.dataset.rates, cls.dataset.parts)

    def _entry(self, part_id, run):
        found = [e for e in self.entries if e.part_id == part_id and e.run == run]
        self.assertEqual(len(found), 1, f"{part_id}/{run} 应恰好一条归因")
        return found[0]

    def test_mirror_forward_is_never_double_counted(self):
        # shift1 是 east1 的同站转发，只能在 mirror 出处里，不产生独立耗用
        self.assertEqual(self.mirrors["east1"], ("shift1",))
        run_b = next(r for r in self.runs if r.run == "runB")
        self.assertEqual((run_b.cycles_lo, run_b.cycles_hi), (44, 44))
        east1_usage = [e for e in self.entries if "east1" in e.sources]
        for entry in east1_usage:
            self.assertNotIn("shift1", entry.sources)

    def test_same_run_overlap_keeps_bounds_not_sum(self):
        # runC: east3=13 与 east2=18 同代重叠，重叠窗物理循环被两台各记一次，
        # 不能相加；覆盖证明下界=上界=较大读数 18
        run_c = next(r for r in self.runs if r.run == "runC")
        self.assertEqual((run_c.cycles_lo, run_c.cycles_hi), (18, 18))
        # 总量确定，但重叠段使到构件的精确映射不可证
        self.assertFalse(run_c.certain)
        codes = {c.code for c in self.conflicts}
        self.assertIn("same-run-overlap", codes)

    def test_cross_assembly_unknown_usage_not_assigned_to_new_part(self):
        # east3[49,75) 跨 l2/l3 边界（55）：l3 内的新件 jaw2 下界必须为 0；
        # jaw1 在 l3 内确定吃到 east2 的 18，跨界 east3 的分配不可证。
        jaw1 = self._entry("jaw1", "runC")
        jaw2 = self._entry("jaw2", "runC")
        self.assertEqual(jaw1.cycles_lo, 18)       # east2 的 18 完全在 l3 内
        self.assertEqual(jaw1.cycles_hi, 18)       # 片和受 run 覆盖证明约束
        self.assertEqual(jaw2.cycles_lo, 0)        # 跨界用量不臆断给新件
        self.assertEqual(jaw2.cycles_hi, 13)       # 最差时 east3 的 13 全落新件
        self.assertFalse(jaw2.certain)
        codes = {c.code for c in self.conflicts}
        self.assertIn("cross-assembly", codes)

    def test_points_follow_mode_and_role_rates(self):
        # runB high 完全在 l2(base2,jaw2)：base 44*2，jaw 44*4
        base2 = self._entry("base2", "runB")
        jaw2 = self._entry("jaw2", "runB")
        self.assertEqual(base2.points_lo, base2.points_hi)
        self.assertEqual(base2.points_hi, 44 * 2)
        self.assertEqual(jaw2.points_hi, 44 * 4)
        # jaw1 在 runB 区间尚未装上 kit2，不应产生任何 runB 归因
        self.assertFalse(any(e.run == "runB" for e in self.entries if e.part_id == "jaw1"))

    def test_mode_exceeds_leg_grade_is_flagged(self):
        codes = {c.code for c in self.conflicts}
        self.assertIn("mode-exceeds-grade", codes)
        messages = [c.message for c in self.conflicts if c.code == "mode-exceeds-grade"]
        self.assertTrue(any("west2" in m for m in messages))
        self.assertTrue(any("east4" in m for m in messages))

    def test_every_usage_entry_keeps_provenance(self):
        for entry in self.entries:
            self.assertTrue(entry.sources, "归因条目必须保留来源出处")
            self.assertIsInstance(entry.span, tuple)
            self.assertLessEqual(entry.points_lo, entry.points_hi)
            self.assertLessEqual(entry.cycles_lo, entry.cycles_hi)


if __name__ == "__main__":
    unittest.main()
