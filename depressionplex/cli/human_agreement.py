"""CLI：人工评分比对与验收。

用法（在仓库根目录）：

    python3 -m depressionplex.cli.human_agreement validate-human \\
        --csv a.csv --csv b.csv --package result_dir
    python3 -m depressionplex.cli.human_agreement report \\
        --package result_dir --csv a.csv --csv b.csv --out agreement_out

校验失败打印全部聚合错误并以非零退出——绝不部分落盘。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from depressionplex.human_agreement import (
    AgreementError,
    dump_report_json,
    build_report,
    load_human_csv,
    load_results,
    prefill_trace_errors,
    render_markdown,
)


def _fail(errors: list[str]) -> int:
    print("REJECTED:", file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    return 1


def cmd_validate_human(args: argparse.Namespace) -> int:
    try:
        results = load_results(Path(args.package))
        durations = {t: r.duration_sec for t, r in results.items()}
        paths = [Path(p) for p in args.csv]
        rows_by_scorer = load_human_csv(paths, durations)
        flat = [r for rws in rows_by_scorer.values() for r in rws]
        software_mobility = {t: r.duration_sec - r.immobility_seconds for t, r in results.items()}
        errs = prefill_trace_errors(flat, software_mobility)
        if errs:
            return _fail(errs)
    except AgreementError as e:
        return _fail(e.errors)
    n_trials = len({r.trial_id for r in flat})
    print(f"OK: {len(rows_by_scorer)} scorers, {n_trials} trials")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    out = Path(args.out)
    try:
        report = build_report(Path(args.package), [Path(p) for p in args.csv])
    except AgreementError as e:
        return _fail(e.errors)
    out.mkdir(parents=True, exist_ok=True)
    (out / "agreement_report.json").write_text(dump_report_json(report), encoding="utf-8")
    (out / "agreement_report.md").write_text(render_markdown(report), encoding="utf-8")
    statuses = {k: v["status"] for k, v in report["gates"].items()}
    print(f"report written to {out}")
    print("gates:", statuses)
    return 0 if all(s == "pass" for s in statuses.values()) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m depressionplex.cli.human_agreement")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("validate-human", help="校验人工 CSV（含越界拒收与预填痕迹检查）")
    p1.add_argument("--csv", action="append", required=True)
    p1.add_argument("--package", required=True, help="软件 per-trial JSON 目录")
    p1.set_defaults(fn=cmd_validate_human)
    p2 = sub.add_parser("report", help="生成 §5 全量报告与 §6 判据")
    p2.add_argument("--csv", action="append", required=True)
    p2.add_argument("--package", required=True)
    p2.add_argument("--out", required=True)
    p2.set_defaults(fn=cmd_report)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
