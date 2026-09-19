"""DP-136 · FST 独立研究入口 CLI：真视频 → 研究诊断 + 叠加短片。

用法（仓库外落盘，`--out-dir` 在仓库里会被拒）：

    python -m depressionplex.cli.fst_research <视频> --out-dir ~/Work/fst_diag \\
        --manifest docs/共用输入身份清单_v1.csv

产三件，全在 `--out-dir`：
- `诊断_<名>.json` —— 研究诊断记录（schema `fst-research-v1`）；
- `叠加_<名>_杯<k>.mp4` —— 每杯一段带帧号/状态烧字的叠加短片，
  给人核"我们看的是不是对的动物区域"（Spec A §5.1）。
  **分析 ROI、水体区、水线分开画**（复核 §9：人核的是三件不同的东西）；
- `几何提案_<名>.json` —— 确认件 wrapper（schema `fst-confirmed-geometry-v1`）
  的**提案态**：binding 已预填（视频 sha256/尺寸/杯号），几何 confirmed=False。
  人核对叠加图后改几何值、翻 confirmed、填确认人/时间，即成确认件。

**人工确认几何的输入（R2-115 G4）**：`--geometry <确认件.json>`。
加载时逐项核对 binding：整段视频 sha256、帧尺寸、cup_ids 与 envelope
实例一致且左到右升序、确认人/确认时间非空、envelope 全部 confirmed 且
validate() 干净（含严格水线区间）。任何一项不符即拒绝——确认件绑定的
不是这段素材时，拿它分析就是张冠李戴。确认件的 sha256 随诊断记录落盘。

**申报空杯绑物理杯号（R2-115 G3）**：`--declared-empty` 的杯号 = 画面
左到右的物理杯号（1 起），不是列表下标。杯候选数与 `--cups` 不符时
物理编号有歧义 ⇒ **拒绝应用申报**（照实记录，相关杯保持未决），不许
让申报顺着过滤后的下标漂移。

退出码：0 = 记录产出且至少一杯看得见动物（或有申报空杯）；
3 = 记录产出但**没有一杯看得见动物**（"看得清"这步没过，Spec A §5.1，
    记录照写、照实报，但不许拿退出码 0 假装这步过了）；
2 = 守卫/用法拒绝；1 = 解码失败。

**本入口不套标准窗**：t0 没给（`--t0-source-s`）时 `protocol_alignment=unknown`，
诊断走整条媒体时间轴。t0 要人给并附证据字符串，脚本不猜、不从清单猜。
"""

from __future__ import annotations

import argparse
import json
import math
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


def _parse_cup_ids(text: str) -> list[int]:
    """解析 `--declared-empty`：返回**物理杯号**（1 起），不减 1、不当下标。

    R2-115 G3：杯号到 cup 的绑定发生在 `cup_geometry.bind_declared_empty`
    （数目有歧义即拒绝应用），这里只做语法解析。
    """
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
        out.append(v)
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
                    help="人工申报的空杯号（**物理杯号**，1 起，逗号分隔，按画面左到右）。"
                         "空杯只认申报；杯候选数与 --cups 不符时拒绝应用（绑定有歧义）")
    ap.add_argument("--geometry", type=Path, default=None,
                    help="人工确认件（fst-confirmed-geometry-v1 wrapper JSON）。"
                         "给了就跳过自动提案，用确认件的 ROI/水线，并把 binding 与"
                         "文件 sha256 随记录落盘；binding 与本视频不符即拒绝")
    ap.add_argument("--roi-above-margin", type=int, default=cg.ROI_ABOVE_WATER_MARGIN_PX,
                    help="分析 ROI 在水线上方留白的像素数（起点值，须在叠加图上人工核对）")
    ap.add_argument("--lost-short-max-gap-frames", type=int,
                    default=perc.DEFAULT_LOST_SHORT_MAX_GAP_FRAMES,
                    help="短暂丢失门的**源帧跨度**上限（前后 observed 抽样点的实际帧号差；"
                         "起点值 11 ≈ 0.44 s @ 25 fps，未经真实素材标定，改动随记录落盘）")
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
        declared = _parse_cup_ids(args.declared_empty)
        out_dir = ov.refuse_in_repo(args.out_dir)
    except ValueError as e:
        print(f"拒绝：{e}", file=sys.stderr)
        return EXIT_REFUSED
    # ---- 数值守卫（R2-115 T3）：坏数在写任何文件**之前**拒绝 ----
    if args.step < 1:
        print(f"拒绝：--step 必须 ≥ 1（得到 {args.step}）；步长 0/负会让抽样序列退化",
              file=sys.stderr)
        return EXIT_REFUSED
    if args.lost_short_max_gap_frames < 1:
        print(f"拒绝：--lost-short-max-gap-frames 必须 ≥ 1（得到 "
              f"{args.lost_short_max_gap_frames}）", file=sys.stderr)
        return EXIT_REFUSED
    if args.min_area < 1:
        print(f"拒绝：--min-area 必须 ≥ 1（得到 {args.min_area}）", file=sys.stderr)
        return EXIT_REFUSED
    if args.roi_above_margin < 0:
        print(f"拒绝：--roi-above-margin 不能为负（得到 {args.roi_above_margin}）",
              file=sys.stderr)
        return EXIT_REFUSED
    for name, val in (("--t0-source-s", args.t0_source_s),
                      ("--dark", args.dark), ("--bright", args.bright),
                      ("--clip-at", args.clip_at)):
        if val is not None and not math.isfinite(val):
            print(f"拒绝：{name} 不是有限数（NaN/Inf）：坏数不进记录、不参与计算",
                  file=sys.stderr)
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

    # ---- 素材角色只认清单登记（R2-115 T3）：不再把任何输入口头称作"原视频" ----
    media_role, offset_evidence, role_problems = tl.resolve_media_role(
        lookup if args.manifest else None)
    if role_problems:
        for prob in role_problems:
            print(f"拒绝：{prob}", file=sys.stderr)
        return EXIT_REFUSED
    clock = (tl.CLOCK_ANALYSIS_MEDIA if media_role == tl.MEDIA_ROLE_TRANSCODE
             else tl.CLOCK_SOURCE_MEDIA)
    try:
        tb = tl.TimeBase(
            fps=info.fps, n_frames=info.n_frames, clock=clock,
            protocol_alignment=(tl.PROTOCOL_ALIGNMENT_KNOWN
                                if args.t0_source_s is not None
                                else tl.PROTOCOL_ALIGNMENT_UNKNOWN),
            t0_source_s=args.t0_source_s,
            analysis_offset_s=None,       # 未查到就是 None：known_t0+转码件会被拒
            offset_evidence=offset_evidence,
            frame_count_source=info.frame_count_source,
            t0_evidence=args.t0_evidence,
            media_role=media_role)
    except ValueError as e:
        print(f"拒绝：{e}", file=sys.stderr)
        return EXIT_REFUSED
    ledger = tl.build_ledger(tb)
    plan = tl.plan_window(tb)

    # ---- 几何来源：人工确认件（--geometry）优先，否则从真帧提案 ----
    # 标定帧两条路都要（背景 max 模型是逐帧分割的基础，与几何来源无关）。
    calib_idx = [int(v) for v in np.linspace(0, info.n_frames - 1, args.calib)]
    try:
        calib_frames = video.frames_at(info, calib_idx)
    except video.VideoError as e:
        print(f"标定抽帧失败：{e}", file=sys.stderr)
        return EXIT_DECODE
    geo_binding = None          # 人工确认件的 binding（G4）
    geo_file_sha = None         # 人工确认件自身 sha256，随记录落盘
    geo_source = "proposal"
    if args.geometry is not None:
        # 人工确认件：绑定整段视频 sha256/尺寸/杯号/确认人/时间，任何一项不符即拒。
        try:
            env, geo_binding, geo_file_sha, props = cg.load_confirmed_file(
                args.geometry, video_sha256=sha, width=info.width, height=info.height,
                video_bytes=info.path.stat().st_size)
        except (ValueError, OSError) as e:
            print(f"拒绝人工确认件：{e}", file=sys.stderr)
            return EXIT_REFUSED
        geo_problems = []       # 确认件已过 validate()，无提案问题
        geo_source = "human_confirmed_file"
        expected_n = None       # 杯号由确认件 binding 核过，不再与 --cups 比
    else:
        # propose_cups 内部用中值帧提水体/水线，并做行梯度旁证；这里不再二次加工
        props, geo_problems = cg.propose_cups(
            calib_frames, n_cups=args.cups, dark=args.dark, bright=args.bright,
            roi_above_margin=args.roi_above_margin)
        env = cg.to_envelope(props, (info.width, info.height))
        expected_n = args.cups
    env_problems = env.validate()

    # ---- 申报空杯绑物理杯号（G3）：有歧义就拒绝应用，不顺下标漂移 ----
    cup_ids = [p.cup_id for p in props]
    try:
        de_binding = cg.bind_declared_empty(cup_ids, declared, expected_n=expected_n)
    except ValueError as e:
        print(f"拒绝：{e}", file=sys.stderr)
        return EXIT_REFUSED
    for prob in de_binding.problems:
        print(f"申报空杯未应用：{prob}", file=sys.stderr)
    # declared_absent 用 CupDiagnoser 的 index（0 起）；binding.applied 是物理杯号（1 起）
    declared_set = {cid - 1 for cid in de_binding.applied}

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
        def on_observed(idx: int, mask: np.ndarray, diag: perc.FrameDiag,
                        gap_records: int) -> None:
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
                    wall_dist_px_cur=diag.wall_dist_px,
                    # R2-115 T2：跨观测缺口的对标出来，summarize 单列不进连续统计
                    gap_records_between=gap_records))
            last_seen[cup] = (idx, mask, diag.centroid, theta)
        return on_observed

    for p in props:
        diagnosers[p.index] = perc.CupDiagnoser(
            p, dark=args.dark, min_area=args.min_area, fps=info.fps,
            lost_short_max_gap_frames=args.lost_short_max_gap_frames,
            declared_absent=(p.index in declared_set),
            on_observed=None if p.index in declared_set else make_on_observed(p.index))

    # pass 1：背景 max（只过分析帧）
    consumed: list[int] = []      # 实际消费（解码并诊断）的帧号——覆盖从它数出
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
            consumed.append(i)    # R2-115 T1：真消费了才计数，不按计划表报
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
            sample_step_frames=args.step,
            declared_absent=(p.index in declared_set),
            geometry={"cup_id": p.cup_id,
                      # G1：分析 ROI / 水体候选区 / 水线是三件不同的东西，全落盘
                      "roi": list(p.roi),
                      "water_body": list(p.water_body) if p.water_body else None,
                      "water_surface_y": p.water_surface_y,
                      "water_surface_basis": p.water_surface_basis,
                      "water_surface_candidates": list(p.water_surface_candidates),
                      "water_surface_reliable": p.water_surface_reliable,
                      "water_surface_unreliable_reason": p.water_surface_unreliable_reason,
                      "basis": p.basis, "confirmed": p.confirmed,
                      # 提案 notes（旁证分歧/无活动/误滤提示）必须随记录落盘：
                      # 它们是给人看的诚实机制，只打在终端上等于没交付。
                      "notes": list(p.notes)},
            features_summary=feat.summarize(pairs[p.index]),
            spatial_scale_px=float(p.width_px)))
        cups_out[-1]["frame_rows"] = _frame_rows(diags, clip_frames)
        cups_out[-1]["overlay_clip"] = clips.get(p.index)

    coverage = tl.coverage_of(plan, n_frames=info.n_frames, consumed=consumed,
                              sample_step=args.step, fps=info.fps)
    record = rec.build_record(
        source={"path": str(info.path), "name": info.path.name,
                "sha256": sha, "bytes": info.path.stat().st_size,
                "fps": info.fps, "n_frames": info.n_frames,
                "width": info.width, "height": info.height,
                "duration_s": info.duration_s,
                "frame_count_source": info.frame_count_source},
        time_base={"clock": tb.clock, "media_role": tb.media_role,
                   "protocol_alignment": tb.protocol_alignment,
                   "t0_source_s": tb.t0_source_s,
                   # R2-115 T3：t0 的依据字符串必须随记录落盘，不只打在终端
                   "t0_evidence": tb.t0_evidence or None,
                   "analysis_offset_s": tb.analysis_offset_s,
                   "offset_evidence": tb.offset_evidence},
        clock_ledger=ledger.to_dict(),
        window={"requested_s": list(plan.requested_s), "alignment": plan.alignment,
                "media_frames": list(plan.media_frames) if plan.media_frames else None,
                "applies_standard_window": plan.applies_standard_window,
                # R2-115 T3：套窗标记 ≠ 统计已按窗截取。本次各杯统计跑在
                # 整条抽样媒体时间轴上；窗口只标"名义窗能否落点"。
                "stats_are_windowed": False,
                "stats_scope": "各杯统计覆盖整条抽样媒体时间轴（未按窗口截取）；"
                               "applies_standard_window=True 仅表示名义窗在素材上"
                               "可算出落点，不表示统计已按窗",
                "reason": plan.reason, "truncated": plan.truncated},
        coverage=coverage.to_dict(),
        # R2-115 T1：抽样口径与实际消费的帧号（计数是抽样记录数，不是连续帧数）
        sampling={"step_frames": args.step,
                  "sample_spacing_s": args.step / info.fps,
                  "sampled_frames": len(consumed),
                  "sample_fraction": (len(consumed) / info.n_frames
                                      if info.n_frames else None),
                  "frame_indices": consumed,
                  "semantics": "所有状态计数（sampled_state_counts）与时间加权"
                               "估计都是抽样记录口径，不是连续逐帧统计；短于 "
                               f"step/fps = {args.step / info.fps:.2f} s 的帧间细节不可见",
                  "tail_note": "视频末尾未被抽样的部分不补帧、不外推"},
        geometry_confirmation=_geometry_confirmation(
            geo_source=geo_source, confirmed=(geo_source == "human_confirmed_file"),
            env_problems=env_problems, geo_problems=geo_problems,
            geometry_path=args.geometry, geo_file_sha=geo_file_sha,
            geo_binding=geo_binding, de_binding=de_binding),
        cups=cups_out,
        manifest=lookup,
        limits=_limits(args, info, geo_source, de_binding))
    rec_path = rec.write_record(record, out_dir / f"诊断_{info.path.stem}.json")
    # 几何提案文件：没给确认件时，落一份**提案态 wrapper**（binding 已预填），
    # 人工核对后改几何/翻 confirmed/填确认人时间即成确认件；给了确认件就不覆盖它。
    if args.geometry is None:
        geo_path = ov.refuse_in_repo(out_dir / f"几何提案_{info.path.stem}.json")
        payload = cg.confirmation_payload(
            env, video_sha256=sha, video_bytes=info.path.stat().st_size,
            width=info.width, height=info.height, cup_ids=cup_ids,
            proposal_context={"geo_problems": geo_problems,
                              "roi_above_margin_px": args.roi_above_margin,
                              "cups": [{"cup_id": p.cup_id, "roi": list(p.roi),
                                        "water_body": list(p.water_body) if p.water_body else None,
                                        "water_surface_y": p.water_surface_y,
                                        "water_surface_reliable": p.water_surface_reliable,
                                        "water_surface_unreliable_reason": p.water_surface_unreliable_reason,
                                        "notes": list(p.notes)} for p in props]})
        geo_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    # ---- 人读摘要 ----
    print(f"源：{info.path.name}  sha256 {sha[:16]}…  {info.n_frames} 帧 @ {info.fps} fps"
          f"（{info.duration_s:.2f} s，帧数来源 {info.frame_count_source}）")
    print(f"对齐：protocol_alignment={tb.protocol_alignment}"
          f"（{plan.reason}）")
    frac = coverage.sample_fraction
    print(f"覆盖：解码 {coverage.consumed_frames}/{coverage.expected_samples} 个抽样帧"
          f"（完整={coverage.decode_complete}）  时间轴占比 "
          f"{f'{frac:.2f}' if frac is not None else '—'}（step={args.step}）"
          f"  截断 {coverage.truncated_frames}")
    if geo_source == "human_confirmed_file":
        print(f"几何来源：人工确认件 {args.geometry}  sha256 {geo_file_sha[:16]}…"
              f"（确认人 {geo_binding.get('confirmed_by')} @ {geo_binding.get('confirmed_at')}）")
    else:
        print("几何来源：真帧提案（confirmed=False，未人工确认）")
    if de_binding.status == cg.BIND_APPLIED and de_binding.applied:
        print(f"申报空杯（物理杯号）：{list(de_binding.applied)}")
    elif de_binding.status == cg.BIND_REFUSED_AMBIGUOUS:
        print(f"申报空杯**未应用**（绑定有歧义）：请求 {declared}，applied []")
    for c in cups_out:
        q = c["sampled_state_counts"]     # 抽样记录计数（R2-115 T1），不是连续时长
        g = c["geometry"]
        wnote = "" if g["water_surface_reliable"] else "（水线不可靠，above_water 记 null）"
        print(f"  杯 {g['cup_id']}: observed {q['observed']}  unclear {q['unclear']}"
              f"  lost_short {q['lost_short']}  declared_absent {q['declared_absent']}"
              f"  水线 {g['water_surface_y']}{wnote}")
        for n in g["notes"]:
            print(f"        注：{n}")
        if g["water_surface_unreliable_reason"]:
            print(f"        水线：{g['water_surface_unreliable_reason']}")
        if c["overlay_clip"]:
            print(f"        叠加短片 {c['overlay_clip']}")
    print(f"几何提案问题 {len(env_problems) + len(geo_problems)} 条（未确认是设计，不是失败）")
    for gp in geo_problems:
        print(f"  - {gp}")
    for gp in de_binding.problems:
        print(f"  - {gp}")
    print(f"记录：{rec_path}")
    return EXIT_OK if any_visible else EXIT_NOTHING_VISIBLE


def _geometry_confirmation(*, geo_source, confirmed, env_problems, geo_problems,
                           geometry_path, geo_file_sha, geo_binding, de_binding) -> dict:
    """记录里的几何确认段。确认件带 binding + 文件 sha256；提案带问题清单。

    R2-115 G4：确认件必须可追溯——谁确认的、什么时候、绑的哪段视频、文件自身
    哈希，全落盘。提案态如实记 confirmed=False + validate/proposal 问题。
    """
    out = {
        "confirmed": confirmed,
        "source": geo_source,
        "validate_problems": list(env_problems),
        "proposal_problems": list(geo_problems),
        "geometry_file": str(geometry_path) if geometry_path else None,
        "geometry_file_sha256": geo_file_sha,
        "binding": dict(geo_binding) if geo_binding else None,
        "declared_empty_binding": {
            "applied_cup_ids": list(de_binding.applied),
            "status": de_binding.status,
            "problems": list(de_binding.problems)},
        "note": ("几何为人工确认件（已绑定视频 sha256/尺寸/确认人）"
                 if confirmed else
                 "几何为提案：人工确认前正式解释与发布验收受限"),
    }
    return out


def _limits(args, info, geo_source, de_binding) -> list[str]:
    limits = [
        "研究诊断：不产出正式 CSV、不进验收路径、不借 TST 发布/标定资质",
        "t0 未知 ⇒ protocol_alignment=unknown，未套 (120,360) 标准窗",
        ("杯体/水线为人工确认件（--geometry）" if geo_source == "human_confirmed_file"
         else "杯体/水线为提案（confirmed=False），未人工确认"),
        "observed 帧内的活动/不动分类未做（新的科学口径，未经批准）",
        "光流/局部运动（动物区内 vs 区外水扰）无实现，标记 not_implemented",
        f"分析抽帧 step={args.step}：计数是抽样记录数；帧间细节"
        f"（< {args.step / info.fps:.2f} s）不可见",
        f"短暂丢失门 lost_short_max_gap_frames={args.lost_short_max_gap_frames} 源帧"
        "（起点值 ≈0.44 s @ 25 fps，未经真实素材标定）：超过门的缺口记 unclear",
        "各杯统计未按窗截取（stats_are_windowed=False）：即使 t0 已知，本次统计"
        "仍覆盖整条抽样时间轴",
    ]
    if de_binding.status == cg.BIND_REFUSED_AMBIGUOUS:
        limits.append("申报空杯未应用：杯候选数与期望不符，物理杯号绑定有歧义，相关杯保持未决")
    return limits


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
            row = {"frame": d.frame, "quality": d.quality,
                   "area_px": d.area_px,
                   "centroid": list(d.centroid) if d.centroid else None,
                   "above_water_frac": d.above_water_frac,
                   "wall_dist_px": d.wall_dist_px}
            if d.above_water_null_reason:
                # G1：above_water_frac 为 null 时把原因一起落盘（不是无声 null，
                # 更不是假 0），复核的人一眼看到"这杯水线不可靠"。
                row["above_water_null_reason"] = d.above_water_null_reason
            rows.append(row)
    return rows


def _render(g, props, masks, last_diags, idx, fps) -> np.ndarray:
    rgb = ov.gray_to_rgb(g)
    for p in props:
        # 复核 §9：分析 ROI、水体候选区、水线是三件不同的东西，分开画，
        # 让人核"ROI 有没有把线上活动圈进来""水线画对没有"。
        r0, c0, r1, c1 = p.roi
        d = last_diags.get(p.index)
        q = d.quality if d is not None else "declared_absent"
        color = ov.QUALITY_COLORS.get(q, ov.COLOR_UNCLEAR)
        ov.draw_rect(rgb, r0, c0, r1, c1, ov.COLOR_TANK)         # 分析 ROI
        if p.water_body is not None:
            wr0, wc0, wr1, wc1 = p.water_body
            ov.draw_rect(rgb, wr0, wc0, wr1, wc1, ov.COLOR_WATER_BODY)  # 水体候选区
        if p.water_surface_y is not None:
            wcol = ov.COLOR_WATER if p.water_surface_reliable else ov.COLOR_UNCLEAR
            ov.draw_hline(rgb, int(round(p.water_surface_y)), c0, c1, wcol)  # 水线
        m = masks.get(p.index)
        if m is not None:
            ov.draw_mask_outline(rgb, m, color)
        label = f"C{p.cup_id} F{idx} T{idx / fps:.1f}S {q[:4].upper()}"
        ov.draw_text_outlined(rgb, max(0, r0 - 8), c0, label, color)
    return rgb


if __name__ == "__main__":
    raise SystemExit(main())
