"""三个时钟与 `protocol_alignment`：时间不猜（Spec A §6.1）。

FST 的一条录像里同时跑着三个**不同步**的时钟，混用即口径事故：

1. `source_media`  —— 原视频的媒体时间（第 0 帧 = 录像按下开始）。
2. `analysis_media` —— 分析素材（转码件）的媒体时间。DP-133 清单实测：
   14 份登记转码件的帧数与源视频一致、pts 起点比源晚 0.54 s，
   即 **分析素材时间 = 原视频媒体时间 + 0.54 s**。这个偏移是**查出来的**，
   不是假设；查不到就写 None，不许填 0。
3. `protocol` —— 协议时间（入水 t0 = 0）。正式口径的 (120, 360) 窗口
   长在这个时钟上。**t0 未取得时这个时钟不存在**，不是"等于媒体时间"。

纪律（逐条对应 Spec A §6.1）：

- t0 已知 ⇒ `t_protocol = t_source - t0_source`，**可以为负**，绝不 clamp
  （入水前那两分钟适应期是负协议时间，clamp 到 0 会把适应期折进窗口）。
- t0 未知 ⇒ 只出媒体时间诊断，标 `protocol_alignment="unknown"`，
  **任何情况下**不许被叫作标准窗验收。
- **绝不**自动截"最后 4 分钟"、绝不静默补帧、绝不丢缺口：
  截断/缺口要报**实际覆盖率**（`Coverage`），让读报告的人自己看见少了什么。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: t0 未知时的对齐标记。诊断 JSON 里出现它 = 这些数字不能当标准窗验收。
PROTOCOL_ALIGNMENT_UNKNOWN = "unknown"
#: t0 已有人工/记录确认时的标记。
PROTOCOL_ALIGNMENT_KNOWN = "known_t0"

CLOCK_SOURCE_MEDIA = "source_media"
CLOCK_ANALYSIS_MEDIA = "analysis_media"
CLOCK_PROTOCOL = "protocol"

#: 名义 FST 计分窗口（协议时间，秒）。常数不动（Spec A §6.1），
#: 变的是"它落在媒体时间轴的哪里"，而那取决于 t0。
NOMINAL_WINDOW_S: tuple[float, float] = (120.0, 360.0)


@dataclass(frozen=True)
class TimeBase:
    """一段素材的时间基准。每个字段要么读出来、要么显式 None，没有默认值。"""

    fps: float
    n_frames: int
    clock: str                       # 本次诊断用的时钟：source_media / analysis_media
    protocol_alignment: str          # unknown | known_t0
    t0_source_s: float | None        # 入水时刻（源媒体时间，秒）；None = 未取得
    analysis_offset_s: float | None  # 分析素材相对原视频的偏移；None = 未查到
    offset_evidence: str = ""        # 偏移从哪条证据来的；未查到则空串
    frame_count_source: str = ""     # nb_frames | packets（video.VideoInfo 原样带出）

    def __post_init__(self) -> None:
        if self.clock not in (CLOCK_SOURCE_MEDIA, CLOCK_ANALYSIS_MEDIA):
            raise ValueError(f"未知时钟: {self.clock}")
        if self.protocol_alignment not in (PROTOCOL_ALIGNMENT_UNKNOWN,
                                           PROTOCOL_ALIGNMENT_KNOWN):
            raise ValueError(f"未知对齐标记: {self.protocol_alignment}")
        if self.protocol_alignment == PROTOCOL_ALIGNMENT_KNOWN and self.t0_source_s is None:
            raise ValueError("声称 known_t0 却没给 t0_source_s——不许半承认")
        if self.fps <= 0:
            raise ValueError(f"fps 必须为正: {self.fps}")

    @property
    def duration_s(self) -> float:
        return self.n_frames / self.fps

    def frame_to_s(self, frame: int) -> float:
        """媒体时间（本素材时钟）。帧号 → 秒，只用实测 fps。"""
        return frame / self.fps

    def s_to_frame(self, s: float) -> int:
        """秒 → 帧号。向下取整：窗口起点那一秒的第一帧属于窗口。"""
        return int(s * self.fps)

    def to_protocol_s(self, media_s: float) -> float | None:
        """媒体时间 → 协议时间。t0 未知 ⇒ **None**，绝不拿媒体时间冒充。"""
        if self.protocol_alignment != PROTOCOL_ALIGNMENT_KNOWN:
            return None
        assert self.t0_source_s is not None
        base = media_s
        if self.clock == CLOCK_ANALYSIS_MEDIA and self.analysis_offset_s is not None:
            base = media_s - self.analysis_offset_s   # 回到源媒体时间再减 t0
        return base - self.t0_source_s                # 可以为负，不 clamp


@dataclass(frozen=True)
class WindowPlan:
    """名义窗口 (120,360) 在这段素材上的落点，以及它**没**覆盖到什么。

    `media_frames is None` 表示本次不套标准窗（alignment=unknown）：
    诊断走整条媒体时间轴，数字只能当诊断。这里**没有**"退而截最后 4 分钟"
    这个分支——那条路会把"没对齐"伪装成"对齐了但换了窗"。
    """

    requested_s: tuple[float, float]
    alignment: str
    media_frames: tuple[int, int] | None   # 媒体帧区间 [a, b)；不套窗则 None
    reason: str
    clamped: bool = False                  # 恒 False：本模块不做 clamp，字段留给审计
    truncated: bool = False                # 窗口超出素材范围（已知 t0 也可能发生）

    @property
    def applies_standard_window(self) -> bool:
        return self.media_frames is not None


def plan_window(tb: TimeBase) -> WindowPlan:
    """把名义窗口落到媒体帧上。t0 未知 ⇒ 不套窗（见 WindowPlan 文档）。"""
    if tb.protocol_alignment != PROTOCOL_ALIGNMENT_KNOWN:
        return WindowPlan(
            requested_s=NOMINAL_WINDOW_S,
            alignment=PROTOCOL_ALIGNMENT_UNKNOWN,
            media_frames=None,
            reason="t0（入水时刻）未取得：不套标准窗，诊断走整条媒体时间轴。"
                   "**不**自动截最后 4 分钟，**不**把录像第 120 秒当入水后第 120 秒。",
        )
    assert tb.t0_source_s is not None
    t0 = tb.t0_source_s
    if tb.clock == CLOCK_ANALYSIS_MEDIA and tb.analysis_offset_s is not None:
        t0 = t0 + tb.analysis_offset_s      # t0 是源时间，换算到分析素材时钟
    a = tb.s_to_frame(t0 + NOMINAL_WINDOW_S[0])
    b = tb.s_to_frame(t0 + NOMINAL_WINDOW_S[1])
    truncated = a < 0 or b > tb.n_frames
    return WindowPlan(
        requested_s=NOMINAL_WINDOW_S,
        alignment=PROTOCOL_ALIGNMENT_KNOWN,
        media_frames=(a, b),
        reason=f"t0={tb.t0_source_s} s（源媒体时间）⇒ 媒体帧 [{a}, {b})",
        truncated=truncated,                # a 可以为负：交给 Coverage 报实际覆盖
    )


@dataclass(frozen=True)
class Coverage:
    """实际覆盖率。**绝不静默补帧/丢缺口**：缺口逐段列出来。"""

    requested_frames: int | None            # 不套窗时 None
    observed_frames: int
    gaps: tuple[tuple[int, int], ...] = ()  # 解码缺口 [a, b)，按帧号
    truncated_frames: int = 0               # 请求了但素材里没有的帧数

    @property
    def coverage_frac(self) -> float | None:
        if not self.requested_frames:
            return None
        return self.observed_frames / self.requested_frames

    def to_dict(self) -> dict:
        return {
            "requested_frames": self.requested_frames,
            "observed_frames": self.observed_frames,
            "gaps": [list(g) for g in self.gaps],
            "truncated_frames": self.truncated_frames,
            "coverage_frac": self.coverage_frac,
        }


def coverage_of(plan: WindowPlan, *, n_frames: int,
                observed: list[int] | None = None,
                gaps: tuple[tuple[int, int], ...] = ()) -> Coverage:
    """由窗口计划与实际解码情况算覆盖率。

    `observed` 给的是**真解出来的帧号**；不给就按窗口满覆盖记
    （`gaps` 里列出的缺口照扣）。截断的帧数如实报，不补。
    """
    if plan.media_frames is None:
        total = n_frames
        lo, hi = 0, n_frames
    else:
        lo, hi = plan.media_frames
        total = max(0, hi - lo)
    lo_c, hi_c = max(0, lo), min(n_frames, hi)
    truncated = total - max(0, hi_c - lo_c)
    if observed is not None:
        got = sum(1 for f in observed if lo_c <= f < hi_c)
    else:
        got = max(0, hi_c - lo_c) - sum(max(0, min(hi_c, b) - max(lo_c, a))
                                        for a, b in gaps)
    return Coverage(requested_frames=total or None, observed_frames=got,
                    gaps=gaps, truncated_frames=truncated)


@dataclass
class ClockLedger:
    """一次诊断的三时钟台账：哪个时钟在用、另外两个为什么不在。

    Spec A §6.1 要"三个时钟"，不是要三个数——要的是**读报告的人看得见
    现在用的是哪一个、另两个缺哪一环**。缺的一环写进 `missing`，不写 0。
    """

    in_use: str
    missing: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"in_use": self.in_use, "missing": dict(self.missing)}


def build_ledger(tb: TimeBase) -> ClockLedger:
    missing: dict[str, str] = {}
    if tb.protocol_alignment != PROTOCOL_ALIGNMENT_KNOWN:
        missing[CLOCK_PROTOCOL] = "t0（入水 vs 录像起点）未取得，待操作人确认"
    else:
        missing.pop(CLOCK_PROTOCOL, None)
    if tb.clock == CLOCK_SOURCE_MEDIA:
        missing[CLOCK_ANALYSIS_MEDIA] = "本次直接读原视频，未走转码件"
    elif tb.analysis_offset_s is None:
        missing[CLOCK_ANALYSIS_MEDIA] = "转码件相对原视频的偏移未查到（不许填 0）"
    return ClockLedger(in_use=tb.clock, missing=missing)
