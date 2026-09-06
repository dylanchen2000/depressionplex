#!/usr/bin/env python3
"""把一段录像跑成 trial 级数字（TST / FST）。

    python3 -m depressionplex.cli.analyze 视频.mp4 --assay TST
    python3 -m depressionplex.cli.analyze 视频.mp4 --assay TST --chambers 4 \
        --csv out.csv

输出的每个 immobility 数字都带分母（可评分帧/窗口帧、unknown 占比、
`TrialValidity`）——无分母的指标不可审计，这是我方对 CSI 的差异化，也是
GLP 硬要求。**排除态隔间不产数字**，只产报警行（`score_gate`，DP-028 N1/N3）。

退出码：0 = 至少一个隔间出了数字；2 = 一个都没出（不许静默返回 0 让上游
以为跑过了）；1 = 解码/探测失败。
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from .. import runner, video
from ..assay_core import trial_report


def _plan_text(info: video.VideoInfo, plan: runner.TrialPlan) -> str:
    L = [f"==== 素材 {info.path.name} ====",
         f"  fps={info.fps:g}（读自文件）｜帧数={info.n_frames}"
         f"（来源 {info.frame_count_source}）｜画幅 {info.width}×{info.height}"
         f"｜时长 {info.duration_s:.2f} s",
         f"  标定抽帧 {len(plan.calib_indices)} 帧："
         f"{list(plan.calib_indices[:6])}{'…' if len(plan.calib_indices) > 6 else ''}",
         f"  隔间 {len(plan.chambers)} 个（结构立柱提案，非人工确认几何）："]
    for ch in plan.chambers:
        s = ("走廊未标定" if ch.corridor is None
             else f"走廊列 {ch.corridor.col_range}、带 {ch.corridor.band_range}、"
                  f"BL估计 {ch.corridor.bl_est if ch.corridor.bl_est else '不可估'}"
                  f"{'、**未收口**' if not ch.corridor.sealed else ''}")
        L.append(f"    ch{ch.index}  列 {ch.col_range}（宽 {ch.width}）｜{s}"
                 f"｜悬挂点 {ch.suspension}")
    for w in plan.warnings:
        L.append(f"  [警告] {w}")
    for cv in plan.trial_validity.chambers:
        L.append(f"  有效性 ch{cv.chamber}: {cv.status}"
                 f"｜在场占比={cv.occupied_fraction}"
                 f"｜不可分割帧占比={cv.unsegmentable_fraction}"
                 f"｜{cv.note or '—'}")
    return "\n".join(L)


CSV_FIELDS = ("trial_id", "assay", "fps", "recording_frames", "window_frames",
              "scorable_frames", "unknown_frames_window", "validity_status",
              "occupied_fraction", "scored", "immobility_s", "immobility_raw_s",
              "mobility_s", "mobility_bouts", "first_mobility_onset_s",
              "gate_messages")


def _row(r: trial_report.TrialReport) -> dict[str, object]:
    mob = r.categories.get("Mobility")
    return {
        "trial_id": r.trial_id, "assay": r.assay, "fps": r.fps,
        "recording_frames": r.recording_frames,
        "window_frames": r.window_frames,
        "scorable_frames": r.scorable_frames_window,
        "unknown_frames_window": r.unknown_frames_window,
        "validity_status": r.validity_status,
        "occupied_fraction": r.occupied_fraction,
        "scored": r.scored,
        # 未放行 ⇒ 留空，**不填 0**：0 会被下游读成"一秒都没不动"。
        "immobility_s": ("" if r.immobility_mirror_pipeline_s is None
                         else round(r.immobility_mirror_pipeline_s, 3)),
        "immobility_raw_s": ("" if r.immobility_mirror_raw_s is None
                             else round(r.immobility_mirror_raw_s, 3)),
        "mobility_s": "" if mob is None else round(mob.seconds_pipeline, 3),
        "mobility_bouts": "" if mob is None else mob.bouts,
        "first_mobility_onset_s": ("" if mob is None or mob.first_onset_s is None
                                   else round(mob.first_onset_s, 3)),
        "gate_messages": " | ".join(r.gate_messages),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="录像 → trial 级 immobility 数字")
    ap.add_argument("video", type=Path)
    ap.add_argument("--assay", required=True, choices=sorted(trial_report.ASSAY_WINDOWS),
                    help="计分窗口两范式不许抹平：TST 全程 6 min / FST 后 4 min")
    ap.add_argument("--chambers", type=int, default=4)
    ap.add_argument("--calib-frames", type=int, default=runner.N_CALIB_FRAMES)
    ap.add_argument("--trial-prefix", default=None,
                    help="试次号前缀，默认取文件名（每隔间后缀 -chN，左起为序）")
    ap.add_argument("--body-area-prior", type=float, default=None,
                    help="身体级面积绝对先验（硬件规格级）；整批脱落时相对判据不可决")
    ap.add_argument("--csv", type=Path, default=None)
    args = ap.parse_args(argv)

    try:
        info, plan, reports, skipped = runner.analyze_video(
            args.video, assay=args.assay, n_chambers=args.chambers,
            trial_prefix=args.trial_prefix, n_calib=args.calib_frames,
            body_area_prior=args.body_area_prior)
    except video.VideoError as e:
        print(f"[解码失败] {e}", file=sys.stderr)
        return 1

    print(_plan_text(info, plan))
    for k in sorted(reports):
        print()
        print(trial_report.trial_report_text(reports[k]))
    for k in sorted(skipped):
        print()
        print(f"==== ch{k}：**未产出数字** ====\n  {skipped[k]}")

    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            w.writeheader()
            for k in sorted(reports):
                w.writerow(_row(reports[k]))
        print(f"\nCSV 已写：{args.csv}（{len(reports)} 行；"
              f"未产出的 {len(skipped)} 个隔间不写进 CSV，见上面的原因）")

    if not reports:
        print("\n[结果] 没有任何隔间产出数字——退出码 2", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
