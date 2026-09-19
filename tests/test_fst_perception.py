"""逐杯分割 + 可见性四态的纪律测试（Spec A §5.1 / §6.2）。

三个真会犯的错，每个一组测试钉住：
1. **短暂丢失 ≠ 空杯**：短缺失归 lost_short，长缺失归 unclear 并写
   long_absence_unexplained——任何"没找到动物"都不许自动变空杯；
2. **静态动物不许被背景吸收**：整段不动的动物在逐像素 max 背景里也是暗的，
   必须报 possible_static_animal_absorbed（unclear），不是"杯里没东西"；
3. **空杯只认人工申报**：declared_absent 的帧原因是"结果为空，不输出 0 秒"。
"""

from __future__ import annotations

import numpy as np

from depressionplex.fst_research import cup_geometry as cg
from depressionplex.fst_research import cup_perception as perc
from depressionplex.fst_research import record as rec

import fst_synth


def _props(scene: fst_synth.Scene, frames) -> list:
    props, problems = cg.propose_cups(frames, n_cups=scene.cups)
    assert not problems, problems
    return props


def test_synth_scene_proposal_matches_construction() -> None:
    """夹具自检：提案的水体区/ROI/水线必须等于构造参数（标签合成，不是真值）。"""
    scene = fst_synth.Scene(cups=2)
    frames = scene.swim_series(12, cup=0)
    props = _props(scene, frames)
    assert len(props) == 2
    for p, cup in zip(props, scene.cup_defs):
        # 水体块 = water_y..r1 行、c0-1..c1+1 列（base() 的画水范围）
        assert p.water_body == (cup.water_y, cup.c0 - 1, cup.r1, cup.c1 + 1)
        # 分析 ROI 顶边在水线上方（G1）
        assert p.roi[0] < p.water_body[0]
        assert p.roi[1:] == p.water_body[1:]
        assert p.water_surface_y == float(cup.water_y)
        assert p.confirmed is False


def test_all_frames_exactly_one_quality_partition_closes() -> None:
    scene = fst_synth.Scene(cups=2)
    frames = scene.swim_series(20, cup=0)
    props = _props(scene, frames)
    for p in props:
        diags = perc.diagnose_cup(frames, list(range(len(frames))), p)
        assert len(diags) == len(frames)
        assert all(d.quality in perc.QUALITIES for d in diags)
        assert rec.check_partition(rec.counts_of(diags), len(diags)) == []


def test_swimming_cup_is_observed() -> None:
    scene = fst_synth.Scene(cups=2)
    frames = scene.swim_series(20, cup=0)
    p = _props(scene, frames)[0]
    diags = perc.diagnose_cup(frames, list(range(20)), p)
    assert all(d.quality == perc.QUALITY_OBSERVED for d in diags)
    d0 = diags[0]
    assert d0.area_px and d0.area_px > 0
    assert d0.centroid is not None and d0.bbox is not None
    assert d0.wall_dist_px is not None and d0.wall_dist_px >= 0
    # 动物在水线下方游 ⇒ 水上面积比例应为 0（水线=构造的 water_y）
    assert d0.above_water_frac == 0.0


def test_empty_cup_frames_are_unclear_long_absence_never_empty() -> None:
    """没动物的杯子：所有帧 unclear，原因写 long_absence_unexplained，不写空杯。"""
    scene = fst_synth.Scene(cups=2)
    frames = scene.swim_series(20, cup=0)      # 只有杯 0 有动物
    p1 = _props(scene, frames)[1]
    diags = perc.diagnose_cup(frames, list(range(20)), p1)
    assert all(d.quality == perc.QUALITY_UNCLEAR for d in diags)
    joined = " ".join(r for d in diags for r in d.reasons)
    assert "long_absence_unexplained" in joined
    assert "不作空杯处理" in joined


def test_short_gap_between_observed_is_lost_short() -> None:
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(20, cup=0, absent=(8, 9, 10))
    p = _props(scene, frames)[0]
    diags = perc.diagnose_cup(frames, list(range(20)), p)
    qualities = [d.quality for d in diags]
    assert qualities[8:11] == [perc.QUALITY_LOST_SHORT] * 3
    assert qualities[:8] == [perc.QUALITY_OBSERVED] * 8
    assert qualities[11:] == [perc.QUALITY_OBSERVED] * 9
    assert any("短暂分割失败" in r for d in diags[8:11] for r in d.reasons)


def test_leading_gap_not_lost_short() -> None:
    """序列开头的缺失没有"前文 observed"，不许归 lost_short。"""
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(20, cup=0, absent=(0, 1))
    p = _props(scene, frames)[0]
    diags = perc.diagnose_cup(frames, list(range(20)), p)
    assert diags[0].quality == perc.QUALITY_UNCLEAR
    assert diags[1].quality == perc.QUALITY_UNCLEAR
    assert diags[2].quality == perc.QUALITY_OBSERVED


def test_long_gap_exceeding_max_is_unclear() -> None:
    scene = fst_synth.Scene(cups=1)
    # R2-115 T2：门是源帧跨度——absent 6..17，前后 observed 在 5 和 18，
    # 跨度 18−5=13 > DEFAULT_LOST_SHORT_MAX_GAP_FRAMES=11 ⇒ unclear
    absent = tuple(range(6, 18))
    frames = scene.swim_series(20, cup=0, absent=absent)
    p = _props(scene, frames)[0]
    diags = perc.diagnose_cup(frames, list(range(20)), p)
    assert all(d.quality == perc.QUALITY_UNCLEAR for d in diags[6:18])
    assert all(d.quality == perc.QUALITY_OBSERVED
               for d in diags[:6] + diags[18:])


def test_static_animal_absorbed_is_flagged_not_empty() -> None:
    """整段不动的动物被背景 max 吸收 ⇒ 必须报 absorbed，绝不报空杯。"""
    scene = fst_synth.Scene(cups=1)
    # 动物固定在同一位置：背景模型会把它当静态结构
    frames = [scene.frame([(scene.cup_defs[0].cx, scene.cup_defs[0].water_y + 10)])
              for _ in range(10)]
    p = _props(scene, frames)[0]
    diags = perc.diagnose_cup(frames, list(range(10)), p, min_area=30)
    joined = " ".join(r for d in diags for r in d.reasons)
    assert "possible_static_animal_absorbed" in joined
    assert not any(d.quality == perc.QUALITY_DECLARED_ABSENT for d in diags)
    assert "不作空杯处理" in joined


def test_declared_absent_skips_segmentation_entirely() -> None:
    scene = fst_synth.Scene(cups=2)
    frames = scene.swim_series(10, cup=0)
    p1 = _props(scene, frames)[1]
    d = perc.CupDiagnoser(p1, declared_absent=True)
    # 连背景都没喂：申报空杯不跑分割，不需要背景模型
    diags = [d.diagnose(i, f) for i, f in enumerate(frames)]
    diags = d.finish()
    assert all(x.quality == perc.QUALITY_DECLARED_ABSENT for x in diags)
    assert all("不输出 0 秒" in r for x in diags for r in x.reasons)


def test_diagnose_without_background_raises() -> None:
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(5, cup=0)
    p = _props(scene, frames)[0]
    d = perc.CupDiagnoser(p)
    try:
        d.diagnose(0, frames[0])
    except RuntimeError as e:
        assert "背景模型" in str(e)
    else:
        raise AssertionError("没喂标定帧就诊断，居然不报错")


def test_low_contrast_is_image_quality_problem() -> None:
    """对比不足 ⇒ unclear 且原因写明是采集问题，不是行为问题。"""
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(5, cup=0)
    p = _props(scene, frames)[0]
    d = perc.CupDiagnoser(p, dark=170.0)       # 暗阈抬到接近水体灰阶
    for f in frames:
        d.see_background(f)
    diag = d.diagnose(0, frames[0])
    assert diag.quality == perc.QUALITY_UNCLEAR
    assert any("采集对比度问题" in r for r in diag.reasons)


def test_rival_component_is_unclear() -> None:
    """两个差不多大的暗连通域 ⇒ 分不清哪个是动物 ⇒ unclear。"""
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(12, cup=0)
    p = _props(scene, frames)[0]
    d = perc.CupDiagnoser(p, min_area=20)
    for f in frames:
        d.see_background(f)
    # 造一帧：真动物 + 一个同尺寸的假暗斑（模拟水面反光阴影）
    g = frames[0].copy()
    r0, c0, r1, c1 = p.water_body
    rival = np.zeros_like(g, dtype=bool)
    rival[r0 + 8:r0 + 16, c0 + 40:c0 + 52] = True
    g[rival] = fst_synth.ANIMAL_GRAY
    diag = d.diagnose(0, g)
    assert diag.quality == perc.QUALITY_UNCLEAR
    assert any("竞争连通域" in r for r in diag.reasons)


def test_oversized_blob_is_unclear() -> None:
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(8, cup=0)
    p = _props(scene, frames)[0]
    d = perc.CupDiagnoser(p)
    for f in frames:
        d.see_background(f)
    g = frames[0].copy()
    r0, c0, r1, c1 = p.water_body
    g[r0 + 1:r1, c0 + 1:c1] = fst_synth.ANIMAL_GRAY   # 几乎整个水体区变暗
    diag = d.diagnose(0, g)
    assert diag.quality == perc.QUALITY_UNCLEAR
    assert any("水面反光" in r or "波纹连片" in r for r in diag.reasons)


def test_touch_wall_helper_distinguishes_unobserved() -> None:
    d_obs = perc.FrameDiag(0, perc.QUALITY_OBSERVED, (), wall_dist_px=1.0)
    d_far = perc.FrameDiag(1, perc.QUALITY_OBSERVED, (), wall_dist_px=20.0)
    d_unc = perc.FrameDiag(2, perc.QUALITY_UNCLEAR, ("x",))
    assert perc.touch_wall(d_obs) is True
    assert perc.touch_wall(d_far) is False
    assert perc.touch_wall(d_unc) is None      # 观测无效 ≠ 没触壁


def test_on_observed_callback_fires_only_for_observed() -> None:
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(10, cup=0, absent=(3,))
    p = _props(scene, frames)[0]
    seen: list = []
    # R2-115 T2：回调第 4 参 = 距上一条 observed 的非 observed 记录条数
    d = perc.CupDiagnoser(p, on_observed=lambda i, m, diag, gap: seen.append((i, gap)))
    for f in frames:
        d.see_background(f)
    for i, f in enumerate(frames):
        d.diagnose(i, f)
    d.finish()
    assert [i for i, _ in seen] == [0, 1, 2, 4, 5, 6, 7, 8, 9]
    gaps = dict(seen)
    assert gaps[4] == 1                    # 帧 3 缺失 ⇒ 帧 4 的配对跨 1 条缺口记录
    assert all(gaps[i] == 0 for i in gaps if i != 4)


def test_streaming_and_batch_agree() -> None:
    """CLI 流式喂与测试整段喂共用状态机 ⇒ 结果必须一致。"""
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(16, cup=0, absent=(5, 6))
    p = _props(scene, frames)[0]
    batch = perc.diagnose_cup(frames, list(range(16)), p)
    d = perc.CupDiagnoser(p)
    for f in frames:
        d.see_background(f)
    stream = [d.diagnose(i, f) for i, f in enumerate(frames)]
    stream = d.finish()
    assert [x.quality for x in batch] == [x.quality for x in stream]
    assert [x.frame for x in batch] == [x.frame for x in stream]


# ---------------------------------------------------------------------------
# R2-115 G1：ROI 含线上留白 ⇒ above_water_frac 能 >0；水线不可靠 ⇒ null + 原因
# ---------------------------------------------------------------------------

def test_above_water_frac_positive_when_mask_crosses_waterline() -> None:
    """G1：剪影跨越已知水线 ⇒ 水上比例必须 >0（旧行为 ROI 顶=水线会恒为假 0）。"""
    scene = fst_synth.Scene(cups=1)
    c = scene.cup_defs[0]
    wy = c.water_y
    # 动物中心压在水线上左右游：剪影跨水线，上半在水上、下半在水下
    frames = [scene.frame([(c.c0 + 14 + (i * 3) % (c.c1 - c.c0 - 28), float(wy))])
              for i in range(14)]
    p = _props(scene, frames)[0]
    assert p.water_surface_reliable is True          # 合成边强，旁证命中
    diags = perc.diagnose_cup(frames, list(range(len(frames))), p)
    obs = [d for d in diags if d.quality == perc.QUALITY_OBSERVED]
    assert obs, "应有 observed 帧"
    assert any(d.above_water_frac is not None and d.above_water_frac > 0.0 for d in obs)
    assert all(d.above_water_null_reason is None for d in obs)


def test_forelimb_activity_above_waterline_is_captured() -> None:
    """G1：身体几乎不动（被背景吸收），只有前肢在水线上扑腾——ROI 含线上留白，
    这段水上活动必须被看见且 above_water_frac>0；旧行为（ROI 顶=水线）会裁掉恒 0。"""
    scene = fst_synth.Scene(cups=1)
    c = scene.cup_defs[0]
    wy = c.water_y
    frames = []
    for i in range(15):
        g = scene.base()
        scene.add_animal(g, c, c.cx, wy + 6, rx=7, ry=5)        # 身体：恒定位，会被吸收
        fx = c.cx + (i % 3 - 1) * 12                            # 前肢：左/中/右不重叠
        scene.add_animal(g, c, fx, wy - 6, rx=4, ry=4)          # 前肢：水线上方
        frames.append(g)
    p = _props(scene, frames)[0]
    assert p.roi[0] < wy                                        # ROI 顶在水线上方
    diags = perc.diagnose_cup(frames, list(range(15)), p)
    obs = [d for d in diags if d.quality == perc.QUALITY_OBSERVED]
    assert obs, "前肢活动应被看见（observed）"
    assert any(d.above_water_frac is not None and d.above_water_frac > 0.0 for d in obs)


def test_unreliable_waterline_gives_null_above_water_with_reason() -> None:
    """G1：水线不可靠（无旁证/旁证分歧）⇒ above_water_frac=null + 原因，不填假 0。"""
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(12, cup=0)
    p_good = _props(scene, frames)[0]
    p_bad = cg.CupProposal(
        index=p_good.index, roi=p_good.roi, water_body=p_good.water_body,
        water_surface_y=p_good.water_surface_y,
        water_surface_basis=p_good.water_surface_basis,
        water_surface_candidates=p_good.water_surface_candidates,
        water_surface_reliable=False,
        water_surface_unreliable_reason="行梯度旁证分歧（测试）")
    diags = perc.diagnose_cup(frames, list(range(12)), p_bad)
    obs = [d for d in diags if d.quality == perc.QUALITY_OBSERVED]
    assert obs
    for d in obs:
        assert d.above_water_frac is None               # 不填假 0
        assert d.above_water_null_reason and "不可靠" in d.above_water_null_reason


def test_none_waterline_gives_null_above_water_with_reason() -> None:
    """G1：水线为 None（提不出）⇒ above_water_frac=null + 原因，不填假 0。"""
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(12, cup=0)
    p_good = _props(scene, frames)[0]
    p_none = cg.CupProposal(
        index=0, roi=p_good.roi, water_body=p_good.water_body,
        water_surface_y=None, water_surface_basis=cg.WATERLINE_BASIS_NONE,
        water_surface_candidates=(), water_surface_reliable=False,
        water_surface_unreliable_reason="水线为 None")
    diags = perc.diagnose_cup(frames, list(range(12)), p_none)
    obs = [d for d in diags if d.quality == perc.QUALITY_OBSERVED]
    assert obs
    for d in obs:
        assert d.above_water_frac is None
        assert d.above_water_null_reason and "None" in d.above_water_null_reason


# ---------------------------------------------------------------------------
# R2-115 T2：lost_short 门长在**源帧跨度**上，分类不随抽样步长漂移
# ---------------------------------------------------------------------------

def _run_sampled(scene, absent, n=60, step=1, fps=25.0, gate=None):
    frames = scene.swim_series(n, cup=0, absent=absent)
    idx = list(range(0, n, step))
    sub = frames[::step]
    props, problems = cg.propose_cups(sub, n_cups=1)
    assert not problems, problems
    kw = dict(fps=fps)
    if gate is not None:
        kw["lost_short_max_gap_frames"] = gate
    return perc.diagnose_cup(sub, idx, props[0], **kw), idx


def test_short_loss_consistent_across_sampling_steps() -> None:
    """同一段 4 源帧的短暂丢失：step=1 与 step=5 都归 lost_short。

    旧版按记录条数判：这条在旧版也一致，但它钉住新门不会把短丢失错杀。
    """
    scene = fst_synth.Scene(cups=1)
    absent = tuple(range(20, 24))
    d1, _ = _run_sampled(scene, absent, step=1)
    miss1 = [d.quality for d in d1 if d.frame in absent]
    assert miss1 == [perc.QUALITY_LOST_SHORT] * 4
    d5, idx5 = _run_sampled(scene, absent, step=5)
    hit = [i for i in idx5 if i in absent]          # 只有样点 20 落在缺失里
    assert hit == [20]
    q5 = [d.quality for d in d5 if d.frame == 20]
    assert q5 == [perc.QUALITY_LOST_SHORT]           # 跨度 25−15=10 ≤ 11


def test_long_loss_consistent_across_sampling_steps() -> None:
    """同一段 12 源帧的长丢失：step=1/5 都归 unclear，不随步长漂成 lost_short。"""
    scene = fst_synth.Scene(cups=1)
    absent = tuple(range(20, 32))
    d1, _ = _run_sampled(scene, absent, step=1)
    assert all(d.quality == perc.QUALITY_UNCLEAR for d in d1 if d.frame in absent)
    d5, _ = _run_sampled(scene, absent, step=5)
    hit = {d.frame: d.quality for d in d5 if d.frame in absent}
    assert hit == {20: perc.QUALITY_UNCLEAR, 25: perc.QUALITY_UNCLEAR,
                   30: perc.QUALITY_UNCLEAR}         # 跨度 35−15=20 > 11


def test_same_record_count_different_span_classifies_differently() -> None:
    """评审复现：同是 10 条缺失**记录**，物理时长不同 ⇒ 分类必须不同。

    旧版门按记录条数（j-i）：两条都是 10 条记录，全归 lost_short——
    step=5 那条约 2 秒的丢失被当成"分割抖了一下"。新门按源帧跨度：
    step=1 跨度 11 ≤ 11 ⇒ lost_short；step=5 跨度 55 ⇒ unclear。
    """
    scene = fst_synth.Scene(cups=1)
    d1, _ = _run_sampled(scene, tuple(range(20, 30)), n=40, step=1)
    q1 = [d.quality for d in d1 if 20 <= d.frame < 30]
    assert q1 == [perc.QUALITY_LOST_SHORT] * 10      # 跨度 30−19=11 ≤ 11
    d5, _ = _run_sampled(scene, tuple(range(20, 70)), n=80, step=5)
    q5 = [d.quality for d in d5 if 20 <= d.frame < 70]
    assert len(q5) == 10                             # 恰好 10 条缺失记录
    assert q5 == [perc.QUALITY_UNCLEAR] * 10         # 跨度 70−15=55 > 11


def test_reason_carries_span_and_gate_provenance() -> None:
    """原因字符串必须写明实测跨度（含秒）与门的参数名/取值——出处可追溯。"""
    scene = fst_synth.Scene(cups=1)
    d1, _ = _run_sampled(scene, tuple(range(20, 30)), n=40, step=1, fps=25.0)
    lost = [d for d in d1 if d.quality == perc.QUALITY_LOST_SHORT][0]
    joined = " ".join(lost.reasons)
    assert "源帧跨度 11" in joined and "≈0.44 s" in joined
    assert "lost_short_max_gap_frames=11" in joined
    dl, _ = _run_sampled(scene, tuple(range(20, 32)), step=1, fps=25.0)
    unc = [d for d in dl if d.quality == perc.QUALITY_UNCLEAR
           and d.frame in range(20, 32)][0]
    joined2 = " ".join(unc.reasons)
    assert "源帧跨度 13" in joined2 and "lost_short_max_gap_frames=11" in joined2
    assert "long_absence_unexplained" in joined2


def test_gate_value_is_configurable_and_recorded_in_reason() -> None:
    """门是研究配置：改了以后原因字符串里的取值跟着变（不许写死 11）。"""
    scene = fst_synth.Scene(cups=1)
    absent = tuple(range(20, 32))                    # 跨度 13
    d, _ = _run_sampled(scene, absent, step=1, gate=20)
    assert all(x.quality == perc.QUALITY_LOST_SHORT for x in d if x.frame in absent)
    assert any("lost_short_max_gap_frames=20" in r for x in d
               if x.frame in absent for r in x.reasons)


def test_wall_dist_excludes_top_edge() -> None:
    """G1：ROI 顶边是水面之上的观察留白、不是物理杯壁 ⇒ 触壁距离只量左/右/底。"""
    scene = fst_synth.Scene(cups=1)
    c = scene.cup_defs[0]
    # 动物在水线上方留白里、靠近 ROI 顶但不贴边；左右小幅移动避免被背景吸收
    frames = [scene.frame([(c.cx + (i % 3 - 1) * 4, float(c.water_y - 12))])
              for i in range(10)]
    p = _props(scene, frames)[0]
    diags = perc.diagnose_cup(frames, list(range(10)), p)
    obs = [d for d in diags if d.quality == perc.QUALITY_OBSERVED]
    assert obs
    r0, c0, r1, c1 = p.roi
    for d in obs:
        br0, bc0, br1, bc1 = d.bbox
        expected = min(bc0 - c0, c1 - bc1, r1 - br1)   # 左/右/底，不含顶
        assert d.wall_dist_px == float(expected)
        # 动物靠近顶边（br0-r0 小），但顶不计入 ⇒ wall_dist 明显大于到顶距离
        assert d.wall_dist_px > br0 - r0
