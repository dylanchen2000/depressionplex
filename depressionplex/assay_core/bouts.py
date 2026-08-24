"""CSI 兼容的 bout 后处理流水线。

顺序严格按 ForcedSwimScan / TailSuspScan 两份手册交叉确认的真实执行顺序：

    First Combine Limit  → 预合并（在去噪之前，可合并单帧记录）
    Noise Thresh         → 删除过短记录
    Combination Limit    → 主合并（Merge Bouts Limit）
    Length Thresh        → 最终最短长度过滤
    Section Size         → 分箱多数投票（仅 Bin-wise 打分模式使用）

参数名刻意与 CSI 保持一致，这是「CSI 兼容模式」的基础——让已装 CSI 的客户
历史数据能对齐。我方推荐的默认参数集另行提供（有物理单位、跨设置稳定）。

区间约定：[start, end] 闭区间，帧号。与手册例子一致（"begins and ends at
frames 1050 and 1145"）。间隔按手册算法定义为 next.start - prev.end。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Sequence

SCORE_RANGE = "range"  # CSI 默认，走完整流水线后的区间
SCORE_FRAME = "frame"  # 原始逐帧，不做平滑
SCORE_AVERAGE = "average"  # 上两者均值
SCORE_BIN = "bin"  # 分箱多数投票


@dataclass(frozen=True)
class Interval:
    """一个 bout。start/end 为闭区间帧号。"""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"非法区间: [{self.start}, {self.end}]")

    @property
    def length(self) -> int:
        """含首尾的帧数。"""
        return self.end - self.start + 1


@dataclass(frozen=True)
class BoutParams:
    """CSI 同名参数。帧为单位者标 _frames，秒为单位者标 _seconds。"""

    first_combine_limit: int = 5
    noise_thresh_frames: int = 5
    combination_limit: int = 30
    length_thresh_frames: int = 30
    section_size_seconds: float = 5.0
    score_method: str = SCORE_RANGE


# 读自 TailSuspScan 手册 Figure 4.1 的 "Forced Swim Settings" 面板。
# 这是 CSI FST 默认值目前唯一可得的一手来源。
CSI_FST_DEFAULTS = BoutParams(
    first_combine_limit=5,
    noise_thresh_frames=5,
    combination_limit=30,
    length_thresh_frames=30,
    section_size_seconds=5.0,
    score_method=SCORE_RANGE,
)

# TailSuspScan 手册明确未给出 Motion/bout 各参数默认值（正文原话：Mobility Thresh
# 是 "just a relative measure"）。因此 TST 沿用 FST 的一组作为起点，待我方数据标定。
CSI_TST_STARTING_POINT = CSI_FST_DEFAULTS


def intervals_from_labels(labels: Sequence[object], target: object) -> list[Interval]:
    """把逐帧标签序列转成目标类别的区间列表。"""
    out: list[Interval] = []
    start: int | None = None
    for i, lab in enumerate(labels):
        if lab == target:
            if start is None:
                start = i
        elif start is not None:
            out.append(Interval(start, i - 1))
            start = None
    if start is not None:
        out.append(Interval(start, len(labels) - 1))
    return out


def _gap(prev: Interval, nxt: Interval) -> int:
    """手册定义的间隔：next.start - prev.end。

    注意这不是"空洞帧数"——相邻紧接的两段间隔为 1 而非 0。照抄手册算法，
    以保证与 CSI 数值可对齐。
    """
    return nxt.start - prev.end


def merge_by_gap(intervals: Iterable[Interval], limit: int) -> list[Interval]:
    """间隔小于 limit 的相邻区间合并。limit <= 0 时不做任何合并。"""
    items = sorted(intervals, key=lambda iv: iv.start)
    if limit <= 0 or not items:
        return items
    out = [items[0]]
    for iv in items[1:]:
        if _gap(out[-1], iv) < limit:
            out[-1] = replace(out[-1], end=max(out[-1].end, iv.end))
        else:
            out.append(iv)
    return out


def drop_shorter_than(intervals: Iterable[Interval], min_frames: int) -> list[Interval]:
    """删除长度小于 min_frames 的区间（长度 >= 阈值者保留）。"""
    if min_frames <= 0:
        return list(intervals)
    return [iv for iv in intervals if iv.length >= min_frames]


def run_pipeline(intervals: Iterable[Interval], params: BoutParams) -> list[Interval]:
    """按 CSI 顺序跑完四段后处理（不含分箱打分）。"""
    ivs = merge_by_gap(intervals, params.first_combine_limit)
    ivs = drop_shorter_than(ivs, params.noise_thresh_frames)
    ivs = merge_by_gap(ivs, params.combination_limit)
    ivs = drop_shorter_than(ivs, params.length_thresh_frames)
    return ivs


def total_frames_in(intervals: Iterable[Interval]) -> int:
    return sum(iv.length for iv in intervals)


def bin_vote(
    intervals: Iterable[Interval],
    *,
    total_frames: int,
    fps: float,
    section_size_seconds: float,
) -> tuple[list[bool], int]:
    """分箱多数投票。

    手册原文：bin 内该行为超过半个 bin 时长，则整个 bin 计为该行为；否则整个
    bin 不计。注意是严格「more than」，故用 > 而非 >=。

    返回 (每个 bin 是否计入, 折算后的总帧数)。
    """
    bin_frames = max(1, int(round(section_size_seconds * fps)))
    occupied = [False] * total_frames
    for iv in intervals:
        for f in range(max(0, iv.start), min(total_frames, iv.end + 1)):
            occupied[f] = True

    votes: list[bool] = []
    counted = 0
    for b0 in range(0, total_frames, bin_frames):
        b1 = min(b0 + bin_frames, total_frames)
        width = b1 - b0
        hits = sum(occupied[b0:b1])
        win = hits > width / 2.0
        votes.append(win)
        if win:
            counted += width
    return votes, counted


@dataclass(frozen=True)
class BoutSummary:
    """单个行为类别的统计结果，字段对标 CSI 的 Bouts / Duration / Duration%。"""

    bouts: int
    frames: int
    seconds: float
    duration_pct: float
    mean_bout_frames: float
    first_onset_frame: int | None  # 潜伏期 latency（CSI 不导出，我方扩展）
    intervals: tuple[Interval, ...]


def summarize(
    intervals: Sequence[Interval], *, total_frames: int, fps: float
) -> BoutSummary:
    frames = total_frames_in(intervals)
    return BoutSummary(
        bouts=len(intervals),
        frames=frames,
        seconds=frames / fps if fps > 0 else 0.0,
        duration_pct=100.0 * frames / total_frames if total_frames > 0 else 0.0,
        mean_bout_frames=frames / len(intervals) if intervals else 0.0,
        first_onset_frame=intervals[0].start if intervals else None,
        intervals=tuple(intervals),
    )


def score(
    labels: Sequence[object],
    target: object,
    *,
    params: BoutParams = CSI_FST_DEFAULTS,
    fps: float = 30.0,
) -> BoutSummary:
    """按指定 Score Method 打分。

    - range   : 走完整流水线（CSI 默认，也是手册推荐的最佳方式）
    - frame   : 原始逐帧，不平滑
    - average : 上两者帧数均值
    - bin     : 流水线后再做分箱多数投票
    """
    total = len(labels)
    raw = intervals_from_labels(labels, target)
    method = params.score_method

    if method == SCORE_FRAME:
        return summarize(raw, total_frames=total, fps=fps)

    smoothed = run_pipeline(raw, params)

    if method == SCORE_RANGE:
        return summarize(smoothed, total_frames=total, fps=fps)

    if method == SCORE_AVERAGE:
        base = summarize(smoothed, total_frames=total, fps=fps)
        avg_frames = (total_frames_in(raw) + total_frames_in(smoothed)) / 2.0
        return replace(
            base,
            frames=int(round(avg_frames)),
            seconds=avg_frames / fps if fps > 0 else 0.0,
            duration_pct=100.0 * avg_frames / total if total > 0 else 0.0,
        )

    if method == SCORE_BIN:
        _, counted = bin_vote(
            smoothed,
            total_frames=total,
            fps=fps,
            section_size_seconds=params.section_size_seconds,
        )
        base = summarize(smoothed, total_frames=total, fps=fps)
        return replace(
            base,
            frames=counted,
            seconds=counted / fps if fps > 0 else 0.0,
            duration_pct=100.0 * counted / total if total > 0 else 0.0,
        )

    raise ValueError(f"未知 Score Method: {method}")
