"""寿命台账：世代、受限恢复点、放行凭证与 best/worst 余量。"""
import unittest

from toolhistory.ledger import build_all_ledgers, evaluate_certificate, remaining_for
from toolhistory.loader import load_dataset
from toolhistory.reconcile import reconcile_cycles

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


class LedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(ROOT / "sources")
        entries, _, _, _ = reconcile_cycles(
            cls.dataset.cycles, cls.dataset.legs, cls.dataset.rates, cls.dataset.parts)
        cls.entries = entries
        cls.ledgers = build_all_ledgers(cls.dataset.parts, cls.dataset.repairs, entries)

    def test_uncertain_usage_keeps_worst_below_best(self):
        jaw1 = self.ledgers["jaw1"]
        best, worst = jaw1.base_remaining()
        self.assertGreater(best, worst)
        self.assertTrue(jaw1.uncertain)
        # 最坏情形已超耗，必须显式呈现为负而不是截零
        self.assertLess(worst, 0)

    def test_restore_credit_is_scoped_to_configuration_and_mode(self):
        jaw1 = self.ledgers["jaw1"]
        # polish-j 恢复 20 点，仅对 (base2,jaw1) low 生效
        in_scope_best, in_scope_worst = remaining_for(jaw1, ("base2", "jaw1"), "low")
        base_best, base_worst = jaw1.base_remaining()
        self.assertEqual(in_scope_best, base_best + 20)
        # 工况不匹配拿不到恢复点
        _, high_worst = remaining_for(jaw1, ("base2", "jaw1"), "high")
        self.assertEqual(high_worst, base_worst)
        # 构型不匹配也拿不到
        _, other_cfg = remaining_for(jaw1, ("base1", "jaw1"), "low")
        self.assertEqual(other_cfg, base_worst)

    def test_restore_does_not_exceed_design_cap_at_effective_time(self):
        # 封顶按凭证生效时点已耗，而不是被后续补录挤没
        jaw1 = self.ledgers["jaw1"]
        credits = {c.repair_id: c.points for c in jaw1.credits}
        self.assertEqual(credits["polish-j"], 20)
        # 加上恢复点后的最好余量仍不得超过设计上限 210
        best, _ = remaining_for(jaw1, ("base2", "jaw1"), "low")
        self.assertLessEqual(best, jaw1.design)

    def test_release_gates_mode_but_adds_no_points(self):
        jaw2 = self.ledgers["jaw2"]
        high = evaluate_certificate(jaw2, ("base2", "jaw2"), "high")
        low = evaluate_certificate(jaw2, ("base2", "jaw2"), "low")
        self.assertTrue(high.approved and low.approved)
        # release 不产生恢复点
        self.assertEqual(jaw2.credits, ())
        # 构型不被任何凭证覆盖
        wrong = evaluate_certificate(jaw2, ("base9", "jaw2"), "high")
        self.assertFalse(wrong.approved)

    def test_overfit_release_only_allows_low_mode(self):
        jaw1 = self.ledgers["jaw1"]
        # overfit-r 在 (base1,jaw1) 只放行 low；high 工单不能用
        low = evaluate_certificate(jaw1, ("base1", "jaw1"), "low")
        high = evaluate_certificate(jaw1, ("base1", "jaw1"), "high")
        self.assertTrue(low.approved)
        self.assertFalse(high.approved)

    def test_part_without_service_history_is_natively_approved(self):
        base3 = self.ledgers["base3"]
        verdict = evaluate_certificate(base3, ("base3", "jaw3"), "high")
        self.assertTrue(verdict.approved)
        self.assertIsNone(verdict.governing)

    def test_history_preserves_all_usage_entries_with_provenance(self):
        jaw1_entries = [e for e in self.entries if e.part_id == "jaw1"]
        runs = {e.run for e in jaw1_entries}
        # jaw1 在 west 的 runA、跨界 runF，以及到 east 后的 runC/runE 都有经历，
        # 整套与构件历史都不随换型/借用重置
        self.assertEqual(runs, {"runA", "runC", "runE", "runF"})
        for entry in jaw1_entries:
            self.assertTrue(entry.sources)
            self.assertTrue(entry.configs, "归因必须携带来源构型")


if __name__ == "__main__":
    unittest.main()
