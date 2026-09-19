"""杯体与水线的**提案**：只提案，不确认（Spec A §4 A1 第 2 条 / §6.2 第 4 行）。

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

人工确认走 `load_confirmed`：吃一份 `GeometryEnvelope` 的 JSON
（`docs/标定文件示例_研究版.json` 同形状），要求 `confirmed=True` 且
validate() 不再报未确认；否则拒绝，不许"读进来顺手当真的用"。
"""

from __future__ import annotations

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


@dataclass(frozen=True)
class CupProposal:
    """一个杯子的几何提案。**confirmed 恒 False**（人工确认走 load_confirmed）。"""

    index: int                                   # 0 起，按画面从左到右
    interior: tuple[int, int, int, int]          # (r0, c0, r1, c1) 动物可达区，闭区间
    water_surface_y: float | None
    water_surface_basis: str
    water_surface_candidates: tuple[float, ...]  # 次强峰也留着，人工确认时看
    basis: str = BASIS_TEMPORAL_VARIANCE
    confirmed: bool = False
    notes: tuple[str, ...] = ()

    @property
    def width_px(self) -> int:
        return self.interior[3] - self.interior[1] + 1

    @property
    def height_px(self) -> int:
        return self.interior[2] - self.interior[0] + 1


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
                 ) -> tuple[list[CupProposal], list[str]]:
    """从一批抽样帧提提案。返回 (按左到右排序的提案, 问题列表)。

    杯内区 = 中值帧上的**水体**连通域：背光是亮的（> water_hi），杯壁/挂钩/
    动物是暗的（< dark），只有水体落在中间灰阶带里，且是一个矩形块。
    水线 = 水体块的顶行（弯月面就是水体的上边界），再用行梯度峰做旁证候选。

    真实素材（DP-136 正常1-4）上中间灰阶带里不止有水：玻璃壁、板缝、
    挂绳阴影、面板分格线也落进来。所以加两条**可解释的结构过滤**，每条
    过滤动作都逐条写进 problems（过滤不许静默）：
    - 宽度 < `min_width`（起点值，见 MIN_CUP_WIDTH_PX）：细缝/窄条不是杯；
    - 贴画面左右边界、或纵贯整帧：面板边条/分格线的形状特征，不是杯。
    过滤后数目仍与 `n_cups` 不符 ⇒ 照旧报「不静默取前 N 个」。

    动物的时间方差掩膜在这里只做**交叉核对**：水体块里一个"有时暗有时亮"的
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
        seen = int(activity[r0:r1 + 1, c0:c1 + 1].sum())
        notes = () if seen else (
            "标定样本内该杯无可见活动（不等于空杯：空杯只认人工申报）",)
        prop = CupProposal(index=i, interior=(r0, c0, r1, c1),
                           water_surface_y=float(r0),
                           water_surface_basis=WATERLINE_BASIS_WATER_BODY_TOP,
                           water_surface_candidates=(),
                           notes=notes)
        props.append(propose_waterline(median, prop))
    return props, problems


def propose_waterline(median_frame: np.ndarray, prop: CupProposal, *,
                      min_peak: float = 4.0) -> CupProposal:
    """给已提案的水线加一条**旁证**：中值帧行均值一阶差分的局部峰。

    用时间中值帧：动物在单帧里是动的，中值帧里动物糊掉了，而水面线（弯月面）
    是静态的水平边，所以在行均值一阶差分上最显眼。

    旁证只旁证、**不改值**：主值是水体外边界（`propose_cups` 定的）。
    所有候选峰**没有一个**落在主值 ±`WATERLINE_CROSSCHECK_TOL_PX` 内 ⇒
    写一条 note（附最近的峰），让人在叠加图上定谁对——脚本不自动选边。
    旁证峰全列在 `water_surface_candidates` 里（按强度降序，最多 6 个）。
    一条峰都没有（对比太弱）也照实记。
    """
    r0, c0, r1, c1 = prop.interior
    # 行带从水体外边界**上方**几行起：水线这条边本身必须落在行带里，
    # 否则差分看不到它（早先版本从 r0+2 起，把要证的边切在了带外）。
    r0i, r1i = max(0, r0 - 4), max(r0 + 3, r1 - 2)
    band = np.asarray(median_frame, dtype=np.float64)[r0i:r1i, c0:c1 + 1]
    notes = list(prop.notes)
    cands: tuple[float, ...] = ()
    if band.shape[0] >= 4 and band.shape[1] >= 4:
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
            elif prop.water_surface_y is not None and not any(
                    abs(y - prop.water_surface_y) <= WATERLINE_CROSSCHECK_TOL_PX
                    for y in cands):
                # 分歧判据：**没有任何**候选峰落在水体外边界 ±容差内。
                # （旧版只比最强峰：真实素材里最强峰常是杯底阴影，
                # 会把旁证命中的正确水线也报成分歧。）
                closest = min(cands, key=lambda y: abs(y - prop.water_surface_y))
                notes.append(
                    f"行梯度旁证：{len(cands)} 个候选峰没有一个落在水体外边界 "
                    f"y={prop.water_surface_y:.0f} ±{WATERLINE_CROSSCHECK_TOL_PX:.0f} px 内"
                    f"（最近 y={closest:.0f}）：两个信号谁对由人工在叠加图上定，脚本不选边")
    elif prop.water_surface_y is None:
        notes.append("杯内区太小，行梯度旁证不可算；水线=None，不填大概值")
    return CupProposal(index=prop.index, interior=prop.interior,
                       water_surface_y=prop.water_surface_y,
                       water_surface_basis=prop.water_surface_basis,
                       water_surface_candidates=cands, basis=prop.basis,
                       confirmed=False, notes=tuple(notes))


def to_envelope(props: list[CupProposal], video_size: tuple[int, int]) -> geo.GeometryEnvelope:
    """提案 → GeometryEnvelope。**全部 confirmed=False**，validate() 必报未确认。

    FST 的必需角色是 tank + water_surface（geometry.REQUIRED_ROLES["FST"]）。
    水线提不出来的杯子只放 tank：validate() 会同时报"缺 water_surface"和
    "未确认"，两条都如实，不为了让 validate 好看而补一条假水线。
    """
    env = geo.GeometryEnvelope(assay="FST", video_size=video_size, confirmed=False)
    for p in props:
        r0, c0, r1, c1 = p.interior
        geo.make_rect(env, geo.ROLE_TANK, float(c0), float(r0),
                      float(c1), float(r1), instance=p.index + 1, confirmed=False)
        if p.water_surface_y is not None:
            geo.make_line(env, geo.ROLE_WATER_SURFACE,
                          float(c0), p.water_surface_y, float(c1), p.water_surface_y,
                          instance=p.index + 1, confirmed=False)
    return env


def load_confirmed(path) -> geo.GeometryEnvelope:
    """读人工确认的几何。确认状态不达标 ⇒ raise，不许顺手当真的用。"""
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
