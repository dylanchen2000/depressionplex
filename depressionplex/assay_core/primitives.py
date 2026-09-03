"""原子原语标注体系（规划文档 §3.5）：标原语，不标复合类别。

一次多标签原语标注，可由**规则表**导出任意多套类别体系：学术口径、
CSI 兼容口径、我方 L1+L2 精细口径。增加一套量表 = 加一个规则表对象，
原语一个都不用重标——这是对标注人力瓶颈的结构性回答。

原语是人工标注的真值原子（布尔或多类），不是机器特征；机器特征到原语的
对应（如孔洞⇒前爪抓尾的候选证据）由复核流程使用，不在本模块硬连。

规则表均为**起点版本**：P0 原语表定稿与双人一致性检验（κ≥0.8）后更新。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# ---- 原语表版本 ----------------------------------------------------------------
# 原语表必须冻结并打 git tag，标注产出必须记录所用版本（项目不变量）。
# 理由：规则表（原语→类别映射）改了不用重标，**但原语表自己改了就必须重标**。
# 若不记版本，事后无法判断某批标注是按哪套原语定义做的，真值即不可审计。
# 改动本表（增删原语、改 kind/cats、改语义）必须同时 bump 此常量并打新 tag。
PRIMITIVE_TABLE_VERSION = "primitives-v1"

# ---- 原语定义 ------------------------------------------------------------------
# kind: "bool" | "cat:up/level/down" 等。assay: 适用范式。

PRIMITIVES: dict[str, dict] = {
    "fore_active":   {"label": "前肢有动作", "assay": "FST,TST", "kind": "bool"},
    "hind_active":   {"label": "后肢有动作", "assay": "FST,TST", "kind": "bool"},
    "trunk_deform":  {"label": "躯干形变", "assay": "FST,TST", "kind": "bool"},
    "rigid_swing":   {"label": "整体刚体摆动", "assay": "FST,TST", "kind": "bool"},
    "axis_orient":   {"label": "身体轴朝向", "assay": "FST,TST",
                      "kind": "cat", "cats": ("up", "level", "down")},
    "touch_wall":    {"label": "触壁/触悬挂杆", "assay": "FST,TST", "kind": "bool"},
    "tail_grasp":    {"label": "前爪抓尾", "assay": "TST", "kind": "bool"},
    "head_above":    {"label": "头在水面上", "assay": "FST", "kind": "bool"},
}

ORIENT_DOWN = "down"    # TST 正常悬挂：头向下
ORIENT_UP = "up"        # 攀爬/上探


def _b(d: dict, k: str) -> bool:
    return bool(d.get(k, False))


def _moving(d: dict) -> bool:
    return _b(d, "fore_active") or _b(d, "hind_active") or _b(d, "trunk_deform")


# ---- 规则表：原语 → 类别 ---------------------------------------------------------
# 每套口径 = {类别名: predicate(原语dict) -> bool}。类别不互斥时允许共现
# （如我方 L2）；互斥口径由导出方按优先级取首个命中（见 export_rubric）。

ACADEMIC_TST: dict[str, object] = {
    # Can et al. 2012：mobility = 触壁/触杆企图、身体剧烈抖动、四肢类跑动作；
    # 仅前肢小动作不计；钟摆不计。发表惯例 mobility+immobility 互补：
    # 仅前肢帧落入 Immobility；尾巴攀爬是试次级排除（逐帧两类别都不命中）。
    "Mobility": lambda d: (
        _b(d, "hind_active") or _b(d, "trunk_deform") or _b(d, "touch_wall")
    ) and not _b(d, "tail_grasp"),
    "Immobility": lambda d: not (
        (_b(d, "hind_active") or _b(d, "trunk_deform") or _b(d, "touch_wall"))
        and not _b(d, "tail_grasp")
    ) and not _b(d, "tail_grasp"),
}

CSI_TST: dict[str, object] = {
    # CSI 标量 blob 运动量原理上分不开钟摆与仅前肢——兼容口径如实复刻其行为。
    "Mobility": lambda d: _moving(d) or _b(d, "rigid_swing"),
    "Immobility": lambda d: not _moving(d) and not _b(d, "rigid_swing"),
}

OURS_TST: dict[str, object] = {
    "Mobility": lambda d: (
        (_b(d, "hind_active") or _b(d, "trunk_deform"))
        and not _b(d, "rigid_swing") and not _b(d, "tail_grasp")
    ),
    "Immobility": lambda d: (
        not _moving(d) and not _b(d, "rigid_swing") and not _b(d, "tail_grasp")
    ),
    "PassiveSwing": lambda d: (
        _b(d, "rigid_swing") and not _moving(d) and not _b(d, "tail_grasp")
    ),
    "ForelimbOnly": lambda d: (
        _b(d, "fore_active") and not _b(d, "hind_active")
        and not _b(d, "trunk_deform") and not _b(d, "tail_grasp")
    ),
    "TailClimbing": lambda d: _b(d, "tail_grasp"),
}

ACADEMIC_FST: dict[str, object] = {
    "Immobility": lambda d: not _moving(d),
    "Swimming": lambda d: _b(d, "hind_active") and not _b(d, "touch_wall"),
    "Climbing": lambda d: _b(d, "fore_active") and _b(d, "touch_wall"),
}

CSI_FST: dict[str, object] = {
    "Float": lambda d: not _moving(d),
    "Swim": lambda d: _b(d, "hind_active") and not _b(d, "touch_wall"),
    "Climb": lambda d: _b(d, "fore_active") and _b(d, "touch_wall"),
    "Struggle": lambda d: (
        _b(d, "trunk_deform") and _b(d, "fore_active") and _b(d, "hind_active")
    ),
}

OURS_FST: dict[str, object] = {
    **{k: v for k, v in ACADEMIC_FST.items()},
    "Diving": lambda d: not _b(d, "head_above"),
    "WallSupportedFloat": lambda d: (
        not _moving(d) and _b(d, "touch_wall") and _b(d, "head_above")
    ),
}

RUBRICS: dict[str, dict[str, object]] = {
    "academic_tst": ACADEMIC_TST,
    "csi_tst": CSI_TST,
    "ours_tst": OURS_TST,
    "academic_fst": ACADEMIC_FST,
    "csi_fst": CSI_FST,
    "ours_fst": OURS_FST,
}

# 互斥口径的优先级（首个命中获胜）；非互斥口径（ours_*）按共现处理。
# 注意：尾巴攀爬是**试次级**排除（金标准：攀爬动物整试次剔除），不是逐帧
# 类别——逐帧表里 tail_grasp 使 Mobility/Immobility 都不命中，该帧即"排除区"。
MUTUALLY_EXCLUSIVE: dict[str, tuple[str, ...]] = {
    "academic_tst": ("Mobility", "Immobility"),
    "csi_tst": ("Mobility", "Immobility"),
    "academic_fst": ("Climbing", "Swimming", "Immobility"),
    "csi_fst": ("Struggle", "Climb", "Swim", "Float"),
}


def export_rubric(
    prim: dict, rubric: str, *, exclusive: bool | None = None
) -> list[str]:
    """原语 dict → 该口径下命中的类别列表。

    exclusive=None 时按口径默认：csi/academic 取优先级首个命中（互斥），
    ours_* 返回全部共现类别。
    """
    table = RUBRICS[rubric]
    hits = [name for name, pred in table.items() if pred(prim)]  # type: ignore[operator]
    if exclusive is None:
        exclusive = rubric in MUTUALLY_EXCLUSIVE
    if exclusive:
        order = MUTUALLY_EXCLUSIVE.get(rubric)
        if order:
            hits = [h for h in order if h in hits]
        return hits[:1]
    return hits


# ---- 标注文件 ↔ 逐帧 -------------------------------------------------------------


@dataclass
class Bout:
    start: int
    end: int
    primitives: dict = field(default_factory=dict)


def expand_to_frames(
    bouts: list[Bout], n_frames: int, prim_id: str, default=False
) -> np.ndarray:
    """把某原语的 bout 级标注展开成逐帧布尔序列。"""
    out = np.full(n_frames, bool(default))
    for b in bouts:
        if b.primitives.get(prim_id, False):
            out[max(0, b.start) : min(n_frames, b.end + 1)] = True
    return out


def Cohen_kappa(a: np.ndarray, b: np.ndarray) -> float:
    """逐帧 Cohen's κ（§6.3 评分者间一致性）。常真/常假退化时返回 1.0/0.0 的
    约定：po=1 且 pe=1 ⇒ 1.0。"""
    a = np.asarray(a, dtype=bool)
    b = np.asarray(b, dtype=bool)
    n = len(a)
    if n == 0:
        return 0.0
    po = float((a == b).mean())
    pa = float(a.mean())
    pb = float(b.mean())
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe >= 1.0 - 1e-12:
        return 1.0
    return float((po - pe) / (1 - pe))


# 稀有原语阈值（评审定，2026-08-25）：出现率低于此的 κ 受基率悖论支配。
RARE_PREVALENCE = 0.05


def agreement_report(a: np.ndarray, b: np.ndarray) -> dict:
    """逐原语一致性报告：κ **必须**与原始一致率、出现率同报。

    基率悖论（评审护栏）：出现率 <5% 的原语（如「前爪抓尾」），两人 99%
    一致 κ 仍可能近 0——不预防会得出"标注员不合格"的错误结论去返工没问题的
    标注。稀有原语附 PABAK（= 2×po−1）。期望按原语分别设，**不用一个全局
    κ 门槛卡所有原语**。
    """
    a = np.asarray(a, dtype=bool)
    b = np.asarray(b, dtype=bool)
    n = len(a)
    po = float((a == b).mean()) if n else 0.0
    prev = float((a | b).mean()) if n else 0.0
    rep = {
        "n_frames": n,
        "prevalence": prev,
        "raw_agreement": po,
        "kappa": Cohen_kappa(a, b),
        "rate_a": float(a.mean()) if n else 0.0,
        "rate_b": float(b.mean()) if n else 0.0,
        "rare": prev < RARE_PREVALENCE,
    }
    if rep["rare"]:
        rep["pabak"] = 2.0 * po - 1.0
    return rep
