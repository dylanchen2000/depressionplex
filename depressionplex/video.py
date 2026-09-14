"""视频帧源：把一段录像解成灰度帧序列。

**位置纪律**：`assay_core` 只依赖 numpy（README 硬规矩），解码要调 ffmpeg，
所以驱动层放在 assay_core 之外。**不许把 subprocess 塞进 assay_core。**

**不猜纪律（这一条是本模块存在的主要理由）**：fps / 帧数 / 尺寸一律从文件读，
读不到就 raise。绝不填一个"常见值"（25 fps）接着算——本项目所有阈值都是**秒**
（单位不变量），秒↔帧换算错了，所有事件时长、所有 bout 参数、所有 immobility
数字一起错，而且错得很安静：报告照样漂亮，只是全是假的。

**帧数两个来源要能追溯**：容器头里的 `nb_frames` 常常缺失或与实际流不符，
缺失时退回逐包计数（慢但准）。哪一种来的记在 `VideoInfo.frame_count_source`
里——出了对不上的事，先看这一栏。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np


#: decoder.source 的取值集合（A4）。三种来源，优先级由 _resolve_ffmpeg_tool 决定。
DECODER_SOURCES = ("env", "bundled", "system")


class VideoError(RuntimeError):
    """解码/探测失败。**不降级、不猜参数**，直接抛给上层。"""


@dataclass(frozen=True)
class VideoInfo:
    """一段录像的实测元数据。**每个字段都是读出来的，没有默认值。**"""

    path: Path
    fps: float
    n_frames: int
    width: int
    height: int
    frame_count_source: str      # "nb_frames"（容器头）| "packets"（逐包计数）

    @property
    def duration_s(self) -> float:
        return self.n_frames / self.fps


def _resolve_ffmpeg_tool(tool_name: str) -> tuple[str, str]:
    """唯一的 ffmpeg 工具解析函数（架构 §0.2 裁决）。

    解析顺序：env → 随包 → PATH。**不许再有别处直接把 "ffmpeg" / "ffprobe" 当命令用。**

    Args:
        tool_name: "ffmpeg" 或 "ffprobe"

    Returns:
        (path, source) 元组：
        - path: 可执行文件的完整路径
        - source: "env" | "bundled" | "system"（单一来源，不许在别处二次推导）

    Raises:
        VideoError: 三处都找不到时，列出找过的路径
    """
    # 1. 环境变量优先（给 CI 与现场排障用，同 DPX_ENGINE_CMD 的路子）
    env_var = f"DPX_{tool_name.upper()}"
    env_path = os.environ.get(env_var)
    if env_path and Path(env_path).exists():
        return (env_path, "env")

    # 2. 随包：冻结时在 sys.executable 旁边的 ffmpeg/ 下，源码跑时在仓根 vendor/ffmpeg/
    is_frozen = getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
    exe_name = f"{tool_name}.exe" if sys.platform == "win32" else tool_name

    if is_frozen:
        # 冻结后：backend/ffmpeg/（与引擎同级）
        bundled = Path(sys.executable).parent / "ffmpeg" / exe_name
    else:
        # 源码：仓根/vendor/ffmpeg/
        repo_root = Path(__file__).resolve().parent.parent  # video.py 在 depressionplex/ 下
        bundled = repo_root / "vendor" / "ffmpeg" / exe_name

    if bundled.exists():
        return (str(bundled), "bundled")

    # 3. 系统 PATH（兜底）
    system_path = shutil.which(tool_name)
    if system_path:
        return (system_path, "system")

    # 都没有 ⇒ 抛 VideoError，把找过的三处路径全列出来
    raise VideoError(
        f"找不到 {tool_name}。驱动层需要 ffmpeg 套件解码视频（assay_core 本身仍只依赖 numpy）。\n"
        f"已查找：\n"
        f"  1. 环境变量 {env_var}={env_path!r}\n"
        f"  2. 随包位置：{bundled}\n"
        f"  3. 系统 PATH：{shutil.which(tool_name)}\n"
        f"三处都不存在。\n"
        f"Windows 安装包会自带 LGPL 版 ffmpeg；源码跑时需手动下载到 vendor/ffmpeg/，"
        f"或用环境变量 {env_var} 指定路径。"
    )


def _run(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise VideoError(f"命令失败（exit {p.returncode}）：{' '.join(cmd)}\n{p.stderr[:400]}")
    return p.stdout


def _parse_rate(text: str | None) -> float | None:
    """把 ffprobe 的 "25/1" 解成 25.0。"0/0"（未知）⇒ None，**不当 0 也不当 25**。"""
    if not text or "/" not in text:
        return None
    num, den = text.split("/", 1)
    try:
        n, d = float(num), float(den)
    except ValueError:
        return None
    if d == 0 or n <= 0:
        return None
    return n / d


def probe(path: str | Path) -> VideoInfo:
    """读 fps / 帧数 / 尺寸。任何一项读不到 ⇒ raise（见模块文档"不猜纪律"）。"""
    p = Path(path)
    if not p.exists():
        raise VideoError(f"视频不存在：{p}")
    ffprobe, _ = _resolve_ffmpeg_tool("ffprobe")
    out = _run([ffprobe, "-v", "error", "-select_streams", "v:0",
                "-show_entries",
                "stream=avg_frame_rate,r_frame_rate,nb_frames,width,height",
                "-of", "json", str(p)])
    streams = json.loads(out).get("streams") or []
    if not streams:
        raise VideoError(f"{p.name} 里没有视频流")
    s = streams[0]

    fps = _parse_rate(s.get("avg_frame_rate")) or _parse_rate(s.get("r_frame_rate"))
    if fps is None:
        raise VideoError(
            f"{p.name}：读不出帧率（avg/r_frame_rate 都不可用）。"
            "**不填默认值**——秒↔帧换算错了，所有时长指标一起错")
    width, height = s.get("width"), s.get("height")
    if not width or not height:
        raise VideoError(f"{p.name}：读不出画幅尺寸")

    nb = s.get("nb_frames")
    if nb not in (None, "", "N/A") and int(nb) > 0:
        n_frames, src = int(nb), "nb_frames"
    else:
        # 容器头没记 ⇒ 逐包计数。慢，但比"用时长×fps 推"可靠：
        # 推出来的帧数与实际差一两帧，窗口边界就会错位。
        out2 = _run([ffprobe, "-v", "error", "-select_streams", "v:0",
                     "-count_packets", "-show_entries", "stream=nb_read_packets",
                     "-of", "json", str(p)])
        st2 = (json.loads(out2).get("streams") or [{}])[0]
        cnt = st2.get("nb_read_packets")
        if cnt in (None, "", "N/A") or int(cnt) <= 0:
            raise VideoError(f"{p.name}：帧数既没记在容器头里也数不出来")
        n_frames, src = int(cnt), "packets"

    return VideoInfo(path=p, fps=float(fps), n_frames=n_frames,
                     width=int(width), height=int(height), frame_count_source=src)


def decode_cmd(info: VideoInfo, *, start_frame: int = 0,
               n_frames: int | None = None) -> list[str]:
    """构造解码命令。**纯函数，可单测**——解码要 ffmpeg，命令拼装不需要。

    用 `select=gte(n,K)` 按**帧号**定位而不是 `-ss` 按时间：`-ss` 会落到最近
    关键帧，误差几帧，而窗口边界（TST 全程 / FST 后 4 min）差几帧就是口径事故。
    """
    if start_frame < 0:
        raise ValueError(f"start_frame 不能为负：{start_frame}")
    ffmpeg, _ = _resolve_ffmpeg_tool("ffmpeg")
    cmd = [ffmpeg, "-v", "error", "-i", str(info.path)]
    if start_frame:
        cmd += ["-vf", f"select=gte(n\\,{start_frame})", "-vsync", "0"]
    if n_frames is not None:
        if n_frames <= 0:
            raise ValueError(f"n_frames 必须为正：{n_frames}")
        cmd += ["-frames:v", str(n_frames)]
    return cmd + ["-f", "rawvideo", "-pix_fmt", "gray", "-"]


def iter_gray(info: VideoInfo, *, start_frame: int = 0,
              n_frames: int | None = None) -> Iterator[np.ndarray]:
    """逐帧吐 uint8 灰度图（h×w）。**不整段进内存。**

    读到半帧（管道被截断）⇒ raise：半帧静默丢掉会让后面所有帧号偏移一位，
    而帧号偏移在报告里完全看不出来。
    """
    cmd = decode_cmd(info, start_frame=start_frame, n_frames=n_frames)  # 里面已调用 _resolve_ffmpeg_tool
    per = info.width * info.height
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    try:
        while True:
            buf = proc.stdout.read(per)
            if not buf:
                break
            if len(buf) != per:
                raise VideoError(
                    f"{info.path.name}：读到不完整的帧（{len(buf)}/{per} 字节）"
                    "——半帧不许静默丢弃，帧号一偏后面全错")
            yield np.frombuffer(buf, dtype=np.uint8).reshape(info.height, info.width)
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        proc.wait()
        if proc.returncode not in (0, None) and err.strip():
            raise VideoError(f"ffmpeg 解码失败：{err[:400]}")


def frames_at(info: VideoInfo, indices: list[int]) -> list[np.ndarray]:
    """取指定帧号的帧（单次顺序解码，只留要的那几帧）。用于标定抽帧。

    刻意不用 4 次 `-ss` 跳转：跳转按关键帧对齐，取到的不是你要的那一帧，
    而标定帧一旦取错，走廊/隔间标定就建在别的画面上。
    """
    want = sorted(set(i for i in indices if 0 <= i < info.n_frames))
    if not want:
        return []
    out: dict[int, np.ndarray] = {}
    need = set(want)
    for i, g in enumerate(iter_gray(info, n_frames=want[-1] + 1)):
        if i in need:
            out[i] = g.copy()          # 管道缓冲会被复用，必须拷
            need.discard(i)
            if not need:
                break
    missing = sorted(need)
    if missing:
        raise VideoError(
            f"{info.path.name}：要的帧没解出来 {missing[:8]}"
            f"（容器头说有 {info.n_frames} 帧，来源 {info.frame_count_source}）"
            "——元数据与实际流不符，不许当没这回事")
    return [out[i] for i in want]
