"""共享工装履历仓的领域模型。

身份原则：部件（part）是实物身份，工装（tool）是 base 与 jaw 在某条
已封定装配关系（leg）下组成的成套构型。所有派生记录都保留来源出处，
无法证明归属的耗用以 best / worst 界限保留，不做单点猜测。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------- 原始记录

@dataclass(frozen=True)
class Part:
    id: str
    role: str            # base | jaw
    model: str
    design: int          # 设计寿命点上限
    owner: str
    intake: int


@dataclass(frozen=True)
class Leg:
    """一条已封定的装配关系，span 为左闭右开的线端分钟区间。"""
    id: str
    line: str
    tool: str
    start: int
    end: int
    base: str
    jaw: str
    grade: str
    intake: int

    @property
    def parts(self) -> tuple[str, str]:
        return self.base, self.jaw


@dataclass(frozen=True)
class CycleRow:
    id: str
    line: str
    tool: str
    run: str
    start: int
    end: int
    generation: str
    before: int
    after: int
    mode: str
    mirror: Optional[str]
    intake: int

    @property
    def count(self) -> int:
        """该代累计端点差值；负差值表示计数重置（由上层处理）。"""
        return self.after - self.before


@dataclass(frozen=True)
class Rate:
    id: str
    mode: str
    role: str
    points: int
    intake: int


@dataclass(frozen=True)
class Repair:
    """维修凭证。

    kind:
      restore  在核定 configuration / modes 内增加 restore 点余量，不超设计上限；
      replace  新实物更换，被换件寿命进入新世代、从新品起算；
      release  针对签名 configuration 与 modes 的放行，不直接增加余量。
    """
    id: str
    part: str
    kind: str
    restore: int
    effective: int
    configuration: tuple[str, ...]
    modes: tuple[str, ...]
    intake: int


@dataclass(frozen=True)
class WorkOrder:
    id: str
    line: str
    start: int
    end: int
    grade: str
    cycles: int
    base_model: str
    jaw_models: tuple[str, ...]
    intake: int


@dataclass(frozen=True)
class Loan:
    """借用安排。state=confirmed 为编号确认，requested 仅为候选请求。"""
    id: str
    order: str
    tool: str
    parts: tuple[str, ...]
    start: int
    end: int
    state: str
    intake: int


@dataclass(frozen=True)
class Ack:
    """实际交收：accepted / returned / refused。"""
    id: str
    loan: str
    part: str
    state: str
    at: int
    intake: int


# ---------------------------------------------------------------- 派生记录

@dataclass(frozen=True)
class UsageEntry:
    """一条计数对某个构件的寿命点归因（带界限与出处）。

    lo/hi 为可证明归属到该 part 的循环数界限（lo<=hi）。
    certain=True 时 lo==hi 且归属已由覆盖的 leg 完全证明。
    legs 为该归因涉及的封定装配 leg；configs 为对应的 (base,jaw) 构型。
    """
    part_id: str
    run: str
    mode: str
    cycles_lo: int
    cycles_hi: int
    points_lo: int
    points_hi: int
    sources: tuple[str, ...]
    span: tuple[int, int]
    generation: str
    certain: bool
    note: str
    legs: tuple[str, ...] = ()
    configs: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class Conflict:
    code: str
    message: str
    sources: tuple[str, ...]
    span: Optional[tuple[int, int]] = None
    run: Optional[str] = None
    part: Optional[str] = None


@dataclass(frozen=True)
class RunReconcile:
    """同一 run 去重/覆盖核对后的循环量结论。"""
    run: str
    tool: str
    generation: str
    mode: str
    span: tuple[int, int]
    cycles_lo: int
    cycles_hi: int
    certain: bool
    sources: tuple[str, ...]
    notes: tuple[str, ...]
    # 每条源行在覆盖证明下可独立贡献的循环量（重叠 run 的有效界限）
    row_cycles: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class Epoch:
    """部件一个实物世代（replace 开启新世代）。"""
    seq: int
    opened_at: int                 # 生效时间；0 表示入仓即新品
    opened_by: Optional[str]       # 开启该世代的 replace 凭证 id
    design: int


@dataclass(frozen=True)
class ServiceRecord:
    """落在某世代上的维修/放行凭证（replace 只开世代，不在此列）。"""
    repair_id: str
    kind: str                      # restore | release
    effective: int
    restore: int
    configuration: tuple[str, ...]
    modes: tuple[str, ...]
    intake: int


@dataclass(frozen=True)
class PartLedger:
    part_id: str
    role: str
    model: str
    design: int
    epochs: tuple[Epoch, ...]
    services: tuple[ServiceRecord, ...]
    # 当前世代累计已耗用寿命点界限（不含受限恢复点）
    used_lo: int
    used_hi: int
    # 受限恢复点：仅在指定 configuration/mode 下可用
    credits: tuple["ScopedCredit", ...]
    uncertain: bool
    conflicts: tuple[Conflict, ...]

    def base_remaining(self) -> tuple[int, int]:
        """通用余量界限（best, worst），worst 可能为负表示已超耗。"""
        return self.design - self.used_lo, self.design - self.used_hi

    def is_exhausted(self) -> bool:
        return self.design - self.used_hi <= 0


@dataclass(frozen=True)
class ScopedCredit:
    repair_id: str
    points: int                    # 核定恢复点（已按上限封顶后的实际入账）
    effective: int
    configuration: tuple[str, ...]
    modes: tuple[str, ...]


@dataclass(frozen=True)
class Availability:
    """部件对某工单需求的余量结论。"""
    part_id: str
    required_points: int
    remaining_best: int
    remaining_worst: int
    fits_certain: bool             # worst 余量也够
    fits_possible: bool            # best 余量够（worst 不够 => 仅可能）
    exhausted: bool


@dataclass(frozen=True)
class CertificateVerdict:
    part_id: str
    approved: bool
    governing: Optional[ServiceRecord]
    reason: str


@dataclass(frozen=True)
class KitOption:
    base: str
    jaw: str
    dispatchable: bool
    reasons: tuple[str, ...]
    base_avail: Optional[Availability]
    jaw_avail: Optional[Availability]


@dataclass(frozen=True)
class OrderVerdict:
    work_order: WorkOrder
    options: tuple[KitOption, ...]
    dispatchable: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class BackfillImpact:
    """补录批次相对上一认识状态的差额。"""
    intake: int
    new_cycle_sources: tuple[str, ...]
    changed_parts: tuple[str, ...]
    blocked_or_shrunk_orders: tuple[str, ...]
    needs_reconfirm_loans: tuple[str, ...]
    executed_loans_preserved: tuple[str, ...]
    uncertain_parts: tuple[str, ...]
    awaiting_parts: tuple[str, ...]
    certified_parts: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Holding:
    """部件在某区间的占用/可承诺状态切片。"""
    start: int
    end: int
    status: str                    # occupied | pending-in | pending-out | free
    reference: str
    detail: str
