"""区间借用、实际交收与可承诺量（ATP）。

规则：
- confirmed 借用对编号确认的构型占用区间；requested 只是候选，不硬占用；
- accepted 才表示实际接到、可在借用方使用；refused/未接收的件不算可用；
- returned 之后该件才可被原线复用；
- 同一部件不能被两条线同时占用，重叠的 confirmed 安排判冲突；
- 允许只接收许可内子件，接收结果反馈到剩余可承诺的装配与可用库存。
"""
from __future__ import annotations

from .models import Ack, Holding, Loan


def _acks_by_loan(acks: list[Ack]) -> dict[str, list[Ack]]:
    grouped: dict[str, list[Ack]] = {}
    for ack in sorted(acks, key=lambda a: a.at):
        grouped.setdefault(ack.loan, []).append(ack)
    return grouped


def part_holdings(loans: list[Loan], acks: list[Ack], part_id: str) -> list[Holding]:
    """还原单个部件在时间轴上的占用/待收切片。

    同一部件在一笔借用里可能有多条回执（先 refused 后补 accepted），
    一律以时间最后的回执为准。
    """
    ack_lookup = _acks_by_loan(acks)
    holdings: list[Holding] = []
    for loan in sorted(loans, key=lambda x: (x.start, x.end)):
        if part_id not in loan.parts:
            continue
        part_acks = [a for a in ack_lookup.get(loan.id, ()) if a.part == part_id]
        latest = part_acks[-1] if part_acks else None
        final_state = latest.state if latest else None
        accepted_at = next((a.at for a in reversed(part_acks) if a.state == "accepted"), None)
        returned_at = next((a.at for a in part_acks if a.state == "returned"), None)

        if loan.state != "confirmed":
            holdings.append(Holding(
                loan.start, loan.end, "pending-out", loan.id,
                f"候选请求 {loan.id}（未确认），当前不硬占用",
            ))
            continue

        if final_state == "refused":
            holdings.append(Holding(
                loan.start, loan.end, "free", loan.id,
                f"借用 {loan.id} 中该件最终被拒收，不形成占用",
            ))
            continue

        if accepted_at is None:
            # 已确认但尚未实际接到：件仍在原线，但已被预留给该安排
            holdings.append(Holding(
                loan.start, loan.end, "pending-in", loan.id,
                f"借用 {loan.id} 已确认但无 accepted 回执，等待实际签收",
            ))
            continue

        # accepted 之后实际占用，直到 returned（无返还则占用到区间末）
        occ_end = returned_at if returned_at is not None else loan.end
        if accepted_at > loan.start:
            holdings.append(Holding(
                loan.start, accepted_at, "pending-in", loan.id,
                f"借用 {loan.id} 等待签收",
            ))
        holdings.append(Holding(
            accepted_at, occ_end, "occupied", loan.id,
            f"借用 {loan.id} 已签收并占用" + (f"，{occ_end} 返还" if returned_at else ""),
        ))
        if returned_at is not None and returned_at < loan.end:
            holdings.append(Holding(
                returned_at, loan.end, "free", loan.id,
                f"借用 {loan.id} 返还后释放",
            ))
    return holdings


def is_available(loans: list[Loan], acks: list[Ack], part_id: str,
                 start: int, end: int) -> tuple[bool, list[str]]:
    """部件在 [start, end) 是否可承诺（无 confirmed 硬占用/待收）。"""
    blockers: list[str] = []
    for holding in part_holdings(loans, acks, part_id):
        if holding.status in {"occupied", "pending-in"} and holding.start < end and start < holding.end:
            blockers.append(f"{holding.status} [{holding.start},{holding.end}) {holding.reference}：{holding.detail}")
    return not blockers, blockers


def find_commitment_conflicts(loans: list[Loan], acks: list[Ack]) -> list[dict]:
    """同一部件被多笔 confirmed 安排同时占用的冲突。"""
    confirmed = [loan for loan in loans if loan.state == "confirmed"]
    conflicts: list[dict] = []
    for i, left in enumerate(confirmed):
        for right in confirmed[i + 1:]:
            if not (left.start < right.end and right.start < left.end):
                continue
            shared = sorted(set(left.parts) & set(right.parts))
            for part_id in shared:
                left_free, _ = is_available([right], acks, part_id, left.start, left.end)
                if not left_free:
                    conflicts.append({
                        "part": part_id,
                        "loans": (left.id, right.id),
                        "span": (max(left.start, right.start), min(left.end, right.end)),
                        "message": f"部件 {part_id} 在 {left.id} 与 {right.id} 重叠区间被重复承诺",
                    })
    return conflicts


def received_parts(loan: Loan, acks: list[Ack]) -> dict[str, str]:
    """一笔借用每个部件的最终回执状态（无回执记 pending）。"""
    result = {pid: "pending" for pid in loan.parts}
    for ack in acks:
        if ack.loan == loan.id and ack.part in result:
            result[ack.part] = ack.state
    return result


def committable_inventory(loans: list[Loan], acks: list[Ack], parts, at: int) -> dict[str, dict]:
    """在时点 at 的可承诺库存视图：逐件给出当前状态与阻塞引用。"""
    view: dict[str, dict] = {}
    for pid in sorted(parts):
        holdings = part_holdings(loans, acks, pid)
        active = [h for h in holdings if h.start <= at < h.end]
        status = active[-1].status if active else "free"
        view[pid] = {
            "part": pid,
            "role": parts[pid].role,
            "model": parts[pid].model,
            "status": status,
            "references": [h.reference for h in active],
            "details": [h.detail for h in active],
        }
    return view
