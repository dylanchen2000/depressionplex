"""几何提案测试：只提案、不确认、不从 CLB 猜（Spec A §4 A1 第 2 条 / §6.2）。

钉住的口径：
- 杯内区/水线从**真帧**（合成夹具的标签构造）来，数值必须与构造参数一致；
- 行梯度只做旁证：**永不改主值**，分歧写 note 由人工在叠加图上定；
- 提案 confirmed 恒 False，validate() 报"未确认"是设计不是失败；
- 人工确认件走 load_confirmed，半确认/错范式/缺角色一律拒。
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
        assert p.interior == (cup.water_y, cup.c0 - 1, cup.r1, cup.c1 + 1)
        assert p.water_surface_y == float(cup.water_y)   # 水线=水体外边界，精确
        assert p.water_surface_basis == cg.WATERLINE_BASIS_WATER_BODY_TOP
        assert p.basis == cg.BASIS_TEMPORAL_VARIANCE
        assert p.confirmed is False
        # 行梯度旁证找到了同一条边（合成场景边强，必须命中）
        assert p.water_surface_candidates and \
            abs(p.water_surface_candidates[0] - p.water_surface_y) <= \
            cg.WATERLINE_CROSSCHECK_TOL_PX


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
    assert props[0].interior == (20, 10, 54, 69)
    filt = [p for p in problems if "结构过滤" in p]
    assert len(filt) == 3                  # 三条滤除、三条留痕
    joined = "".join(filt)
    assert "起点值" in joined and "左右边界" in joined and "纵贯整帧" in joined
    assert not any("不静默取前 N 个" in p for p in problems)   # 数目对上


def test_waterline_crosscheck_never_changes_value() -> None:
    """旁证峰与水体外边界分歧 >3px ⇒ 只写 note，主值原样保留。"""
    median = np.full((40, 60), 200.0)
    median[22:, 5:56] = 100.0                       # 真实水平边在 y=22
    prop = cg.CupProposal(index=0, interior=(18, 5, 35, 55),
                          water_surface_y=18.0,
                          water_surface_basis=cg.WATERLINE_BASIS_WATER_BODY_TOP,
                          water_surface_candidates=())
    out = cg.propose_waterline(median, prop)
    assert out.water_surface_y == 18.0              # 值没被旁证改
    assert out.water_surface_candidates[0] == 22.0  # 峰在 22
    assert any("脚本不选边" in n for n in out.notes)
    assert out.confirmed is False


def test_crosscheck_weak_matching_peak_not_framed_by_stronger_one() -> None:
    """真实素材回归（DP-136）：杯底阴影/架边的梯度比弯月面强得多。

    旧版只比**最强**峰：主值 18 明明有一个 18.0 的候选峰命中，却因为
    29 的强峰被报成"分歧"。新口径：任何候选峰落在 ±容差内即算旁证命中。
    """
    median = np.full((40, 60), 200.0)
    median[18:29, 5:56] = 190.0                 # 弱边：y=18（|Δ|≈9）
    median[29:, 5:56] = 100.0                   # 强边：y=29（|Δ|≈76，杯底阴影）
    prop = cg.CupProposal(index=0, interior=(18, 5, 35, 55),
                          water_surface_y=18.0,
                          water_surface_basis=cg.WATERLINE_BASIS_WATER_BODY_TOP,
                          water_surface_candidates=())
    out = cg.propose_waterline(median, prop)
    assert out.water_surface_y == 18.0          # 旁证永不改主值
    assert 29.0 in out.water_surface_candidates
    assert 18.0 in out.water_surface_candidates
    assert out.water_surface_candidates[0] == 29.0   # 按强度降序
    assert not any("脚本不选边" in n for n in out.notes)   # 弱峰命中 ⇒ 不冤枉


def test_waterline_no_peak_is_recorded() -> None:
    median = np.full((40, 60), 170.0)               # 全平：没有任何水平边
    prop = cg.CupProposal(index=0, interior=(10, 5, 30, 55),
                          water_surface_y=10.0,
                          water_surface_basis=cg.WATERLINE_BASIS_WATER_BODY_TOP,
                          water_surface_candidates=())
    out = cg.propose_waterline(median, prop)
    assert out.water_surface_candidates == ()
    assert any("找不到够强的水平边峰" in n for n in out.notes)
    assert out.water_surface_y == 10.0


def test_to_envelope_unconfirmed_by_design() -> None:
    props = [cg.CupProposal(index=0, interior=(74, 17, 106, 93),
                            water_surface_y=74.0,
                            water_surface_basis=cg.WATERLINE_BASIS_WATER_BODY_TOP,
                            water_surface_candidates=(74.0,)),
             cg.CupProposal(index=1, interior=(74, 127, 106, 203),
                            water_surface_y=None,      # 提不出水线：不填大概值
                            water_surface_basis=cg.WATERLINE_BASIS_NONE,
                            water_surface_candidates=())]
    env = cg.to_envelope(props, (220, 120))
    assert env.assay == "FST" and env.confirmed is False
    assert len(env.by_role(geo.ROLE_TANK)) == 2
    assert len(env.by_role(geo.ROLE_WATER_SURFACE)) == 1   # 只有杯 0 有水线
    assert all(not p.confirmed for p in env.primitives)
    problems = env.validate()
    assert any("未确认的几何对象" in p for p in problems)  # 设计如此


def test_proposal_confirmed_flag_is_frozen_false() -> None:
    p = cg.CupProposal(index=0, interior=(0, 0, 10, 10), water_surface_y=None,
                       water_surface_basis=cg.WATERLINE_BASIS_NONE,
                       water_surface_candidates=())
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


def test_multi_cup_envelope_validates_per_instance() -> None:
    """一帧多杯（真实 FST 素材就是 4 杯）：validate 按同号配对，不许炸也不许串。

    回归：几何 validate 旧版用 one(water_surface)，多于一条水线直接
    GeometryError——研究入口在真实 4 杯素材上第一步就崩（DP-136 修复）。
    """
    props = [cg.CupProposal(index=k, interior=(74, 17 + 110 * k, 106, 93 + 110 * k),
                            water_surface_y=74.0,
                            water_surface_basis=cg.WATERLINE_BASIS_WATER_BODY_TOP,
                            water_surface_candidates=(74.0,))
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
    props = [cg.CupProposal(index=0, interior=(74, 17, 106, 93),
                            water_surface_y=74.0,
                            water_surface_basis=cg.WATERLINE_BASIS_WATER_BODY_TOP,
                            water_surface_candidates=(74.0,))]
    env = cg.to_envelope(props, (220, 120))
    env2 = geo.GeometryEnvelope.from_json(env.to_json())
    assert env2.confirmed is False
    assert all(not p.confirmed for p in env2.primitives)
    assert json.loads(env.to_json())["assay"] == "FST"
