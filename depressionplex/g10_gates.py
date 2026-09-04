"""G10 三支子门的报告层（DP-034 建，**DP-043 重定位**）。

## DP-043（2026-09-04）：G10 从"验收门"降为"兜底报警 + 单向安全门"

道俊定的输入契约：**"后面我们会告诉你哪个是空的，甚至空的就不该放进软件里去分析。"**
⇒ **自动发现空隔间不再是产品要求。** 对应四条改动，一条都不许多砍：

1. **G10a 的"召回"从门下架**，降为**参考值**（`declared_empty` 由人给，机器不需要自己发现）。
2. **"假阳性 = 0"保留并升级为安全属性**：把**有动物**的隔间判成空 = **静默丢掉一只真动物**。
   DP-032 已真实发生一次，还连带让那只老鼠**至今从未被任何人评分**。
   ⇒ 这一侧**硬失败、不可豁免**，也是本模块唯一还能判 FAIL 的东西。
3. **`PHANTOM-IMMOBILITY` 报警保留**（在 `validity.score_gate`）：把空场判成
   `immobility=360 s` 会**伪造最强抑郁表型**——GLP 语境下最不能有的错误。
   即使空隔间已被人工声明排除，兜底也要在（人会忘记声明）。
4. **G10b 保持"召回不可测"如实声明**，不假装可测。

## 三支各自的形状（永不合并，各带分母）

- **G10a never_occupied**：召回 = **参考值不设门**；假阳性 = **0，安全属性**。
- **G10b detached（中途脱落）**：本批正样本 **0** ⇒ **召回不可测**，只报假阳性。
  报告必须明写"G10a 通过不能替代 G10b"——不许用 a 的绿灯声称脱落已验证。
- **G10c tail_grasp**：出现率 0 ⇒ 判据**挂起**；κ 零方差退化打"未定义"，
  **不得写 1.0**（观察一致=期望一致时 κ 的 0/0 不是完美一致）。

## 本批真值分布（2026-09-04 修正，此前记错过两次）

`30mg_2周-ch4` **有老鼠，从头到尾都有**（道俊人眼反复确认，以此为准，不再论证）。
只有 `20mg_3周-ch4` 从头到尾没有。⇒ 两个分母口径：

    28 个隔间槽位：G10a 正 **1**（`20mg_3周-ch4`）/ 真负 **27**
    27 个已切片试次：正 1 / 真负 26（`30mg_2周-ch4` **从未被切片**，是漏切）

**两条已作废的旧记载，不许再出现在任何文档或注释里**：
  ✗ "正 2（`20mg_3周-ch4`、`30mg_2周-ch4`）/ 真负 26"——第二个正例是错的（那里有老鼠）
  ✗ "G10b 唯一候选 30mg_2周-ch4 经 DP-005 确认是 never_occupied"——DP-005 已重开，原结论错
  ✗ "G10 假阳性 0 已在实测层坐实"——**实测是假阳性 1**，软件把有鼠的 `30mg_2周` 隔间 4 判成空

G10b 的正样本仍是 0，但**理由变了**：不是"唯一候选其实是空场"，而是**本批根本没有中途脱落的素材**。

纪律：报告生成器不写死本数据集的正负名单——正例/真负由调用方给的
`SlotRecord.truth` 决定；这里只锁**门的形状**（假阳=0 硬失败；召回只报不判）。
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
    #: DP-043 输入契约：**人在录入时声明这个隔间是空的**。声明为空的槽位
    #: 按契约**根本不进分析管道**，因此不计入 G10a 召回（召回已下架为参考值）。
    declared_empty: bool = False
    #: 管道最终递出来的 immobility（秒）。声明为空却还有数字 ⇒ 幻影，见下。
    pipeline_immobility_s: float | None = None

    def __post_init__(self) -> None:
        if self.truth not in TRUTHS:
            raise ValueError(f"truth={self.truth!r} 不在 {TRUTHS}")
        if (self.truth == TRUTH_NEVER_OCCUPIED
                and self.occupied_fraction is not None
                and self.occupied_fraction != 0.0):
            raise ValueError(
                f"{self.trial_id}: 真值'从未有动物'但在场占比={self.occupied_fraction}"
                "≠0——N2 要求恰为 0，不是'很低'；占比非 0 说明真值标错了，拒收")
        if self.declared_empty and self.truth == TRUTH_OCCUPIED:
            raise ValueError(
                f"{self.trial_id}: 人声明为空，但真值是'有动物'——**当场拒收，不许悄悄跑**。"
                "声明错了会让一只真动物被整条管道跳过（DP-032 已真实发生过一次），"
                "这种错必须在入口炸掉，不能靠下游发现")


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

    # ---- G10a: never_occupied（DP-043：召回下架为参考值，假阳侧升级为安全属性）----
    for cohort, recs in (("全部 28 隔间槽位口径", slots),
                         ("已切片试次口径", [s for s in slots if s.sliced])):
        hit, npos, fp, nneg = _gate_counts(
            [r for r in recs if r.truth in (TRUTH_NEVER_OCCUPIED, TRUTH_OCCUPIED)],
            V.STATUS_NEVER_OCCUPIED)
        # 判定**只看假阳性**：召回已由 DP-043 下架（空隔间由人声明，机器不必自己发现）
        lines.append(
            f"G10a 从未有动物［口径：{cohort}，分母 {len(recs)}］"
            f"  召回 {hit}/{npos}（**参考值，不设门**，DP-043）"
            f"  假阳性 {fp}/{nneg} ⇒ {'PASS' if fp == 0 else 'FAIL'}"
            f"（**安全属性，硬失败不可豁免**）")
        if fp:
            wrong = [r.trial_id for r in recs
                     if r.truth == TRUTH_OCCUPIED
                     and r.software_status == V.STATUS_NEVER_OCCUPIED]
            lines.append(
                f"  [安全属性失败] 软件把**有动物**的隔间判成空：{wrong}"
                "——这是静默丢掉一只真动物，不是精度问题。"
                "该动物会被整条管道跳过、且不会有任何报错（DP-032 已真实发生一次，"
                "那只老鼠至今从未被任何人评分）。修好之前不许出验收报告")
    # ---- G10b: detached ----
    hit, npos, fp, nneg = _gate_counts(
        [r for r in slots if r.truth in (TRUTH_DETACHED, TRUTH_OCCUPIED)],
        V.STATUS_DETACHED)
    if npos == 0:
        lines.append(
            "G10b 中途脱落  召回 **不可测**（本数据集无正样本——**本批没有中途脱落的"
            "素材**；旧记载'唯一候选 30mg_2周-ch4 是空场'已作废，那里从头到尾有老鼠）"
            f"  假阳性 {fp}/{nneg}"
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

    # ---- DP-043 输入契约层：人声明为空的槽位 + 幻影兜底 ----
    declared = [r for r in slots if r.declared_empty]
    lines.append(
        f"［DP-043 输入契约］人声明为空的槽位 {len(declared)}/{len(slots)}"
        f"：{[r.trial_id for r in declared] or '无'}"
        "——按契约**不进分析管道**，不计入 G10a 召回")
    phantom = [r for r in declared if r.pipeline_immobility_s is not None]
    if phantom:
        lines.append(
            "[PHANTOM-IMMOBILITY 报警] 以下槽位已声明为空，管道却还递出了 immobility："
            + "; ".join(f"{r.trial_id}={r.pipeline_immobility_s:.1f}s" for r in phantom)
            + "——空场判出 immobility 会**伪造最强抑郁表型**（趋近 360 s = 最重抑郁），"
              "GLP 语境下最不能有的错误。声明为空就必须一个数字都不出，"
              "**不许静默**（同 `validity.score_gate` 的 N1/N3）")
    return "\n".join(lines)


def g10_safety_ok(slots: Sequence[SlotRecord]) -> bool:
    """G10 唯一还能判 FAIL 的东西：**把有动物的隔间判成空 = 0 次**（DP-043 安全属性）。

    调用方**不许**用"G10 报告跑出来了"当通过——报告里召回是参考值、G10b 召回不可测、
    G10c 挂起，只有这一条是硬门。幻影 immobility 同样计入失败（伪造抑郁表型）。
    """
    if not slots:
        raise ValueError("空输入不许出'安全属性通过'（静默兜底禁令）")
    fp = any(r.truth == TRUTH_OCCUPIED
             and r.software_status == V.STATUS_NEVER_OCCUPIED for r in slots)
    phantom = any(r.declared_empty and r.pipeline_immobility_s is not None
                  for r in slots)
    return not fp and not phantom
