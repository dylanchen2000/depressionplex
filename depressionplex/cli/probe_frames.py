#!/usr/bin/env python3
"""对一串连续帧做采集端 + 分割质量体检。

这是 P2 那道**硬门**的量具：
  1. 采集端：动物/背景灰度对比度  ≥100 灰阶差、≥2×
  2. 分割：  轮廓面积逐帧抖动     ≤ 2% BL²
  3. RAD：   刚体/关节残差分解，看噪声底

用法：
    python3 -m depressionplex.cli.probe_frames frames/seq_*.png --chambers 4

    # 带胶带走廊标定（标定帧建议从全片均匀抽 20–40 帧）：
    python3 -m depressionplex.cli.probe_frames frames/seq_*.png --chambers 4 \
        --calibrate-from calib/c_*.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from ..assay_core import rad as R
from ..assay_core import segment as S
from ..assay_core import silhouette as sil
from ..assay_core import validity as V

AREA_JITTER_GATE = 0.02  # 2% BL²
CONTRAST_ABS_GATE = 100.0
CONTRAST_RATIO_GATE = 2.0


def load_gray(path: Path) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.open(path).convert("L")).astype(np.float64)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("frames", nargs="+", type=Path)
    ap.add_argument("--chambers", type=int, default=4)
    ap.add_argument(
        "--calibrate-from",
        nargs="+",
        type=Path,
        default=[],
        help="胶带走廊标定帧（建议从全片均匀抽 20–40 帧）。给了则第 4 节走走廊路径",
    )
    ap.add_argument(
        "--body-area-prior",
        type=float,
        default=None,
        help="身体级面积绝对先验（硬件规格级）。整批脱落时相对判据不可决，"
        "给先验则报 unknown 而非自洽 valid",
    )
    args = ap.parse_args(argv)

    paths = sorted(args.frames)
    grays = [load_gray(p) for p in paths]
    print(f"帧数 {len(grays)}  尺寸 {grays[0].shape[1]}x{grays[0].shape[0]}")

    rep = S.contrast_report(grays[0])
    print("\n== 1. 采集端对比度 ==")
    print(f"  阈值(Otsu)   {rep['threshold']:.1f}")
    print(f"  背景均值     {rep['background_mean']:.1f}")
    print(f"  暗物均值     {rep['animal_mean']:.1f}")
    print(f"  绝对差       {rep['abs_diff']:.1f}   (门槛 ≥{CONTRAST_ABS_GATE:.0f})")
    print(f"  比值         {rep['ratio']:.2f}x  (门槛 ≥{CONTRAST_RATIO_GATE:.1f}x)")
    print(f"  判定         {'通过' if rep['passes_gate'] else '不通过'}")

    chambers = S.find_chambers(grays[0])
    print(f"\n== 2. 隔间定位（自动提案，非权威标定）==\n  {len(chambers)} 个: {chambers}")
    if args.chambers and len(chambers) != args.chambers:
        print(f"  [警告] 与期望的 {args.chambers} 个不符——标定需人工确认")

    # 胶带走廊标定（提案，非权威）。给了 --calibrate-from 才启用。
    corridors: dict[int, S.TapeCorridor] = {}
    calib_grays: list[np.ndarray] = []
    if args.calibrate_from:
        calib_paths = sorted(args.calibrate_from)
        calib_grays = [load_gray(p) for p in calib_paths]
        print(f"\n== 2.5 胶带走廊标定（提案，非权威；{len(calib_grays)} 帧）==")
        if len(calib_grays) < 2:
            print("  [警告] 标定帧不足 2 帧，退回无走廊路径")
        else:
            for k, (c0, c1) in enumerate(chambers, 1):
                corr = S.calibrate_tape_corridor(
                    [g[:, c0 : c1 + 1] for g in calib_grays]
                )
                if corr is None:
                    print(f"  隔间{k}: 标定失败 → 退回无走廊路径")
                    continue
                corridors[k] = corr
                seal_note = (
                    "已收口"
                    if corr.sealed
                    else "[警告] 未收口(band_unsealed)：无悬挂运动块/估不出 BL，"
                    "带底为扩展值，可能已进盒区"
                )
                print(
                    f"  隔间{k}: 走廊列 {corr.col_range[0]}-{corr.col_range[1]}"
                    f"（宽 {corr.col_range[1] - corr.col_range[0] + 1} px）"
                    f"  行 {corr.row_range[0]}-{corr.row_range[1]}"
                    f"  置信 {corr.confidence:.3f}  面板带 {corr.band_range} {seal_note}"
                )

    # 试次级有效性需要"全片标定帧的动物面积剖面"：逐帧走走廊路径记录面积。
    calib_areas: dict[int, list[float | None]] = {}
    if args.calibrate_from and len(calib_grays) >= 2:
        for k, (c0, c1) in enumerate(chambers, 1):
            prof: list[float | None] = []
            for g in calib_grays:
                r = S.segment_animal(g[:, c0 : c1 + 1], corridor=corridors.get(k))
                prof.append(
                    float(r.mask.sum()) if r.ok and r.mask is not None else None
                )
            calib_areas[k] = prof

    nf = S.structural_noise_floor(grays)
    print("\n== 3. 分割噪声底（静态高对比结构，非动物）==")
    if "delta_mean" in nf:
        print(
            f"  取样区: 面板带以上 {int(nf['region_rows'])} 行  面积 {nf['area_mean']:.0f} px"
        )
        print(
            f"  面积抖动 |Δ| 均值 {nf['delta_mean']:.2f} px 最大 {nf['delta_max']:.2f} px"
            "   ← 这才是纯分割噪声"
        )
    else:
        print(f"  无法估计: {nf}")

    print("\n== 4. 动物分割与逐帧抖动 ==")
    print("  注意：动物在动时，抖动 = 分割噪声 + 真实形变。RAD 残差可判断是否在动。")
    all_pass = bool(rep["passes_gate"])
    current_areas: dict[int, list[float | None]] = {}
    for k, (c0, c1) in enumerate(chambers, 1):
        results = [
            S.segment_animal(g[:, c0 : c1 + 1], corridor=corridors.get(k))
            for g in grays
        ]
        current_areas[k] = [
            float(r.mask.sum()) if r.ok and r.mask is not None else None
            for r in results
        ]
        ok = [r.mask for r in results if r.ok and r.mask is not None]
        flags = sorted({f for r in results for f in r.flags})
        reasons = sorted({r.reason for r in results if not r.ok})
        if len(ok) < 2:
            print(f"  隔间{k}: 可用帧不足（{len(ok)}/{len(results)}）→ unknown  {reasons}")
            all_pass = False
            continue

        areas = np.array([float(m.sum()) for m in ok])
        bl = R.trial_body_length(ok)
        bl2 = bl * bl if bl > 0 else float("nan")
        d_area = np.abs(np.diff(areas))
        jitter = d_area / bl2
        # 门用 p90 而非单帧 max：门要量的是**噪声底**，而单帧离群值并不会淹没
        # immobility 信号——bout 后处理的 Noise Thresh / Min Length 会把它滤掉。
        # max 仍然打印出来作为诊断量。
        jit_p90 = float(np.percentile(jitter, 90))
        elong = [sil.metrics(m, with_holes=False) for m in ok]
        el = [e.elongation for e in elong if e is not None]

        res = []
        for i in range(1, len(ok)):
            r = R.decompose(ok[i - 1], ok[i], bl=bl)
            if r is not None:
                res.append(r.residual)

        # 合理性检查：防止"选错静态物体"导致抖动门假通过。
        # 2026-08-24 教训：误选底部收集盒时，面积抖动 0.6px、RAD 残差 0.0001，
        # 门会漂亮地通过——因为静态物当然不抖。门是必要条件，不是充分条件。
        roi_area = grays[0].shape[0] * (c1 - c0 + 1)
        sanity: list[str] = []
        if areas.mean() > 0.10 * roi_area:
            sanity.append(f"面积占 ROI {areas.mean()/roi_area:.0%}，疑似框到装置")
        if np.mean(el) < 1.3:
            sanity.append(f"伸展度 {np.mean(el):.2f} 过低，疑似非动物")
        if d_area.max() < 1.0 and (not res or np.max(res) < 1e-3):
            sanity.append("跨帧几乎零变化，疑似静态物体")
        if reasons:
            sanity.append(f"部分帧失败: {reasons}")

        # 门只在动物"较静"时才有判据意义（否则测到的是信号）。
        moving = bool(res) and float(np.mean(res)) > 0.02
        gate = jit_p90 <= AREA_JITTER_GATE and not sanity
        if moving and not gate:
            sanity.append("动物在动（RAD 残差 > 0.02），本帧段不适合用于噪声门判定")
        all_pass = all_pass and gate
        print(
            f"  隔间{k}: 可用 {len(ok)}/{len(results)} | 面积 {areas.mean():.0f}±{areas.std():.0f} px"
            f" | BL {bl:.1f} px | 伸展度 {np.mean(el):.2f}"
        )
        print(
            f"          面积抖动 |Δ| 均值 {d_area.mean():.1f} px 最大 {d_area.max():.1f} px"
            f" → 归一化 均值 {jitter.mean():.4f} p90 {jit_p90:.4f} 最大 {jitter.max():.4f}"
            f"  [{'通过' if gate else ('信号主导' if moving else '不通过')} 门槛 {AREA_JITTER_GATE}]"
        )
        if res:
            print(
                f"          RAD 关节残差 均值 {np.mean(res):.4f} 最大 {np.max(res):.4f}"
                f"  = 合成噪声底的 {np.mean(res)/0.0103:.1f} 倍"
                f"  → {'在动' if np.mean(res) > 0.02 else '较静'}"
            )
        if "delta_mean" in nf and bl2 == bl2:
            print(
                f"          噪声底折算到本隔间: 均值 {nf['delta_mean']/bl2:.4f}"
                f" 最大 {nf['delta_max']/bl2:.4f}  (门槛 {AREA_JITTER_GATE})"
            )
        if flags:
            print(f"          QC 标记: {flags}")
        for w in sanity:
            print(f"          [合理性警告] {w}")

    if calib_areas:
        tv = V.assess_trial_validity(
            calib_areas,
            current_areas,
            body_area_prior=args.body_area_prior,
        )
        print("\n== 5. 试次级有效性（从未有动物/脱落/截断/有效）==")
        for c in tv.chambers:
            frac = "—" if c.occupied_fraction is None else f"{c.occupied_fraction:.2f}"
            print(
                f"  隔间{c.chamber}: {c.status}  最大动物面积 {c.max_area:.0f}"
                f"  在场占比 {frac}"
                f"  参考 {c.ref_body_area:.0f}（门槛 {c.body_threshold:.0f}）"
                + (f"  {c.note}" if c.note else "")
            )
        if tv.never_occupied:
            print(
                f"  [建议排除] {tv.never_occupied}：从未有动物（never_occupied）——"
                "布置/录制问题，非实验失败；金标准本就要求排除，"
                "属产品特性而非失败（G10a 口径，不并入脱落）"
            )
        if tv.detached:
            print(
                f"  [建议排除] {tv.detached}：脱落/悬挂失效（detached）——"
                "**实验失败须上报**；金标准要求排除（G10b 口径，不并入空场）"
            )
        if tv.needs_repair:
            print(
                f"  [!!疑似截断] {tv.needs_repair}：标定帧存在过身体、当前仅尾级——"
                "这是分割 bug，不得按脱落静默丢弃"
            )
        if tv.needs_repair:
            all_pass = False

    print(
        f"\n== 总判定 ==  {'全部通过' if all_pass else '有项未通过或为信号主导，见上'}"
    )
    return 0 if all_pass else 2


if __name__ == "__main__":
    sys.exit(main())
