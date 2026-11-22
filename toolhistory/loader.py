"""从 sources/ 原件加载并做结构校验。

只依赖标准库；JSON 原件是不可信数据，加载时按 source-fields.json 声明
逐字段校验，失败即抛 ``DataError``，不静默吞掉。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import Ack, CycleRow, Leg, Loan, Part, Rate, Repair, WorkOrder


class DataError(ValueError):
    """源数据结构或语义形状不合法。"""


@dataclass
class Dataset:
    parts: dict[str, Part]
    legs: list[Leg]
    cycles: list[CycleRow]
    rates: list[Rate]
    repairs: list[Repair]
    work_orders: list[WorkOrder]
    loans: list[Loan]
    acks: list[Ack]
    intakes: list[int]

    def upto(self, intake: int) -> "Dataset":
        """返回只包含到达批次 <= intake 的视图（用于 as-of 核算）。"""
        keep = lambda rows: [r for r in rows if r.intake <= intake]  # noqa: E731
        return Dataset(
            parts=dict(self.parts),
            legs=keep(self.legs),
            cycles=keep(self.cycles),
            rates=keep(self.rates),
            repairs=keep(self.repairs),
            work_orders=keep(self.work_orders),
            loans=keep(self.loans),
            acks=keep(self.acks),
            intakes=[i for i in self.intakes if i <= intake],
        )


def _check_range(value, where: str):
    if not (isinstance(value, list) and len(value) == 2
            and isinstance(value[0], int) and isinstance(value[1], int)
            and value[0] < value[1]):
        raise DataError(f"{where}: 非法左闭右开区间 {value!r}")


def _check_shape(record: dict, schema: dict[str, str], table: str, rec_id: str):
    if set(record) != set(schema):
        raise DataError(
            f"{table}/{rec_id}: 字段集合不符，期望 {sorted(schema)}，实得 {sorted(record)}"
        )
    for name, kind in schema.items():
        value = record[name]
        if value is None:
            continue
        if kind == "int" and not isinstance(value, int):
            raise DataError(f"{table}/{rec_id}.{name}: 期望 int，实得 {type(value).__name__}")
        if kind == "text" and not isinstance(value, str):
            raise DataError(f"{table}/{rec_id}.{name}: 期望 text")
        if kind == "range":
            _check_range(value, f"{table}/{rec_id}.{name}")
        if kind == "items" and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
            raise DataError(f"{table}/{rec_id}.{name}: 期望 text 列表")


def load_dataset(source_dir: str | Path) -> Dataset:
    source_dir = Path(source_dir)
    schema_path = source_dir.parent / "source-fields.json"
    if not schema_path.exists():
        raise DataError(f"缺少字段声明 {schema_path}")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    buckets: dict[str, list[dict]] = {table: [] for table in schema}
    intakes: set[int] = set()
    seen_ids: set[tuple[str, str]] = set()

    for path in sorted(source_dir.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        intake, table, records = doc["intake"], doc["table"], doc["records"]
        if table not in buckets:
            raise DataError(f"{path.name}: 未知表 {table}")
        if f"{intake:02d}-{table}" != path.stem:
            raise DataError(f"{path.name}: 文件名与 intake/table 不一致")
        intakes.add(intake)
        for raw in records:
            rec_id = raw["id"]
            key = (table, rec_id)
            if key in seen_ids:
                raise DataError(f"{table}/{rec_id}: 记录 id 重复")
            seen_ids.add(key)
            _check_shape(raw, schema[table], table, rec_id)
            enriched = dict(raw)
            enriched["intake"] = intake
            buckets[table].append(enriched)

    parts = {
        r["id"]: Part(r["id"], r["role"], r["model"], r["design"], r["owner"], r["intake"])
        for r in buckets["parts"]
    }
    legs = [
        Leg(r["id"], r["line"], r["tool"], r["span"][0], r["span"][1],
            r["base"], r["jaw"], r["grade"], r["intake"])
        for r in buckets["legs"]
    ]
    cycles = [
        CycleRow(r["id"], r["line"], r["tool"], r["run"], r["span"][0], r["span"][1],
                 r["generation"], r["before"], r["after"], r["mode"], r["mirror"], r["intake"])
        for r in buckets["cycle"]
    ]
    rates = [
        Rate(r["id"], r["mode"], r["role"], r["points"], r["intake"])
        for r in buckets["rates"]
    ]
    repairs = [
        Repair(r["id"], r["part"], r["kind"], r["restore"], r["effective"],
               tuple(r["configuration"]), tuple(r["modes"]), r["intake"])
        for r in buckets["repairs"]
    ]
    work_orders = [
        WorkOrder(r["id"], r["line"], r["span"][0], r["span"][1], r["grade"],
                  r["cycles"], r["base_model"], tuple(r["jaw_models"]), r["intake"])
        for r in buckets["work_orders"]
    ]
    loans = [
        Loan(r["id"], r["order"], r["tool"], tuple(r["parts"]),
             r["span"][0], r["span"][1], r["state"], r["intake"])
        for r in buckets["loans"]
    ]
    acks = [
        Ack(r["id"], r["loan"], r["part"], r["state"], r["at"], r["intake"])
        for r in buckets["acks"]
    ]

    _cross_validate(parts, legs, cycles, rates, repairs, work_orders, loans, acks)
    return Dataset(parts, legs, cycles, rates, repairs, work_orders, loans, acks, sorted(intakes))


def _cross_validate(parts, legs, cycles, rates, repairs, work_orders, loans, acks):
    def require_part(pid, where):
        if pid not in parts:
            raise DataError(f"{where}: 引用了不存在的部件 {pid}")

    for leg in legs:
        require_part(leg.base, f"legs/{leg.id}")
        require_part(leg.jaw, f"legs/{leg.id}")
        if parts[leg.base].role != "base" or parts[leg.jaw].role != "jaw":
            raise DataError(f"legs/{leg.id}: base/jaw 角色错位")
    for row in cycles:
        if row.mirror is not None and not any(c.id == row.mirror for c in cycles):
            raise DataError(f"cycle/{row.id}: mirror 指向不存在的源行 {row.mirror}")
    for rep in repairs:
        require_part(rep.part, f"repairs/{rep.id}")
        for pid in rep.configuration:
            require_part(pid, f"repairs/{rep.id}")
        if rep.kind not in {"restore", "replace", "release"}:
            raise DataError(f"repairs/{rep.id}: 未知 kind {rep.kind}")
    loan_ids = {loan.id for loan in loans}
    for ack in acks:
        if ack.loan not in loan_ids:
            raise DataError(f"acks/{ack.id}: 引用了不存在的借用 {ack.loan}")
        require_part(ack.part, f"acks/{ack.id}")
        if ack.state not in {"accepted", "returned", "refused"}:
            raise DataError(f"acks/{ack.id}: 未知回执状态 {ack.state}")
    for loan in loans:
        for pid in loan.parts:
            require_part(pid, f"loans/{loan.id}")
    # 同一工具的 leg 区间不得重叠（封定关系互斥）
    by_tool: dict[str, list[Leg]] = {}
    for leg in legs:
        by_tool.setdefault(leg.tool, []).append(leg)
    for tool, tool_legs in by_tool.items():
        ordered = sorted(tool_legs, key=lambda x: (x.start, x.end))
        for prev, nxt in zip(ordered, ordered[1:]):
            if nxt.start < prev.end and not (nxt.start >= prev.end):
                raise DataError(f"legs: 工具 {tool} 的封定区间重叠 {prev.id}/{nxt.id}")
