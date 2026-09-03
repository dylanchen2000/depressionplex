"""DP-034：G10 三支子门报告层测试——门槛写死、两支永不合并计数。

锁死的性质（SPEC §9 / DP-031 评审结论）：
- G10a（never_occupied）与 G10b（detached）各自召回、各自假阳：把有动物的
  隔间判成 never_occupied 是 G10a 的假阳，判成 detached 是 G10b 的假阳——
  互相顶不了数，这正是"不许合并"的计数层落实；
- G10b 正样本为 0 ⇒ 召回**不可测**，PASS 只覆盖假阳侧，且报告必须自带
  "G10a 的 PASS 不等于脱落已验证"的警示行；
- G10c 零出现率 ⇒ κ 退化(0/0)记"未定义"、判据挂起，**不得记 1.0**；
- N2 入账强制：真值 never_occupied 但在场占比 ≠0 ⇒ 拒收（不是警告）；
- 空输入 ⇒ 报错，不许静默出"全过"。
"""

from __future__ import annotations

from depressionplex.assay_core import validity as V
from depressionplex.g10_gates import (
    TRUTH_DETACHED, TRUTH_NEVER_OCCUPIED, TRUTH_OCCUPIED,
    SlotRecord, TailGraspPair, cohen_kappa, g10_report,
)


def _occupied(n: int, tag: str = "occ") -> list[SlotRecord]:
    return [SlotRecord(f"{tag}{i}", TRUTH_OCCUPIED, V.STATUS_VALID,
                       occupied_fraction=1.0) for i in range(n)]


def test_gates_do_not_share_false_positive_counts() -> None:
    """有鼠隔间被判空 = G10a 假阳；被判脱落 = G10b 假阳。两支各数各的。"""
    slots = _occupied(26) + [
        SlotRecord("20mg_3周-ch4", TRUTH_NEVER_OCCUPIED,
                   V.STATUS_NEVER_OCCUPIED, occupied_fraction=0.0),
        # 一个 never 被软件判成 detached：算 G10a 的召回缺口，
        # 但**不算** G10b 的假阳（G10b 假阳只数 truth=occupied 的）
        SlotRecord("30mg_2周#slot4", TRUTH_NEVER_OCCUPIED,
                   V.STATUS_DETACHED, sliced=False, occupied_fraction=0.0),
    ]
    txt = g10_report(slots)
    a28 = next(l for l in txt.splitlines() if l.startswith("G10a") and "28" in l)
    assert "召回 1/2" in a28 and "假阳性 0/26" in a28 and "FAIL" in a28
    b = next(l for l in txt.splitlines() if l.startswith("G10b"))
    assert "假阳性 0/26" in b, "never→detached 被算进 G10b 假阳 = 合并计数，打回"
    assert "不可测" in b and "PASS（仅假阳侧）" in b
    assert "不得据此声称" in txt, "报告必须自带 G10a≠G10b 的警示行"


def test_g10b_fp_when_occupied_chamfered_as_detached() -> None:
    """真脱落的假阳侧：有鼠隔间被误判 detached ⇒ G10b FAIL（哪怕 G10a 全绿）。"""
    slots = _occupied(26, tag="ch")
    slots[3] = SlotRecord("ch3", TRUTH_OCCUPIED, V.STATUS_DETACHED,
                          occupied_fraction=0.9)  # frozen dataclass：重建替换
    slots.append(SlotRecord("空场", TRUTH_NEVER_OCCUPIED,
                            V.STATUS_NEVER_OCCUPIED, occupied_fraction=0.0))
    txt = g10_report(slots)
    a = next(l for l in txt.splitlines() if l.startswith("G10a"))
    b = next(l for l in txt.splitlines() if l.startswith("G10b"))
    assert "PASS" in a, "G10a 全中且无假阳"
    assert "假阳性 1/26" in b and "FAIL" in b


def test_g10b_with_real_positives_reports_recall() -> None:
    """换一批有真脱落正样本的数据：召回从'不可测'变成实打实的 n/n。"""
    slots = _occupied(4) + [
        SlotRecord("脱落1", TRUTH_DETACHED, V.STATUS_DETACHED,
                   occupied_fraction=0.5),
        SlotRecord("脱落2", TRUTH_DETACHED, V.STATUS_VALID,
                   occupied_fraction=1.0),   # 漏检
    ]
    txt = g10_report(slots)
    b = next(l for l in txt.splitlines() if l.startswith("G10b"))
    assert "召回 1/2" in b and "不可测" not in b and "FAIL" in b


def test_kappa_degenerate_is_undefined_not_one() -> None:
    pairs = [TailGraspPair(f"t{i}", human=False, software=False)
             for i in range(10)]
    assert cohen_kappa(pairs) is None, "零出现率 κ=0/0——记 1.0 = 凭空制造验证"
    txt = g10_report(_occupied(4), tail_grasp=pairs)
    assert "κ=未定义" in txt and "判据挂起" in txt and "1.000" not in txt
    # 对照：非退化的一致/不一致仍可算
    good = [TailGraspPair("a", True, True), TailGraspPair("b", True, True),
            TailGraspPair("c", False, False), TailGraspPair("d", False, False)]
    k = cohen_kappa(good)
    assert k is not None and abs(k - 1.0) < 1e-12
    txt2 = g10_report(_occupied(4), tail_grasp=good)
    assert "κ=1.000" in txt2 and "挂起" not in txt2


def test_slot_record_enforces_N2_on_input() -> None:
    """真值说'从未有动物'、在场占比却是 0.25 ⇒ 真值标错或 N2 失守——拒收。"""
    try:
        SlotRecord("x", TRUTH_NEVER_OCCUPIED, V.STATUS_NEVER_OCCUPIED,
                   occupied_fraction=0.25)
        assert False, "N2：占比必须恰为 0，不是'很低'"
    except ValueError as e:
        assert "N2" in str(e)
    # 0.0 收下；None（未记录）允许但报告必须打报警行——不许静默当 0
    SlotRecord("y", TRUTH_NEVER_OCCUPIED, V.STATUS_NEVER_OCCUPIED,
               occupied_fraction=0.0)
    txt = g10_report([SlotRecord("缺占比", TRUTH_NEVER_OCCUPIED,
                                 V.STATUS_NEVER_OCCUPIED)])
    assert "[报警]" in txt and "缺占比" in txt


def test_two_denominator_cohorts_both_printed() -> None:
    """28 槽位口径 vs 27 已切片口径：两行都给、分母写明，不许只报一个。"""
    slots = _occupied(26) + [
        SlotRecord("20mg_3周-ch4", TRUTH_NEVER_OCCUPIED,
                   V.STATUS_NEVER_OCCUPIED, occupied_fraction=0.0),
        SlotRecord("30mg_2周#slot4", TRUTH_NEVER_OCCUPIED,
                   V.STATUS_NEVER_OCCUPIED, sliced=False, occupied_fraction=0.0),
    ]
    assert len(slots) == 28 and sum(1 for s in slots if s.sliced) == 27
    txt = g10_report(slots)
    a_lines = [l for l in txt.splitlines() if l.startswith("G10a 从未有动物")]
    assert len(a_lines) == 2
    full = next(l for l in a_lines if "全部 28" in l)
    sliced = next(l for l in a_lines if "已切片" in l)
    assert "召回 2/2" in full and "分母 28" in full
    assert "召回 1/1" in sliced and "分母 27" in sliced
    assert all("PASS" in l for l in a_lines)


def test_empty_input_alarms() -> None:
    try:
        g10_report([])
        assert False, "空输入出'全过'是静默兜底，明令禁止"
    except ValueError:
        pass
