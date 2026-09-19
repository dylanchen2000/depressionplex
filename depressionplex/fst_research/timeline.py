"""三个时钟与 `protocol_alignment`：时间不猜（Spec A §6.1）。

FST 的一条录像里同时跑着三个**不同步**的时钟，混用即口径事故：

1. `source_media`  —— 原视频的媒体时间（第 0 帧 = 录像按下开始）。
2. `analysis_media` —— 分析素材（转码件）的媒体时间。DP-133 清单实测：
   14 份登记转码件的帧数与源视频一致、**raw PTS 起点**比源晚 0.54 s。
   R2-115 T3（与 #114 同一口径）：这 0.54 s 只是两文件 pts_min 的
   **raw PTS 起点差**；两条素材的媒体本地时间（首帧 = 0）都可能从 0 起，
   raw PTS 差**不能**未经换算证据就当媒体本地偏移使用。偏移查不到/未核实
   就写 None 并拒绝换算，不许填 0，也不许静默跳过减法。
3. `protocol` —— 协议时间（入水 t0 = 0）。正式口径的 (120, 360) 窗口
   长在这个时钟上。**t0 未取得时这个时钟不存在**，不是"等于媒体时间"。
   t0 是科学事实不是参数：`known_t0` 必须带依据字符串（`t0_evidence`）。

纪律（逐条对应 Spec A §6.1）：

- t0 已知 ⇒ `t_protocol = t_source - t0_source`，**可以为负**，绝不 clamp
  （入水前那两分钟适应期是负协议时间，clamp 到 0 会把适应期折进窗口）。
- t0 未知 ⇒ 只出媒体时间诊断，标 `protocol_alignment="unknown"`，
  **任何情况下**不许被叫作标准窗验收。
- **绝不**自动截"最后 4 分钟"、绝不静默补帧、绝不丢缺口：
  截断/缺口要报**实际覆盖率**（`Coverage`），让读报告的人自己看见少了什么。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

#: t0 未知时的对齐标记。诊断 JSON 里出现它 = 这些数字不能当标准窗验收。
PROTOCOL_ALIGNMENT_UNKNOWN = "unknown"
#: t0 已有人工/记录确认时的标记。
PROTOCOL_ALIGNMENT_KNOWN = "known_t0"

CLOCK_SOURCE_MEDIA = "source_media"
CLOCK_ANALYSIS_MEDIA = "analysis_media"
CLOCK_PROTOCOL = "protocol"

#: 素材角色（R2-115 T3）：输入文件是原视频还是转码件，只认 DP-133 共用
#: 清单的登记行，不由 CLI 口头声称。查不到 ⇒ unverified（不声称任何一种）。
MEDIA_ROLE_SOURCE = "source_video"
MEDIA_ROLE_TRANSCODE = "transcode"
MEDIA_ROLE_UNVERIFIED = "unverified"
MEDIA_ROLES = (MEDIA_ROLE_SOURCE, MEDIA_ROLE_TRANSCODE, MEDIA_ROLE_UNVERIFIED)

#: 清单 role 列的词表映射（docs/共用输入身份清单_v1.csv 实际取值）。
MANIFEST_ROLE_SOURCE = "source_video"
MANIFEST_ROLE_TRANSCODE = frozenset({"csi_transcode", "transcode_intermediate",
                                     "transcode"})

#: 名义 FST 计分窗口（协议时间，秒）。常数不动（Spec A §6.1），
#: 变的是"它落在媒体时间轴的哪里"，而那取决于 t0。
NOMINAL_WINDOW_S: tuple[float, float] = (120.0, 360.0)


@dataclass(frozen=True)
class TimeBase:
    """一段素材的时间基准。每个字段要么读出来、要么显式 None，没有默认值。

    R2-115 T3 的三条拒绝（都在构造时炸，不留给下游"静默跳过"的机会）：
    - `known_t0` 没带 `t0_evidence` ⇒ 拒（t0 是科学事实不是参数）；
    - `analysis_media` 钟 + `known_t0` 但 `analysis_offset_s=None` ⇒ 拒：
      t0 定义在**源媒体时间**上，没有已核实偏移就换算不了——旧行为是
      静默跳过减法、拿分析素材时间冒充源时间，那是把"未知"算成"0"；
    - 任何时间量 NaN/Inf ⇒ 拒（坏数不许进记录，更不许参与窗口计算）。
    """

    fps: float
    n_frames: int
    clock: str                       # 本次诊断用的时钟：source_media / analysis_media
    protocol_alignment: str          # unknown | known_t0
    t0_source_s: float | None        # 入水时刻（源媒体时间，秒）；None = 未取得
    analysis_offset_s: float | None  # 分析素材相对原视频的偏移；None = 未查到
    offset_evidence: str = ""        # 偏移从哪条证据来的；未查到则空串
    frame_count_source: str = ""     # nb_frames | packets（video.VideoInfo 原样带出）
    t0_evidence: str = ""            # t0 的依据字符串；known_t0 时必填（随记录落盘）
    media_role: str = MEDIA_ROLE_UNVERIFIED   # 清单核出的素材角色，见 MEDIA_ROLES

    def __post_init__(self) -> None:
        if self.clock not in (CLOCK_SOURCE_MEDIA, CLOCK_ANALYSIS_MEDIA):
            raise ValueError(f"未知时钟: {self.clock}")
        if self.protocol_alignment not in (PROTOCOL_ALIGNMENT_UNKNOWN,
                                           PROTOCOL_ALIGNMENT_KNOWN):
            raise ValueError(f"未知对齐标记: {self.protocol_alignment}")
        if self.media_role not in MEDIA_ROLES:
            raise ValueError(f"未知素材角色: {self.media_role}")
        if not math.isfinite(self.fps) or self.fps <= 0:
            raise ValueError(f"fps 必须为正且有限: {self.fps}")
        if self.n_frames < 0:
            raise ValueError(f"n_frames 不能为负: {self.n_frames}")
        for name, val in (("t0_source_s", self.t0_source_s),
                          ("analysis_offset_s", self.analysis_offset_s)):
            if val is not None and not math.isfinite(val):
                raise ValueError(f"{name} 不是有限数（NaN/Inf）：时间不猜，拒绝")
        if self.protocol_alignment == PROTOCOL_ALIGNMENT_KNOWN:
            if self.t0_source_s is None:
                raise ValueError("声称 known_t0 却没给 t0_source_s——不许半承认")
            if not self.t0_evidence.strip():
                raise ValueError("known_t0 没带 t0_evidence：t0 是科学事实不是参数，"
                                 "必须随附依据字符串（落进记录）")
            if self.clock == CLOCK_ANALYSIS_MEDIA and self.analysis_offset_s is None:
                raise ValueError(
                    "analysis_media 时钟 + known_t0，但转码件相对原视频的偏移未查到："
                    "t0 定义在源媒体时间上，没有偏移无法换算——拒绝猜测，"
                    "也不静默跳过减法（那等于把未知偏移当 0）")

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
        """媒体时间 → 协议时间。t0 未知 ⇒ **None**，绝不拿媒体时间冒充。

        analysis_media 钟必须已有核实偏移（构造时已保证）；偏移 None 时
        这里**炸**而不是静默跳过减法（R2-115 T3：跳过 = 把未知当 0）。
        """
        if self.protocol_alignment != PROTOCOL_ALIGNMENT_KNOWN:
            return None
        assert self.t0_source_s is not None
        base = media_s
        if self.clock == CLOCK_ANALYSIS_MEDIA:
            if self.analysis_offset_s is None:      # 构造已拒；防御性保留
                raise ValueError("analysis_media 钟偏移未查到：拒绝换算（不填 0）")
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
    if tb.clock == CLOCK_ANALYSIS_MEDIA:
        if tb.analysis_offset_s is None:            # 构造已拒；防御性保留
            raise ValueError("analysis_media 钟偏移未查到：窗口落点无法换算（不填 0）")
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
    """覆盖率三分（R2-115 T1）：解码完整性 / 抽样密度，各自独立报。

    旧版把"抽到的帧数"除以"时间轴帧数"叫 coverage_frac，抽样密度被冒充成
    解码完整性（step=5 时永远 ≈0.2，看着像丢了 80% 的帧）。现在：
    - **解码完整性**：计划消费的样点（expected_samples）vs 真消费的
      （consumed_frames，从实际消费的帧号数出来），缺口逐段列，不补；
    - **抽样密度**：step / 间距秒 / 占时间轴比例——这是选择，不是损失；
    - **抽样帧的可见性**（observed/unclear/…）在每杯记录的
      sampled_state_counts 里，不在本段混报。
    """

    requested_frames: int | None            # 分析区间（窗∩素材，或整条）帧数
    consumed_frames: int                    # 实际消费（解码并诊断）的帧数，区间内
    expected_samples: int                   # 抽样计划在区间内应消费的帧点数
    sample_step: int | None = None          # 抽帧步长（源帧）；None = 未记录
    sample_spacing_s: float | None = None   # 步长折合秒（step/fps）
    gaps: tuple[tuple[int, int], ...] = ()  # 解码缺口 [a, b)，按帧号
    truncated_frames: int = 0               # 请求了但素材里没有的帧数

    @property
    def sample_fraction(self) -> float | None:
        """实际看过占时间轴的比例（含抽样密度与解码缺失，两者在别处分报）。"""
        if not self.requested_frames:
            return None
        return self.consumed_frames / self.requested_frames

    @property
    def decode_complete(self) -> bool:
        """计划消费的样点是否全部真消费且无解码缺口。"""
        return self.consumed_frames >= self.expected_samples and not self.gaps

    def to_dict(self) -> dict:
        return {
            "requested_frames": self.requested_frames,
            "truncated_frames": self.truncated_frames,
            "decode": {
                "expected_samples": self.expected_samples,
                "consumed_frames": self.consumed_frames,
                "gaps": [list(g) for g in self.gaps],
                "complete": self.decode_complete,
            },
            "sampling": {
                "step_frames": self.sample_step,
                "spacing_s": self.sample_spacing_s,
                "sample_fraction": self.sample_fraction,
                "semantics": "计数是抽样记录数；sample_fraction 是实际看过占"
                             "时间轴的比例（抽样密度是选择，不是解码损失）",
            },
            "note": "覆盖三分：decode=解码完整性，sampling=抽样密度；"
                    "抽样帧的可见性在每杯 sampled_state_counts，不在此混报",
        }


def coverage_of(plan: WindowPlan, *, n_frames: int, consumed: list[int],
                gaps: tuple[tuple[int, int], ...] = (),
                sample_step: int | None = None,
                fps: float | None = None) -> Coverage:
    """由窗口计划与**实际消费的帧号**算覆盖率（R2-115 T1）。

    `consumed` 必须是真消费（解码并诊断）的帧号列表，不是计划表——
    覆盖从实际消费的帧号数出来，计划与实际的差就是解码完整性问题。
    """
    if plan.media_frames is None:
        total = n_frames
        lo, hi = 0, n_frames
    else:
        lo, hi = plan.media_frames
        total = max(0, hi - lo)
    lo_c, hi_c = max(0, lo), min(n_frames, hi)
    truncated = total - max(0, hi_c - lo_c)
    consumed_in = sum(1 for f in consumed if lo_c <= f < hi_c)
    step = sample_step if sample_step and sample_step > 0 else 1
    expected = len(range(lo_c, hi_c, step))
    spacing = (step / fps) if (fps and fps > 0) else None
    return Coverage(requested_frames=total or None, consumed_frames=consumed_in,
                    expected_samples=expected, sample_step=sample_step,
                    sample_spacing_s=spacing, gaps=tuple(gaps),
                    truncated_frames=truncated)


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


def resolve_media_role(lookup: dict | None) -> tuple[str, str, list[str]]:
    """清单查询结果 → (media_role, offset_evidence, problems)。

    R2-115 T3：CLI 不再把任何输入都口头称作"原视频/未经转码"。素材角色
    只认 DP-133 共用清单的登记行（role 列）：
    - 登记为 source_video ⇒ `source_video`；
    - 登记为转码类角色 ⇒ `transcode`（时间轴按转码件解释，偏移未核实）；
    - 查不到/没给清单 ⇒ `unverified`（不声称任何一种）；
    - 同一 sha256 既登记原视频又登记转码件 ⇒ **角色有歧义，拒绝猜**，
      problems 非空，由调用方拒绝执行。
    """
    if lookup is None or not lookup.get("found"):
        return (MEDIA_ROLE_UNVERIFIED,
                "素材角色未经清单核实（未提供清单或 sha256 未登记）："
                "不声称原视频，也不声称转码件", [])
    roles = {str(a.get("role", "")) for a in lookup.get("aliases", [])}
    has_src = MANIFEST_ROLE_SOURCE in roles
    trans_hits = roles & MANIFEST_ROLE_TRANSCODE
    if has_src and trans_hits:
        return (MEDIA_ROLE_UNVERIFIED, "",
                [f"同一 sha256 在清单里既登记 source_video 又登记 {sorted(trans_hits)}："
                 "素材角色有歧义，拒绝猜（先人工核对清单登记行）"])
    if has_src:
        return (MEDIA_ROLE_SOURCE,
                "清单 role=source_video：登记为原视频，本次直接读原视频，"
                "未经转码件", [])
    if trans_hits:
        return (MEDIA_ROLE_TRANSCODE,
                f"清单 role={sorted(trans_hits)}：登记为转码件，本时间轴是转码件"
                "自身媒体时间；相对原视频的偏移未核实（raw PTS 起点差≠媒体本地"
                "偏移，不许填 0）", [])
    return (MEDIA_ROLE_UNVERIFIED,
            f"清单登记角色 {sorted(roles)} 不是视频角色标注：素材角色未核实，"
            "不声称原视频", [])


def build_ledger(tb: TimeBase) -> ClockLedger:
    missing: dict[str, str] = {}
    if tb.protocol_alignment != PROTOCOL_ALIGNMENT_KNOWN:
        missing[CLOCK_PROTOCOL] = "t0（入水 vs 录像起点）未取得，待操作人确认"
    else:
        missing.pop(CLOCK_PROTOCOL, None)
    if tb.clock == CLOCK_SOURCE_MEDIA:
        if tb.media_role == MEDIA_ROLE_SOURCE:
            missing[CLOCK_ANALYSIS_MEDIA] = "清单登记 source_video：本次直接读原视频，未走转码件"
        else:
            # 角色未核实 ⇒ 不许声称"这是原视频"（R2-115 T3）
            missing[CLOCK_ANALYSIS_MEDIA] = (
                "素材角色未经清单核实：本时间轴是输入文件自身媒体时间，不声称它是"
                "原视频；若它实为转码件，相对原视频的偏移未查到（不许填 0）")
    elif tb.analysis_offset_s is None:
        missing[CLOCK_ANALYSIS_MEDIA] = "转码件相对原视频的偏移未查到（不许填 0）"
    return ClockLedger(in_use=tb.clock, missing=missing)
