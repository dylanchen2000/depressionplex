#!/usr/bin/env python3
"""DP-061：把人工秒表的按键段导成逐段时间线（与软件侧同一种行形状）。

    python3 -m depressionplex.cli.export_human_timeline \\
        [-d data/human_scores/raw] -o data/frozen/DP-061_人工时间线.csv

**为什么要这张表**：DP-055 的问题是"总量对得上、逐个试次对不上"（平均偏差
+1.74 s，逐试次平均绝对偏差 35.15 s）。总量比不出**分歧长在哪一段**，段比得出。
软件侧同形状的表由 `analyze --timeline-csv` 出。

**口径不在这里**：并集一律走 `human_agreement.union_holds`（DP-012 已裁过的
唯一 mobile 口径：先排序再取并集，乱序的裸和只作记账）；是否采信一律看
`load_audit_json` 给的 `status`，本工具不重裁。段长之和 = 该行已发布的
`mobile_union_s`，由测试钉住。

**已知缺口**（不静默）：
- 抢救出来的 `human_scores_张_*_SALVAGED_*.csv` **没有按键段**，只有汇总数 ⇒
  这些试次进不了本表，运行时会明确列出来，不是悄悄少几行。
- 人工计时目前只有 TST。`--assay` 默认 TST；FST 秒表数据到位前不要改它。

退出码：0 正常；1 一行都没导出（不许静默返回 0 让上游以为跑过了）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..assay_core import timeline
from ..human_agreement import load_audit_json, union_holds

ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="人工秒表按键段 → 逐段时间线")
    ap.add_argument("-d", "--data-dir", type=Path,
                    default=ROOT / "data" / "human_scores" / "raw")
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--assay", default="TST",
                    help="人工计时目前只有 TST；FST 秒表数据到位前不要改")
    args = ap.parse_args(argv)

    rows: list[dict[str, object]] = []
    skipped: list[str] = []
    for path in sorted(args.data_dir.glob("timer_audit_*.json")):
        _meta, trial_rows = load_audit_json(path)
        status_by_id = {r.trial_id: r for r in trial_rows}
        raw = json.loads(path.read_text(encoding="utf-8"))
        for rec in raw.get("records", []):
            tid = rec["trial_id"]
            tr = status_by_id.get(tid)
            scorer = tr.scorer_id if tr else raw.get("scorer_id", path.stem)
            if tr is None or tr.status != "accepted":
                why = (tr.reject_reason or f"status={tr.status}") if tr else \
                    "该试次在 load_audit_json 的行里找不到 ⇒ 不采信"
                rows.append(timeline.note_row(
                    trial_id=tid, assay=args.assay,
                    source=timeline.SOURCE_HUMAN, scorer_id=scorer,
                    note=f"人工评分未采信 ⇒ 无段（不是 0）：{why}"))
                continue
            u = union_holds(rec.get("holds", []))
            note = ""
            if u.unsorted:
                note = "按键段乱序 ⇒ 已排序后取并集（裸和虚高 %.2f s，仅记账）" % (
                    u.naive_sum_s - u.total_s)
            rows += timeline.rows_from_human_holds(
                u.segments, trial_id=tid, scorer_id=scorer,
                assay=args.assay, note=note)

    for path in sorted(args.data_dir.glob("*SALVAGED*.csv")):
        skipped.append(path.name)

    n = timeline.write_csv(rows, args.output)
    seg = sum(1 for r in rows if r["start_s"] != "")
    print(f"已写 {args.output}：{n} 行（其中段 {seg} 行，说明行 {n - seg} 行）")
    if skipped:
        print("**以下文件没有按键段，进不了本表**（只有汇总数，不是悄悄少行）：")
        for s in skipped:
            print(f"  - {s}")
    if n == 0:
        print("[结果] 一行都没导出——退出码 1", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
