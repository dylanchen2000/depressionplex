"""DP-136 · FST 独立研究入口 CLI：真视频 → 研究诊断 + 叠加短片。

用法（仓库外落盘，`--out-dir` 在仓库里会被拒）：

    python -m depressionplex.cli.fst_research <视频> --out-dir ~/Work/fst_diag \\
        --manifest docs/共用输入身份清单_v1.csv

产三件，全在 `--out-dir`：
- `诊断_<名>.json` —— 研究诊断记录（schema `fst-research-v1`）；
- `叠加_<名>_杯<k>.mp4` —— 每杯一段带帧号/状态烧字的叠加短片，
  给人核"我们看的是不是对的动物区域"（Spec A §5.1）；
- `几何提案_<名>.json` —— 杯体/水线**提案**（confirmed=False），
  人工确认的工作底稿，不是确认件。

退出码：0 = 记录产出且至少一杯看得见动物（或有申报空杯）；
3 = 记录产出但**没有一杯看得见动物**（"看得清"这步没过，Spec A §5.1，
    记录照写、照实报，但不许拿退出码 0 假装这步过了）；
2 = 守卫/用法拒绝；1 = 解码失败。

**本入口不套标准窗**：t0 没给（`--t0-source-s`）时 `protocol_alignment=unknown`，
诊断走整条媒体时间轴。t0 要人给并附证据字符串，脚本不猜、不从清单猜。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from .. import video
from ..assay_core import silhouette as sil
from ..fst_research import cup_features as feat
from ..fst_research import cup_geometry as cg
from ..fst_research import cup_perception as perc
from ..fst_research import isolation
from ..fst_research import overlay as ov
from ..fst_research import record as rec
from ..fst_research import timeline as tl
from ._stdio import force_utf8

EXIT_OK = 0
EXIT_DECODE = 1
EXIT_REFUSED = 2
EXIT_NOTHING_VISIBLE = 3

#: 每杯诊断记录里保留的非 observed 逐帧行上限。超出只留计数与 top 原因——
#: 记录是给人读的，不是数据库。
MAX_REASON_ROWS = 500


def _parse_indices(text: str) -> list[int]:
    if not text.strip():
        return []
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        v = int(part)
        if v < 1:
            raise ValueError(f"--declared-empty 的杯号从 1 起：{v}")
        out.append(v - 1)
    return sorted(set(out))


def main(argv: list[str] | None = None) -> int:
    force_utf8()
    ap = argparse.ArgumentParser(
        prog="fst_research",
        description="FST 独立研究入口：真视频 → 研究诊断 + 叠加短片（不进仓库、不套标准窗）")
    ap.add_argument("video", type=Path, help="原视频路径")
    ap.add_argument("--out-dir", type=Path, required=True,
                    help="落盘目录；仓库工作树/data//tests/ 一律拒绝")
    ap.add_argument("--step", type=int, default=5,
                    help="分析抽帧步长（帧）；默认 5 = 25 fps 下 5 Hz")
    ap.add_argument("--cups", type=int, default=4, help="期望杯数；数目不符即报问题")
    ap.add_argument("--calib", type=int, default=40, help="背景/几何标定的抽帧数")
    ap.add_argument("--declared-empty", default="",
                    help="人工申报的空杯号（1 起，逗号分隔）。空杯只认申报")
    ap.add_argument("--clip", type=int, default=100, help="每杯叠加短片的分析帧数")
    ap.add_argument("--clip-at", type=float, default=0.4,
                    help="短片起点取分析序列的位置比例（默认 40%% 处，避开开场手忙脚乱）")
    ap.add_argument("--manifest", type=Path, default=None,
                    help="DP-133 共用身份清单；不给则身份关联记 null")
    ap.add_argument("--t0-source-s", type=float, default=None,
                    help="入水时刻（源媒体时间，秒）。要人给，脚本不猜")
    ap.add_argument("--t0-evidence", default="",
                    help="--t0-source-s 的依据字符串；给 t0 不给依据即拒")
    ap.add_argument("--self-check", action="store_true",
                    help="只跑路径隔离自检（静态 AST 闸），不碰视频")
    ap.add_argument("--dark", type=float, default=96.0, help="暗阈（起点值，换采集条件要重看直方图）")
    ap.add_argument("--bright", type=float, default=150.0, help="亮阈（同上）")
    ap.add_argument("--min-area", type=int, default=40, help="候选连通域最小面积（px）")
    args = ap.parse_args(argv)

    if args.self_check:
        violations, files = isolation.self_check()
        print(f"路径隔离自检：扫了 {len(files)} 个文件")
        for f in files:
            print(f"  - {f}")
        if violations:
            print(f"违规 {len(violations)} 条：", file=sys.stderr)
            for v in violations:
                print(f"  ! {v}", file=sys.stderr)
            return EXIT_REFUSED
        print("违规 0 条：研究入口未引用任何 TST 专属符号/被禁模块")
        return EXIT_OK

    if args.t0_source_s is not None and not args.t0_evidence.strip():
        print("给了 --t0-source-s 却没给 --t0-evidence：t0 是科学事实不是参数，"
              "必须带依据字符串", file=sys.stderr)
        return EXIT_REFUSED
    try:
        declared = _parse_indices(args.declared_empty)
        out_dir = ov.refuse_in_repo(args.out_dir)
    except ValueError as e:
        print(f"拒绝：{e}", file=sys.stderr)
        return EXIT_REFUSED

    try:
        info = video.probe(args.video)
    except video.VideoError as e:
        print(f"解码/探测失败：{e}", file=sys.stderr)
        return EXIT_DECODE

    sha = rec.sha256_of(info.path)
    lookup = rec.manifest_lookup(args.manifest, sha) if args.manifest else {
        "found": False, "aliases": [], "material_id": None,
        "note": "未提供 --manifest：不做身份关联，不猜"}

    tb = tl.TimeBase(
        fps=info.fps, n_frames=info.n_frames, clock=tl.CLOCK_SOURCE_MEDIA,
        protocol_alignment=(tl.PROTOCOL_ALIGNMENT_KNOWN if args.t0_source_s is not None
                            else tl.PROTOCOL_ALIGNMENT_UNKNOWN),
        t0_source_s=args.t0_source_s,
        analysis_offset_s=None,
        offset_evidence="本次直接读原视频，未经转码件",
        frame_count_source=info.frame_count_source)
    ledger = tl.build_ledger(tb)
    plan = tl.plan_window(tb)

    # ---- 标定：背景 max + 杯体/水线提案 ----
    calib_idx = [int(v) for v in np.linspace(0, info.n_frames - 1, args.calib)]
    try:
        calib_frames = video.frames_at(info, calib_idx)
    except video.VideoError as e:
        print(f"标定抽帧失败：{e}", file=sys.stderr)
        return EXIT_DECODE
    # propose_cups 内部用中值帧提水体/水线，并做行梯度旁证；这里不再二次加工
    props, geo_problems = cg.propose_cups(calib_frames, n_cups=args.cups,
                                          dark=args.dark, bright=args.bright)
    env = cg.to_envelope(props, (info.width, info.height))
    env_problems = env.validate()

    declared_set = set(declared)
    for i in sorted(declared_set):
        if i >= len(props):
            print(f"拒绝：申报空杯 #{i + 1} 但只找到 {len(props)} 个杯", file=sys.stderr)
            return EXIT_REFUSED

    # ---- 分析序列与短片窗口 ----
    analyzed = list(range(0, info.n_frames, args.step))
    start_pos = int(len(analyzed) * min(max(args.clip_at, 0.0), 0.95))
    clip_frames = set(analyzed[start_pos:start_pos + args.clip])

    writers: dict[int, ov.ClipWriter] = {}
    clip_masks: dict[int, dict[int, np.ndarray]] = {p.index: {} for p in props}
    last_seen: dict[int, tuple[int, np.ndarray, tuple[float, float], float] | None] = {
        p.index: None for p in props}
    pairs: dict[int, list[feat.PairFeatures]] = {p.index: [] for p in props}
    diags_all: dict[int, list[perc.FrameDiag]] = {}
    diagnosers: dict[int, perc.CupDiagnoser] = {}

    def make_on_observed(cup: int):
        def on_observed(idx: int, mask: np.ndarray, diag: perc.FrameDiag) -> None:
            if idx in clip_frames:
                clip_masks[cup][idx] = mask
            assert diag.centroid is not None      # observed 帧必有质心
            theta = _theta_of(mask)
            prev = last_seen[cup]
            if prev is not None:
                p_idx, p_mask, p_cent, p_theta = prev
                pairs[cup].append(feat.pair_features(
                    frame_prev=p_idx, frame_cur=idx, fps=info.fps,
                    mask_prev=p_mask, mask_cur=mask,
                    cent_prev=p_cent, cent_cur=diag.centroid,
                    theta_prev=p_theta, theta_cur=theta,
                    spatial_scale_px=float(props[cup].width_px),
                    above_water_frac_cur=diag.above_water_frac,
                    wall_dist_px_cur=diag.wall_dist_px))
            last_seen[cup] = (idx, mask, diag.centroid, theta)
        return on_observed

    for p in props:
        diagnosers[p.index] = perc.CupDiagnoser(
            p, dark=args.dark, min_area=args.min_area,
            declared_absent=(p.index in declared_set),
            on_observed=None if p.index in declared_set else make_on_observed(p.index))

    # pass 1：背景 max（只过分析帧）
    try:
        for i, g in enumerate(video.iter_gray(info)):
            if i % args.step:
                continue
            for d in diagnosers.values():
                if not d.declared_absent:
                    d.see_background(g)
        # pass 2：逐帧诊断 + 短片
        for i, g in enumerate(video.iter_gray(info)):
            if i % args.step:
                continue
            for p in props:
                diagnosers[p.index].diagnose(i, g)
            if i in clip_frames:
                rgb = _render(g, props, {c: clip_masks[c].get(i) for c in clip_masks},
                              {c: diagnosers[c].last() for c in diagnosers},
                              i, info.fps)
                for c in clip_masks:
                    if c not in writers:
                        writers[c] = ov.ClipWriter(
                            out_dir / f"叠加_{info.path.stem}_杯{c + 1}.mp4",
                            fps=info.fps / args.step, size=(info.width, info.height))
                    writers[c].write(rgb)
    except video.VideoError as e:
        print(f"解码失败：{e}", file=sys.stderr)
        for w in writers.values():
            w.abort()          # 半截 mp4 不许留在磁盘上冒充证据
        return EXIT_DECODE

    clips: dict[int, str] = {}
    for c, w in writers.items():
        clips[c] = str(w.close())

    # ---- 组装记录 ----
    cups_out = []
    any_visible = False
    for p in props:
        diags = diagnosers[p.index].finish()
        diags_all[p.index] = diags
        counts = rec.counts_of(diags)
        if counts[perc.QUALITY_OBSERVED] or p.index in declared_set:
            any_visible = True
        cups_out.append(rec.cup_record(
            cup_index=p.index, diags=diags, fps=info.fps,
            declared_absent=(p.index in declared_set),
            geometry={"interior": list(p.interior),
                      "water_surface_y": p.water_surface_y,
                      "water_surface_basis": p.water_surface_basis,
                      "water_surface_candidates": list(p.water_surface_candidates),
                      "basis": p.basis, "confirmed": p.confirmed,
                      # 提案 notes（旁证分歧/无活动/误滤提示）必须随记录落盘：
                      # 它们是给人看的诚实机制，只打在终端上等于没交付。
                      "notes": list(p.notes)},
            features_summary=feat.summarize(pairs[p.index]),
            spatial_scale_px=float(p.width_px)))
        cups_out[-1]["frame_rows"] = _frame_rows(diags, clip_frames)
        cups_out[-1]["overlay_clip"] = clips.get(p.index)

    coverage = tl.coverage_of(plan, n_frames=info.n_frames, observed=analyzed)
    record = rec.build_record(
        source={"path": str(info.path), "name": info.path.name,
                "sha256": sha, "bytes": info.path.stat().st_size,
                "fps": info.fps, "n_frames": info.n_frames,
                "width": info.width, "height": info.height,
                "duration_s": info.duration_s,
                "frame_count_source": info.frame_count_source},
        time_base={"clock": tb.clock, "protocol_alignment": tb.protocol_alignment,
                   "t0_source_s": tb.t0_source_s,
                   "analysis_offset_s": tb.analysis_offset_s,
                   "offset_evidence": tb.offset_evidence},
        clock_ledger=ledger.to_dict(),
        window={"requested_s": list(plan.requested_s), "alignment": plan.alignment,
                "media_frames": list(plan.media_frames) if plan.media_frames else None,
                "applies_standard_window": plan.applies_standard_window,
                "reason": plan.reason, "truncated": plan.truncated},
        coverage=coverage.to_dict(),
        geometry_confirmation={"confirmed": False,
                               "validate_problems": env_problems,
                               "proposal_problems": geo_problems,
                               "note": "几何为提案：人工确认前正式解释与发布验收受限"},
        cups=cups_out,
        manifest=lookup,
        limits=[
            "研究诊断：不产出正式 CSV、不进验收路径、不借 TST 发布/标定资质",
            "t0 未知 ⇒ protocol_alignment=unknown，未套 (120,360) 标准窗",
            "杯体/水线为提案（confirmed=False），未人工确认",
            "observed 帧内的活动/不动分类未做（新的科学口径，未经批准）",
            "光流/局部运动（动物区内 vs 区外水扰）无实现，标记 not_implemented",
            f"分析抽帧 step={args.step}：帧间细节（< {args.step / info.fps:.2f} s）不可见",
        ])
    rec_path = rec.write_record(record, out_dir / f"诊断_{info.path.stem}.json")
    geo_path = ov.refuse_in_repo(out_dir / f"几何提案_{info.path.stem}.json")
    geo_path.write_text(env.to_json(), encoding="utf-8")

    # ---- 人读摘要 ----
    print(f"源：{info.path.name}  sha256 {sha[:16]}…  {info.n_frames} 帧 @ {info.fps} fps"
          f"（{info.duration_s:.2f} s，帧数来源 {info.frame_count_source}）")
    print(f"对齐：protocol_alignment={tb.protocol_alignment}"
          f"（{plan.reason}）")
    print(f"覆盖：{coverage.observed_frames}/{coverage.requested_frames or '整条'} 帧"
          f"  截断 {coverage.truncated_frames}")
    for c in cups_out:
        q = c["quality_counts"]
        print(f"  杯 {c['cup'] + 1}: observed {q['observed']}  unclear {q['unclear']}"
              f"  lost_short {q['lost_short']}  declared_absent {q['declared_absent']}"
              f"  水线 {c['geometry']['water_surface_y']}")
        for n in c["geometry"]["notes"]:
            print(f"        注：{n}")
        if c["overlay_clip"]:
            print(f"        叠加短片 {c['overlay_clip']}")
    print(f"几何提案问题 {len(env_problems) + len(geo_problems)} 条（未确认是设计，不是失败）")
    for gp in geo_problems:
        print(f"  - {gp}")
    print(f"记录：{rec_path}")
    return EXIT_OK if any_visible else EXIT_NOTHING_VISIBLE


def _theta_of(mask: np.ndarray) -> float:
    m = sil.metrics(mask, with_holes=False)
    return m.theta if m is not None else 0.0


def _frame_rows(diags: list[perc.FrameDiag], clip_frames: set[int]) -> list[dict]:
    rows = []
    reasons = 0
    for d in diags:
        if d.quality != perc.QUALITY_OBSERVED and reasons < MAX_REASON_ROWS:
            reasons += 1
            rows.append({"frame": d.frame, "quality": d.quality,
                         "reasons": list(d.reasons)})
        elif d.frame in clip_frames:
            rows.append({"frame": d.frame, "quality": d.quality,
                         "area_px": d.area_px,
                         "centroid": list(d.centroid) if d.centroid else None,
                         "above_water_frac": d.above_water_frac,
                         "wall_dist_px": d.wall_dist_px})
    return rows


def _render(g, props, masks, last_diags, idx, fps) -> np.ndarray:
    rgb = ov.gray_to_rgb(g)
    for p in props:
        r0, c0, r1, c1 = p.interior
        d = last_diags.get(p.index)
        q = d.quality if d is not None else "declared_absent"
        color = ov.QUALITY_COLORS.get(q, ov.COLOR_UNCLEAR)
        ov.draw_rect(rgb, r0, c0, r1, c1, ov.COLOR_TANK)
        if p.water_surface_y is not None:
            ov.draw_hline(rgb, int(round(p.water_surface_y)), c0, c1, ov.COLOR_WATER)
        m = masks.get(p.index)
        if m is not None:
            ov.draw_mask_outline(rgb, m, color)
        label = f"C{p.index + 1} F{idx} T{idx / fps:.1f}S {q[:4].upper()}"
        ov.draw_text_outlined(rgb, max(0, r0 - 8), c0, label, color)
    return rgb


if __name__ == "__main__":
    raise SystemExit(main())
