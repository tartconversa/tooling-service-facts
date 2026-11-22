"""命令行查询界面（仅标准库）。

用法示例：
  python -E -m toolhistory.cli summary
  python -E -m toolhistory.cli part jaw1
  python -E -m toolhistory.cli runs
  python -E -m toolhistory.cli conflicts
  python -E -m toolhistory.cli orders
  python -E -m toolhistory.cli inventory --at 120
  python -E -m toolhistory.cli backfill 3
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .repository import ToolHistoryRepository

DEFAULT_SOURCES = Path(__file__).resolve().parents[1] / "sources"


def _repo(args) -> ToolHistoryRepository:
    return ToolHistoryRepository.from_sources(args.sources)


def cmd_summary(args):
    repo = _repo(args)
    print("== 部件余量（best / worst，点）==")
    for pid in sorted(repo.dataset.parts):
        ledger = repo.ledger(pid)
        best, worst = ledger.base_remaining()
        flag = " *不确定" if ledger.uncertain else ""
        print(f"  {pid:6s} {ledger.role:4s} {ledger.model:3s} "
              f"design={ledger.design:4d}  remaining best={best:4d} worst={worst:4d}{flag}")
    print(f"\n冲突 {len(repo.all_conflicts())} 条，承诺冲突 {len(repo.commitment_conflicts)} 笔")


def cmd_part(args):
    repo = _repo(args)
    history = repo.part_history(args.part_id)
    ledger = history["ledger"]
    print(f"== {args.part_id}（{ledger.role}/{ledger.model}, design={ledger.design}）==")
    print(f"余量 best={history['remaining_best']} worst={history['remaining_worst']} "
          f"世代数={len(ledger.epochs)} 不确定={ledger.uncertain}")
    print("\n-- 封定装配 --")
    for leg in history["legs"]:
        print(f"  [{leg.start:3d},{leg.end:3d}) {leg.tool}@{leg.line} "
              f"base={leg.base} jaw={leg.jaw} grade={leg.grade} (leg {leg.id})")
    print("\n-- 历次耗用核算 --")
    for usage in history["usages"]:
        print(f"  run={usage.run:5s} mode={usage.mode:4s} "
              f"cycles[{usage.cycles_lo},{usage.cycles_hi}] "
              f"points[{usage.points_lo},{usage.points_hi}] "
              f"sources={','.join(usage.sources)} certain={usage.certain}")
        print(f"      {usage.note}")
    print("\n-- 维修/放行凭证 --")
    for repair in history["repairs"]:
        print(f"  {repair.id:9s} {repair.kind:8s} @{repair.effective} "
              f"restore={repair.restore} config={list(repair.configuration)} modes={list(repair.modes)}")
    print("\n-- 借用占用切片 --")
    for holding in history["holdings"]:
        print(f"  [{holding.start:3d},{holding.end:3d}) {holding.status:10s} "
              f"{holding.reference}: {holding.detail}")


def cmd_runs(args):
    repo = _repo(args)
    print("== run 覆盖证明（mirror 转发不计入）==")
    for rec in repo.run_reconciliation():
        kind = "确定" if rec.certain else "界限"
        print(f"  run={rec.run:5s} tool={rec.tool} gen={rec.generation} mode={rec.mode} "
              f"cycles[{rec.cycles_lo},{rec.cycles_hi}] {kind} sources={','.join(rec.sources)}")
        for note in rec.notes:
            print(f"      · {note}")
    print("\n-- mirror 出处 --")
    for source, forwards in sorted(repo.mirrors.items()):
        print(f"  {source} <- 转发 {','.join(forwards)}（不二次扣减）")


def cmd_conflicts(args):
    repo = _repo(args)
    print("== 计数/装配/凭证冲突与待核 ==")
    for code, messages in sorted(repo.conflict_codes().items()):
        print(f"[{code}] {len(messages)} 条")
        for message in messages:
            print(f"  - {message}")
    print("\n== 重复承诺冲突 ==")
    for conflict in repo.commitment_conflicts:
        print(f"  - {conflict['message']} 区间={conflict['span']}")


def cmd_orders(args):
    repo = _repo(args)
    print("== 工单可派用 ==")
    for verdict in repo.evaluate_orders():
        wo = verdict.work_order
        print(f"\n{wo.id} @{wo.line} [{wo.start},{wo.end}) grade={wo.grade} "
              f"cycles={wo.cycles} base={wo.base_model} jaws={list(wo.jaw_models)}")
        print(f"  结论: {'可派用' if verdict.dispatchable else '不可直接派用'}")
        for reason in verdict.reasons:
            print(f"  · {reason}")
        for option in verdict.options:
            status = "可派" if option.dispatchable else "受阻/边际"
            print(f"  构型 ({option.base},{option.jaw}) -> {status}")
            for avail in (option.base_avail, option.jaw_avail):
                if avail is None:
                    continue
                print(f"      {avail.part_id}: 需求 {avail.required_points} "
                      f"best={avail.remaining_best} worst={avail.remaining_worst} "
                      f"certain={avail.fits_certain} possible={avail.fits_possible}")
            for reason in option.reasons:
                print(f"      - {reason}")


def cmd_inventory(args):
    repo = _repo(args)
    print(f"== 时点 {args.at} 的可承诺库存 ==")
    for pid, view in repo.inventory(args.at).items():
        refs = ",".join(view["references"]) or "-"
        print(f"  {pid:6s} {view['role']:4s} {view['model']:3s} "
              f"status={view['status']:10s} refs={refs}")
        for detail in view["details"]:
            print(f"      {detail}")


def cmd_backfill(args):
    repo = _repo(args)
    impact = repo.backfill(args.intake)
    print(f"== 补录批次 {args.intake} 的影响 ==")
    print(f"新计数来源: {', '.join(impact.new_cycle_sources) or '无'}")
    print(f"核算改变构件: {', '.join(impact.changed_parts) or '无'}")
    print(f"受阻/缩小工单: {', '.join(impact.blocked_or_shrunk_orders) or '无'}")
    print(f"需重新确认的未发安排: {', '.join(impact.needs_reconfirm_loans) or '无'}")
    print(f"已执行而保留的借用: {', '.join(impact.executed_loans_preserved) or '无'}")
    print(f"[不确定用量] {', '.join(impact.uncertain_parts) or '无'}")
    print(f"[等待签收]   {', '.join(impact.awaiting_parts) or '无'}")
    print(f"[凭证确证]   {', '.join(impact.certified_parts) or '无'}")
    for note in impact.notes:
        print(f"  · {note}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="共享工装履历仓查询")
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("summary", help="部件余量概览").set_defaults(func=cmd_summary)

    part_p = sub.add_parser("part", help="构件历次核算")
    part_p.add_argument("part_id")
    part_p.set_defaults(func=cmd_part)

    sub.add_parser("runs", help="计数覆盖证明").set_defaults(func=cmd_runs)
    sub.add_parser("conflicts", help="冲突与待核清单").set_defaults(func=cmd_conflicts)
    sub.add_parser("orders", help="工单可派用理由").set_defaults(func=cmd_orders)

    inv = sub.add_parser("inventory", help="时点可承诺库存")
    inv.add_argument("--at", type=int, required=True)
    inv.set_defaults(func=cmd_inventory)

    bf = sub.add_parser("backfill", help="补录影响分析")
    bf.add_argument("intake", type=int)
    bf.set_defaults(func=cmd_backfill)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
