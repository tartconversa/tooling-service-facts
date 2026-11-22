"""换型可用性：合套核查、逐件余量不抵消、凭证工况门与占用门。"""
import unittest

from toolhistory.dispatch import required_points
from toolhistory.loader import load_dataset
from toolhistory.repository import ToolHistoryRepository

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


class DispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = ToolHistoryRepository.from_sources(ROOT / "sources")
        cls.verdicts = {v.work_order.id: v for v in cls.repo.evaluate_orders()}

    def _option(self, order_id, base, jaw):
        verdict = self.verdicts[order_id]
        for option in verdict.options:
            if option.base == base and option.jaw == jaw:
                return option
        self.fail(f"{order_id} 缺少构型 ({base},{jaw})")

    def test_wo4_dispatchable_only_virgin_kit3_candidate(self):
        verdict = self.verdicts["wo4"]
        self.assertTrue(verdict.dispatchable)
        good = self._option("wo4", "base3", "jaw3")
        self.assertTrue(good.dispatchable)
        self.assertTrue(good.base_avail.fits_certain and good.jaw_avail.fits_certain)
        # 其余候选都含边际/受阻件，不能因为某件余量大就抵消
        for option in verdict.options:
            if (option.base, option.jaw) != ("base3", "jaw3"):
                self.assertFalse(option.dispatchable)

    def test_high_grade_wo2_blocked_by_worn_jaw_and_pending_receipt(self):
        verdict = self.verdicts["wo2"]
        self.assertFalse(verdict.dispatchable)
        opt = self._option("wo2", "base1", "jaw4")
        # jaw4 最好够、最坏不够：边际件不得按确定可派
        self.assertTrue(opt.jaw_avail.fits_possible)
        self.assertFalse(opt.jaw_avail.fits_certain)
        self.assertTrue(any("占用/签收门失败" in r for r in opt.reasons))

    def test_kit_total_margin_cannot_offset_individual_failure(self):
        # jaw1 对 high 工单硬失败（最好余量也不够），即使 base1 余量大也不可派
        opt = self._option("wo3", "base2", "jaw1")
        self.assertTrue(opt.base_avail.fits_certain)
        self.assertFalse(opt.jaw_avail.fits_possible)
        self.assertFalse(opt.dispatchable)
        self.assertTrue(any("余量门失败" in r for r in opt.reasons))

    def test_release_certificate_gates_mode(self):
        # jaw1 的 overfit-r 只放行 low，wo5 是 high -> 凭证门失败
        opt = self._option("wo5", "base1", "jaw1")
        self.assertTrue(any("凭证门失败" in r and "low" in r for r in opt.reasons))

    def test_required_points_uses_mode_role_rates(self):
        rates = self.repo.dataset.rates
        self.assertEqual(required_points(rates, "jaw", "high", 10), 40)
        self.assertEqual(required_points(rates, "base", "low", 10), 10)
        with self.assertRaises(KeyError):
            required_points(rates, "jaw", "ultra", 1)

    def test_every_order_reports_explicit_reasons(self):
        for verdict in self.verdicts.values():
            for option in verdict.options:
                if not option.dispatchable:
                    self.assertTrue(option.reasons, "受阻构型必须给出可派用理由")


if __name__ == "__main__":
    unittest.main()
