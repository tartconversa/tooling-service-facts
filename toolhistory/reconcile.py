"""线端计数的覆盖核对与构件归因。

处理顺序：
1. mirror 行是被转发源行的重复站点转发，只登记出处，不参与任何加总；
2. 同一 run 的多条计数按累计端点做覆盖证明，重叠范围不直接相加；
3. 计数区间跨过 leg 换件边界时，能证明在界内的循环照常归属，
   跨界部分不能证明分配，保留每构件 [0, n] 的最坏/最好界限；
4. 计数重置（同 run 出现新一代、或端点倒退）保留出处并按代独立累计。

输出的 :class:`UsageEntry` 始终带 sources 出处与 best/worst 点界限。
"""
from __future__ import annotations

from .models import Conflict, CycleRow, Leg, RunReconcile, UsageEntry

GRADE_ORDER = {"low": 1, "high": 2}


# ---------------------------------------------------------------- 区间工具

def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _intersect(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int] | None:
    start, end = max(a[0], b[0]), min(a[1], b[1])
    return (start, end) if start < end else None


def _legs_at(legs: list[Leg], start: int, end: int) -> list[Leg]:
    return [leg for leg in legs if leg.start < end and start < leg.end]


# ---------------------------------------------------------------- 单 run 覆盖

def _reconcile_run(run: str, rows: list[CycleRow]) -> tuple[list[RunReconcile], list[Conflict]]:
    conflicts: list[Conflict] = []
    sources = tuple(r.id for r in rows)
    span = (min(r.start for r in rows), max(r.end for r in rows))
    tool = rows[0].tool

    by_gen: dict[str, list[CycleRow]] = {}
    for row in rows:
        by_gen.setdefault(row.generation, []).append(row)

    reconciles: list[RunReconcile] = []
    if len(by_gen) > 1:
        conflicts.append(Conflict(
            "generation-change",
            f"run {run} 出现 {len(by_gen)} 个计数世代，按代独立累计后相加",
            sources, span, run,
        ))

    for generation, gen_rows in by_gen.items():
        notes: list[str] = []
        # 端点倒退：单条计数内部重置
        for row in gen_rows:
            if row.before > row.after:
                conflicts.append(Conflict(
                    "counter-reset",
                    f"源行 {row.id} 端点倒退（{row.before}->{row.after}），按重置后 {row.after} 计",
                    (row.id,), (row.start, row.end), run,
                ))
                notes.append(f"{row.id}:端点倒退重置")

        intervals = [(r.start, r.end) for r in gen_rows]
        union = _merge_intervals(intervals)
        overlap_segments: list[tuple[int, int]] = []
        for i, a in enumerate(intervals):
            for b in intervals[i + 1:]:
                seg = _intersect(a, b)
                if seg:
                    overlap_segments.append(seg)
        if overlap_segments:
            conflicts.append(Conflict(
                "same-run-overlap",
                f"run {run} 同代计数范围重叠 {overlap_segments}，重叠段不重复相加",
                sources, (min(s[0] for s in overlap_segments), max(s[1] for s in overlap_segments)),
                run,
            ))
            notes.append("同代范围重叠，按覆盖证明取界限")

        # 同代累计端点的覆盖证明：
        #  - 范围重叠时，重叠窗内任一物理循环被两台计数器各记一次，故物理
        #    循环上界取较大累计读数（不能把重叠部分相加）；
        #  - 范围互不相交（独立区间）才可直接相加。
        lo = max(r.after for r in gen_rows)
        if overlap_segments:
            hi = max(max(0, r.count) for r in gen_rows)
        else:
            hi = sum(max(0, r.count) for r in gen_rows)
        hi = max(lo, hi)
        row_cycles = tuple((r.id, min(max(0, r.count), hi)) for r in gen_rows)
        mode = gen_rows[0].mode
        reconciles.append(RunReconcile(
            run=run, tool=tool, generation=generation, mode=mode,
            span=(union[0][0], union[-1][1]), cycles_lo=lo, cycles_hi=hi,
            # 总量可确定，但只要存在重叠段，循环到时间片/构件的精确映射就不可证
            certain=(lo == hi and not overlap_segments),
            sources=sources, notes=tuple(notes), row_cycles=row_cycles,
        ))
    return reconciles, conflicts


# ---------------------------------------------------------------- 归因

def _split_components(row: CycleRow, legs: list[Leg]) -> list[dict]:
    """把一条计数区间按覆盖它的 leg 切成组件，并标注归属是否已证明。

    每个组件是一个时间片：该片只落在一条封定 leg 上时，该片的循环
    归属完全确定；一条计数行被切成多片（跨过换件边界）时，行总量在
    各片之间如何分配无法证明，由上层按 best/worst 界限处理。
    """
    cuts = {row.start, row.end}
    touching = _legs_at(legs, row.start, row.end)
    for leg in touching:
        cuts.add(max(row.start, min(row.end, leg.start)))
        cuts.add(max(row.start, min(row.end, leg.end)))
    cuts = sorted(cuts)
    components: list[dict] = []
    for start, end in zip(cuts, cuts[1:]):
        active = _legs_at(legs, start, end)
        if not active:
            components.append({
                "span": (start, end), "legs": (), "parts": (),
                "certain": False, "reason": "区间内无封定 leg，归属待核",
            })
            continue
        # 同一时刻同一工具只有一条封定 leg（加载时已校验互斥）
        leg = active[0]
        components.append({
            "span": (start, end), "legs": (leg.id,),
            "parts": leg.parts,
            "certain": len(cuts) == 2,  # 只有未被边界切开才是整条确定
            "reason": "时间片落在单条 leg 内" if len(cuts) == 2
            else f"跨界组件，仅证明 {leg.id} 界内占用，片间分配不可证",
        })
    return components


def reconcile_cycles(cycles: list[CycleRow], legs: list[Leg], rates, parts) -> tuple[
    list[UsageEntry], list[RunReconcile], list[Conflict], dict[str, tuple[str, ...]]
]:
    conflicts: list[Conflict] = []
    mirrors: dict[str, tuple[str, ...]] = {}

    real_rows: list[CycleRow] = []
    for row in cycles:
        if row.mirror is not None:
            mirrors.setdefault(row.mirror, ())
            mirrors[row.mirror] = mirrors[row.mirror] + (row.id,)
            conflicts.append(Conflict(
                "mirror-duplicate",
                f"源行 {row.id} 是 {row.mirror} 的重复站点转发，不计入加总",
                (row.id, row.mirror), (row.start, row.end), row.run,
            ))
        else:
            real_rows.append(row)

    rate_lookup = {(r.mode, r.role): r.points for r in rates}

    # run -> 覆盖结论
    run_groups: dict[str, list[CycleRow]] = {}
    for row in real_rows:
        run_groups.setdefault(row.run, []).append(row)

    run_reconciles: list[RunReconcile] = []
    run_bounds: dict[str, tuple[int, int]] = {}
    row_cap: dict[str, int] = {}
    for run, rows in run_groups.items():
        recs, confs = _reconcile_run(run, rows)
        conflicts.extend(confs)
        run_reconciles.extend(recs)
        run_bounds[run] = (sum(r.cycles_lo for r in recs), sum(r.cycles_hi for r in recs))
        for rec in recs:
            for source_id, effective in rec.row_cycles:
                row_cap[source_id] = effective

    # 每个 term 携带其来源 leg 与 (base,jaw) 构型，聚合时再算 best/worst 界限
    part_hi_terms: dict[str, list[dict]] = {}
    leg_by_tool: dict[str, list[Leg]] = {}
    for leg in legs:
        leg_by_tool.setdefault(leg.tool, []).append(leg)

    for row in real_rows:
        tool_legs = leg_by_tool.get(row.tool, [])
        components = _split_components(row, tool_legs)
        if len(components) > 1:
            conflicts.append(Conflict(
                "cross-assembly",
                f"源行 {row.id} 跨过物理换件/装配边界，跨界用量不臆断给新件",
                (row.id,), (row.start, row.end), row.run,
            ))
        # 行有效量：重叠 run 已把重复覆盖摊回，单构件归因不能超过该有效量
        row_count = min(max(0, row.count), row_cap.get(row.id, max(0, row.count)))
        for comp in components:
            if not comp["parts"]:
                conflicts.append(Conflict(
                    "unattributable-window",
                    f"源行 {row.id} 在 {comp['span']} 无封定装配关系，用量归属待核",
                    (row.id,), comp["span"], row.run,
                ))
                continue
            leg_id = comp["legs"][0]
            comp_leg = next(l for l in tool_legs if l.id == leg_id)
            config = (comp_leg.base, comp_leg.jaw)
            for part_id in comp["parts"]:
                part_hi_terms.setdefault(part_id, []).append({
                    "count": row_count, "source": row.id, "mode": row.mode,
                    "span": comp["span"], "generation": row.generation,
                    "certain": comp["certain"], "leg": leg_id, "config": config,
                })

    # 装配等级与工况冲突（high 工况跑在 low 等级 leg 上）
    for row in real_rows:
        for leg in _legs_at(leg_by_tool.get(row.tool, []), row.start, row.end):
            if GRADE_ORDER.get(row.mode, 0) > GRADE_ORDER.get(leg.grade, 0):
                conflicts.append(Conflict(
                    "mode-exceeds-grade",
                    f"源行 {row.id} 工况 {row.mode} 高于 leg {leg.id} 等级 {leg.grade}",
                    (row.id, leg.id), (max(row.start, leg.start), min(row.end, leg.end)),
                    row.run,
                ))

    # 汇总成 UsageEntry（每个 part × run 一条），循环界限换算寿命点
    entries: list[UsageEntry] = []
    grouped: dict[tuple[str, str], list[dict]] = {}
    for part_id, terms in part_hi_terms.items():
        for term in terms:
            grouped.setdefault((part_id, _run_of_source(real_rows, term["source"])), []).append(term)
    for (part_id, run), terms in sorted(grouped.items()):
        _run_lo, run_hi = run_bounds[run]
        # 下界：该 run 内完全证明在界内的时间片计数之和（同一源行只计一次）
        cycles_lo = sum(t["count"] for t in terms if t["certain"])
        # 上界：按源行去重后各时间片最大可能之和，再以 run 覆盖证明上界封顶
        per_row_hi: dict[str, int] = {}
        for term in terms:
            per_row_hi[term["source"]] = max(per_row_hi.get(term["source"], 0), term["count"])
        cycles_hi = min(run_hi, sum(per_row_hi.values()))
        if cycles_hi < cycles_lo:
            cycles_hi = cycles_lo

        role = parts[part_id].role
        sources = tuple(dict.fromkeys(t["source"] for t in terms))
        span_all = (min(t["span"][0] for t in terms), max(t["span"][1] for t in terms))
        generation = terms[0]["generation"]
        entry_legs = tuple(dict.fromkeys(t["leg"] for t in terms))
        configs = tuple(dict.fromkeys(t["config"] for t in terms))

        # 按工况把循环界限换算为寿命点：已证明循环计入下界，各组件最大可能计入上界
        points_lo = points_hi = 0
        missing_rate = False
        for term in terms:
            rate = rate_lookup.get((term["mode"], role))
            if rate is None:
                missing_rate = True
                conflicts.append(Conflict(
                    "missing-rate", f"缺少 mode={term['mode']} role={role} 的扣减率",
                    (term["source"],), run=run, part=part_id,
                ))
                continue
            if term["certain"]:
                points_lo += term["count"] * rate
            points_hi += term["count"] * rate
        if missing_rate:
            continue
        note = "归属已由封定 leg 完全证明" if cycles_lo == cycles_hi else \
            "跨界/重叠用量保留最坏最好界限，不可臆断归新件"
        entries.append(UsageEntry(
            part_id=part_id, run=run, mode=terms[0]["mode"],
            cycles_lo=cycles_lo, cycles_hi=cycles_hi,
            points_lo=points_lo, points_hi=points_hi,
            sources=sources, span=span_all,
            generation=generation, certain=cycles_lo == cycles_hi, note=note,
            legs=entry_legs, configs=configs,
        ))

    entries.sort(key=lambda e: (e.part_id, e.run))
    return entries, run_reconciles, conflicts, mirrors


def _run_of_source(rows: list[CycleRow], source_id: str) -> str:
    return next(r.run for r in rows if r.id == source_id)
