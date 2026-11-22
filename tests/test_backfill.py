"""迟到补录的差额、as-of 回放与三分类验证。"""
import unittest

from toolhistory.backfill import backfill_impact
from toolhistory.loader import load_dataset
from toolhistory.repository import ToolHistoryRepository

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


class BackfillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.full = load_dataset(ROOT / "sources")

    def test_intake3_brings_uncertain_usage_to_affected_parts(self):
        impact = backfill_impact(self.full, 3)
        self.assertEqual(set(impact.new_cycle_sources), {"east3", "east4", "west3"})
        # 跨界/重叠补录命中的构件都要进入不确定桶
        self.assertIn("jaw1", impact.uncertain_parts)
        self.assertIn("jaw2", impact.uncertain_parts)
        for pid in impact.changed_parts:
            self.assertIn(pid, impact.uncertain_parts)

    def test_executed_loans_are_never_silently_rewritten(self):
        impact3 = backfill_impact(self.full, 3)
        # hand1/hand2 都已有 accepted，历史必须保留，只能出现在 preserved
        self.assertIn("hand1", impact3.executed_loans_preserved)
        self.assertIn("hand2", impact3.executed_loans_preserved)
        self.assertNotIn("hand1", impact3.needs_reconfirm_loans)
        self.assertNotIn("hand2", impact3.needs_reconfirm_loans)

    def test_unissued_requests_get_reconfirmed_or_blocked(self):
        impact3 = backfill_impact(self.full, 3)
        # hand3/hand4 尚未确认执行，且其部件核算被补录改变
        self.assertEqual(set(impact3.needs_reconfirm_loans), {"hand3", "hand4"})
        # 受影响工单必须显式列出差额
        self.assertIn("wo3", impact3.blocked_or_shrunk_orders)

    def test_three_buckets_are_distinguishable(self):
        impact2 = backfill_impact(self.full, 2)
        impact3 = backfill_impact(self.full, 3)
        # 凭证确证：jaw2/jaw4 有放行凭证，独立于不确定桶存在
        self.assertIn("jaw2", impact3.certified_parts)
        self.assertIn("jaw4", impact3.certified_parts)
        # 批次 2 下 base1 被拒收，不属于等待签收；批次 3 签收后也不属于
        self.assertNotIn("base1", impact2.awaiting_parts)
        self.assertNotIn("base1", impact3.awaiting_parts)
        # 三类桶语义可同时呈现（不确定与凭证可重叠，等待签收独立）
        self.assertTrue(impact3.uncertain_parts and impact3.certified_parts)

    def test_as_of_snapshot_does_not_see_future_records(self):
        prior = self.full.upto(2)
        prior_repo = ToolHistoryRepository(prior)
        current_repo = ToolHistoryRepository(self.full)
        # 补录前 jaw2 的最坏余量必须好于补录后（补录只能收紧不能放宽最坏值）
        before = prior_repo.ledger("jaw2").used_hi
        after = current_repo.ledger("jaw2").used_hi
        self.assertLessEqual(before, after)
        # 补录前不存在 runE/runF
        runs_before = {r.run for r in prior_repo.run_reconciliation()}
        self.assertNotIn("runE", runs_before)
        self.assertNotIn("runF", runs_before)

    def test_backfill_tightens_dispatchability_but_keeps_history(self):
        prior = self.full.upto(2)
        prior_repo = ToolHistoryRepository(prior)
        current_repo = ToolHistoryRepository(self.full)
        prior_dispatch = {v.work_order.id: v.dispatchable
                          for v in prior_repo.evaluate_orders()}
        current_dispatch = {v.work_order.id: v.dispatchable
                            for v in current_repo.evaluate_orders()}
        # 补录不能把不可派变成可派（信息增加只会收紧）
        for order_id, was in prior_dispatch.items():
            if not was:
                self.assertFalse(current_dispatch[order_id],
                                 f"{order_id} 不应因补录而放宽为可派")
        # 已执行借用在两个快照里都能查到完整回执链
        history_after = current_repo.part_history("jaw1")
        self.assertTrue(any(h.reference == "hand1" for h in history_after["holdings"]))


if __name__ == "__main__":
    unittest.main()
