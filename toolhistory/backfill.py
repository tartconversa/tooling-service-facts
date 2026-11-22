"""迟到补录的影响分析。

同一事实在不同到达批次下重新核算：
- 尚未发出的安排可缩小或阻塞；
- 已执行的借用不能悄悄改成没借过，历史保留，只重算受影响构件的余量；
- 放行证明只涵盖签发时认可的构型/工况，补录后需重新确认的安排显式列出。
部件三分类：
  uncertain  补录带来的不确定用量落在其上；
  awaiting   仍在等待实际签收；
  certified  被既有凭证确证仍可使用。
"""
from __future__ import annotations

from .ledger import build_all_ledgers, evaluate_certificate
from .loans import part_holdings, received_parts
from .models import BackfillImpact
from .reconcile import reconcile_cycles


def _snapshot(dataset):
    entries, run_recs, conflicts, mirrors = reconcile_cycles(
        dataset.cycles, dataset.legs, dataset.rates, dataset.parts)
    ledgers = build_all_ledgers(dataset.parts, dataset.repairs, entries)
    return entries, ledgers, conflicts


def _horizon(dataset) -> int:
    """认识时点：已发生业务事件（交收、维修、计数）的最大时间。

    leg 末端与工单区间属于未来计划，不参与“当前认识到哪里”的判定。
    """
    ticks = [ack.at for ack in dataset.acks]
    ticks += [rep.effective for rep in dataset.repairs]
    ticks += [row.end for row in dataset.cycles]
    return max(ticks) if ticks else 0


def backfill_impact(full_dataset, intake: int) -> BackfillImpact:
    prior = full_dataset.upto(intake - 1)
    current = full_dataset.upto(intake)

    prior_entries, prior_ledgers, _ = _snapshot(prior)
    cur_entries, cur_ledgers, _ = _snapshot(current)
    horizon = _horizon(current)

    new_sources = tuple(sorted(
        row.id for row in current.cycles if row.intake == intake))

    prior_bounds = {pid: (l.used_lo, l.used_hi) for pid, l in prior_ledgers.items()}
    changed_parts: list[str] = []
    uncertain_parts: list[str] = []
    for pid, ledger in cur_ledgers.items():
        before = prior_bounds.get(pid, (0, 0))
        if (ledger.used_lo, ledger.used_hi) != before:
            changed_parts.append(pid)
        if ledger.uncertain and any(e.part_id == pid and not e.certain for e in cur_entries):
            uncertain_parts.append(pid)

    # 等待签收：confirmed 借用中无 accepted 回执的件（part_holdings 据此给
    # pending-in 切片）。即使计划窗口落在 horizon 之后，“尚未实际接到”仍成立。
    awaiting: set[str] = set()
    for loan in current.loans:
        if loan.state != "confirmed":
            continue
        receipts = received_parts(loan, current.acks)
        for pid, state in receipts.items():
            if state != "pending":
                continue
            holdings = part_holdings(current.loans, current.acks, pid)
            if any(h.reference == loan.id and h.status == "pending-in" for h in holdings):
                awaiting.add(pid)

    uncertain_set = set(uncertain_parts)

    # 凭证确证：在 horizon 前签发、覆盖自身构型与至少一个工况的放行/恢复凭证。
    # 凭证证明的是“签发时认可的配置仍可按其 modes 使用”；即使该件另有不确定
    # 用量，凭证本身仍成立，因此本桶独立于 uncertain，只与 awaiting 互斥
    # （尚未实际接到的件不能算“仍可使用”）。
    certified: set[str] = set()
    for pid, ledger in cur_ledgers.items():
        if pid in awaiting:
            continue
        for service in ledger.services:
            if service.effective > horizon:
                continue
            verdict = evaluate_certificate(
                ledger, service.configuration, service.modes[0], horizon)
            if verdict.approved:
                certified.add(pid)
                break

    # 借用安排：已执行（存在 accepted）历史保留不可回滚；
    # 受补录影响且尚未执行的安排（confirmed 或候选请求）需要重新确认
    needs_reconfirm: list[str] = []
    preserved: list[str] = []
    for loan in current.loans:
        touched = bool(set(loan.parts) & set(changed_parts))
        receipts = received_parts(loan, current.acks)
        executed = any(s == "accepted" for s in receipts.values())
        if executed:
            preserved.append(loan.id)
        elif touched:
            needs_reconfirm.append(loan.id)

    # 受影响工单：其候选构型含 changed_parts，或其借用被要求重新确认
    blocked: list[str] = []
    from .dispatch import evaluate_all
    prior_verdicts = {v.work_order.id: v.dispatchable
                      for v in evaluate_all(prior.work_orders, prior.legs, prior.parts,
                                            prior_ledgers, prior.rates, prior.loans, prior.acks)}
    cur_verdicts = evaluate_all(current.work_orders, current.legs, current.parts,
                                cur_ledgers, current.rates, current.loans, current.acks)
    for verdict in cur_verdicts:
        wid = verdict.work_order.id
        affected_parts = {
            pid for option in verdict.options for pid in (option.base, option.jaw)
        } & set(changed_parts)
        was_dispatchable = prior_verdicts.get(wid, False)
        if (was_dispatchable and not verdict.dispatchable) or \
                (affected_parts and not verdict.dispatchable):
            blocked.append(wid)

    notes: list[str] = []
    if new_sources:
        notes.append(f"补录计数 {','.join(new_sources)} 越过已确认借用/返修时间，重算受影响构件界限")
    notes.append("已执行借用的历史不回滚，仅更新返还后的核算与后续可承诺工况")
    notes.append("放行凭证只覆盖签发时认可的构型与工况，超范围派用需重新确认")

    return BackfillImpact(
        intake=intake,
        new_cycle_sources=new_sources,
        changed_parts=tuple(sorted(changed_parts)),
        blocked_or_shrunk_orders=tuple(sorted(set(blocked))),
        needs_reconfirm_loans=tuple(sorted(needs_reconfirm)),
        executed_loans_preserved=tuple(sorted(preserved)),
        uncertain_parts=tuple(sorted(set(uncertain_parts))),
        awaiting_parts=tuple(sorted(awaiting)),
        certified_parts=tuple(sorted(certified - awaiting)),
        notes=tuple(notes),
    )
