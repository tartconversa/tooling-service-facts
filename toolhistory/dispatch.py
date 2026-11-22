"""换型可用性：合套核查与工单可派用理由。

每一件都单独过三道门，任何一件不过整套即不可派，禁止用整套总余量互相抵消：
  1. 构型门：base/jaw 料号符合工单、且有在工单前封定的同套 leg 证据；
  2. 余量门：逐件 best/worst 余量对照本工单工况点数（rates × cycles）；
  3. 凭证/占用/签收门：放行凭证覆盖工况、无 confirmed 重叠占用、待收件不可用。
"""
from __future__ import annotations

from .ledger import CertificateVerdict, evaluate_certificate, remaining_for
from .loans import is_available
from .models import Availability, KitOption, OrderVerdict, WorkOrder


def required_points(rates, role: str, mode: str, cycles: int) -> int:
    table = {(r.mode, r.role): r.points for r in rates}
    if (mode, role) not in table:
        raise KeyError(f"缺少 mode={mode} role={role} 的扣减率")
    return table[(mode, role)] * cycles


def _candidate_configs(work_order: WorkOrder, legs, parts) -> list[tuple[str, str]]:
    """所有在工单开始前曾封定为同一套、且料号匹配的 (base, jaw)。"""
    configs: set[tuple[str, str]] = set()
    for leg in legs:
        if leg.start >= work_order.start:
            continue
        base, jaw = parts.get(leg.base), parts.get(leg.jaw)
        if base is None or jaw is None:
            continue
        if base.model == work_order.base_model and jaw.model in work_order.jaw_models:
            configs.add((leg.base, leg.jaw))
    return sorted(configs)


def _part_gate(part_id, configuration, work_order, ledgers, rates) -> tuple[Availability, CertificateVerdict, list[str]]:
    ledger = ledgers[part_id]
    need = required_points(rates, ledger.role, work_order.grade, work_order.cycles)
    # 余量须含该构型/工况下适用的受限恢复点（restore），但不与其他件抵消
    best, worst = remaining_for(ledger, configuration, work_order.grade)
    avail = Availability(
        part_id=part_id, required_points=need,
        remaining_best=best, remaining_worst=worst,
        fits_certain=worst >= need, fits_possible=best >= need,
        exhausted=worst <= 0,
    )
    # 凭证按当前认识状态核对构型与工况覆盖；as-of 回放由 backfill 侧传 at
    verdict = evaluate_certificate(ledger, configuration, work_order.grade)
    reasons: list[str] = []
    if not avail.fits_possible:
        reasons.append(
            f"{part_id} 最好余量 {best} < 需求 {need}（最坏余量 {worst}），余量门失败"
        )
    elif not avail.fits_certain:
        reasons.append(
            f"{part_id} 仅最好余量 {best} 够需求 {need}，最坏余量 {worst} 不足，属边际不确定"
        )
    if not verdict.approved:
        reasons.append(f"{part_id} 凭证门失败：{verdict.reason}")
    return avail, verdict, reasons


def evaluate_order(work_order: WorkOrder, legs, parts, ledgers, rates,
                   loans, acks) -> OrderVerdict:
    options: list[KitOption] = []
    order_reasons: list[str] = []

    candidates = _candidate_configs(work_order, legs, parts)
    if not candidates:
        order_reasons.append("无在工单前封定且料号匹配的成套构型证据")

    for base_id, jaw_id in candidates:
        config = (base_id, jaw_id)
        reasons: list[str] = []
        base_avail, base_cert, base_reasons = _part_gate(
            base_id, config, work_order, ledgers, rates)
        jaw_avail, jaw_cert, jaw_reasons = _part_gate(
            jaw_id, config, work_order, ledgers, rates)
        reasons.extend(base_reasons)
        reasons.extend(jaw_reasons)

        for pid in config:
            ok, blockers = is_available(loans, acks, pid, work_order.start, work_order.end)
            if not ok:
                for blocker in blockers:
                    reasons.append(f"{pid} 占用/签收门失败：{blocker}")

        # 边际件：best 够但 worst 不够，不能按确定可派处理
        marginal = any(not a.fits_certain for a in (base_avail, jaw_avail) if a is not None)
        hard_fail = any(not a.fits_possible for a in (base_avail, jaw_avail)) \
            or any("凭证门失败" in r for r in reasons) \
            or any("占用/签收门失败" in r for r in reasons)
        dispatchable = not hard_fail and not marginal
        if marginal and not hard_fail:
            reasons.append("存在边际不确定件，成套不可抵消，只能条件承诺")
        options.append(KitOption(
            base=base_id, jaw=jaw_id, dispatchable=dispatchable,
            reasons=tuple(reasons), base_avail=base_avail, jaw_avail=jaw_avail,
        ))

    dispatchable = any(option.dispatchable for option in options)
    if not dispatchable and not order_reasons:
        order_reasons.append("没有任何候选构型通过全部三道门")
    return OrderVerdict(
        work_order=work_order, options=tuple(options),
        dispatchable=dispatchable, reasons=tuple(order_reasons),
    )


def evaluate_all(work_orders, legs, parts, ledgers, rates, loans, acks) -> list[OrderVerdict]:
    return [
        evaluate_order(wo, legs, parts, ledgers, rates, loans, acks)
        for wo in sorted(work_orders, key=lambda w: w.start)
    ]
