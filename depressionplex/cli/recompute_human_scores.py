#!/usr/bin/env python3
"""DP-012：人工评分校验器 + 并集重算，产出 43 试次重算表。

用法（在仓库根目录）：
    python3 -m depressionplex.cli.recompute_human_scores \\
        [-d data/human_scores/raw]
        [-o data/human_scores/recomputed/human_scores_recomputed_DP-012.csv]
        [--repeats-output <已声明重评的另存路径>]
        [--strict]

退出码：0 正常；1 有完整性告警（越窗/乱序编号/CSV 对不上账）；
2（--strict）有拒绝入库试次——"报错"的另一种写法，给流水线用。

同一 `(scorer_id, trial_id)` 出现两条读数且读数不同时**报错停机**（DP-082/
DP-124），出路是在数据目录下写 `rescore_declarations.csv`；被声明为复评的读数
不进重算表，用 `--repeats-output` 另存。

本工具**不算任何一致性指标**（那是 DP-013/DP-014 的事）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..human_agreement import build_table, format_report, write_table_csv
from . import _stdio

ROOT = Path(__file__).resolve().parents[2]   # depressionplex/cli/x.py → 仓库根


def main() -> int:
    _stdio.force_utf8()

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-d", "--data-dir", type=Path,
                    default=ROOT / "data" / "human_scores" / "raw")
    ap.add_argument("-o", "--output", type=Path, default=None,
                    help="重算表 CSV 输出路径；不给则只打印报告")
    ap.add_argument("--repeats-output", type=Path, default=None,
                    help="已声明重评（复评）的另存路径；只做同一评分员的组内"
                         "重测一致性，**不参与评分员间一致性**")
    ap.add_argument("--strict", action="store_true",
                    help="有拒绝入库试次即退出码 2")
    args = ap.parse_args()

    res = build_table(args.data_dir)
    print(format_report(res))
    if args.output:
        write_table_csv(res.rows, args.output)
        print(f"\n重算表已写出: {args.output}")
    if args.repeats_output:
        # 一条复评都没有时照样写出（只有表头）：拿不到文件与"确实没有复评"
        # 是两件事，不许让下游靠文件存不存在去猜。
        write_table_csv(res.repeat_rows, args.repeats_output)
        print(f"已声明重评另存: {args.repeats_output}（{res.n_repeats} 条）")

    integrity = bool(res.window_violations or res.order_mismatches
                     or res.crosscheck_mismatches)
    if integrity:
        return 1
    if args.strict and res.n_rejected:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
