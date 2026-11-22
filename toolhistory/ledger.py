"""构件寿命台账：世代、受限恢复点与放行凭证。

- replace：被换件从新品开始新世代，历史世代保留可查，整套历史不重置；
- restore：在核定 configuration/mode 内增加可用余量，且不超过设计上限；
- release：仅对签名构型与工况放行，是派用工况的凭证而非余量。
无法证明归属的耗用以 best/worst 界限沉淀，余量最坏值可能为负（已超耗）。
"""
from __future__ import annotations

from .models import (
    CertificateVerdict, Epoch, Part, PartLedger, ScopedCredit, ServiceRecord,
    UsageEntry,
)


class LedgerError(ValueError):
    pass


def _epoch_at(epochs: tuple[Epoch, ...], at: int) -> int:
    """返回时间 at 所处的世代序号。"""
    current = 0
    for epoch in epochs:
        if epoch.opened_at <= at:
            current = epoch.seq
        else:
            break
    return current


def _usage_in_configuration(usage: UsageEntry, configuration: tuple[str, ...]) -> bool:
    """该耗用是否（可能）落在凭证签名构型内。

    跨界耗用可能触达多个构型时取保守口径：只要其中一个构型匹配即计入，
    避免把恢复点额度错误清零。
    """
    if not usage.configs:
        return True  # 无构型信息的旧数据不做额外惩罚
    return any(all(pid in config for pid in configuration) for config in usage.configs)


def build_ledger(part: Part, repairs, usages: list[UsageEntry]) -> PartLedger:
    repairs = sorted([r for r in repairs if r.part == part.id], key=lambda r: r.effective)

    # 第一遍：构建世代（replace 开启新实物世代）
    epochs: list[Epoch] = [Epoch(0, 0, None, part.design)]
    for rep in repairs:
        if rep.kind == "replace":
            epochs.append(Epoch(epochs[-1].seq + 1, rep.effective, rep.id, part.design))
    current_epoch = epochs[-1]

    # 维修/放行凭证按生效时点归入世代；新实物世代不继承旧世代凭证
    services: list[ServiceRecord] = []
    for rep in repairs:
        if rep.kind == "replace":
            continue
        if _epoch_at(tuple(epochs), rep.effective) != current_epoch.seq:
            continue
        services.append(ServiceRecord(
            rep.id, rep.kind, rep.effective, rep.restore,
            rep.configuration, rep.modes, rep.intake,
        ))

    # 把耗用按其来源区间的起点归入当时生效的世代（换件之后的用量不进旧世代）
    current_seq = current_epoch.seq
    current_usages = sorted(
        (u for u in usages if _epoch_at(tuple(epochs), u.span[0]) == current_seq),
        key=lambda u: u.span[0],
    )
    used_lo = sum(u.points_lo for u in current_usages)
    used_hi = sum(u.points_hi for u in current_usages)
    uncertain = any(not u.certain for u in current_usages)

    # restore 在核定范围内增加余量且不得超过设计上限。
    # 封顶按凭证生效时点、且落在该凭证签名构型内的累计已耗计算：
    # 凭证只对签名构型/工况背书，其他构型下的历史耗用不占用该恢复点额度；
    # 之后补录的耗用也不能把已入账的恢复点挤没。
    credits: list[ScopedCredit] = []
    cumulative_hi = 0
    usage_index = 0
    restore_services = sorted((s for s in services if s.kind == "restore"),
                              key=lambda s: s.effective)
    for scoped in restore_services:
        while usage_index < len(current_usages) and \
                current_usages[usage_index].span[0] <= scoped.effective:
            usage = current_usages[usage_index]
            # 仅当该耗用的来源 leg 构型与凭证构型一致时才计入封顶基数
            if _usage_in_configuration(usage, scoped.configuration):
                cumulative_hi += usage.points_hi
            usage_index += 1
        headroom = max(0, part.design - cumulative_hi)
        granted = min(scoped.restore, headroom)
        credits.append(ScopedCredit(
            scoped.repair_id, granted, scoped.effective,
            scoped.configuration, scoped.modes,
        ))

    return PartLedger(
        part_id=part.id, role=part.role, model=part.model, design=part.design,
        epochs=tuple(epochs), services=tuple(services),
        used_lo=used_lo, used_hi=used_hi, credits=tuple(credits),
        uncertain=uncertain, conflicts=(),
    )


def build_all_ledgers(parts, repairs, usages: list[UsageEntry]) -> dict[str, PartLedger]:
    by_part: dict[str, list[UsageEntry]] = {}
    for usage in usages:
        by_part.setdefault(usage.part_id, []).append(usage)
    return {
        pid: build_ledger(part, repairs, by_part.get(pid, []))
        for pid, part in parts.items()
    }


# ---------------------------------------------------------------- 余量与凭证

def remaining_for(ledger: PartLedger, configuration: tuple[str, ...], mode: str) -> tuple[int, int]:
    """给定构型与工况下的可用余量 (best, worst)，含适用的受限恢复点。"""
    best_base, worst_base = ledger.base_remaining()
    credit = 0
    for scoped in ledger.credits:
        if ledger.part_id in scoped.configuration and mode in scoped.modes \
                and all(pid in configuration for pid in scoped.configuration):
            credit += scoped.points
    return best_base + credit, worst_base + credit


def evaluate_certificate(
    ledger: PartLedger, configuration: tuple[str, ...], mode: str,
    at: int | None = None,
) -> CertificateVerdict:
    """放行凭证核对：只有覆盖签名构型与工况的凭证才认可。

    ``at`` 给定时只采信该时点之前签发的凭证（用于 as-of/补录回放）；
    缺省表示按当前认识状态核对，不限制签发时间。
    没有任何维修/放行历史的部件视为天然可用（设计构型内）。
    """
    in_scope = [
        s for s in ledger.services
        if (at is None or s.effective <= at)
        and ledger.part_id in s.configuration
        and all(pid in configuration for pid in s.configuration)
    ]
    if not in_scope:
        if not ledger.services:
            return CertificateVerdict(ledger.part_id, True, None, "无维修历史，设计构型内天然可用")
        return CertificateVerdict(
            ledger.part_id, False, None,
            f"现有凭证均不覆盖构型 {configuration} 或时间 {at}",
        )
    # 取最近一次覆盖该构型的凭证；其 modes 决定当前认可的工况
    governing = max(in_scope, key=lambda s: s.effective)
    approved = mode in governing.modes
    reason = (f"凭证 {governing.repair_id} 认可工况 {','.join(governing.modes)}"
              if approved else
              f"凭证 {governing.repair_id} 仅认可 {','.join(governing.modes)}，不含 {mode}")
    return CertificateVerdict(ledger.part_id, approved, governing, reason)
