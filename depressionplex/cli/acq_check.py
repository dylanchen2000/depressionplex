"""采集自检 CLI（DP-109 B7）：对比度 / 分割噪声底 / 面积抖动 p90。

这一页在产品里的作用：**「你这套采集条件，我们的算法能不能用？」**
不达标要当场说不达标，而不是让客户跑完 6 分钟拿到一份看着正常的数字。

三个读数的数学全在 assay_core/segment.py：
  contrast_report() / structural_noise_floor() / find_chambers()
本模块只做「抽帧 → 调它们 → 序列化」，一行都不重写数学。

输出契约（架构 §3.5）：
  stdout  — 单个 JSON 对象，键集固定（见 ACQ_JSON_KEYS）
  stderr  — 进度 NDJSON（照 cli/analyze.py 的 _progress 路子）
  退出码  — 0=测到读数（通过与不通过都算），2=测不出（无面板带/帧不足），1=解码失败

退出码纪律：门不通过是**一个结果**，不是一次故障。
若把「不通过」映射成非零码，GUI 会显示成「自检程序崩了」，
客户永远看不到「你的对比度只有 40 灰阶，请加背光板」这句有用的话。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..assay_core import rad as R
from ..assay_core import segment as S
from ..assay_core.segment import (
    GATE_CONTRAST_ABS,
    GATE_CONTRAST_RATIO,
    GATE_AREA_JITTER_P90,
)
from .. import video as V
# MOVING_RESIDUAL 与 GATE_AREA_JITTER_P90 数值相同但意义不同：前者判"动物是否在动"，
# 后者判"面积抖动噪声底是否达标"。统一从 probe_frames 引入，不在本文件重复定义。
from .probe_frames import MOVING_RESIDUAL

# ── 抽帧参数（写死，不给命令行开关——开关一给，两次自检就不可比）──────────────
N_WINDOWS: int = 5
FRAMES_PER_WINDOW: int = 8
_MARGIN: int = FRAMES_PER_WINDOW  # 首尾各留出的余量帧数（与窗口帧数相同）

# ── 最小帧数要求 ───────────────────────────────────────────────────────────────
# = 5 个 8 帧窗口 + 首尾各 8 帧余量 = 5×8 + 2×8 = 56
_MIN_FRAMES: int = N_WINDOWS * FRAMES_PER_WINDOW + 2 * _MARGIN

# ── JSON 键集合（测试 §3.1 用于验证键完整性）──────────────────────────────────
ACQ_JSON_KEYS = ("generated_at", "video", "sampling", "gates", "chambers", "reference")
ACQ_VIDEO_KEYS = ("path", "fps", "n_frames", "width", "height")
ACQ_SAMPLING_KEYS = ("n_windows", "frames_per_window", "frame_indices")
ACQ_GATES_KEYS = ("contrast", "noise_floor", "area_jitter")
ACQ_CONTRAST_KEYS = ("value", "ratio", "threshold_abs", "threshold_ratio", "passed")
ACQ_NOISE_FLOOR_KEYS = ("value", "n_frames")
ACQ_AREA_JITTER_KEYS = ("value", "threshold", "passed", "chamber", "chamber_residual", "moving")
ACQ_CHAMBER_KEYS = ("index", "area_jitter_p90", "rad_residual")
ACQ_REFERENCE_KEYS = (
    "contrast_abs", "contrast_ratio", "noise_floor_px", "area_jitter_p90",
    "source_video", "frames_start", "frames_end", "width", "height",
    "fps", "total_frames", "sha256", "measured_at",
)


def _progress(msg: str) -> None:
    """向 stderr 发一条进度 NDJSON（照 cli/analyze.py 路子）。"""
    print(json.dumps({"status": msg}, ensure_ascii=False), file=sys.stderr, flush=True)


def _compute_frame_indices(n_frames: int) -> list[int]:
    """按抽帧规则算出 N_WINDOWS × FRAMES_PER_WINDOW 个帧号。

    窗口中心落在 (i + 0.5) / N_WINDOWS 的可用区间内，首尾各留 _MARGIN 帧余量。
    """
    usable_start = _MARGIN
    usable_end = n_frames - 1 - _MARGIN
    usable_len = usable_end - usable_start

    indices: list[int] = []
    for i in range(N_WINDOWS):
        center_frac = (i + 0.5) / N_WINDOWS
        center = usable_start + center_frac * usable_len
        win_start = int(round(center - FRAMES_PER_WINDOW / 2))
        # 确保不越出可用区间
        win_start = max(usable_start, min(win_start, usable_end - FRAMES_PER_WINDOW + 1))
        indices.extend(range(win_start, win_start + FRAMES_PER_WINDOW))
    return indices


def _load_reference() -> dict | None:
    """从冻结文件 tests/fixtures/p2_reference.json 读参考值。

    参考素材（`10mg 2周.mp4`）不在仓里，沙箱和 CI 都没有它。
    返回 None 意味着参考文件不存在（不应在正常部署里出现）。
    """
    ref_path = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "p2_reference.json"
    if not ref_path.exists():
        return None
    with open(ref_path, encoding="utf-8") as f:
        return json.load(f)


def _run_acq_check(frames: list[np.ndarray], frame_indices: list[int],
                   info_path: str, info_fps: float, info_n_frames: int,
                   info_width: int, info_height: int) -> tuple[int, dict]:
    """核心计算：frames 已按 frame_indices 顺序取好（N_WINDOWS × FRAMES_PER_WINDOW 帧）。

    Returns:
        (exit_code, json_dict)
        exit_code 0 = 测到读数；2 = 测不出（无面板带/帧不足）
    """
    n_total = N_WINDOWS * FRAMES_PER_WINDOW
    assert len(frames) == n_total, f"期望 {n_total} 帧，得到 {len(frames)}"

    # ── 1. 用第一个窗口的第一帧做对比度检测 ──────────────────────────────────
    _progress("计算对比度")
    first_frame = frames[0]
    rep = S.contrast_report(first_frame)

    has_panel = "abs_diff" in rep
    if not has_panel:
        # 没有面板带 → 测不出 → rc=2
        # 依然输出 JSON（GUI 要看到"量不了"而不是程序崩了）
        reference = _load_reference()
        out = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "video": {
                "path": info_path,
                "fps": info_fps,
                "n_frames": info_n_frames,
                "width": info_width,
                "height": info_height,
            },
            "sampling": {
                "n_windows": N_WINDOWS,
                "frames_per_window": FRAMES_PER_WINDOW,
                "frame_indices": frame_indices,
            },
            "gates": {
                "contrast": {
                    "value": None,
                    "ratio": None,
                    "threshold_abs": GATE_CONTRAST_ABS,
                    "threshold_ratio": GATE_CONTRAST_RATIO,
                    "passed": None,
                },
                "noise_floor": {
                    "value": None,
                    "n_frames": 0,
                },
                "area_jitter": {
                    "value": None,
                    "threshold": GATE_AREA_JITTER_P90,
                    "passed": None,
                    "chamber": None,
                    "chamber_residual": None,
                    "moving": None,
                },
            },
            "chambers": [],
            "reference": reference,
        }
        return 2, out

    contrast_value = rep["abs_diff"]
    contrast_ratio = rep["ratio"]
    contrast_passed = bool(rep["passes_gate"])

    # ── 2. 分割噪声底（用全部 40 帧，静态高对比结构不受窗口跨越影响）─────────
    _progress("计算分割噪声底")
    nf = S.structural_noise_floor(frames)
    nf_value: float | None = nf.get("delta_mean") if "delta_mean" in nf else None
    nf_n_frames = len(frames)

    # ── 3. 定位隔间（用第一帧）────────────────────────────────────────────────
    _progress("定位隔间")
    chamber_cols = S.find_chambers(first_frame)
    if not chamber_cols:
        # 找不到隔间 → 测不出
        reference = _load_reference()
        out = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "video": {
                "path": info_path,
                "fps": info_fps,
                "n_frames": info_n_frames,
                "width": info_width,
                "height": info_height,
            },
            "sampling": {
                "n_windows": N_WINDOWS,
                "frames_per_window": FRAMES_PER_WINDOW,
                "frame_indices": frame_indices,
            },
            "gates": {
                "contrast": {
                    "value": contrast_value,
                    "ratio": contrast_ratio,
                    "threshold_abs": GATE_CONTRAST_ABS,
                    "threshold_ratio": GATE_CONTRAST_RATIO,
                    "passed": contrast_passed,
                },
                "noise_floor": {
                    "value": nf_value,
                    "n_frames": nf_n_frames,
                },
                "area_jitter": {
                    "value": None,
                    "threshold": GATE_AREA_JITTER_P90,
                    "passed": None,
                    "chamber": None,
                    "chamber_residual": None,
                    "moving": None,
                },
            },
            "chambers": [],
            "reference": reference,
        }
        return 2, out

    # ── 4. 逐隔间计算面积抖动 p90 与 RAD 残差 ────────────────────────────────
    # 按窗口迭代：帧 i*FRAMES_PER_WINDOW .. (i+1)*FRAMES_PER_WINDOW-1 是一个窗口内的连续帧
    _progress("计算各隔间面积抖动与 RAD 残差")
    chamber_results: list[dict] = []

    for ch_idx, (c0, c1) in enumerate(chamber_cols, 1):
        all_jitter: list[float] = []
        all_residuals: list[float] = []

        for w in range(N_WINDOWS):
            win_frames = frames[w * FRAMES_PER_WINDOW: (w + 1) * FRAMES_PER_WINDOW]
            # 分割每帧
            segs = [S.segment_animal(g[:, c0:c1 + 1]) for g in win_frames]
            masks = [r.mask for r in segs if r.ok and r.mask is not None]
            if len(masks) < 2:
                continue

            # 面积抖动（相邻帧差，归一化）
            areas = np.array([float(m.sum()) for m in masks])
            bl = R.trial_body_length(masks)
            if bl <= 0:
                continue
            bl2 = bl * bl
            d_area = np.abs(np.diff(areas))
            for v in d_area:
                all_jitter.append(float(v) / bl2)

            # RAD 残差
            for i in range(1, len(masks)):
                res = R.decompose(masks[i - 1], masks[i], bl=bl)
                if res is not None:
                    all_residuals.append(res.residual)

        jitter_p90: float | None = float(np.percentile(all_jitter, 90)) if all_jitter else None
        mean_residual: float | None = float(np.mean(all_residuals)) if all_residuals else None

        chamber_results.append({
            "index": ch_idx,
            "area_jitter_p90": jitter_p90,
            "rad_residual": mean_residual,
        })

    # ── 5. 选 RAD 残差最低的隔间做面积抖动门判定 ─────────────────────────────
    # 规则：取 RAD 残差最低的隔间（最可能处于静止状态），其面积抖动才是纯噪声底的代理
    best_ch: dict | None = None
    for ch in chamber_results:
        if ch["rad_residual"] is not None:
            if best_ch is None or ch["rad_residual"] < best_ch["rad_residual"]:
                best_ch = ch

    if best_ch is not None and best_ch["area_jitter_p90"] is not None:
        jitter_best = best_ch["area_jitter_p90"]
        jitter_passed = jitter_best <= GATE_AREA_JITTER_P90
        best_ch_index = best_ch["index"]
        best_ch_residual = best_ch["rad_residual"]
        # 如果该隔间的残差超过 MOVING_RESIDUAL 水平，动物在动，抖动读数是信号不是噪声底
        moving_flag: bool | None = bool(best_ch_residual > MOVING_RESIDUAL)
    else:
        jitter_best = None
        jitter_passed = None
        best_ch_index = None
        best_ch_residual = None
        moving_flag = None

    # ── 6. 加载参考值 ────────────────────────────────────────────────────────
    reference = _load_reference()

    # ── 7. 组装 JSON ─────────────────────────────────────────────────────────
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "video": {
            "path": info_path,
            "fps": info_fps,
            "n_frames": info_n_frames,
            "width": info_width,
            "height": info_height,
        },
        "sampling": {
            "n_windows": N_WINDOWS,
            "frames_per_window": FRAMES_PER_WINDOW,
            "frame_indices": frame_indices,
        },
        "gates": {
            "contrast": {
                "value": contrast_value,
                "ratio": contrast_ratio,
                "threshold_abs": GATE_CONTRAST_ABS,
                "threshold_ratio": GATE_CONTRAST_RATIO,
                "passed": contrast_passed,
            },
            "noise_floor": {
                "value": nf_value,
                "n_frames": nf_n_frames,
            },
            "area_jitter": {
                "value": jitter_best,
                "threshold": GATE_AREA_JITTER_P90,
                "passed": jitter_passed,
                "chamber": best_ch_index,
                "chamber_residual": best_ch_residual,
                "moving": moving_flag,
            },
        },
        "chambers": chamber_results,
        "reference": reference,
    }
    return 0, out


def main(argv: list[str] | None = None) -> int:
    """采集自检入口。

    退出码：0=测到读数（通过/不通过都算），2=测不出，1=解码失败。
    **不把「不通过」映射成非零码**——那样 GUI 只会显示「自检程序崩了」。
    """
    ap = argparse.ArgumentParser(
        description="采集自检：对比度 / 分割噪声底 / 面积抖动 p90"
    )
    ap.add_argument("--video", required=True, type=str, help="待检视频路径")
    ap.add_argument("--chambers", required=True, type=int, help="隔间数（提案）")
    ap.add_argument("--json", action="store_true", help="（保留标志，当前总输出 JSON）")
    args = ap.parse_args(argv)

    # ── 探测视频元数据 ────────────────────────────────────────────────────────
    _progress("探测视频元数据")
    try:
        info = V.probe(args.video)
    except Exception as exc:
        print(
            json.dumps({"error": f"视频解码失败：{exc}"}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1

    # ── 检查帧数是否足够 ───────────────────────────────────────────────────────
    if info.n_frames < _MIN_FRAMES:
        _progress(
            f"帧数不足：{info.n_frames} 帧 < 最小要求 {_MIN_FRAMES} 帧"
            f"（{N_WINDOWS} 窗口 × {FRAMES_PER_WINDOW} 帧 + 首尾各 {_MARGIN} 帧余量）"
        )
        reference = _load_reference()
        out = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "video": {
                "path": str(info.path),
                "fps": info.fps,
                "n_frames": info.n_frames,
                "width": info.width,
                "height": info.height,
            },
            "sampling": {
                "n_windows": N_WINDOWS,
                "frames_per_window": FRAMES_PER_WINDOW,
                "frame_indices": [],
            },
            "gates": {
                "contrast": {
                    "value": None,
                    "ratio": None,
                    "threshold_abs": GATE_CONTRAST_ABS,
                    "threshold_ratio": GATE_CONTRAST_RATIO,
                    "passed": None,
                },
                "noise_floor": {
                    "value": None,
                    "n_frames": 0,
                },
                "area_jitter": {
                    "value": None,
                    "threshold": GATE_AREA_JITTER_P90,
                    "passed": None,
                    "chamber": None,
                    "chamber_residual": None,
                    "moving": None,
                },
            },
            "chambers": [],
            "reference": reference,
        }
        print(json.dumps(out, ensure_ascii=False), flush=True)
        return 2

    # ── 计算抽帧索引 ───────────────────────────────────────────────────────────
    frame_indices = _compute_frame_indices(info.n_frames)

    # ── 解码采样帧 ────────────────────────────────────────────────────────────
    _progress(f"解码 {len(frame_indices)} 帧（{N_WINDOWS} 窗口 × {FRAMES_PER_WINDOW} 帧）")
    try:
        frames = V.frames_at(info, frame_indices)
    except Exception as exc:
        print(
            json.dumps({"error": f"帧解码失败：{exc}"}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1

    # ── 核心计算 ───────────────────────────────────────────────────────────────
    _progress("计算采集指标")
    rc, out = _run_acq_check(
        frames, frame_indices,
        info_path=str(info.path),
        info_fps=info.fps,
        info_n_frames=info.n_frames,
        info_width=info.width,
        info_height=info.height,
    )

    # ── 输出 JSON（唯一数字出口）──────────────────────────────────────────────
    _progress("完成")
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
