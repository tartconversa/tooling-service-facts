"""区间借用、交收回执与可承诺量（ATP）的相互制约。"""
import unittest

from toolhistory.loader import load_dataset
from toolhistory.loans import (
    committable_inventory, find_commitment_conflicts, is_available,
    part_holdings, received_parts,
)

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


class LoanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(ROOT / "sources")
        cls.loans, cls.acks = cls.dataset.loans, cls.dataset.acks

    def test_accepted_part_is_occupied_until_returned(self):
        # hand1: jaw1 在 56 签收、98 返还
        holdings = part_holdings(self.loans, self.acks, "jaw1")
        occupied = [h for h in holdings if h.status == "occupied"]
        self.assertTrue(any(h.start == 56 and h.end == 98 for h in occupied))
        ok, blockers = is_available(self.loans, self.acks, "jaw1", 60, 90)
        self.assertFalse(ok)
        self.assertTrue(blockers)
        # 返还后可复用
        ok_after, _ = is_available(self.loans, self.acks, "jaw1", 100, 102)
        self.assertTrue(ok_after)

    def test_refused_part_does_not_become_unavailable(self):
        # 批次 2 视图：hand2 的 base1 在 104 被拒收，不形成占用
        ds2 = self.dataset.upto(2)
        receipts = received_parts(next(l for l in ds2.loans if l.id == "hand2"), ds2.acks)
        self.assertEqual(receipts["base1"], "refused")
        holdings = part_holdings(ds2.loans, ds2.acks, "base1")
        self.assertTrue(
            all(h.status != "occupied" for h in holdings if h.reference == "hand2"))

    def test_late_acceptance_replaces_refusal_and_feeds_back_inventory(self):
        # 批次 2：base1 被拒收，105-109 仍可承诺；批次 3 补 ack6，110 签收后占用
        ds2 = self.dataset.upto(2)
        ok_refused, _ = is_available(ds2.loans, ds2.acks, "base1", 105, 109)
        self.assertTrue(ok_refused)
        ok_during, blockers = is_available(self.loans, self.acks, "base1", 111, 120)
        self.assertFalse(ok_during)
        self.assertTrue(any("hand2" in b for b in blockers))

    def test_requested_loan_does_not_hard_commit(self):
        # hand3/hand4 是 requested，候选状态不允许硬占用同一批件
        for loan_id in ("hand3", "hand4"):
            loan = next(l for l in self.loans if l.id == loan_id)
            self.assertEqual(loan.state, "requested")
        ok, _ = is_available(self.loans, self.acks, "base2", 112, 120)
        self.assertTrue(ok)
        view = committable_inventory(self.loans, self.acks, self.dataset.parts, 115)
        self.assertEqual(view["base2"]["status"], "pending-out")

    def test_partial_receipt_updates_remaining_committable(self):
        # 批次 2 视图：hand2 只接到 jaw4（占用），base1 被拒收后仍可另行承诺。
        # 终态下 ack6 又签收 base1，故这里固定在 as-of 批次 2 验证部分接收。
        ds2 = self.dataset.upto(2)
        view = committable_inventory(ds2.loans, ds2.acks, ds2.parts, 115)
        self.assertEqual(view["jaw4"]["status"], "occupied")
        self.assertNotEqual(view["base1"]["status"], "occupied")
        # 终态视图下 base1 已补签收，115 处于占用
        final_view = committable_inventory(self.loans, self.acks, self.dataset.parts, 115)
        self.assertEqual(final_view["base1"]["status"], "occupied")

    def test_no_two_confirmed_loans_hold_same_part_simultaneously(self):
        # 样本中 confirmed 安排经回执后不产生真实重叠硬占用
        conflicts = find_commitment_conflicts(self.loans, self.acks)
        self.assertEqual(conflicts, [])

    def test_synthetic_overlapping_confirmed_loans_are_detected(self):
        from toolhistory.models import Loan
        synthetic = list(self.loans) + [
            Loan("handX", "woX", "kit9", ("base2", "jaw2"), 60, 70, "confirmed", 9),
            Loan("handY", "woY", "kit9", ("base2", "jaw2"), 65, 75, "confirmed", 9),
        ]
        conflicts = find_commitment_conflicts(synthetic, self.acks)
        parts = {c["part"] for c in conflicts}
        self.assertIn("base2", parts)
        self.assertIn("jaw2", parts)


if __name__ == "__main__":
    unittest.main()
