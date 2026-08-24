"""试次级有效性：脱落 / 截断 / 有效（TST 产品特性，CSI 两份手册均无）。

TST 金标准（Can et al. 2012）要求排除脱落/攀爬等动物——自动检测脱落因此是
卖点而非副产品。本模块把"这隔间里到底有没有过动物"提升为**试次级判据**，
不埋在分割失败原因里。

核心判据（评审定稿，2026-08-24）：全片标定帧里该隔间是否**存在过身体级
面积**（≥ body_frac × 同批其他隔间面积中位数）。

- 从未存在 ⇒ `detached`：真实脱落/未悬挂，建议排除（金标准本就要求）。
- 存在过、但当前分析只给出尾级面积 ⇒ `truncated_suspect`：**这是 bug**
  （分割把活鼠截断成尾块），必须上报修复，绝不允许自动贴上"脱落"标签
  静默丢弃——"说得通的叙事盖住 bug"正是本项目反复撞到的失败模式。
- 其余 ⇒ `valid`。

判据只依赖面积量级，逐帧分割细节变了也稳定；参考量取**其他**隔间的中位数，
脱落隔间自身不参与定标。

**相对判据的已知边界**：全试次所有隔间都只有尾级面积时（整批脱落），相对
判据自洽地"认为"尾级就是身体级——不可决。此时用 `body_area_prior`
（硬件规格级绝对先验，如按规格分辨率与预期 BL 推出的最小身体面积）兜底：
全局参考低于先验 ⇒ 全部报 unknown。不给先验则该边界保持不可决（调用方
自行承担），probe_frames 以 --body-area-prior 显式传入。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

STATUS_VALID = "valid"
STATUS_DETACHED = "detached"
STATUS_TRUNCATED = "truncated_suspect"
STATUS_UNKNOWN = "unknown"  # 参考量不可估（全试次都无身体级面积等）

# 与分割 min_area 同量级的下限：小于它的"面积"视为噪声，不进中位数。
_AREA_FLOOR = 20.0


@dataclass(frozen=True)
class ChamberValidity:
    chamber: int
    status: str
    max_area: float          # 标定帧里观测到的最大动物级掩膜面积
    ref_body_area: float     # 其他隔间面积中位数（参考量）
    body_threshold: float    # body_frac × ref_body_area
    ever_had_body: bool
    note: str


@dataclass(frozen=True)
class TrialValidity:
    chambers: tuple[ChamberValidity, ...]

    @property
    def exclude(self) -> tuple[int, ...]:
        """金标准口径建议排除的隔间：真实脱落。"""
        return tuple(
            c.chamber for c in self.chambers if c.status == STATUS_DETACHED
        )

    @property
    def needs_repair(self) -> tuple[int, ...]:
        """疑似截断 bug 的隔间：不得入统计，先修分割。"""
        return tuple(
            c.chamber for c in self.chambers if c.status == STATUS_TRUNCATED
        )


def _areas(profile: list[float | None]) -> np.ndarray:
    vals = [float(a) for a in profile if a is not None and not np.isnan(a)]
    vals = [v for v in vals if v >= _AREA_FLOOR]
    return np.asarray(vals, dtype=float)


def assess_trial_validity(
    calib_areas: dict[int, list[float | None]],
    current_areas: dict[int, list[float | None]] | None = None,
    *,
    body_frac: float = 0.5,
    body_area_prior: float | None = None,
) -> TrialValidity:
    """逐隔间判定试次有效性。

    `calib_areas`：全片均匀标定帧上、走廊路径分割出的动物掩膜面积（无掩膜
    记 None）。`current_areas`：当前分析批次的面积；给了才能识别截断（标定
    有过身体、当前却只有尾级面积）。
    """
    meds: dict[int, float] = {}
    maxes: dict[int, float] = {}
    for k, v in calib_areas.items():
        a = _areas(v)
        meds[k] = float(np.median(a)) if a.size else 0.0
        maxes[k] = float(a.max()) if a.size else 0.0

    # 两遍法：尾级面积会污染参考量。先用全局中位数粗判"有身体"的隔间，
    # 参考量再只取其他**有身体**隔间的中位数。
    pos = [m for m in meds.values() if m > 0]
    body_ids = (
        [k for k, m in meds.items() if m >= body_frac * float(np.median(pos))]
        if pos
        else []
    )

    # 绝对先验兜底：整批脱落时相对判据不可决（见模块 docstring）。
    if body_area_prior is not None and (
        not pos or float(np.median(pos)) < body_area_prior
    ):
        return TrialValidity(
            tuple(
                ChamberValidity(
                    k, STATUS_UNKNOWN, maxes[k], 0.0, 0.0, False,
                    "全局参考低于绝对先验：本批无身体级面积，整批疑似脱落，"
                    "不得相对自洽地判 valid",
                )
                for k in sorted(calib_areas)
            )
        )

    out: list[ChamberValidity] = []
    for k in sorted(calib_areas):
        others = [meds[j] for j in body_ids if j != k and meds[j] > 0]
        if not others:
            if k in body_ids and meds[k] > 0:
                # 唯一有身体的隔间：以自身为参考（其余皆尾级/空）。
                ref = meds[k]
                out.append(ChamberValidity(
                    k, STATUS_VALID, maxes[k], ref, body_frac * ref, True,
                    "仅自身有身体级面积，参考取自身",
                ))
                continue
            out.append(ChamberValidity(
                k, STATUS_UNKNOWN, maxes[k], 0.0, 0.0, False,
                "无有身体的隔间可作参考，参考量不可估",
            ))
            continue
        ref = float(np.median(others))
        thr = body_frac * ref
        ever = maxes[k] >= thr
        if not ever:
            out.append(ChamberValidity(
                k, STATUS_DETACHED, maxes[k], ref, thr, False,
                "全片标定帧从未出现身体级面积：真实脱落/未悬挂，建议排除",
            ))
            continue
        if current_areas is not None:
            cur = _areas(current_areas.get(k, []))
            if cur.size and float(np.median(cur)) < thr:
                out.append(ChamberValidity(
                    k, STATUS_TRUNCATED, maxes[k], ref, thr, True,
                    "标定帧存在过身体，当前分析仅尾级面积：疑似截断 bug，"
                    "不得按脱落处理",
                ))
                continue
        out.append(ChamberValidity(
            k, STATUS_VALID, maxes[k], ref, thr, True, ""
        ))
    return TrialValidity(tuple(out))
