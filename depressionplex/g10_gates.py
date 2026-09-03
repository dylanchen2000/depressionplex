"""G10 三支子门的报告层（DP-034）：**门槛写死，三支永不合并**。

为什么必须拆（DP-031/034 评审结论）：`validity.py` 旧版把"从未有动物"折进
`detached`，一个口径吃两种失效——用空场正样本冒充"脱落检测已验证"是虚假验收。
本模块把三支各自立门、各自带分母：

- **G10a never_occupied（从未有动物）**：门槛 = 召回 n/n **且** 假阳性 0/m，
  分母口径必须写明（本数据集两口径不同，见下）。
- **G10b detached（中途脱落）**：本批素材正样本为 **0**（唯一候选 30mg_2周-ch4
  经 DP-005 确认是 never_occupied）⇒ **召回不可测**，只能报假阳性。
  报告里必须明写"G10a 通过不能替代 G10b"——不许用 a 的绿灯声称脱落已验证。
- **G10c tail_grasp**：出现率 0 ⇒ 判据**挂起**；κ 遇零方差退化必须打
  "未定义"，**不得写 1.0**（观察一致=期望一致时 κ 的 0/0 不是完美一致）。

本批（27 试次交付集 + DP-032 全扫）的真值分布，两个分母口径：
  28 个隔间槽位：G10a 正 2（`20mg_3周-ch4`、`30mg_2周-ch4`）/ 真负 26
  27 个已切片试次：正 1 / 真负 26（`30mg_2周-ch4` 从未切片）
"正 1/真负 27"是 DP-032 跑之前的旧数，**已作废**，不许再出现。

纪律：报告生成器不写死本数据集的正负名单——正例/真负由调用方给的
`SlotRecord.truth` 决定；这里只锁**门的形状**（召回=全中 且 假阳=0）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .assay_core import validity as V

#: 隔间槽位的试次真值（人工/场记口径，不是软件输出）。
TRUTH_NEVER_OCCUPIED = "never_occupied"
TRUTH_OCCUPIED = "occupied"
TRUTH_DETACHED = "detached"
TRUTHS = (TRUTH_NEVER_OCCUPIED, TRUTH_OCCUPIED, TRUTH_DETACHED)


@dataclass(frozen=True)
class SlotRecord:
    """一个隔间槽位：真值 vs 软件判定。

    trial_id：切片过的用 "<视频>-ch<N>"；从未切片的槽位用 "<视频>#slot<N>"
    （`sliced=False`），两口径的分母因此都能算。
    """
    trial_id: str
    truth: str                 # TRUTHS 之一
    software_status: str       # validity.STATUS_* 字符串
    sliced: bool = True
    occupied_fraction: float | None = None

    def __post_init__(self) -> None:
        if self.truth not in TRUTHS:
            raise ValueError(f"truth={self.truth!r} 不在 {TRUTHS}")
        if (self.truth == TRUTH_NEVER_OCCUPIED
                and self.occupied_fraction is not None
                and self.occupied_fraction != 0.0):
            raise ValueError(
                f"{self.trial_id}: 真值'从未有动物'但在场占比={self.occupied_fraction}"
                "≠0——N2 要求恰为 0，不是'很低'；占比非 0 说明真值标错了，拒收")


@dataclass(frozen=True)
class TailGraspPair:
    """tail_grasp 逐试次双人/机-人标签（G10c 的 κ 原料）。"""
    trial_id: str
    human: bool
    software: bool


def cohen_kappa(pairs: Sequence[TailGraspPair]) -> float | None:
    """二分类 Cohen's κ。**退化情形返回 None（未定义），绝不返回 1.0**。

    κ = (po − pe) / (1 − pe)；双方恒等于同一常数时 pe=1，κ 是 0/0——
    "两个评分员都没见过 tail_grasp" 记成 κ=1.0 = 凭空制造验证（DP-034 明令禁止）。
    """
    if not pairs:
        return None
    n = len(pairs)
    po = sum(1 for p in pairs if p.human == p.software) / n
    h_pos = sum(1 for p in pairs if p.human) / n
    s_pos = sum(1 for p in pairs if p.software) / n
    pe = h_pos * s_pos + (1 - h_pos) * (1 - s_pos)
    if pe >= 1.0 - 1e-12:
        return None
    return (po - pe) / (1.0 - pe)


_TRUTH_TO_STATUS = {TRUTH_NEVER_OCCUPIED: V.STATUS_NEVER_OCCUPIED,
                    TRUTH_DETACHED: V.STATUS_DETACHED}


def _gate_counts(records: Sequence["SlotRecord"], want_status: str
                 ) -> tuple[int, int, int, int]:
    """(召回, 正样本数, 假阳性数, 真负数)——某一支的计数。

    假阳性**只数本支状态**：G10b 的假阳 = 软件把有动物的隔间判成 `detached`，
    判成 `never_occupied` 是 G10a 的假阳——两支各算各的，这正是"不许合并"的
    计数层落实。
    """
    pos = [r for r in records if _TRUTH_TO_STATUS.get(r.truth) == want_status]
    neg = [r for r in records if r.truth == TRUTH_OCCUPIED]
    hit = sum(1 for r in pos if r.software_status == want_status)
    fp = sum(1 for r in neg if r.software_status == want_status)
    return hit, len(pos), fp, len(neg)


def g10_report(slots: Sequence[SlotRecord],
               tail_grasp: Sequence[TailGraspPair] = ()) -> str:
    """打印 G10a/b/c 三行独立判定。每支自带分母，**永不合并成单一 G10 数**。"""
    if not slots:
        raise ValueError("G10 报告需要槽位记录——空输入不许出'全过'（静默兜底禁令）")
    bad_frac = [r.trial_id for r in slots
                if r.truth == TRUTH_NEVER_OCCUPIED and r.occupied_fraction is None]
    lines = ["═══ G10（三支子门，门槛写死，不合并）═══"]

    # ---- G10a: never_occupied ----
    for cohort, recs in (("全部 28 隔间槽位口径", slots),
                         ("已切片试次口径", [s for s in slots if s.sliced])):
        hit, npos, fp, nneg = _gate_counts(
            [r for r in recs if r.truth in (TRUTH_NEVER_OCCUPIED, TRUTH_OCCUPIED)],
            V.STATUS_NEVER_OCCUPIED)
        verdict = ("PASS" if (npos == 0 or hit == npos) and fp == 0 else "FAIL")
        lines.append(
            f"G10a 从未有动物［口径：{cohort}，分母 {len(recs)}］ 召回 {hit}/{npos}"
            f"  假阳性 {fp}/{nneg}  ⇒ {verdict}")
    # ---- G10b: detached ----
    hit, npos, fp, nneg = _gate_counts(
        [r for r in slots if r.truth in (TRUTH_DETACHED, TRUTH_OCCUPIED)],
        V.STATUS_DETACHED)
    if npos == 0:
        lines.append(
            "G10b 中途脱落  召回 **不可测**（本数据集无正样本——30mg_2周-ch4 经"
            f" DP-005 确认是 never_occupied）  假阳性 {fp}/{nneg}"
            f"  ⇒ {'PASS（仅假阳侧）' if fp == 0 else 'FAIL'}")
    else:
        lines.append(
            f"G10b 中途脱落  召回 {hit}/{npos}  假阳性 {fp}/{nneg}"
            f"  ⇒ {'PASS' if hit == npos and fp == 0 else 'FAIL'}")
    lines.append("G10a 的 PASS 只验证'从未有动物'这一支，**不得据此声称"
                 "'脱落检测已验证**（脱落召回在本批不可测）")
    # ---- G10c: tail_grasp ----
    n_pos = sum(1 for p in tail_grasp if p.human or p.software)
    k = cohen_kappa(tail_grasp)
    k_txt = "未定义（零出现率退化 0/0，**不得记 1.0**）" if k is None else f"{k:.3f}"
    lines.append(
        f"G10c tail_grasp  出现 {n_pos}/{len(tail_grasp)} 试次"
        f"  κ={k_txt}"
        + ("  ⇒ **判据挂起**（无正样本可判，未定义≠通过）" if k is None else ""))
    if bad_frac:
        lines.append(f"[报警] 以下 never_occupied 槽位缺在场占比（N2 无法核对）：{bad_frac}")
    return "\n".join(lines)
