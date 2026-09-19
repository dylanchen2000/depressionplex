"""杯体几何的**提案**与**人工确认件**：三个概念分开，只提案不确认。

Spec A §4 A1 第 2 条 / §6.2 第 4 行；二轮复核 R2-115 G 组把语义钉死：

**三个几何概念必须分开**（G1）——

| 概念 | 字段 | 是什么 |
|---|---|---|
| 杯/分析 ROI | `roi` | 找动物用的区域。**必须容纳水线上方**的头/前肢活动：FST 里动物扒杯壁、探头出水面是真实行为，ROI 顶边=水线会把它们裁掉，`above_water_frac` 就永远是假 0 |
| 水体候选区 | `water_body` | 中值帧中间灰阶连通域的 bbox（提案的水线来源）。人工确认件没有这个派生量 ⇒ None |
| 水线 | `water_surface_y` | 水面高度。不可靠（无旁证/旁证分歧）⇒ `water_surface_reliable=False` + 原因；下游水线特征必须置 **null + 原因**，不许填假 0 |

几何从哪来、不从哪来，这一条写死：

- **从真帧来**：动物是唯一"有时暗、有时亮"的东西，杯壁/挂钩/水线是静态的。
  逐帧取 min/max 之后，「min 暗 且 max 亮」的像素集合 = 动物到过的地方，
  连通域即各杯内区。这个信号不依赖任何 TST 几何假设，也不需要背光面板行带。
- **不从 CLB 来**：`.CLB` 里那些没讲清出处的数字（杯径、水高、像素/毫米）**绝不**
  换算成坐标。它们是 CSI 的参数，不是我们的测量；拿来当几何等于把假设当观测。
- **不确认**：所有提案 `confirmed=False`。`to_envelope` 交给
  `GeometryEnvelope.validate()` 时必然报「未确认的几何对象」——**这是设计**：
  人工确认之前，几何未确认状态必须机器可见（Spec A §6.2 第 4 行：
  时间窗几何未确认 ⇒ 可保留诊断，正式解释与发布验收受限）。

**人工确认的输入契约（G4）**：CLI 只吃 `load_confirmed_file`——一份带
binding 的 wrapper JSON（schema `fst-confirmed-geometry-v1`），绑定
**整段视频 sha256、帧尺寸、杯号集合、确认人、确认时间**，加载时算出
**文件自身 sha256** 供记录引用；任何一项对不上就拒绝。人只负责在叠加图
上核对并改 JSON 里的几何值/确认字段；文件生成与绑定由 Agent/脚本做
（`confirmation_payload` 从提案预填）。裸 envelope JSON（无 binding）**不是**
CLI 的合法输入——`load_confirmed` 只留作几何语义检查，不做输入通道。

**申报空杯的绑定（G3）**：`--declared-empty` 的杯号绑定**物理杯号**
（= 从左到右的 cup_id，1 起），不是"过滤后 props 列表下标"。杯候选数目
与期望不符（漏检/误检）⇒ 物理编号有歧义 ⇒ **拒绝应用申报**、照实记录、
相关杯保持未决——不许让申报顺着列表错位漂到别的杯上。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np

from ..assay_core import geometry as geo
from ..assay_core import segment as seg

#: 提案的来源标记。诊断 JSON 里写它，读的人知道几何是算出来的还是人给的。
BASIS_TEMPORAL_VARIANCE = "temporal_dark_variance"
BASIS_HUMAN_CONFIRMED = "human_confirmed_envelope"

#: 水线候选的依据标记。
WATERLINE_BASIS_WATER_BODY_TOP = "median_frame_water_body_top"
WATERLINE_BASIS_ROW_GRADIENT = "median_frame_row_gradient"
WATERLINE_BASIS_HUMAN = "human_confirmed"
WATERLINE_BASIS_NONE = "no_reliable_horizontal_edge"

#: 水体外边界与行梯度旁证峰允许的分歧（px）。超过就写 note 给人工确认看，
#: **不**自动改水线——两个信号谁对，得人在叠加图上定。
WATERLINE_CROSSCHECK_TOL_PX = 3.0

#: 杯候选的最小宽度（px）。**起点值，不是普适常数**：来自 DP-136 对
#: 正常1-4.mp4 中值帧的实测——真烧杯水体宽 ≈92-95px，而板缝/边条/分格线
#: ≤17px，两簇之间有大空档才取 40。换采集条件必须重看叠加图并重记。
#: 每个被滤掉的连通域都逐条写进 problems：过滤不许静默（口径同
#: 「不静默取前 N 个」），误滤了真人能看见、能改。
MIN_CUP_WIDTH_PX = 40

#: 分析 ROI 在水线上方留白的高度（px）。**起点值，且不是实测出来的**：
#: 真实素材上杯口沿/架子的位置没有量过，30px 只是"够容下探头与前肢、
#: 又不至于吞进上层架子阴影"的保守起点。必须逐素材在叠加图上人工核对
#: （R2-115 G1：ROI 要能容下线上活动；留白多大是研究参数，记录里要写）。
ROI_ABOVE_WATER_MARGIN_PX = 30

#: 人工确认件 wrapper 的 schema 标记（G4）。
CONFIRMED_SCHEMA = "fst-confirmed-geometry-v1"


@dataclass(frozen=True)
class CupProposal:
    """一个杯子的几何。**提案 confirmed 恒 False**；人工确认件走
    `proposals_from_confirmed`（confirmed=True, basis=human_confirmed_envelope）。

    字段语义见模块 docstring：`roi`（分析区域，含线上留白）/
    `water_body`（水体候选区，可为 None）/ `water_surface_y`（水线，可为 None）
    三件事分开，不许再拿水体 bbox 当整只动物的观察区。
    """

    index: int                                   # 0 起，按画面从左到右；cup_id = index+1
    roi: tuple[int, int, int, int]               # (r0, c0, r1, c1) 分析区域，闭区间
    water_body: tuple[int, int, int, int] | None  # 水体候选区（派生来源）；确认件为 None
    water_surface_y: float | None
    water_surface_basis: str
    water_surface_candidates: tuple[float, ...]  # 次强峰也留着，人工确认时看
    water_surface_reliable: bool                 # 水线可用（旁证命中或人工确认）才可算水线特征
    water_surface_unreliable_reason: str | None = None
    basis: str = BASIS_TEMPORAL_VARIANCE
    confirmed: bool = False
    notes: tuple[str, ...] = ()

    @property
    def cup_id(self) -> int:
        """物理杯号（1 起）。申报空杯、叠加图 C 标号、记录里的 cup 都用它。"""
        return self.index + 1

    @property
    def width_px(self) -> int:
        return self.roi[3] - self.roi[1] + 1

    @property
    def height_px(self) -> int:
        return self.roi[2] - self.roi[0] + 1


def varying_dark_mask(frames: list[np.ndarray], *, dark: float = 96.0,
                      bright: float = 150.0) -> np.ndarray:
    """「有时暗、有时亮」的像素 = 动物到过的地方。

    静态暗的东西（杯壁、挂钩、边框）在 max 图里仍暗 ⇒ 被 `mx > bright` 排除；
    静态亮的东西（背光、空水区）在 min 图里仍亮 ⇒ 被 `mn < dark` 排除。
    阈值是**起点值**：换一批采集条件要重看直方图并重记，不是普适常数。
    """
    if not frames:
        raise ValueError("varying_dark_mask 需要至少一帧")
    stack = np.stack([np.asarray(f, dtype=np.float64) for f in frames])
    mn = stack.min(axis=0)
    mx = stack.max(axis=0)
    return (mn < dark) & (mx > bright)


def propose_cups(frames: list[np.ndarray], *, n_cups: int | None = None,
                 min_area: int = 200,
                 dark: float = 96.0, water_hi: float = 186.0,
                 bright: float = 150.0,
                 min_width: int = MIN_CUP_WIDTH_PX,
                 roi_above_margin: int = ROI_ABOVE_WATER_MARGIN_PX,
                 ) -> tuple[list[CupProposal], list[str]]:
    """从一批抽样帧提提案。返回 (按左到右排序的提案, 问题列表)。

    水体候选区 = 中值帧上的**水体**连通域：背光是亮的（> water_hi），
    杯壁/挂钩/动物是暗的（< dark），只有水体落在中间灰阶带里，且是一个矩形块。
    水线 = 水体块的顶行（弯月面就是水体的上边界），再用行梯度峰做旁证候选。
    **分析 ROI = 水体区向上加 `roi_above_margin` 留白**（G1）：动物头/前肢
    在水线上的活动必须能被看见，`above_water_frac` 才可能真的 >0。

    真实素材（DP-136 正常1-4）上中间灰阶带里不止有水：玻璃壁、板缝、
    挂绳阴影、面板分格线也落进来。所以加两条**可解释的结构过滤**，每条
    过滤动作都逐条写进 problems（过滤不许静默）：
    - 宽度 < `min_width`（起点值，见 MIN_CUP_WIDTH_PX）：细缝/窄条不是杯；
    - 贴画面左右边界、或纵贯整帧：面板边条/分格线的形状特征，不是杯。
    过滤后数目仍与 `n_cups` 不符 ⇒ 照旧报「不静默取前 N 个」。

    动物的时间方差掩膜在这里只做**交叉核对**：ROI 里一个"有时暗有时亮"的
    像素都没有 ⇒ 记一条 note（标定样本里这杯没见着活动）。
    **note 不是空杯判定**——空杯只认人工申报（cup_perception）。

    `n_cups` 给定时数目不符即报问题（不静默取前 N 个）；不给时取全部连通域。
    """
    median = np.median(np.stack([np.asarray(f, dtype=np.float64)
                                 for f in frames]), axis=0)
    water = (median > dark) & (median < water_hi)
    comps = seg.label_components(water, min_area=min_area)
    problems: list[str] = []
    fh, fw = median.shape
    kept = []
    for comp in sorted(comps, key=lambda c: c.bbox[1]):
        r0, c0, r1, c1 = comp.bbox
        why: list[str] = []
        if c1 - c0 + 1 < min_width:
            why.append(f"宽 {c1 - c0 + 1}px < 起点值 {min_width}px")
        if c0 == 0 or c1 == fw - 1:
            why.append("贴画面左右边界（疑似面板边条）")
        if r0 == 0 and r1 == fh - 1:
            why.append("纵贯整帧（疑似分格线）")
        if why:
            problems.append(
                f"结构过滤：连通域 x{c0}..{c1} y{r0}..{r1}"
                f"（{'；'.join(why)}）不作杯候选——"
                "若确认误滤，请人工看叠加图并调起点值。")
        else:
            kept.append(comp)
    if n_cups is not None and len(kept) != n_cups:
        problems.append(
            f"期望 {n_cups} 个杯内区，中值帧水体结构过滤后剩 {len(kept)} 个"
            f"（过滤前 {len(comps)} 个，面积≥{min_area}）。"
            "不静默取前 N 个：数目不符先人工看叠加图。")
    activity = varying_dark_mask(frames, dark=dark, bright=bright)
    props: list[CupProposal] = []
    for i, comp in enumerate(kept):
        r0, c0, r1, c1 = comp.bbox
        roi = (max(0, r0 - roi_above_margin), c0, r1, c1)
        rr0, rc0, rr1, rc1 = roi
        seen = int(activity[rr0:rr1 + 1, rc0:rc1 + 1].sum())
        notes = () if seen else (
            "标定样本内该杯无可见活动（不等于空杯：空杯只认人工申报）",)
        prop = CupProposal(
            index=i, roi=roi, water_body=(r0, c0, r1, c1),
            water_surface_y=float(r0),
            water_surface_basis=WATERLINE_BASIS_WATER_BODY_TOP,
            water_surface_candidates=(),
            water_surface_reliable=False,
            water_surface_unreliable_reason="水线尚未经行梯度旁证（propose_waterline 未跑）",
            notes=notes)
        props.append(propose_waterline(median, prop))
    return props, problems


def propose_waterline(median_frame: np.ndarray, prop: CupProposal, *,
                      min_peak: float = 4.0) -> CupProposal:
    """给已提案的水线加一条**旁证**并定其可靠性：中值帧行均值一阶差分局部峰。

    用时间中值帧：动物在单帧里是动的，中值帧里动物糊掉了，而水面线（弯月面）
    是静态的水平边，所以在行均值一阶差分上最显眼。

    旁证只旁证、**不改值**：主值是水体外边界（`propose_cups` 定的）。
    可靠性判据（G1：不可靠水线 ⇒ 下游特征 null + 原因，不是假 0）：

    - 有任一候选峰落在主值 ±`WATERLINE_CROSSCHECK_TOL_PX` 内 ⇒ reliable=True；
    - **没有任何**候选峰落在容差内 ⇒ reliable=False + 分歧 note（附最近的峰），
      谁对由人在叠加图上定——脚本不自动选边；
    - 一条峰都没有（对比太弱）⇒ reliable=False + "无旁证" note。

    旁证峰全列在 `water_surface_candidates` 里（按强度降序，最多 6 个）。
    """
    body = prop.water_body if prop.water_body is not None else prop.roi
    r0, c0, r1, c1 = body
    # 行带从水体外边界**上方**几行起：水线这条边本身必须落在行带里，
    # 否则差分看不到它（早先版本从 r0+2 起，把要证的边切在了带外）。
    r0i, r1i = max(0, r0 - 4), max(r0 + 3, r1 - 2)
    band = np.asarray(median_frame, dtype=np.float64)[r0i:r1i, c0:c1 + 1]
    notes = list(prop.notes)
    cands: tuple[float, ...] = ()
    reliable = False
    reason: str | None = prop.water_surface_unreliable_reason
    if prop.water_surface_y is None:
        reason = "水线为 None（提不出，不填大概值）：水线特征不可算"
    elif band.shape[0] >= 4 and band.shape[1] >= 4:
        grad = np.abs(np.diff(band.mean(axis=1)))
        if grad.size >= 3:
            order = np.argsort(grad)[::-1]
            # 只留局部峰：比左右邻行都强的才算候选，避免一条宽边被拆成相邻两行
            peaks = [int(i) for i in order if 0 < i < grad.size - 1
                     and grad[i] >= grad[i - 1] and grad[i] >= grad[i + 1]]
            # 留前 6 强：真实素材（DP-136 正常1-4）里弯月面峰偏弱（|Δ|≈20），
            # 杯底阴影/架边更强（|Δ|≈58）——截太狠会把对的弱峰挤掉，
            # 让旁证反过来冤枉正确的水线。
            peaks = [p for p in peaks if grad[p] >= min_peak][:6]
            # diff 的第 p 项是行带第 p 行与第 p+1 行之差；变暗的那一侧
            # （水体）从第 p+1 行起，所以峰的绝对 y = r0i + p + 1。
            cands = tuple(float(r0i + p + 1) for p in peaks)
            if not cands:
                notes.append("行梯度旁证：杯内区找不到够强的水平边峰"
                             "（水线主值仍取水体外边界，未经旁证）")
                reason = "行梯度旁证无峰：水线未经佐证，水线特征不可算"
            elif not any(abs(y - prop.water_surface_y)
                         <= WATERLINE_CROSSCHECK_TOL_PX for y in cands):
                # 分歧判据：**没有任何**候选峰落在水体外边界 ±容差内。
                # （旧版只比最强峰：真实素材里最强峰常是杯底阴影，
                # 会把旁证命中的正确水线也报成分歧。）
                closest = min(cands, key=lambda y: abs(y - prop.water_surface_y))
                notes.append(
                    f"行梯度旁证：{len(cands)} 个候选峰没有一个落在水体外边界 "
                    f"y={prop.water_surface_y:.0f} ±{WATERLINE_CROSSCHECK_TOL_PX:.0f} px 内"
                    f"（最近 y={closest:.0f}）：两个信号谁对由人工在叠加图上定，脚本不选边")
                reason = (f"行梯度旁证分歧（最近候选 y={closest:.0f} vs 主值 "
                          f"y={prop.water_surface_y:.0f}）：水线待人工确认，水线特征不可算")
            else:
                reliable = True
                reason = None
        else:
            reason = "行梯度旁证不可算（行带太小）：水线未经佐证"
    else:
        notes.append("杯内区太小，行梯度旁证不可算；水线未经佐证，不填大概值")
        reason = "杯内区太小，行梯度旁证不可算：水线未经佐证"
    return CupProposal(index=prop.index, roi=prop.roi, water_body=prop.water_body,
                       water_surface_y=prop.water_surface_y,
                       water_surface_basis=prop.water_surface_basis,
                       water_surface_candidates=cands,
                       water_surface_reliable=reliable,
                       water_surface_unreliable_reason=reason,
                       basis=prop.basis, confirmed=False, notes=tuple(notes))


def to_envelope(props: list[CupProposal], video_size: tuple[int, int]) -> geo.GeometryEnvelope:
    """提案 → GeometryEnvelope。**全部 confirmed=False**，validate() 必报未确认。

    FST 的必需角色是 tank + water_surface（geometry.REQUIRED_ROLES["FST"]）。
    tank 矩形 = **分析 ROI**（含线上留白），不是水体 bbox：共享 validate()
    对水线用严格开区间（R2-115 G2 恢复旧语义），水线压 ROI 顶边的提案会被
    照实报"不在垂直范围"——留白正确的提案天然满足严格不等式，研究层不靠
    放宽共享契约过关。水线提不出来的杯子只放 tank：validate() 会同时报
    "缺 water_surface"和"未确认"，两条都如实，不为了让 validate 好看而补假水线。
    """
    env = geo.GeometryEnvelope(assay="FST", video_size=video_size, confirmed=False)
    for p in props:
        r0, c0, r1, c1 = p.roi
        geo.make_rect(env, geo.ROLE_TANK, float(c0), float(r0),
                      float(c1), float(r1), instance=p.cup_id, confirmed=False)
        if p.water_surface_y is not None:
            geo.make_line(env, geo.ROLE_WATER_SURFACE,
                          float(c0), p.water_surface_y, float(c1), p.water_surface_y,
                          instance=p.cup_id, confirmed=False)
    return env


# ---------------------------------------------------------------------------
# G3：申报空杯 ↔ 物理杯号绑定
# ---------------------------------------------------------------------------

BIND_APPLIED = "applied"
BIND_REFUSED_AMBIGUOUS = "refused_ambiguous"
BIND_NONE = "none"
#: 通道申报给了、但通道↔物理杯映射没有可验证依据：映射未决，本次不应用。
#: applied（任何记录里）只说明程序执行了申报，**不**表示映射已经验证。
BIND_MAPPING_UNRESOLVED = "mapping_unresolved"


@dataclass(frozen=True)
class DeclaredEmptyBinding:
    """申报空杯的绑定结果。applied 里是**物理杯号**（1 起）。"""

    applied: tuple[int, ...]
    status: str
    problems: tuple[str, ...]


def bind_declared_empty(cup_ids: list[int], declared_cup_ids: list[int], *,
                        expected_n: int | None = None,
                        unresolved_note: str | None = None) -> DeclaredEmptyBinding:
    """把人工申报的空杯号绑到物理杯号上；有歧义就**拒绝应用**（G3）。

    - `cup_ids`：本次运行实际存在的物理杯号（提案 = 左到右 1..n；
      人工确认件 = 确认文件里核对过的 instance 号）。
    - 申报了不存在的杯号 ⇒ raise ValueError（用错杯号是硬错误，整跑拒绝）。
    - `expected_n` 给定且与实际杯数不符（漏检/多检）⇒ 物理编号可能整体错位，
      **拒绝应用全部申报**、照实记 problems、相关杯保持未决——不许顺着
      过滤后的列表下标漂移到别的杯上。
    - 人工确认件（expected_n=None）杯号已由 binding 核对过，直接应用。
    - `unresolved_note`（R3-115 ①）：上游申报是**通道**形态、通道↔物理杯映射
      没有可验证依据时，`declared_cup_ids` 传空 + 本注记非空 ⇒ status
      `mapping_unresolved`、不应用到任何杯、注记进 problems。映射找到依据再应用；
      没有依据就保持未决。applied 只说明程序执行了申报，不表示映射已验证。
    """
    declared = sorted(set(declared_cup_ids))
    if not declared:
        if unresolved_note:
            return DeclaredEmptyBinding((), BIND_MAPPING_UNRESOLVED, (unresolved_note,))
        return DeclaredEmptyBinding((), BIND_NONE, ())
    ids = sorted(cup_ids)
    if len(set(ids)) != len(ids):
        raise ValueError(f"物理杯号有重复：{cup_ids}——绑定有歧义，拒绝")
    unknown = [c for c in declared if c not in set(ids)]
    if unknown:
        raise ValueError(
            f"申报空杯 {unknown} 不在实际杯号 {ids} 里（杯号从 1 起、按画面左到右）")
    problems: list[str] = []
    if expected_n is not None and len(ids) != expected_n:
        problems.append(
            f"杯候选数 {len(ids)} ≠ 期望 {expected_n}（可能漏检/误检），"
            "物理杯号绑定有歧义：本次**不应用**申报空杯 "
            f"{declared}，相关杯保持未决（不作空杯处理，也不出正常杯结论）；"
            "先人工看叠加图或用 --geometry 确认件绑定杯号。")
        return DeclaredEmptyBinding((), BIND_REFUSED_AMBIGUOUS, tuple(problems))
    if ids != list(range(1, len(ids) + 1)):
        problems.append(
            f"物理杯号不是完整的 1..{len(ids)}（实际 {ids}）：编号有缺口，"
            f"拒绝应用申报空杯 {declared}，保持未决。")
        return DeclaredEmptyBinding((), BIND_REFUSED_AMBIGUOUS, tuple(problems))
    return DeclaredEmptyBinding(tuple(declared), BIND_APPLIED, ())


# ---------------------------------------------------------------------------
# G4：人工确认件（带 binding 的 wrapper JSON）
# ---------------------------------------------------------------------------

def confirmation_payload(env: geo.GeometryEnvelope, *, video_sha256: str,
                         video_bytes: int, width: int, height: int,
                         cup_ids: list[int],
                         proposal_context: dict | None = None) -> dict:
    """生成确认件/提案文件的 wrapper 结构（Agent 生成，人只改几何与确认字段）。

    提案态：`envelope.confirmed=False`、`confirmed_by/at` 留空——
    `load_confirmed_file` 会拒绝这种文件当确认件用（半确认比没有更危险）。
    """
    return {
        "schema": CONFIRMED_SCHEMA,
        "status": ("proposal_unconfirmed" if not env.confirmed
                   else "human_confirmed"),
        "binding": {
            "video_sha256": video_sha256,
            "video_bytes": video_bytes,
            "width": width,
            "height": height,
            "cup_ids": sorted(cup_ids),
            "confirmed_by": "",
            "confirmed_at": "",
            "confirmed_basis": "",
        },
        "envelope": json.loads(env.to_json()),
        "proposal_context": proposal_context,
        "instructions": (
            "这是脚本生成的**提案态** wrapper（status=proposal_unconfirmed），"
            "**不要手工编辑本 JSON**。人工确认走「确认表 + 构建脚本」，同事全程不碰 JSON："
            "①在材料脚本产出的 `确认表_<视频>.md` 上逐杯给 ROI/水线 verdict"
            "（采用候选 / 修正并填坐标 / 无法确认——**两条候选都允许错、允许无法确认**），"
            "并填确认人/确认时间(ISO 8601)/看了哪些材料；②由构建脚本"
            "（build_confirmed_geometry.py）据表应用坐标、翻 envelope.confirmed 与每个 "
            "primitive 的 confirmed、填 binding，并校验水线**严格**在 tank（分析 ROI，"
            "顶边在水线上方）顶底边之间、杯号 1..n 左到右升序，产出确认件；任一杯"
            "无法确认/字段未填即**拒绝产件、保持提案态**（半确认比没有更危险）；"
            "③用 --geometry 指回构建脚本产出的确认件回灌重跑。"
            "video_sha256/width/height/cup_ids 由脚本绑定，**不要改**：改了加载即拒。"),
    }


def load_confirmed_file(path, *, video_sha256: str, width: int, height: int,
                        video_bytes: int | None = None):
    """读人工确认件（wrapper JSON）。返回 (env, binding, file_sha256, props)。

    拒绝清单（全部 ValueError，话要说清楚）：schema 不对；binding 的视频
    sha256/尺寸/字节数与实际素材不符；cup_ids 与 envelope 实例不符或不是
    1..n；确认人/确认时间为空；envelope 未整体确认或 validate() 有问题
    （含严格水线区间、同号重复、孤儿水线——共享契约原样生效）；tank
    左到右顺序与 instance 升序不一致（物理杯号不许与画面顺序拧着）。
    """
    from pathlib import Path as _P
    p = _P(path)
    raw = p.read_bytes()
    file_sha = hashlib.sha256(raw).hexdigest()
    doc = json.loads(raw.decode("utf-8"))
    if not isinstance(doc, dict) or doc.get("schema") != CONFIRMED_SCHEMA:
        raise ValueError(
            f"{p.name}: 不是 {CONFIRMED_SCHEMA} 确认件（schema 缺失或不对）。"
            "裸 envelope JSON 不是 CLI 的合法输入：确认件必须带 binding"
            "（视频 sha256/尺寸/杯号/确认人/确认时间）。")
    binding = doc.get("binding") or {}
    if (binding.get("video_sha256") or "").lower() != video_sha256.lower():
        raise ValueError(
            f"{p.name}: binding.video_sha256 与本视频不符——确认件绑定的不是这段素材，拒绝")
    if video_bytes is not None and binding.get("video_bytes") not in (None, video_bytes):
        raise ValueError(f"{p.name}: binding.video_bytes 与本视频不符，拒绝")
    if binding.get("width") != width or binding.get("height") != height:
        raise ValueError(
            f"{p.name}: binding 尺寸 {binding.get('width')}x{binding.get('height')} "
            f"≠ 实际 {width}x{height}，拒绝")
    if not str(binding.get("confirmed_by") or "").strip():
        raise ValueError(f"{p.name}: binding.confirmed_by 为空——没有确认人的文件不是确认件")
    if not str(binding.get("confirmed_at") or "").strip():
        raise ValueError(f"{p.name}: binding.confirmed_at 为空——没有确认时间的文件不是确认件")
    cup_ids = binding.get("cup_ids") or []
    env = geo.GeometryEnvelope.from_json(json.dumps(doc.get("envelope") or {}))
    if env.assay != "FST":
        raise ValueError(f"{p.name}: 确认件的范式是 {env.assay}，不是 FST")
    if not env.confirmed:
        raise ValueError(f"{p.name}: envelope.confirmed=False——这是提案/模板，不是确认件")
    problems = env.validate()
    if problems:
        raise ValueError(f"{p.name}: 确认件自身校验不过: {problems}")
    tanks = sorted(env.by_role(geo.ROLE_TANK),
                   key=lambda t: min(x for x, _ in t.coords))
    if not tanks or not env.by_role(geo.ROLE_WATER_SURFACE):
        raise ValueError(f"{p.name}: 确认件缺 tank 或 water_surface（FST 必需角色）")
    inst = [t.instance for t in tanks]
    if sorted(inst) != list(range(1, len(inst) + 1)):
        raise ValueError(
            f"{p.name}: tank 实例号 {sorted(inst)} 不是 1..{len(inst)}——物理杯号有歧义，拒绝")
    if inst != sorted(inst):
        raise ValueError(
            f"{p.name}: tank 实例号（{inst}）与画面左到右顺序不一致——"
            "物理杯号必须从左到右 1..n，申报空杯才绑不错位；拒绝")
    if sorted(cup_ids) != sorted(inst):
        raise ValueError(
            f"{p.name}: binding.cup_ids {sorted(cup_ids)} ≠ envelope 实例号 {sorted(inst)}，拒绝")
    return env, binding, file_sha, proposals_from_confirmed(env)


def proposals_from_confirmed(env: geo.GeometryEnvelope) -> list[CupProposal]:
    """确认件 envelope → CupProposal 列表（confirmed=True，水线 reliable=True）。

    调用前必须过 `load_confirmed_file` 的核对（实例号 1..n、左到右升序、
    validate 干净）。ROI = 人画的 tank；water_body 是提案期的派生量，
    确认件没有 ⇒ None；水线 = 人画的 water_surface（人工确认即可靠）。
    """
    tanks = sorted(env.by_role(geo.ROLE_TANK),
                   key=lambda t: min(x for x, _ in t.coords))
    lines = env.by_role(geo.ROLE_WATER_SURFACE)
    props: list[CupProposal] = []
    for tank in tanks:
        x0, y0, x1, y1 = geo.rect_bounds(tank)
        matched = [ln for ln in lines if ln.instance == tank.instance]
        wy = None
        if matched:
            ys = [y for ln in matched for _, y in ln.coords]
            wy = float(sum(ys) / len(ys))
        props.append(CupProposal(
            index=tank.instance - 1,
            roi=(int(round(y0)), int(round(x0)), int(round(y1)), int(round(x1))),
            water_body=None,
            water_surface_y=wy,
            water_surface_basis=WATERLINE_BASIS_HUMAN,
            water_surface_candidates=(),
            water_surface_reliable=wy is not None,
            water_surface_unreliable_reason=None if wy is not None else "确认件没画水线",
            basis=BASIS_HUMAN_CONFIRMED,
            confirmed=True,
            notes=()))
    return props


def load_confirmed(path) -> geo.GeometryEnvelope:
    """读一份**裸** envelope JSON 并检查确认语义（不是 CLI 输入通道）。

    CLI 的人工确认输入走 `load_confirmed_file`（带 binding，G4）。本函数只
    用于测试与离线检查：confirmed=True 且 validate() 干净才算确认件；
    否则 raise，不许"读进来顺手当真的用"。
    """
    from pathlib import Path as _P
    env = geo.GeometryEnvelope.from_json(_P(path).read_text(encoding="utf-8"))
    if env.assay != "FST":
        raise ValueError(f"确认几何的范式是 {env.assay}，不是 FST")
    if not env.confirmed:
        raise ValueError("envelope.confirmed=False：这不是人工确认件，是提案")
    # 不筛掉「未确认」问题：确认件里混着 confirmed=False 的 primitive
    # （手改 JSON 常见错）同样拒绝——半确认的几何比没有更危险。
    problems = env.validate()
    if problems:
        raise ValueError(f"确认件自身校验不过: {problems}")
    if not env.by_role(geo.ROLE_TANK) or not env.by_role(geo.ROLE_WATER_SURFACE):
        raise ValueError("确认件缺 tank 或 water_surface（FST 必需角色）")
    return env
