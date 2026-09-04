"""DP-034 建、**DP-043 重定位**：G10 三支子门报告层测试。

DP-043 之后 G10 的形状（道俊输入契约："空的我们会告诉你，空的不该进软件"）：
- **G10a 召回已下架为参考值**：只报不判，召回缺口**不再**导致 FAIL；
- **假阳性 = 0 升级为安全属性**：把有动物的隔间判成空 = 静默丢一只真动物
  （DP-032 真实发生过一次，那只老鼠至今无人评分）⇒ **硬失败不可豁免**，
  也是本模块唯一还能判 FAIL 的东西（`g10_safety_ok`）；
- **声明为空却真值有动物 ⇒ 构造时就炸**，不许悄悄跑；
- **PHANTOM-IMMOBILITY 兜底报警保留**：空场递出 immobility 会伪造最强抑郁表型。

本文件里的槽位除注明外都是**合成数据**：报告生成器不认识本批的正负名单
（真值一律由调用方给），所以测试不该、也不能拿它来主张本批真值。

DP-034 起就锁死、DP-043 未动的性质（SPEC §9 / DP-031 评审结论）：
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
    SlotRecord, TailGraspPair, cohen_kappa, g10_report, g10_safety_ok,
)


def _occupied(n: int, tag: str = "occ") -> list[SlotRecord]:
    return [SlotRecord(f"{tag}{i}", TRUTH_OCCUPIED, V.STATUS_VALID,
                       occupied_fraction=1.0) for i in range(n)]


def test_gates_do_not_share_false_positive_counts() -> None:
    """有鼠隔间被判空 = G10a 假阳；被判脱落 = G10b 假阳。两支各数各的。"""
    slots = _occupied(26) + [
        SlotRecord("20mg_3周-ch4", TRUTH_NEVER_OCCUPIED,
                   V.STATUS_NEVER_OCCUPIED, occupied_fraction=0.0),
        # 一个 never 被软件判成 detached：算 G10a 的召回缺口（DP-043 后只是参考值），
        # 但**不算** G10b 的假阳（G10b 假阳只数 truth=occupied 的）
        SlotRecord("合成空场#slot9", TRUTH_NEVER_OCCUPIED,
                   V.STATUS_DETACHED, sliced=False, occupied_fraction=0.0),
    ]
    txt = g10_report(slots)
    a28 = next(l for l in txt.splitlines() if l.startswith("G10a") and "28" in l)
    assert "召回 1/2" in a28 and "参考值，不设门" in a28
    assert "假阳性 0/26" in a28
    assert "PASS" in a28, "DP-043：召回缺口**不再**判 FAIL，只有假阳侧能判 FAIL"
    b = next(l for l in txt.splitlines() if l.startswith("G10b"))
    assert "假阳性 0/26" in b, "never→detached 被算进 G10b 假阳 = 合并计数，打回"
    assert "不可测" in b and "PASS（仅假阳侧）" in b
    assert "不得据此声称" in txt, "报告必须自带 G10a≠G10b 的警示行"
    assert g10_safety_ok(slots), "没有'有鼠判空'⇒ 安全属性通过"


def test_recall_miss_alone_does_not_fail_g10a() -> None:
    """DP-043 的核心改动：空隔间一个都没发现，只要没冤枉有鼠的，G10a 仍 PASS。

    理由：空隔间由人在录入时声明（`declared_empty`），机器不需要自己发现。
    这一条如果回归（召回缺口又判 FAIL），说明有人把下架的门偷偷装回去了。
    """
    slots = _occupied(5) + [
        SlotRecord("漏检的空场", TRUTH_NEVER_OCCUPIED,
                   V.STATUS_VALID, occupied_fraction=0.0),   # 软件当成正常试次
    ]
    txt = g10_report(slots)
    a = next(l for l in txt.splitlines() if l.startswith("G10a"))
    assert "召回 0/1" in a and "参考值，不设门" in a
    assert "假阳性 0/5" in a and "PASS" in a and "FAIL" not in a
    assert g10_safety_ok(slots)


def test_false_positive_is_hard_fail_safety_property() -> None:
    """有鼠隔间被判空 = 静默丢一只真动物 ⇒ 硬失败，且必须点名是哪一条。"""
    slots = _occupied(3)
    slots[1] = SlotRecord("有鼠却被判空", TRUTH_OCCUPIED,
                          V.STATUS_NEVER_OCCUPIED, occupied_fraction=0.9)
    txt = g10_report(slots)
    a = next(l for l in txt.splitlines() if l.startswith("G10a"))
    assert "假阳性 1/3" in a and "FAIL" in a
    assert "安全属性，硬失败不可豁免" in a, "不许留豁免口子"
    fail = next(l for l in txt.splitlines() if "[安全属性失败]" in l)
    assert "有鼠却被判空" in fail, "必须点名，否则没法追那只动物"
    assert not g10_safety_ok(slots)


def test_declared_empty_conflicting_with_truth_raises_at_construction() -> None:
    """人声明为空、真值却是有动物 ⇒ 入口就炸。靠下游发现太晚（DP-032 的教训）。"""
    try:
        SlotRecord("声明错了", TRUTH_OCCUPIED, V.STATUS_VALID,
                   occupied_fraction=1.0, declared_empty=True)
        assert False, "声明为空 + 真值有动物必须拒收"
    except ValueError as e:
        assert "当场拒收" in str(e)


def test_declared_empty_slots_listed_and_not_counted_in_recall() -> None:
    """输入契约层：声明为空的槽位单列成行，且不因'没被发现'算召回缺口。"""
    slots = _occupied(4) + [
        SlotRecord("人已声明的空场", TRUTH_NEVER_OCCUPIED, V.STATUS_VALID,
                   occupied_fraction=0.0, declared_empty=True),
    ]
    txt = g10_report(slots)
    line = next(l for l in txt.splitlines() if "DP-043 输入契约" in l)
    assert "1/5" in line and "人已声明的空场" in line and "不进分析管道" in line
    a = next(l for l in txt.splitlines() if l.startswith("G10a"))
    assert "PASS" in a, "声明为空的槽位没被机器'发现'不构成失败"


def test_phantom_immobility_alarm_and_safety_failure() -> None:
    """声明为空却还递出 immobility ⇒ 伪造抑郁表型，报警 + 安全属性失败。"""
    slots = _occupied(4) + [
        SlotRecord("空场却出数", TRUTH_NEVER_OCCUPIED, V.STATUS_VALID,
                   occupied_fraction=0.0, declared_empty=True,
                   pipeline_immobility_s=357.2),
    ]
    txt = g10_report(slots)
    alarm = next(l for l in txt.splitlines() if "PHANTOM-IMMOBILITY" in l)
    assert "空场却出数=357.2s" in alarm and "伪造最强抑郁表型" in alarm
    assert not g10_safety_ok(slots), "幻影 immobility 必须计入失败，不是只报警"
    # 对照：不出数就没有这行
    ok = _occupied(4) + [
        SlotRecord("空场不出数", TRUTH_NEVER_OCCUPIED, V.STATUS_VALID,
                   occupied_fraction=0.0, declared_empty=True),
    ]
    assert "PHANTOM-IMMOBILITY" not in g10_report(ok) and g10_safety_ok(ok)


def test_safety_ok_rejects_empty_input() -> None:
    """空输入不许出'安全属性通过'——那是最典型的静默兜底。"""
    try:
        g10_safety_ok([])
        assert False, "空输入必须报错"
    except ValueError:
        pass


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
    """28 槽位口径 vs 27 已切片口径：两行都给、分母写明，不许只报一个。

    这里的"未切片空场"是**合成**的：本批真实分布是正 1（`20mg_3周-ch4`）/ 真负 27，
    未切片的那个槽位 `30mg_2周` 隔间 4 **有老鼠**（旧记载说它是空场，已作废）。
    合成一个未切片正例只为验证"未切片槽位只进 28 口径、不进 27 口径"。
    """
    slots = _occupied(26) + [
        SlotRecord("20mg_3周-ch4", TRUTH_NEVER_OCCUPIED,
                   V.STATUS_NEVER_OCCUPIED, occupied_fraction=0.0),
        SlotRecord("合成未切片空场#slot9", TRUTH_NEVER_OCCUPIED,
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
