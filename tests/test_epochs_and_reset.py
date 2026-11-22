"""换件世代与计数重置的针对性验证（合成数据，不依赖样本）。"""
import unittest

from toolhistory.ledger import build_ledger
from toolhistory.models import Part, Repair, UsageEntry
from toolhistory.reconcile import _reconcile_run
from toolhistory.models import CycleRow


def _usage(part, run, points, start, certain=True, config=("b", "j")):
    return UsageEntry(
        part_id=part, run=run, mode="high",
        cycles_lo=points, cycles_hi=points,
        points_lo=points, points_hi=points,
        sources=(f"{run}-src",), span=(start, start + 1),
        generation="g", certain=certain, note="t",
        legs=("leg",), configs=(config,))


class ReplaceEpochTests(unittest.TestCase):
    def test_replace_opens_new_epoch_and_new_part_starts_fresh(self):
        part = Part("j", "jaw", "J7", 100, "east", 1)
        repairs = [
            Repair("r1", "j", "restore", 10, 20, ("b", "j"), ("high",), 1),
            Repair("swap", "j", "replace", 0, 50, ("b", "j"), ("high",), 2),
        ]
        usages = [
            _usage("j", "old", 80, 10),   # 旧世代耗用
            _usage("j", "new", 30, 60),   # 换件后新世代耗用
        ]
        ledger = build_ledger(part, repairs, usages)
        # 两个世代，新世代从新品 design=100 起算
        self.assertEqual(len(ledger.epochs), 2)
        self.assertEqual(ledger.epochs[1].opened_by, "swap")
        # 当前余量只反映新世代的 30 耗用，旧世代的 80 不跟随重置到新件
        best, worst = ledger.base_remaining()
        self.assertEqual((best, worst), (70, 70))
        # 旧世代的 restore 凭证不延续为新世代恢复点
        self.assertEqual(ledger.credits, ())

    def test_usage_before_replacement_kept_in_history_but_not_current(self):
        part = Part("j", "jaw", "J7", 100, "east", 1)
        repairs = [Repair("swap", "j", "replace", 0, 50, ("b", "j"), ("high",), 1)]
        usages = [_usage("j", "old", 95, 10), _usage("j", "new", 10, 60)]
        ledger = build_ledger(part, repairs, usages)
        # 即使旧世代已近耗尽，新件仍有完整设计寿命
        self.assertEqual(ledger.base_remaining(), (90, 90))
        self.assertEqual(ledger.used_hi, 10)


class CounterResetTests(unittest.TestCase):
    def test_backward_endpoint_is_flagged_as_reset(self):
        row = CycleRow("c1", "east", "kit", "runX", 0, 10, "g", 80, 5,
                       "high", None, 1)
        recs, conflicts = _reconcile_run("runX", [row])
        self.assertTrue(any(c.code == "counter-reset" for c in conflicts))
        # 倒退后按重置后的正读数计，不产生负循环
        self.assertGreaterEqual(recs[0].cycles_lo, 0)

    def test_generation_change_splits_independent_totals(self):
        rows = [
            CycleRow("c1", "east", "kit", "runX", 0, 10, "g1", 0, 12,
                     "high", None, 1),
            CycleRow("c2", "east", "kit", "runX", 10, 20, "g2", 0, 7,
                     "high", None, 1),
        ]
        recs, conflicts = _reconcile_run("runX", rows)
        self.assertTrue(any(c.code == "generation-change" for c in conflicts))
        # 两代独立累计后相加 = 19，而不是只取某个端点
        self.assertEqual(sum(r.cycles_hi for r in recs), 19)


if __name__ == "__main__":
    unittest.main()
