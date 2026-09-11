#!/usr/bin/env python3
"""DP-094：CSI（FST）结果 × 人工评分 对照表。

用法（在仓库根目录）：
    python3 -m depressionplex.cli.csi_fst_compare \\
        --csi-dir <CSI 导出目录> \\
        --human-dir data/human_scores/incoming \\
        --out data/derived/fst_human_vs_csi.csv

按 (recording, chamber) 把 CSI 行和人工行连起来。人工行只用
`human_agreement.load_audit_json` 出来的 TrialRow，取 immobility_s（并集口径，DP-010 起）。
人工同一场有多人多批 → **每人每批一行，不许平均、不许挑一个**。

铁律（R2/R4）：
  - CSI 侧不动时长只用 Ranges 口径（csi_immobile_ranges_s）。
  - csi_immobile_frames_time_s / csi_immobile_average_s **只作体检列原样输出**，
    任何聚合都不许用它们。
  - 本工具**不设门槛、不判定 CSI 对错、不出"通过/不通过"**，只出对照列。

退出码：0 正常；2 解析/连接错误（CsiParseError 等）。
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

from ..csi.fst_import import (
    CsiParseError,
    RECORDING_ALIASES,
    immobile_ranges_s,
    load_csi_dir,
)
from ..human_agreement import load_audit_json

ROOT = Path(__file__).resolve().parents[2]   # depressionplex/cli/x.py → 仓库根

#: 输出列，顺序钉死（规格 §5）。
COLUMNS = [
    "recording", "chamber", "trial_id",
    "scorer_id", "batch_date", "human_window_s", "human_immobility_s",
    "human_n_hold_segments", "human_holds_unsorted", "human_naive_inflation_s",
    "csi_window_s", "csi_immobile_ranges_s", "csi_swim_s", "csi_escape_s", "csi_passdive_s",
    "csi_climb_ranges_s", "csi_immobile_frames_time_s", "csi_immobile_average_s",
    "delta_csi_minus_human_s", "window_mismatch_s",
]

_BATCH_DATE_RE = re.compile(r"_(\d{4}-\d{2}-\d{2})_")
_FST_PREFIX_RE = re.compile(r"^FST-")


def _f(v: float | None, nd: int = 2) -> str:
    """浮点 → CSV 字符串；None → 空。抹掉累加/相减的尾数。"""
    if v is None:
        return ""
    return f"{round(float(v), nd):.{nd}f}"


def _bool(v: bool | None) -> str:
    if v is None:
        return ""
    return "True" if v else "False"


def build_rows(csi_dir: Path, human_dir: Path) -> tuple[list[dict], list[str]]:
    """产出对照行（dict，键为 COLUMNS）+ 未覆盖录像名列表。"""
    tanks, _set_params, _clbs = load_csi_dir(csi_dir)

    # (recording, chamber) → CsiTank；重复就是数据问题，报出来不猜
    chamber_map: dict[tuple[str, int], object] = {}
    for t in tanks:
        key = (t.recording, t.chamber)
        if key in chamber_map:
            raise CsiParseError(f"CSI 孔位重复: {key} 来自 {chamber_map[key].source_file} 和 {t.source_file}")
        chamber_map[key] = t

    audit_files = sorted(human_dir.glob("timer_audit_FST_*.json"))
    if not audit_files:
        raise CsiParseError(f"{human_dir}: 没有 timer_audit_FST_*.json")

    rows: list[dict] = []
    uncovered: set[str] = set()
    for path in audit_files:
        m = _BATCH_DATE_RE.search(path.name)
        if not m:
            raise CsiParseError(f"审计文件名取不到 batch_date（需 _YYYY-MM-DD_）: {path.name}")
        batch_date = m.group(1)
        header, hrows = load_audit_json(path, on_reject="mark")
        scorer_id = header["scorer_id"]
        for r in hrows:
            recording = _FST_PREFIX_RE.sub("", r.video)
            recording = RECORDING_ALIASES.get(recording, recording)
            tank = chamber_map.get((recording, r.chamber))
            if tank is None:
                uncovered.add(recording)
                continue
            accepted = (r.status == "accepted")
            human_imm = r.immobility_s if accepted else None
            csi_imm = immobile_ranges_s(tank)
            stats = tank.statistics
            csi_ft = stats.get("Frames/Time", {}).get("Immobile")
            csi_avg = stats.get("Average of Frames and Ranges", {}).get("Immobile")
            delta = (csi_imm - human_imm) if (human_imm is not None) else None
            win_mis = (tank.window_s - r.window_s) if (r.window_s is not None) else None
            rows.append({
                "recording": recording,
                "chamber": r.chamber,
                "trial_id": r.trial_id,
                "scorer_id": scorer_id,
                "batch_date": batch_date,
                "human_window_s": _f(r.window_s),
                "human_immobility_s": _f(human_imm),
                "human_n_hold_segments": r.n_hold_segments,
                "human_holds_unsorted": _bool(r.holds_unsorted),
                "human_naive_inflation_s": _f(r.naive_inflation_s),
                "csi_window_s": _f(tank.window_s),
                "csi_immobile_ranges_s": _f(csi_imm),
                "csi_swim_s": _f(tank.ranges_s.get("Swim", 0.0)),
                "csi_escape_s": _f(tank.ranges_s.get("Escape", 0.0)),
                "csi_passdive_s": _f(tank.ranges_s.get("PassDive", 0.0)),
                "csi_climb_ranges_s": _f(tank.ranges_s.get("Climb", 0.0)),
                "csi_immobile_frames_time_s": _f(csi_ft),
                "csi_immobile_average_s": _f(csi_avg),
                "delta_csi_minus_human_s": _f(delta),
                "window_mismatch_s": _f(win_mis),
                # 内部用，不写进 CSV
                "_delta": delta,
            })

    rows.sort(key=lambda d: (d["recording"], d["chamber"], d["scorer_id"], d["batch_date"], d["trial_id"]))
    return rows, sorted(uncovered)


def markdown_summary(rows: list[dict]) -> str:
    """每个 (scorer_id, batch_date) 一行：场数 / 平均|CSI−人工| / 偏差均值(CSI−人工)。

    **不许有任何"通过/不通过"字样**（R4）。均值只对有 delta 的行算。
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for d in rows:
        groups[(d["scorer_id"], d["batch_date"])].append(d)

    lines = [
        "| scorer_id | batch_date | 场数 | 平均\\|CSI−人工\\| (s) | 偏差均值(CSI−人工) (s) |",
        "|---|---|---|---|---|",
    ]
    for (scorer, batch) in sorted(groups):
        grp = groups[(scorer, batch)]
        deltas = [d["_delta"] for d in grp if d["_delta"] is not None]
        n = len(grp)
        if deltas:
            mean_abs = sum(abs(x) for x in deltas) / len(deltas)
            mean_dev = sum(deltas) / len(deltas)
            lines.append(f"| {scorer} | {batch} | {n} | {mean_abs:.2f} | {mean_dev:+.2f} |")
        else:
            lines.append(f"| {scorer} | {batch} | {n} |  |  |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csi-dir", type=Path, required=True,
                    help="CSI 导出目录（含 28 个孔位 xlsx + .SET + .CLB）")
    ap.add_argument("--human-dir", type=Path,
                    default=ROOT / "data" / "human_scores" / "incoming")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "data" / "derived" / "fst_human_vs_csi.csv")
    args = ap.parse_args(argv)

    try:
        rows, uncovered = build_rows(args.csi_dir, args.human_dir)
    except CsiParseError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2

    for rec in uncovered:
        print(f"CSI 未覆盖：{rec}", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for d in rows:
            w.writerow(d)

    print(markdown_summary(rows))
    print(f"\n对照表已写出: {args.out}（{len(rows)} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
