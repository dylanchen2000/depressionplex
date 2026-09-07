"""驱动层测试：合成 4 隔间画面 → 逐隔间 trial 数字。

**为什么能不带视频素材测**：`analyze_frames` 以下全是纯 numpy，`analyze_video`
只负责把文件解成帧。整条判定链（标定 → 分割 → 有效性 → 特征 → 事件 → 报告）
在这里被真的跑了一遍，不是 mock。

场景约定与 `tests/test_segment.py` 一致（268 高、隔间 ROI 95 宽、顶框 70 行、
胶带列 44–49），四隔间之间夹结构立柱（整幅高度都暗）——`find_chambers` 靠
"暗行占比"区分立柱与胶带。
"""

from __future__ import annotations

import numpy as np

from depressionplex import runner as R
from depressionplex.assay_core import rad, validity
from depressionplex.maskseq import PackedMasks

H = 268
W_CH = 95
W_PILLAR = 4
N_CH = 4
FPS = 10.0

MOVE, STILL, EMPTY = "move", "still", "empty"


def _chamber(kind: str, i: int) -> np.ndarray:
    """一个隔间 ROI。胶带列 44–49 触顶（走廊标定靠它）。

    `MOVE` 隔间**不能只平移**：平移是刚体运动，RAD 按设计把它当被动摆动扣掉
    （这正是 RAD 存在的理由——钟摆式晃动不是挣扎）。要触发 Mobility 判据必须
    真的形变，且形变要落在**近悬挂点那一侧**（`rho_hind ≥ θ_hind = 0.30`，
    对应金标准"仅前肢活动不计"）。所以这里让上端逐帧伸缩，列上再叠一点平移
    ——平移只为让走廊标定的暗频率法能把动物与静止胶带区分开。
    """
    g = np.full((H, W_CH), 250.0)
    g[0:70, :] = 10.0                        # 顶框
    g[H - 9:, :] = 10.0                      # 底框
    g[70:150, 44:50] = 15.0                  # 竖直胶带
    if kind == EMPTY:
        return g                             # 没有动物：分割会失败（不是"面积 0"）
    if kind == MOVE:
        top = 150 if i % 2 == 0 else 158     # 上端伸缩 = 近悬挂点侧的形变
        col = 36 + 3 * (i % 6)
        g[top:190, col:col + 12] = 20.0
    else:                                    # STILL：一动不动 ⇒ 应判 immobility
        g[152:182, 18:30] = 20.0             # 列避开胶带，免得静态动物被标成走廊
    return g


def _frame(kinds: tuple[str, ...], i: int) -> np.ndarray:
    """整幅画面：立柱 | 隔间 | 立柱 | … | 立柱。"""
    w = N_CH * W_CH + (N_CH + 1) * W_PILLAR
    g = np.full((H, w), 10.0)                # 底色 = 立柱（整幅高度都暗）
    for k, kind in enumerate(kinds):
        c0 = W_PILLAR + k * (W_CH + W_PILLAR)
        g[:, c0:c0 + W_CH] = _chamber(kind, i)
    return g


def _frames(kinds: tuple[str, ...], n: int) -> list[np.ndarray]:
    return [_frame(kinds, i) for i in range(n)]


KINDS4 = (MOVE, STILL, MOVE, STILL)


# ---- 标定抽帧 ---------------------------------------------------------------


def test_calibration_indices_span_whole_recording() -> None:
    """全片均匀抽，**不是开头连续 n 帧**：开头动物几乎不动，走廊标定会把
    动物当成静态胶带（暗频率法的前提是"动物在动、胶带不动"）。"""
    idx = R.calibration_indices(9000, n=24)
    assert len(idx) == 24
    assert idx[0] == 0 and idx[-1] == 8999
    assert idx == sorted(set(idx))
    gaps = {b - a for a, b in zip(idx, idx[1:])}
    assert max(gaps) - min(gaps) <= 1, f"抽帧不均匀：{sorted(gaps)}"


def test_calibration_indices_short_and_degenerate() -> None:
    assert R.calibration_indices(3, n=24) == [0, 1, 2]
    assert R.calibration_indices(1, n=24) == [0]
    for bad in (0, -5):
        try:
            R.calibration_indices(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"帧数 {bad} 应被拒绝")


# ---- 计划（几何标定 + 有效性） ------------------------------------------------


def test_build_plan_needs_two_calibration_frames() -> None:
    try:
        R.build_plan(_frames(KINDS4, 1))
    except ValueError as e:
        assert "标定帧" in str(e)
    else:
        raise AssertionError("单帧标定必须 raise")


def test_build_plan_locates_four_chambers_with_corridors() -> None:
    plan = R.build_plan(_frames(KINDS4, 8))
    assert len(plan.chambers) == N_CH, [c.col_range for c in plan.chambers]
    assert [c.index for c in plan.chambers] == [1, 2, 3, 4]
    for ch in plan.chambers:
        assert 90 <= ch.width <= 97, (ch.index, ch.width)
        assert ch.corridor is not None, f"ch{ch.index} 走廊标定失败"
        assert ch.suspension is not None
        assert "非人工确认几何" in ch.source, "几何量必须自带来源标签"
    # 列区间左起递增，且与切片 chN 同序（DP-042：编号错位会把两只鼠的数字对调）
    starts = [c.col_range[0] for c in plan.chambers]
    assert starts == sorted(starts)


def test_suspension_is_corridor_top_not_frame_centre() -> None:
    """悬挂点 = 胶带顶端 × 走廊列中心。**估不出就是 None，不许拿画面中心凑**：
    猜出来的悬挂点会让 `rho_hind`（尾侧残差占比，金标准"仅前肢不计"的判据）
    随机取到头侧，报告照样漂亮但判据已经失效。"""
    plan = R.build_plan(_frames(KINDS4, 8))
    ch = plan.chambers[0]
    x, y = ch.suspension
    assert abs(x - 46.5) <= 2.0, x
    assert y <= 75.0, f"悬挂点 y={y} 不在胶带顶端（顶框 70 行）"
    assert R._suspension_from_corridor(None)[0] is None
    assert "不猜" in R._suspension_from_corridor(None)[1]


def test_chamber_count_mismatch_warns_and_keeps_measured_count() -> None:
    """立柱只分出 3 个 ⇒ 报警并**按实测的 3 个继续**，绝不等分成 4 份。
    等分四列与真实立柱位置不是一回事（DP-042 实测）。"""
    kinds = (MOVE, STILL, MOVE)
    frames = []
    for i in range(8):
        w = 3 * W_CH + 4 * W_PILLAR
        g = np.full((H, w), 10.0)
        for k, kind in enumerate(kinds):
            c0 = W_PILLAR + k * (W_CH + W_PILLAR)
            g[:, c0:c0 + W_CH] = _chamber(kind, i)
        frames.append(g)
    plan = R.build_plan(frames, n_chambers=4)
    assert len(plan.chambers) == 3
    assert any("chamber_count_mismatch" in w for w in plan.warnings), plan.warnings
    assert any("不凑数不等分切" in w for w in plan.warnings)


def test_empty_chamber_is_unknown_not_never_occupied() -> None:
    """DP-032 端到端复核：一个隔间从头到尾分割不出来 ⇒ `unknown`，
    **永不** `never_occupied`。`None` 是"这一帧没看成"，不是"这里没有动物"——
    旧代码把两者混成一条路，于是在从未成功观测的情况下断言了"从来没有动物"，
    `30mg 2周` 隔间 4 那只鼠就这样被静默丢掉。"""
    plan = R.build_plan(_frames((MOVE, EMPTY, MOVE, STILL), 8))
    by = {cv.chamber: cv for cv in plan.trial_validity.chambers}
    cv = by[2]
    assert cv.status != validity.STATUS_NEVER_OCCUPIED, (
        "空隔间不得被断言'从来没有动物'——本数据集上该结论不可测")
    assert cv.status == validity.STATUS_UNKNOWN, cv
    assert cv.unsegmentable_fraction == 1.0, cv
    assert by[1].status == validity.STATUS_VALID, by[1]


# ---- 掩膜序列 ---------------------------------------------------------------


def test_segment_series_single_pass_fills_every_chamber() -> None:
    """一次顺序扫帧同时喂满所有隔间：每隔间各扫一遍要解码 N 次，
    6 min 素材就是 N 倍解码时间。"""
    frames = _frames(KINDS4, 6)
    plan = R.build_plan(frames)
    seqs = R.segment_series(frames, plan)
    assert set(seqs) == {1, 2, 3, 4}
    for k, seq in seqs.items():
        assert isinstance(seq, PackedMasks)
        assert len(seq) == 6, (k, len(seq))
        assert seq.shape[0] == H


def test_empty_chamber_masks_are_missing_not_zero() -> None:
    frames = _frames((MOVE, EMPTY, MOVE, STILL), 6)
    plan = R.build_plan(frames)
    seqs = R.segment_series(frames, plan)
    assert seqs[2].n_missing == 6, "空隔间应逐帧记'没看见'"
    assert all(a is None for a in seqs[2].areas())
    assert seqs[1].n_missing == 0


# ---- 全链 ------------------------------------------------------------------


#: 全链跑一次要解 4 个隔间 × N 帧的 RAD，测试之间共享结果。
_CACHE: dict[tuple, tuple] = {}

#: 窗口必须长于 `bouts.BoutParams.length_thresh_frames`（=30，**CSI 同名参数，
#: 按帧不按秒**），否则任何 bout 都会被流水线丢掉、Mobility 恒为 0——
#: 第一版测试用 24 帧，四个隔间的 immobility 全等于整窗，看着像"判据没接上"，
#: 其实是窗口比最短 bout 还短。60 帧 @10 fps = 6 s，留够余量。
N_FRAMES = 60


def _run(kinds: tuple[str, ...] = KINDS4, n: int = N_FRAMES, assay: str = "TST"):
    key = (kinds, n, assay)
    if key not in _CACHE:
        frames = _frames(kinds, n)
        _CACHE[key] = R.analyze_frames(frames[:8], frames, fps=FPS, assay=assay,
                                       trial_prefix="合成")
    return _CACHE[key]


def test_analyze_frames_produces_numbers_per_chamber() -> None:
    """这条就是"给视频能不能出数据"的回归：四个隔间各出一份带分母的 trial 报告。"""
    plan, reports, skipped = _run()
    assert set(reports) == {1, 2, 3, 4}, (sorted(reports), skipped)
    for k, r in reports.items():
        assert r.trial_id == f"合成-ch{k}"
        assert r.assay == "TST"
        assert r.fps == FPS
        assert r.recording_frames == N_FRAMES
        assert r.window_frames == N_FRAMES, "窗口比录像长 ⇒ 截断并报警，不是报错"
        assert any("truncat" in w or "截断" in w or "短" in w for w in r.warnings), r.warnings
        # 分母必须在：没有分母的 immobility 不可审计（GLP + 对 CSI 的差异化）
        assert r.scorable_frames_window + r.unknown_frames_window == r.window_frames
        assert r.validity_status
        assert "Mobility" in r.categories


def test_still_animal_scores_more_immobility_than_moving_one() -> None:
    """一动不动的隔间 immobility 必须显著高于持续移动的隔间。

    这不是"跑通了"而已——它检验 θ_mob（FROZEN 0.0175）这条链真的在按残差判定，
    而不是无论输入都吐同一个数。
    """
    plan, reports, skipped = _run()
    assert not skipped, skipped
    still = [reports[k].immobility_mirror_pipeline_s for k in (2, 4)]
    moving = [reports[k].immobility_mirror_pipeline_s for k in (1, 3)]
    assert all(v is not None for v in still + moving), (still, moving)
    assert min(still) > max(moving), (
        f"静止隔间 {still} 未高于移动隔间 {moving}——判据没在起作用")


def test_unscorable_chamber_yields_no_number_but_others_still_do() -> None:
    """一个隔间跑不出来不该让另外三个也没结果；但跳过的原因必须显式带回。

    空隔间是 `unknown`（非排除态），`score_gate` 仍会拦住它出数字——
    绝不能因为"看起来像 360 s 不动"就产出最强抑郁表型（DP-052）。
    """
    plan, reports, skipped = _run(kinds=(MOVE, EMPTY, MOVE, STILL))
    assert 1 in reports and 3 in reports and 4 in reports, (sorted(reports), skipped)
    if 2 in reports:
        r = reports[2]
        assert not r.scored, "空隔间不得被放行计分"
        assert r.immobility_mirror_pipeline_s is None, "未放行必须留空，不许填 0"
        assert r.gate_messages
    else:
        assert skipped[2]


def test_fst_window_is_not_tst_window() -> None:
    """两范式计分窗口不许抹平：TST 全程 6 min、FST 后 4 min。

    同一批帧在 TST 下出数字，在 FST 下**一个数字都不许出**——FST 窗口起点
    120 s 落在这段合成录像之外。悄悄改用全程会把两个范式的数字混成一套账。
    """
    _, tst_reports, _ = _run(assay="TST")
    assert tst_reports
    _, reports, skipped = _run(assay="FST")
    assert not reports, "FST 窗口在录像外，不得产出任何数字"
    assert set(skipped) == {1, 2, 3, 4}
    assert all(("窗口" in s or "120" in s) for s in skipped.values()), skipped


def test_analyze_chamber_refuses_without_suspension() -> None:
    frames = _frames(KINDS4, 6)
    plan = R.build_plan(frames)
    seqs = R.segment_series(frames, plan)
    ch = plan.chambers[0]
    broken = R.ChamberPlan(index=ch.index, col_range=ch.col_range,
                           corridor=ch.corridor, suspension=None,
                           source="走廊标定失败 ⇒ 悬挂点不可估（不猜）")
    try:
        R.analyze_chamber(seqs[1], broken, None, fps=FPS, assay="TST",
                          trial_id="合成-ch1")
    except ValueError as e:
        assert "悬挂点" in str(e) and "拒绝产出数字" in str(e)
    else:
        raise AssertionError("悬挂点缺失时不许产出数字")


def test_analyze_chamber_computes_trial_bl_once() -> None:
    """试次体长**只许算一次**，且算出来的那一个就是喂给残差归一化的那一个。

    修之前 `analyze_chamber` 辛苦算出的 `trial_body_length` 只喂了爬尾巴，
    真正做归一化的分母在 `decompose_series` 里**又算了一遍**（每帧一次
    `sil.metrics`，9000 帧的录像就是白跑一遍）。调用计数是这件事唯一
    不靠肉眼的证据：>1 就说明分母又退回自己算了，注入被绕过。
    """
    frames = _frames(KINDS4, 6)
    plan = R.build_plan(frames)
    seqs = R.segment_series(frames, plan)
    ch = plan.chambers[0]
    assert ch.suspension is not None

    real = rad.trial_body_length
    calls = []

    def counting(masks):                      # noqa: ANN001, ANN202
        calls.append(1)
        return real(masks)

    rad.trial_body_length = counting          # type: ignore[assignment]
    try:
        rep = R.analyze_chamber(seqs[ch.index], ch, None, fps=FPS,
                               assay="TST", trial_id="合成-ch1")
    finally:
        rad.trial_body_length = real           # type: ignore[assignment]

    assert len(calls) == 1, "试次体长被算了 %d 次，注入没生效" % len(calls)
    assert rep.trial_id == "合成-ch1"


# ------------------------------------------------------- DP-073-R 归一化分母口径


def test_bl_denominator_per_trial_keeps_each_chamber_its_own() -> None:
    """冻结口径：每个隔间用自己的 BL；算不出的给 `None` 而不是 0（DP-032）。"""
    got = R.bl_denominator({1: 30.0, 2: 40.0, 3: 0.0}, "per_trial")
    assert got == {1: 30.0, 2: 40.0, 3: None}


def test_bl_denominator_recording_median_ignores_unusable_chambers() -> None:
    """同录像中位：**只由算得出 BL 的隔间决定**。

    一个空隔间或分割全崩的隔间（BL=0）不该把另外三个的分母带偏——它带的不是
    "这只动物比较短"的信息，它压根没有信息。
    """
    got = R.bl_denominator({1: 26.0, 2: 30.0, 3: 49.0, 4: 0.0}, "recording_median")
    assert set(got.values()) == {30.0}, got
    assert sorted(got) == [1, 2, 3, 4], "算不出 BL 的隔间也要拿到分母（它的掩膜还在）"
    # 偶数个可用 ⇒ 取中间两个的平均，别静默偏向某一侧
    assert R.bl_denominator({1: 20.0, 2: 30.0}, "recording_median")[1] == 25.0


def test_bl_denominator_all_unusable_gives_none_not_a_substitute() -> None:
    """一个录像里没有任何隔间算出 BL ⇒ 全给 `None`。

    "没有分母"是事实，不许拿 0、拿 `corridor.bl_est`、或拿别的录像的数顶上
    （DP-032 + DP-058）。
    """
    assert R.bl_denominator({1: 0.0, 2: -1.0}, "recording_median") == {1: None, 2: None}


def test_bl_denominator_rejects_undeclared_mode() -> None:
    """口径必须是白名单里的一个，默认值也必须在白名单里。"""
    assert R.DEFAULT_BL_NORM_MODE in R.BL_NORM_MODES
    try:
        R.bl_denominator({1: 30.0}, "cohort_median_but_not_implemented")
    except ValueError as exc:
        assert "bl_norm_mode" in str(exc)
    else:
        raise AssertionError("没声明过的口径没有被挡下")


def test_recording_median_mode_gives_every_chamber_the_same_denominator() -> None:
    """接线检查：口径确实一路传到了 `analyze_chamber` 的分母参数上。

    这条测的是"传到了"，不是"传得对"——分母取得对不对由 `bl_denominator` 的
    几条纯函数测试负责。分成两层是因为端到端跑一遍看不出分母是哪来的。
    """
    frames = _frames(KINDS4, 6)
    plan = R.build_plan(frames)
    seqs = R.segment_series(frames, plan)
    real = R.analyze_chamber
    seen: dict[str, dict[int, float | None]] = {}

    def spy(masks, ch, cv, **kw):             # noqa: ANN001, ANN003, ANN202
        seen[tag][ch.index] = kw.get("bl_norm")
        return real(masks, ch, cv, **kw)

    R.analyze_chamber = spy                   # type: ignore[assignment]
    try:
        for tag, mode in (("median", "recording_median"), ("per", "per_trial")):
            seen[tag] = {}
            R._reports(plan, seqs, fps=FPS, assay="TST", prefix="合成",
                       bl_norm_mode=mode)
    finally:
        R.analyze_chamber = real              # type: ignore[assignment]

    assert len(seen["median"]) >= 2, seen
    assert len(set(seen["median"].values())) == 1, (
        "recording_median 下各隔间分母不同：%r" % seen["median"])
    assert all(v is not None for v in seen["per"].values()), seen["per"]


def test_external_bl_norm_actually_reaches_the_residual() -> None:
    """调用方直接给的分母必须真的改变残差尺度，不能被静默忽略。

    残差 = 变化像素 / 分母² ⇒ 把分母放大 30 倍，残差缩小约 900 倍 ⇒ 全部落到
    θ_mob（FROZEN 0.0175）以下 ⇒ 连持续形变的隔间也会被判成不动。
    **这条不是在主张该这么用**，它是在证明这个参数不是装饰。
    """
    frames = _frames(KINDS4, N_FRAMES)
    base = R.analyze_frames(frames[:8], frames, fps=FPS, assay="TST",
                            trial_prefix="合成")[1]
    huge = R.analyze_frames(frames[:8], frames, fps=FPS, assay="TST",
                            trial_prefix="合成", bl_norm=1000.0)[1]
    for k in (1, 3):                          # MOVE 隔间
        a = base[k].immobility_mirror_pipeline_s
        b = huge[k].immobility_mirror_pipeline_s
        assert a is not None and b is not None, (k, a, b)
        assert b > a, "隔间 %d：分母放大后 immobility 没升（%r → %r）" % (k, a, b)

