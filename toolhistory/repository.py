"""履历仓门面：一次性构建并提供全部查询。

查询能力对应交付要求：
- part_history     构件历次核算（世代、耗用条目、维修凭证、余量）
- run_reconciliation / conflicts / mirrors  计数出处与覆盖证明
- evaluate_orders  工单可派用理由（合套、逐件余量、凭证/占用/签收门）
- holdings / inventory / commitment_conflicts  借用占用与可承诺库存
- backfill         迟到补录的差额与受影响安排
"""
from __future__ import annotations

from pathlib import Path

from .backfill import backfill_impact
from .dispatch import evaluate_all
from .ledger import build_all_ledgers, evaluate_certificate, remaining_for
from .loader import Dataset, load_dataset
from .loans import (
    committable_inventory, find_commitment_conflicts, is_available,
    part_holdings, received_parts,
)
from .models import BackfillImpact, OrderVerdict, PartLedger, UsageEntry
from .reconcile import reconcile_cycles


class ToolHistoryRepository:
    def __init__(self, dataset: Dataset):
        self.dataset = dataset
        self.usages, self.run_reconciles, self.conflicts, self.mirrors = reconcile_cycles(
            dataset.cycles, dataset.legs, dataset.rates, dataset.parts)
        self.ledgers = build_all_ledgers(dataset.parts, dataset.repairs, self.usages)
        self.order_verdicts = evaluate_all(
            dataset.work_orders, dataset.legs, dataset.parts, self.ledgers,
            dataset.rates, dataset.loans, dataset.acks)
        self.commitment_conflicts = find_commitment_conflicts(dataset.loans, dataset.acks)

    # ------------------------------------------------------------ 构造
    @classmethod
    def from_sources(cls, source_dir: str | Path) -> "ToolHistoryRepository":
        return cls(load_dataset(source_dir))

    # ------------------------------------------------------------ 构件经历
    def part_history(self, part_id: str) -> dict:
        if part_id not in self.dataset.parts:
            raise KeyError(f"未知部件 {part_id}")
        ledger = self.ledgers[part_id]
        part = self.dataset.parts[part_id]
        best, worst = ledger.base_remaining()
        legs = [leg for leg in self.dataset.legs
                if part_id in (leg.base, leg.jaw)]
        repairs = [r for r in self.dataset.repairs if r.part == part_id]
        usages = [u for u in self.usages if u.part_id == part_id]
        return {
            "part": part,
            "ledger": ledger,
            "remaining_best": best,
            "remaining_worst": worst,
            "legs": sorted(legs, key=lambda l: l.start),
            "repairs": sorted(repairs, key=lambda r: r.effective),
            "usages": usages,
            "holdings": part_holdings(self.dataset.loans, self.dataset.acks, part_id),
        }

    def usage_provenance(self, part_id: str) -> list[UsageEntry]:
        return [u for u in self.usages if u.part_id == part_id]

    def remaining(self, part_id: str, configuration: tuple[str, ...], mode: str) -> tuple[int, int]:
        return remaining_for(self.ledgers[part_id], configuration, mode)

    def certificate(self, part_id: str, configuration: tuple[str, ...], mode: str, at: int | None = None):
        return evaluate_certificate(self.ledgers[part_id], configuration, mode, at)

    # ------------------------------------------------------------ 计数核对
    def run_reconciliation(self) -> list:
        return list(self.run_reconciles)

    def all_conflicts(self) -> list:
        return list(self.conflicts)

    def conflict_codes(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for conflict in self.conflicts:
            grouped.setdefault(conflict.code, []).append(conflict.message)
        return grouped

    # ------------------------------------------------------------ 派用
    def evaluate_orders(self) -> list[OrderVerdict]:
        return list(self.order_verdicts)

    def order_reasons(self, order_id: str) -> OrderVerdict:
        for verdict in self.order_verdicts:
            if verdict.work_order.id == order_id:
                return verdict
        raise KeyError(f"未知工单 {order_id}")

    # ------------------------------------------------------------ 借用/库存
    def holdings(self, part_id: str):
        return part_holdings(self.dataset.loans, self.dataset.acks, part_id)

    def availability(self, part_id: str, start: int, end: int):
        return is_available(self.dataset.loans, self.dataset.acks, part_id, start, end)

    def loan_receipt(self, loan_id: str) -> dict[str, str]:
        loan = next((l for l in self.dataset.loans if l.id == loan_id), None)
        if loan is None:
            raise KeyError(f"未知借用 {loan_id}")
        return received_parts(loan, self.dataset.acks)

    def inventory(self, at: int) -> dict[str, dict]:
        return committable_inventory(
            self.dataset.loans, self.dataset.acks, self.dataset.parts, at)

    # ------------------------------------------------------------ 补录
    def backfill(self, intake: int) -> BackfillImpact:
        return backfill_impact(self.dataset, intake)

    def ledger(self, part_id: str) -> PartLedger:
        return self.ledgers[part_id]
