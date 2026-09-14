#!/usr/bin/env python3
"""P2 参考素材验证脚本（DP-109 B7）。

**前提**：在有 `10mg 2周.mp4` 的机器上跑。沙箱和 CI 都没有这段素材，
所以本脚本不在测试套件里；这一条在交付报告里必须明写（本项目铁律：查不到 ≠ 没有）。

用法：
    python3 scripts/verify_p2_reference.py --video ~/Work/heavy/depression/悬尾/10mg\ 2周.mp4

本脚本用 acq_check CLI 跑一次自检，把结果与冻结表（tests/fixtures/p2_reference.json）对比：
- 对比度绝对差、比值、噪声底、面积抖动 p90 各有 10% 容差
- 素材元数据（分辨率、帧率、总帧数）精确匹配（零容差）
- SHA256 冻结表目前写 null，所以不做哈希比对；有素材的人可以手动核

如果比对不通过，非零退出。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# 容差
_REL_TOL = 0.10  # 10%

REF_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "p2_reference.json"


def _detect_ffmpeg_version() -> str | None:
    """探测当前环境的 ffmpeg 版本字符串（用于解码器对账）。

    返回 None 表示找不到 ffmpeg，返回字符串为版本行（如 "ffmpeg version 6.0..."）。
    """
    import re
    import shutil
    try:
        # 先试 depressionplex.video 内部的路径解析逻辑，再 fallback 到系统 PATH
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path is None:
            return None
        out = subprocess.run(
            [ffmpeg_path, "-version"], capture_output=True, text=True, timeout=5
        )
        first_line = out.stdout.splitlines()[0] if out.stdout else out.stderr.splitlines()[0]
        return first_line.strip()
    except Exception as e:
        return f"(探测失败: {e})"


def _load_ref() -> dict:
    with open(REF_PATH, encoding="utf-8") as f:
        return json.load(f)


def _close(got: float | None, want: float, tol: float) -> bool:
    if got is None:
        return False
    return abs(got - want) / max(abs(want), 1e-12) <= tol


def main() -> int:
    ap = argparse.ArgumentParser(description="验证 P2 参考素材的 acq_check 读数")
    ap.add_argument("--video", required=True, type=str, help="10mg 2周.mp4 路径")
    ap.add_argument("--chambers", type=int, default=4, help="隔间数（默认 4）")
    args = ap.parse_args()

    ref = _load_ref()
    video_path = Path(args.video)

    if not video_path.exists():
        print(f"视频文件不存在：{video_path}", file=sys.stderr)
        return 2

    print(f"冻结表：{REF_PATH}")
    print(f"素材  ：{video_path}")
    print(f"参考容差：{_REL_TOL * 100:.0f}%")
    print()

    # 运行 acq_check
    cmd = [
        sys.executable, "-m", "depressionplex.cli.acq_check",
        "--video", str(video_path),
        "--chambers", str(args.chambers),
        "--json",
    ]
    print(f"运行：{' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parent.parent)
    if result.returncode == 1:
        print(f"acq_check 解码失败（rc=1）：{result.stderr}", file=sys.stderr)
        return 1
    if result.returncode == 2:
        print(f"acq_check 测不出（rc=2）：{result.stderr}", file=sys.stderr)
        return 2

    try:
        got = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"acq_check 输出解析失败：{e}", file=sys.stderr)
        print(f"stdout: {result.stdout[:500]}", file=sys.stderr)
        return 2

    errors: list[str] = []

    # 元数据精确比对
    video_info = got["video"]
    if video_info["width"] != ref["width"] or video_info["height"] != ref["height"]:
        errors.append(
            f"分辨率不符：得到 {video_info['width']}×{video_info['height']}，"
            f"冻结值 {ref['width']}×{ref['height']}"
        )
    if abs(video_info["fps"] - ref["fps"]) > 0.01:
        errors.append(f"帧率不符：得到 {video_info['fps']}，冻结值 {ref['fps']}")
    if video_info["n_frames"] != ref["total_frames"]:
        errors.append(
            f"总帧数不符：得到 {video_info['n_frames']}，冻结值 {ref['total_frames']}"
        )

    # 读数容差比对
    gates = got["gates"]
    contrast_val = gates["contrast"]["value"]
    if not _close(contrast_val, ref["contrast_abs"], _REL_TOL):
        errors.append(
            f"对比度绝对差不符（{_REL_TOL * 100:.0f}% 容差）："
            f"得到 {contrast_val}，冻结值 {ref['contrast_abs']}"
        )

    contrast_ratio = gates["contrast"]["ratio"]
    if not _close(contrast_ratio, ref["contrast_ratio"], _REL_TOL):
        errors.append(
            f"对比度比值不符（{_REL_TOL * 100:.0f}% 容差）："
            f"得到 {contrast_ratio}，冻结值 {ref['contrast_ratio']}"
        )

    nf_val = gates["noise_floor"]["value"]
    if not _close(nf_val, ref["noise_floor_px"], _REL_TOL):
        errors.append(
            f"噪声底不符（{_REL_TOL * 100:.0f}% 容差）："
            f"得到 {nf_val}，冻结值 {ref['noise_floor_px']}"
        )

    jitter_val = gates["area_jitter"]["value"]
    if not _close(jitter_val, ref["area_jitter_p90"], _REL_TOL):
        errors.append(
            f"面积抖动 p90 不符（{_REL_TOL * 100:.0f}% 容差）："
            f"得到 {jitter_val}，冻结值 {ref['area_jitter_p90']}"
        )

    # ── 解码器信息（DP-071 教训）────────────────────────────────────────────
    # 先探测本次用的 ffmpeg 版本，用于对账
    current_ffmpeg = _detect_ffmpeg_version()
    frozen_ffmpeg = ref.get("decoder", {}).get("ffmpeg_version") if ref.get("decoder") else None

    if frozen_ffmpeg is None:
        # 冻结表解码器身份未知（2026-08-24 实测时未记录）
        # 不能因此放过比对，也不能因此判失败——照常比数值，但明确打印解码器差异风险
        print(
            f"⚠  参考读数的解码器身份未知（冻结表 decoder.ffmpeg_version=null）。\n"
            f"   本次复现用的是：{current_ffmpeg}\n"
            f"   若数值不符，第一嫌疑是解码器差异而不是算法变了（见 DP-071）。\n"
            f"   建议事后更新冻结表：将 decoder.ffmpeg_version 填为 {current_ffmpeg!r}"
        )
    elif frozen_ffmpeg != current_ffmpeg:
        print(
            f"⚠  解码器版本不匹配：\n"
            f"   冻结表：{frozen_ffmpeg}\n"
            f"   本次  ：{current_ffmpeg}\n"
            f"   若数值不符，第一嫌疑是解码器差异（DP-071）。"
        )
    else:
        print(f"解码器版本一致：{current_ffmpeg}")

    # SHA256 检查（冻结表目前为 null，提示用户手动核）
    if ref.get("sha256") is None:
        print("注意：SHA256 冻结值为 null，未做哈希比对。如需锁定素材完整性，请手动计算并更新冻结表。")

    if errors:
        print("比对失败：")
        for e in errors:
            print(f"  ✗ {e}")
        return 1

    print("全部比对通过：")
    print(f"  对比度绝对差: {contrast_val:.1f} 灰阶（参考 {ref['contrast_abs']}）")
    print(f"  对比度比值  : {contrast_ratio:.2f}×（参考 {ref['contrast_ratio']}）")
    print(f"  噪声底      : {nf_val:.2f} px（参考 {ref['noise_floor_px']}）")
    print(f"  面积抖动 p90: {jitter_val:.4f}（参考 {ref['area_jitter_p90']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
