"""几何提案测试：只提案、不确认、不从 CLB 猜（Spec A §4 A1 第 2 条 / §6.2）。

钉住的口径：
- **三个几何概念分开**（R2-115 G1）：分析 ROI（含线上留白）/ 水体候选区 /
  水线是三件不同的东西。ROI 顶边必须在水线**上方**，容纳探头/前肢活动，
  `above_water_frac` 才可能真的 >0；水线不可靠 ⇒ 下游 null + 原因，不填假 0；
- 水线主值从**真帧**（合成夹具的标签构造）来，行梯度只做旁证：**永不改主值**，
  分歧写 note + 标 reliable=False，由人工在叠加图上定；
- 提案 confirmed 恒 False，validate() 报"未确认"是设计不是失败；
- to_envelope 用 **ROI** 当 tank ⇒ 水线严格在 ROI 顶底之间（G2 严格开区间天然满足）；
- **申报空杯绑物理杯号**（G3）：杯候选数与期望不符 ⇒ 拒绝应用，不顺下标漂移；
- **人工确认件走带 binding 的 wrapper**（G4）：绑定视频 sha256/尺寸/杯号/确认人，
  任何一项不符即拒；裸 envelope 的 load_confirmed 只留作语义检查，不是 CLI 输入通道。
"""

from __future__ import annotations

import dataclasses
import json
import tempfile
from pathlib import Path

import numpy as np

from depressionplex.assay_core import geometry as geo
from depressionplex.fst_research import cup_geometry as cg

import fst_synth


def test_varying_dark_mask_selects_only_movers() -> None:
    frames = [np.full((20, 20), 200.0) for _ in range(3)]
    for f in frames:
        f[2:5, 2:5] = 30.0                    # 静态暗（杯壁）
        f[10:13, 10:13] = 200.0               # 静态亮（背光）
    frames[0][15:18, 15:18] = 30.0            # 动物：第 0 帧暗
    frames[1][16:19, 15:18] = 30.0            # 第 1 帧挪了一行
    m = cg.varying_dark_mask(frames)
    assert not m[2:5, 2:5].any()              # 静态暗被排除（max 仍暗）
    assert not m[10:13, 10:13].any()          # 静态亮被排除（min 仍亮）
    assert m[15, 15] and m[18, 17]            # 动物到过的地方入选
    assert m[15:18, 15:18].sum() == 9


def test_propose_cups_matches_labeled_construction() -> None:
    scene = fst_synth.Scene(cups=2)
    frames = scene.swim_series(16, cup=0)
    props, problems = cg.propose_cups(frames, n_cups=2)
    assert problems == []
    assert [p.index for p in props] == [0, 1]     # 从左到右编号
    for p, cup in zip(props, scene.cup_defs):
        # 水体候选区 = 标签构造的水体 bbox（旧版叫 interior 的那块）
        assert p.water_body == (cup.water_y, cup.c0 - 1, cup.r1, cup.c1 + 1)
        # 分析 ROI = 水体向上加留白（G1）：顶边必须在水线上方
        assert p.roi == (max(0, cup.water_y - cg.ROI_ABOVE_WATER_MARGIN_PX),
                         cup.c0 - 1, cup.r1, cup.c1 + 1)
        assert p.roi[0] < p.water_body[0]          # ROI 顶 < 水体顶（= 水线）
        assert p.water_surface_y == float(cup.water_y)   # 水线=水体外边界，精确
        assert p.water_surface_basis == cg.WATERLINE_BASIS_WATER_BODY_TOP
        assert p.basis == cg.BASIS_TEMPORAL_VARIANCE
        assert p.confirmed is False
        # 行梯度旁证找到了同一条边（合成场景边强，必须命中 ⇒ 可靠）
        assert p.water_surface_candidates and \
            abs(p.water_surface_candidates[0] - p.water_surface_y) <= \
            cg.WATERLINE_CROSSCHECK_TOL_PX
        assert p.water_surface_reliable is True
        assert p.water_surface_unreliable_reason is None


def test_roi_above_water_margin_admits_above_waterline_activity() -> None:
    """G1 核心：ROI 必须把水线**上方**纳进来，否则探头/前肢永远看不见。

    构造一帧：动物主体在水下，但有一块明确在水线**上方**（模拟抬头/扒壁）。
    ROI 顶边在水线上方 ⇒ 那块上方像素落在 ROI 内、能进 above_water 计算；
    若 ROI 顶边=水线（旧行为），上方像素被裁掉，above_water_frac 恒为假 0。
    """
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(12, cup=0)
    props, _ = cg.propose_cups(frames, n_cups=1)
    p = props[0]
    wy = int(round(p.water_surface_y))
    # ROI 顶边严格在水线上方，留白 = 起点值（clamp 到 0）
    assert p.roi[0] <= wy - 1
    assert wy - p.roi[0] == min(wy, cg.ROI_ABOVE_WATER_MARGIN_PX)
    # 水体顶边（= 水线）在 ROI 内部，不是 ROI 顶边
    assert p.roi[0] < p.water_body[0] <= p.roi[2]


def test_inactive_cup_gets_note_not_empty_verdict() -> None:
    scene = fst_synth.Scene(cups=2)
    frames = scene.swim_series(16, cup=0)          # 杯 1 从没有动物
    props, _ = cg.propose_cups(frames, n_cups=2)
    assert props[0].notes == ()
    assert len(props[1].notes) == 1
    assert "不等于空杯" in props[1].notes[0]
    assert "人工申报" in props[1].notes[0]


def test_cup_count_mismatch_is_problem_never_silent_truncation() -> None:
    scene = fst_synth.Scene(cups=2)
    frames = scene.swim_series(8, cup=0)
    props, problems = cg.propose_cups(frames, n_cups=3)
    assert len(props) == 2                          # 找到的都返回
    assert len(problems) == 1
    assert "不静默取前 N 个" in problems[0]


def test_structural_filters_drop_seams_and_borders_loudly() -> None:
    """真实素材回归（DP-136 正常1-4）：板缝/边条/分格线也落中间灰阶带。

    旧版把 9 个连通域全当杯。过滤三规则（窄、贴左右边界、纵贯整帧）
    必须把它们滤掉，且**每条过滤都写进 problems**——过滤不许静默。
    """
    img = np.full((60, 120), 220.0)        # 亮背景（背光 > water_hi）
    img[20:55, 10:70] = 140.0              # 真杯水体：宽 60px ≥ 40
    img[10:50, 75:90] = 140.0              # 细缝：宽 15px < 40（面积 600 过 min_area）
    img[5:55, 0:6] = 140.0                 # 左边条：贴左边界 + 窄
    img[0:60, 110:116] = 140.0             # 分格线状：纵贯整帧 + 窄
    frames = [img.copy() for _ in range(3)]
    props, problems = cg.propose_cups(frames, n_cups=1)
    assert len(props) == 1
    assert props[0].water_body == (20, 10, 54, 69)
    # ROI 顶边 = 水体顶 20 - 30 clamp 到 0（这里贴到画面上边）
    assert props[0].roi == (0, 10, 54, 69)
    filt = [p for p in problems if "结构过滤" in p]
    assert len(filt) == 3                  # 三条滤除、三条留痕
    joined = "".join(filt)
    assert "起点值" in joined and "左右边界" in joined and "纵贯整帧" in joined
    assert not any("不静默取前 N 个" in p for p in problems)   # 数目对上


def test_waterline_crosscheck_never_changes_value() -> None:
    """旁证峰与水体外边界分歧 >3px ⇒ 只写 note + 标不可靠，主值原样保留。"""
    median = np.full((40, 60), 200.0)
    median[22:, 5:56] = 100.0                       # 真实水平边在 y=22
    prop = cg.CupProposal(index=0, roi=(18, 5, 35, 55), water_body=(18, 5, 35, 55),
                          water_surface_y=18.0,
                          water_surface_basis=cg.WATERLINE_BASIS_WATER_BODY_TOP,
                          water_surface_candidates=(),
                          water_surface_reliable=False)
    out = cg.propose_waterline(median, prop)
    assert out.water_surface_y == 18.0              # 值没被旁证改
    assert out.water_surface_candidates[0] == 22.0  # 峰在 22
    assert any("脚本不选边" in n for n in out.notes)
    assert out.water_surface_reliable is False       # G1：分歧 ⇒ 不可靠
    assert out.water_surface_unreliable_reason and "分歧" in out.water_surface_unreliable_reason
    assert out.confirmed is False


def test_crosscheck_weak_matching_peak_not_framed_by_stronger_one() -> None:
    """真实素材回归（DP-136）：杯底阴影/架边的梯度比弯月面强得多。

    旧版只比**最强**峰：主值 18 明明有一个 18.0 的候选峰命中，却因为
    29 的强峰被报成"分歧"。新口径：任何候选峰落在 ±容差内即算旁证命中。
    """
    median = np.full((40, 60), 200.0)
    median[18:29, 5:56] = 190.0                 # 弱边：y=18（|Δ|≈9）
    median[29:, 5:56] = 100.0                   # 强边：y=29（|Δ|≈76，杯底阴影）
    prop = cg.CupProposal(index=0, roi=(18, 5, 35, 55), water_body=(18, 5, 35, 55),
                          water_surface_y=18.0,
                          water_surface_basis=cg.WATERLINE_BASIS_WATER_BODY_TOP,
                          water_surface_candidates=(),
                          water_surface_reliable=False)
    out = cg.propose_waterline(median, prop)
    assert out.water_surface_y == 18.0          # 旁证永不改主值
    assert 29.0 in out.water_surface_candidates
    assert 18.0 in out.water_surface_candidates
    assert out.water_surface_candidates[0] == 29.0   # 按强度降序
    assert not any("脚本不选边" in n for n in out.notes)   # 弱峰命中 ⇒ 不冤枉
    assert out.water_surface_reliable is True        # 命中 ⇒ 可靠


def test_waterline_no_peak_is_recorded() -> None:
    median = np.full((40, 60), 170.0)               # 全平：没有任何水平边
    prop = cg.CupProposal(index=0, roi=(10, 5, 30, 55), water_body=(10, 5, 30, 55),
                          water_surface_y=10.0,
                          water_surface_basis=cg.WATERLINE_BASIS_WATER_BODY_TOP,
                          water_surface_candidates=(),
                          water_surface_reliable=False)
    out = cg.propose_waterline(median, prop)
    assert out.water_surface_candidates == ()
    assert any("找不到够强的水平边峰" in n for n in out.notes)
    assert out.water_surface_y == 10.0
    assert out.water_surface_reliable is False       # G1：无旁证 ⇒ 不可靠
    assert out.water_surface_unreliable_reason and "无峰" in out.water_surface_unreliable_reason


def test_waterline_none_is_unreliable_with_reason() -> None:
    """G1：水线为 None（提不出）⇒ reliable=False + 原因，下游不许填假 0。"""
    median = np.full((40, 60), 170.0)
    prop = cg.CupProposal(index=0, roi=(10, 5, 30, 55), water_body=(10, 5, 30, 55),
                          water_surface_y=None,
                          water_surface_basis=cg.WATERLINE_BASIS_NONE,
                          water_surface_candidates=(),
                          water_surface_reliable=False)
    out = cg.propose_waterline(median, prop)
    assert out.water_surface_y is None
    assert out.water_surface_reliable is False
    assert out.water_surface_unreliable_reason and "None" in out.water_surface_unreliable_reason


def test_to_envelope_unconfirmed_by_design() -> None:
    props = [cg.CupProposal(index=0, roi=(44, 17, 106, 93), water_body=(74, 17, 106, 93),
                            water_surface_y=74.0,
                            water_surface_basis=cg.WATERLINE_BASIS_WATER_BODY_TOP,
                            water_surface_candidates=(74.0,),
                            water_surface_reliable=True),
             cg.CupProposal(index=1, roi=(44, 127, 106, 203), water_body=(74, 127, 106, 203),
                            water_surface_y=None,      # 提不出水线：不填大概值
                            water_surface_basis=cg.WATERLINE_BASIS_NONE,
                            water_surface_candidates=(),
                            water_surface_reliable=False)]
    env = cg.to_envelope(props, (220, 120))
    assert env.assay == "FST" and env.confirmed is False
    assert len(env.by_role(geo.ROLE_TANK)) == 2
    assert len(env.by_role(geo.ROLE_WATER_SURFACE)) == 1   # 只有杯 0 有水线
    assert all(not p.confirmed for p in env.primitives)
    problems = env.validate()
    assert any("未确认的几何对象" in p for p in problems)  # 设计如此


def test_to_envelope_roi_puts_waterline_strictly_inside() -> None:
    """G1+G2：tank=ROI（含线上留白）⇒ 水线严格在 ROI 顶底之间，validate 不报越界。

    研究层不靠放宽共享契约（严格开区间）过关：正确提案的 ROI 顶边在水线上方，
    水线天然落在开区间内。
    """
    props = [cg.CupProposal(index=0, roi=(44, 17, 106, 93), water_body=(74, 17, 106, 93),
                            water_surface_y=74.0,
                            water_surface_basis=cg.WATERLINE_BASIS_WATER_BODY_TOP,
                            water_surface_candidates=(74.0,),
                            water_surface_reliable=True)]
    env = cg.to_envelope(props, (220, 120))
    problems = env.validate()
    assert not any("垂直范围" in p for p in problems)      # 水线严格在 ROI 内
    assert not any("缺同号" in p for p in problems)
    assert any("未确认" in p for p in problems)            # 只剩"未确认"这一条设计问题


def test_proposal_confirmed_flag_is_frozen_false() -> None:
    p = cg.CupProposal(index=0, roi=(0, 0, 10, 10), water_body=None,
                       water_surface_y=None,
                       water_surface_basis=cg.WATERLINE_BASIS_NONE,
                       water_surface_candidates=(),
                       water_surface_reliable=False)
    try:
        p.confirmed = True                          # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("CupProposal 不是 frozen：confirmed 能被顺手改真")


def _confirmed_env_json(tmp: Path, *, assay="FST", confirmed=True,
                        water=True, prim_confirmed=True) -> Path:
    env = geo.GeometryEnvelope(assay=assay, video_size=(640, 480),
                               confirmed=confirmed)
    geo.make_rect(env, geo.ROLE_TANK, 10, 20, 100, 200,
                  instance=1, confirmed=prim_confirmed)
    if water:
        geo.make_line(env, geo.ROLE_WATER_SURFACE, 10, 60, 100, 60,
                      instance=1, confirmed=prim_confirmed)
    p = tmp / "env.json"
    p.write_text(env.to_json(), encoding="utf-8")
    return p


def test_load_confirmed_roundtrip() -> None:
    """裸 envelope 的 load_confirmed 仍可用（语义检查，不是 CLI 输入通道）。"""
    with tempfile.TemporaryDirectory() as td:
        env = cg.load_confirmed(_confirmed_env_json(Path(td)))
    assert env.confirmed is True and env.assay == "FST"
    assert abs(env.water_surface_y() - 60.0) < 1e-9


def test_load_confirmed_refusals() -> None:
    cases = {
        "confirmed=False": dict(confirmed=False),
        "非 FST 范式": dict(assay="TST"),
        "缺 water_surface": dict(water=False),
        "primitive 半确认": dict(prim_confirmed=False),   # 手改 JSON 常见错
    }
    with tempfile.TemporaryDirectory() as td:
        for i, (name, kw) in enumerate(cases.items()):
            sub = Path(td) / f"case{i}"
            sub.mkdir()
            try:
                cg.load_confirmed(_confirmed_env_json(sub, **kw))
            except ValueError as e:
                assert str(e)                       # 拒绝要有话说清楚
            else:
                raise AssertionError(f"load_confirmed 没拒绝: {name}")


def test_load_confirmed_missing_file_raises() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            cg.load_confirmed(Path(td) / "no_such.json")
        except (FileNotFoundError, OSError):
            pass
        else:
            raise AssertionError("不存在的确认件居然没报错")


def test_proposals_from_confirmed_marks_confirmed_and_reliable() -> None:
    """G4：确认件 envelope → CupProposal（confirmed=True，水线 reliable=True，
    water_body=None 因为那是提案期派生量，确认件没有）。"""
    env = _confirmed_multi_env(n=2, confirmed=True)
    props = cg.proposals_from_confirmed(env)
    assert [p.cup_id for p in props] == [1, 2]        # 左到右
    for p in props:
        assert p.confirmed is True
        assert p.basis == cg.BASIS_HUMAN_CONFIRMED
        assert p.water_body is None
        assert p.water_surface_reliable is True
        assert p.water_surface_y == 74.0
        # ROI = 人画的 tank
        assert p.roi == (44, 17 + 110 * p.index, 106, 93 + 110 * p.index)


def test_multi_cup_envelope_validates_per_instance() -> None:
    """一帧多杯（真实 FST 素材就是 4 杯）：validate 按同号配对，不许炸也不许串。

    回归：几何 validate 旧版用 one(water_surface)，多于一条水线直接
    GeometryError——研究入口在真实 4 杯素材上第一步就崩（DP-136 修复）。
    """
    props = [cg.CupProposal(index=k, roi=(44, 17 + 110 * k, 106, 93 + 110 * k),
                            water_body=(74, 17 + 110 * k, 106, 93 + 110 * k),
                            water_surface_y=74.0,
                            water_surface_basis=cg.WATERLINE_BASIS_WATER_BODY_TOP,
                            water_surface_candidates=(74.0,),
                            water_surface_reliable=True)
             for k in range(2)]
    env = cg.to_envelope(props, (220, 120))
    problems = env.validate()                    # 不 raise
    assert any("未确认" in p for p in problems)   # 提案必然未确认
    assert not any("垂直范围" in p for p in problems)   # 水线都在各自杯内
    assert not any("缺同号" in p for p in problems)
    # 把杯 1 的水线挪到杯 0 的高度之外：必须报"不在垂直范围"，且指名 tank_2
    env.primitives = [p for p in env.primitives
                      if not (p.semantic_role == geo.ROLE_WATER_SURFACE
                              and p.instance == 2)]
    geo.make_line(env, geo.ROLE_WATER_SURFACE, 127.0, 10.0, 203.0, 10.0,
                  instance=2, confirmed=False)
    problems = env.validate()
    assert any("tank_2" in p and "垂直范围" in p for p in problems)
    # 拿掉杯 0 的水线：多杯时不许共用别杯的水线，报"缺同号"
    env2 = cg.to_envelope(props, (220, 120))
    env2.primitives = [p for p in env2.primitives
                       if not (p.semantic_role == geo.ROLE_WATER_SURFACE
                               and p.instance == 1)]
    assert any("tank_1" in p and "缺同号" in p for p in env2.validate())


def test_to_envelope_json_roundtrip_stays_unconfirmed() -> None:
    """几何提案 JSON 落盘再读回来，confirmed 还是 False——不会路上变真。"""
    props = [cg.CupProposal(index=0, roi=(44, 17, 106, 93), water_body=(74, 17, 106, 93),
                            water_surface_y=74.0,
                            water_surface_basis=cg.WATERLINE_BASIS_WATER_BODY_TOP,
                            water_surface_candidates=(74.0,),
                            water_surface_reliable=True)]
    env = cg.to_envelope(props, (220, 120))
    env2 = geo.GeometryEnvelope.from_json(env.to_json())
    assert env2.confirmed is False
    assert all(not p.confirmed for p in env2.primitives)
    assert json.loads(env.to_json())["assay"] == "FST"


# ---------------------------------------------------------------------------
# G3：申报空杯 ↔ 物理杯号绑定
# ---------------------------------------------------------------------------

def test_bind_declared_empty_applies_when_count_matches() -> None:
    b = cg.bind_declared_empty([1, 2, 3, 4], [2, 4], expected_n=4)
    assert b.status == cg.BIND_APPLIED
    assert b.applied == (2, 4)                     # 物理杯号，不是下标
    assert b.problems == ()


def test_bind_declared_empty_refuses_when_count_mismatch() -> None:
    """G3 核心：4 杯漏检成 3 杯 ⇒ 物理编号有歧义 ⇒ 拒绝应用申报，相关杯保持未决。

    申报"杯 2 空"，但只找到 3 个杯——过滤后的下标 1 到底是物理杯 2 还是杯 3？
    绑错就把空杯结论漂到别的杯上。宁可拒绝、照实记，也不猜。
    """
    b = cg.bind_declared_empty([1, 2, 3], [2], expected_n=4)
    assert b.status == cg.BIND_REFUSED_AMBIGUOUS
    assert b.applied == ()                         # 一个都不应用
    assert b.problems and "不应用" in b.problems[0]


def test_bind_declared_empty_no_drift_on_spurious_extra_cup() -> None:
    """多检出一个假杯（5 个候选，期望 4）⇒ 同样拒绝，申报不漂移。"""
    b = cg.bind_declared_empty([1, 2, 3, 4, 5], [3], expected_n=4)
    assert b.status == cg.BIND_REFUSED_AMBIGUOUS
    assert b.applied == ()


def test_bind_declared_empty_confirmed_geometry_applies_without_expected_n() -> None:
    """人工确认件（expected_n=None）：杯号已由 binding 核过，直接应用。"""
    b = cg.bind_declared_empty([1, 2, 3, 4], [1], expected_n=None)
    assert b.status == cg.BIND_APPLIED
    assert b.applied == (1,)


def test_bind_declared_empty_unknown_cup_raises() -> None:
    """申报了不存在的物理杯号 ⇒ 硬错误（用错杯号），整跑拒绝。"""
    try:
        cg.bind_declared_empty([1, 2, 3], [5], expected_n=3)
    except ValueError as e:
        assert "不在实际杯号" in str(e)
    else:
        raise AssertionError("申报不存在的杯号居然没报错")


def test_bind_declared_empty_unresolved_note_not_applied() -> None:
    """R3-115 ①：通道申报无映射证据 ⇒ 映射未决、不应用，注记进 problems。"""
    b = cg.bind_declared_empty([1, 2, 3, 4], [], expected_n=4,
                               unresolved_note="通道申报 [4] 映射未决：本次不应用")
    assert b.status == cg.BIND_MAPPING_UNRESOLVED
    assert b.applied == ()
    assert len(b.problems) == 1 and "不应用" in b.problems[0]
    # 没有注记的空申报仍然是"没申报"，不是"未决"
    n = cg.bind_declared_empty([1, 2], [], expected_n=2)
    assert n.status == cg.BIND_NONE and n.problems == ()


def test_bind_declared_empty_none_is_noop() -> None:
    b = cg.bind_declared_empty([1, 2, 3, 4], [], expected_n=4)
    assert b.status == cg.BIND_NONE and b.applied == ()


def test_bind_declared_empty_gapped_ids_refused() -> None:
    """物理杯号不是完整 1..n（有缺口）⇒ 编号有歧义，拒绝应用。"""
    b = cg.bind_declared_empty([1, 2, 4], [2], expected_n=None)
    assert b.status == cg.BIND_REFUSED_AMBIGUOUS
    assert b.applied == ()


# ---------------------------------------------------------------------------
# G4：人工确认件（带 binding 的 wrapper）
# ---------------------------------------------------------------------------

def _confirmed_multi_env(n=2, *, confirmed=True, video_size=(220, 120)):
    """n 杯确认件 envelope：tank y44..106（ROI 含线上留白），水线 y=74。"""
    env = geo.GeometryEnvelope(assay="FST", video_size=video_size, confirmed=confirmed)
    for k in range(n):
        c0 = 17 + 110 * k
        c1 = 93 + 110 * k
        geo.make_rect(env, geo.ROLE_TANK, c0, 44, c1, 106,
                      instance=k + 1, confirmed=confirmed)
        geo.make_line(env, geo.ROLE_WATER_SURFACE, c0, 74, c1, 74,
                      instance=k + 1, confirmed=confirmed)
    return env


_SHA = "ab" * 32


def _write_confirmed(tmp: Path, env, *, sha=_SHA, width=220, height=120,
                     cup_ids=None, confirmed_by="tester",
                     confirmed_at="2026-09-19T00:00:00Z", video_bytes=1234,
                     name="confirmed.json"):
    cup_ids = cup_ids if cup_ids is not None else [t.instance for t in env.by_role(geo.ROLE_TANK)]
    payload = cg.confirmation_payload(env, video_sha256=sha, video_bytes=video_bytes,
                                      width=width, height=height, cup_ids=cup_ids)
    payload["binding"]["confirmed_by"] = confirmed_by
    payload["binding"]["confirmed_at"] = confirmed_at
    p = tmp / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def test_load_confirmed_file_roundtrip() -> None:
    env = _confirmed_multi_env(n=2, confirmed=True)
    with tempfile.TemporaryDirectory() as td:
        p = _write_confirmed(Path(td), env)
        env2, binding, file_sha, props = cg.load_confirmed_file(
            p, video_sha256=_SHA, width=220, height=120, video_bytes=1234)
    assert env2.confirmed is True
    assert binding["confirmed_by"] == "tester"
    assert len(file_sha) == 64                      # 确认件自身 sha256 供记录引用
    assert [pr.cup_id for pr in props] == [1, 2]
    assert all(pr.confirmed and pr.water_surface_reliable for pr in props)


def test_load_confirmed_file_refuses_wrong_sha() -> None:
    env = _confirmed_multi_env(n=2, confirmed=True)
    with tempfile.TemporaryDirectory() as td:
        p = _write_confirmed(Path(td), env, sha=_SHA)
        try:
            cg.load_confirmed_file(p, video_sha256="cd" * 32, width=220, height=120)
        except ValueError as e:
            assert "sha256" in str(e) and "不是这段素材" in str(e)
        else:
            raise AssertionError("视频 sha 不符的确认件居然通过了")


def test_load_confirmed_file_refuses_wrong_size() -> None:
    env = _confirmed_multi_env(n=2, confirmed=True)
    with tempfile.TemporaryDirectory() as td:
        p = _write_confirmed(Path(td), env, width=220, height=120)
        try:
            cg.load_confirmed_file(p, video_sha256=_SHA, width=999, height=120)
        except ValueError as e:
            assert "尺寸" in str(e)
        else:
            raise AssertionError("尺寸不符的确认件居然通过了")


def test_load_confirmed_file_refuses_unconfirmed_template() -> None:
    """G4：提案态 wrapper（confirmed_by 空 + envelope.confirmed=False）不是确认件。"""
    env = _confirmed_multi_env(n=2, confirmed=False)
    with tempfile.TemporaryDirectory() as td:
        # 不填 confirmed_by/at（保持模板态）
        p = _write_confirmed(Path(td), env, confirmed_by="", confirmed_at="")
        try:
            cg.load_confirmed_file(p, video_sha256=_SHA, width=220, height=120)
        except ValueError as e:
            assert "confirmed_by" in str(e) or "confirmed" in str(e)
        else:
            raise AssertionError("未确认模板居然被当确认件加载")


def test_load_confirmed_file_refuses_missing_confirmer() -> None:
    env = _confirmed_multi_env(n=2, confirmed=True)
    with tempfile.TemporaryDirectory() as td:
        p = _write_confirmed(Path(td), env, confirmed_by="  ")
        try:
            cg.load_confirmed_file(p, video_sha256=_SHA, width=220, height=120)
        except ValueError as e:
            assert "confirmed_by" in str(e)
        else:
            raise AssertionError("确认人为空的确认件居然通过了")


def test_load_confirmed_file_refuses_cupid_mismatch() -> None:
    """binding.cup_ids 与 envelope 实例号不符 ⇒ 拒绝（杯号绑定对不上）。"""
    env = _confirmed_multi_env(n=2, confirmed=True)
    with tempfile.TemporaryDirectory() as td:
        p = _write_confirmed(Path(td), env, cup_ids=[1, 3])   # envelope 是 1,2
        try:
            cg.load_confirmed_file(p, video_sha256=_SHA, width=220, height=120)
        except ValueError as e:
            assert "cup_ids" in str(e)
        else:
            raise AssertionError("cup_ids 不符的确认件居然通过了")


def test_load_confirmed_file_refuses_reordered_instances() -> None:
    """G3+G4：tank 实例号与画面左到右顺序拧着 ⇒ 物理杯号会绑错，拒绝。"""
    env = geo.GeometryEnvelope(assay="FST", video_size=(220, 120), confirmed=True)
    # 左杯（x 小）给 instance=2，右杯（x 大）给 instance=1 —— 编号与画面顺序相反
    geo.make_rect(env, geo.ROLE_TANK, 17, 44, 93, 106, instance=2, confirmed=True)
    geo.make_line(env, geo.ROLE_WATER_SURFACE, 17, 74, 93, 74, instance=2, confirmed=True)
    geo.make_rect(env, geo.ROLE_TANK, 127, 44, 203, 106, instance=1, confirmed=True)
    geo.make_line(env, geo.ROLE_WATER_SURFACE, 127, 74, 203, 74, instance=1, confirmed=True)
    with tempfile.TemporaryDirectory() as td:
        p = _write_confirmed(Path(td), env, cup_ids=[1, 2])
        try:
            cg.load_confirmed_file(p, video_sha256=_SHA, width=220, height=120)
        except ValueError as e:
            assert "左到右" in str(e)
        else:
            raise AssertionError("实例号与画面顺序拧着的确认件居然通过了")


def test_load_confirmed_file_refuses_bare_envelope() -> None:
    """裸 envelope JSON（无 schema/binding）不是 CLI 的合法输入。"""
    env = _confirmed_multi_env(n=1, confirmed=True)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bare.json"
        p.write_text(env.to_json(), encoding="utf-8")
        try:
            cg.load_confirmed_file(p, video_sha256=_SHA, width=220, height=120)
        except ValueError as e:
            assert "确认件" in str(e) or "schema" in str(e)
        else:
            raise AssertionError("裸 envelope 居然被当确认件加载")


def test_confirmation_payload_prefills_binding() -> None:
    """Agent 生成、人只改几何/确认字段：binding 由脚本预填（视频 sha/尺寸/杯号）。"""
    env = _confirmed_multi_env(n=2, confirmed=False)
    payload = cg.confirmation_payload(env, video_sha256=_SHA, video_bytes=99,
                                      width=220, height=120, cup_ids=[1, 2])
    assert payload["schema"] == cg.CONFIRMED_SCHEMA
    assert payload["status"] == "proposal_unconfirmed"
    b = payload["binding"]
    assert b["video_sha256"] == _SHA and b["video_bytes"] == 99
    assert b["width"] == 220 and b["height"] == 120 and b["cup_ids"] == [1, 2]
    assert b["confirmed_by"] == "" and b["confirmed_at"] == ""   # 留给人填
    assert "instructions" in payload
