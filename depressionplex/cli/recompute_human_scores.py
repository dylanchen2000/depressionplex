#!/usr/bin/env python3
"""DP-012：人工评分校验器 + 并集重算，产出 43 试次重算表。

用法（在仓库根目录）：
    python3 -m depressionplex.cli.recompute_human_scores \\
        [-d data/human_scores/raw]
        [-o data/human_scores/recomputed/human_scores_recomputed_DP-012.csv]
        [--strict]

退出码：0 正常；1 有完整性告警（越窗/乱序编号/CSV 对不上账）；
2（--strict）有拒绝入库试次——"报错"的另一种写法，给流水线用。

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
    ap.add_argument("--strict", action="store_true",
                    help="有拒绝入库试次即退出码 2")
    args = ap.parse_args()

    res = build_table(args.data_dir)
    print(format_report(res))
    if args.output:
        write_table_csv(res.rows, args.output)
        print(f"\n重算表已写出: {args.output}")

    integrity = bool(res.window_violations or res.order_mismatches
                     or res.crosscheck_mismatches)
    if integrity:
        return 1
    if args.strict and res.n_rejected:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
