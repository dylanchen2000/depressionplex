"""DP-013：LOVO-CV 框架测试（合成真值）。

这里验证的是**管道正确性**：留一是否干净、每折 θ 是否输出、G2 是否可算、
bout 参数是否真的没被拟合。合成数据上的 r / CV 数值本身不是 G2/G7 的证据
（那些要等人工数据接进来的 DP-014 报告），断言因此只钉结构不变量与
宽松的物理合理性，不钉具体统计值。
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from depressionplex.assay_core import bouts, rules
from depressionplex import lovo_cv as L


def _grid():
    return [round(float(x), 4) for x in np.arange(0.005, 0.0501, 0.0025)]


# ---------------------------------------------------------------- 结构不变量


def test_lovo_holds_out_every_video_exactly_once() -> None:
    samples = L.synthetic_lovo_trials(window_s=60.0, fps=10.0)
    assert len(samples) == 27 and len({s.video for s in samples}) == 7
    res = L.lovo_cv(samples, theta_grid=_grid())
    assert len(res.folds) == 7
    assert res.n_predictions == 27
    held = [f.held_out_video for f in res.folds]
    assert sorted(held) == sorted({s.video for s in samples})
    assert len(set(held)) == 7, "同一视频不得被留两次或漏留"
    for f in res.folds:
        assert f.predictions, f"留出视频 {f.held_out_video} 竟然没有预测行"
        for p in f.predictions:
            assert p.video == f.held_out_video, "预测行必须属于留出视频（样本外）"
            assert p.held_out, "预测行必须标记 held_out"
        assert f.n_fit_trials == 27 - len(f.predictions), \
            "训练集 = 全部样本 − 留出视频，一行都不许多"


def test_theta_values_are_the_G2_raw_material() -> None:
    res = L.lovo_cv(L.synthetic_lovo_trials(window_s=60.0, fps=10.0), theta_grid=_grid())
    assert len(res.theta_values) == 7, "每折的 θ 值本身必须输出——G2 的原料"
    th = np.asarray(res.theta_values)
    assert th.min() >= L.THETA_SEARCH_MIN and th.max() <= L.THETA_SEARCH_MAX
    assert 0.012 < np.median(th) < 0.024, "θ 中位数应落在合成标定带附近"
    expect_cv = round(100.0 * th.std(ddof=1) / th.mean(), 2)
    assert res.theta_cv_pct == expect_cv, "G2 = 7 个 θ 的变异系数（ddof=1，×100）"
    assert "G2" in res.summary() and "变异系数" in res.summary(), \
        "派工单：summary 必须直接打印 G2 门槛对照"


def test_only_theta_is_a_search_dimension() -> None:
    """拟合的只有 θ_mob 一个标量：bout 参数影响目标曲线，但从不进搜索维度。"""
    samples = L.synthetic_lovo_trials(window_s=40.0, fps=10.0, seed=7)
    fit = L.fit_theta_mob(samples, grid=_grid())
    names = {f.name for f in dataclasses.fields(L.ThetaFit)}
    assert "bout" not in " ".join(names).lower(), \
        "ThetaFit 不得携带 bout 参数——搜索维度只有 θ 一个标量"
    assert all(c >= 0.0 for c in fit.curve), "MAE 曲线不可能为负"
    alt = bouts.BoutParams(first_combine_limit=1, noise_thresh_frames=1,
                           combination_limit=2, length_thresh_frames=2)
    fit_alt = L.fit_theta_mob(samples, grid=_grid(), bout_params=alt)
    assert fit_alt.grid == fit.grid, "换 bout 参数不得改变 θ 搜索网格"
    assert fit_alt.curve != fit.curve, \
        "bout 参数连目标函数都不影响 ⇒ 它没被用，管道接错了"


def test_single_video_dataset_alarms() -> None:
    samples = [s for s in L.synthetic_lovo_trials(window_s=20.0, fps=10.0, seed=3)
               if s.video == "10mg_2周"]
    try:
        L.lovo_cv(samples)
        assert False, "单视频必须报警：in-sample 拟合冒充 LOVO 是统计欺诈"
    except ValueError as e:
        assert "≥2" in str(e)


def test_denominators_travel_with_predictions() -> None:
    """G9：每条样本外预测自带分母（可评分帧/总帧、unknown 占比）。"""
    res = L.lovo_cv(L.synthetic_lovo_trials(window_s=60.0, fps=10.0), theta_grid=_grid())
    rows = L.prediction_rows(res)
    assert len(rows) == 27
    for row in rows:
        assert row["total_frames"] > 0
        assert row["scoreable_frames"] <= row["total_frames"]
        assert 0.0 <= row["unknown_fraction"] <= 1.0
        assert row["bout_params_frozen"] is True
        assert 0.0 <= row["truth_immobility_s"] <= 60.0
        assert 0.0 <= row["software_immobility_pipeline_s"] <= 60.0
        expect = 1.0 - row["scoreable_frames"] / row["total_frames"]
        assert abs(expect - row["unknown_fraction"]) < 1e-3, \
            "unknown 占比必须与 scoreable 缺的帧数自洽（不许两套账）"
    assert any(r["unknown_fraction"] > 0 for r in rows), \
        "生成器以 ~15% 概率掺 NaN 帧，27 条全干净说明掺帧失效了"


def test_frozen_ship_value_untouched_after_lovo() -> None:
    """框架跑完不许污染出货默认值——θ_mob 的 FROZEN 状态是审计链的一部分。"""
    before = rules.TstRulesParams().theta_mob
    assert before == 0.0175, "shipping 默认值变了？未经拍板不得改 FROZEN"
    L.lovo_cv(L.synthetic_lovo_trials(window_s=30.0, fps=10.0, seed=11),
              theta_grid=_grid())
    assert rules.TstRulesParams().theta_mob == before, "拟合结果被回写进出货参数了"
    assert bouts.CSI_TST_STARTING_POINT == bouts.BoutParams(
        first_combine_limit=5, noise_thresh_frames=5, combination_limit=30,
        length_thresh_frames=30, section_size_seconds=5.0)


def test_truth_cannot_be_software_output_by_construction() -> None:
    """TrialSample 构造时真值越界必须拒收（不 clamp）；truth_source 白名单。"""
    s = L.synthetic_lovo_trials(window_s=10.0, fps=10.0, seed=5)[0]
    try:
        L.TrialSample(trial_id="x", video="v", features=s.features,
                      truth_mobile_s=-3.0, window_s=10.0)
        assert False, "负真值必须拒收，不 clamp"
    except ValueError:
        pass
    try:
        L.TrialSample(trial_id="x", video="v", features=s.features,
                      truth_mobile_s=11.0, window_s=10.0)
        assert False, "真值越窗必须拒收"
    except ValueError:
        pass
    try:
        L.TrialSample(trial_id="x", video="v", features=s.features,
                      truth_mobile_s=1.0, window_s=10.0, truth_source="software")
        assert False, "truth_source='software' = 循环论证，白名单必须挡住"
    except ValueError:
        pass


# ---------------------------------------------------------------- 端到端（真 RAD）


def test_end_to_end_masks_to_theta() -> None:
    """掩膜 → build_tst_features（真 RAD）→ LOVO 的最小管道连通性。

    刻意小：4 试次 × 2 视频 @25 fps。这里只验"接通且行为合理"，不验精度
    ——合成残差的相位回卷会让个别帧漏判；bout 的 length_thresh=30 帧在
    25 fps 下 = 1.2 s，所以激活段取 ≥40 帧，别让管道把它整段磨掉。
    精度证据在 test_rules.py 与 STATUS 的实测基线里。
    """
    from synth import draw_body

    fps = 25.0
    shape = (120, 120)

    def still_block(n):
        return [draw_body(shape, (60, 70), np.pi / 2, 34.0, 14.0)
                for _ in range(n)]

    def active_block(n, amp_deg=55.0):
        out = []
        for i in range(n):
            limb = np.deg2rad(amp_deg) * np.sin(2 * np.pi * 0.8 * i / fps)
            # θ=−π/2：肢体（躯干 +u 端）转向悬挂点一侧 ⇒ 尾侧参与，计 Mobility
            out.append(draw_body(shape, (60, 70), -np.pi / 2, 34.0, 14.0,
                                 limb_angle=limb, limb_len=18.0, limb_width=6.0))
        return out

    samples = []
    # 两视频的静帧数不同：若静帧一样多，immobility 恒等 ⇒ r 真值侧零方差
    for video, n_act, n_still0, n_still1 in (("10mg_2周", 40, 50, 40),
                                             ("30mg_2周", 60, 30, 30)):
        for ch in (1, 2):
            masks = (still_block(n_still0) + active_block(n_act)
                     + still_block(n_still1))
            f = rules.build_tst_features(masks, suspension=(60.0, 5.0), fps=fps)
            window = len(masks) / fps
            samples.append(L.TrialSample(
                trial_id=f"{video}-ch{ch}", video=video, features=f,
                truth_mobile_s=n_act / fps, window_s=window,
                truth_source="synthetic"))
    res = L.lovo_cv(samples, theta_grid=[round(float(x), 4)
                                         for x in np.arange(0.005, 0.0501, 0.005)])
    assert res.n_predictions == 4
    assert all(L.THETA_SEARCH_MIN <= t <= L.THETA_SEARCH_MAX
               for t in res.theta_values)
    rows = L.prediction_rows(res)
    assert len(rows) == 4
    assert all(0.0 <= r["truth_immobility_s"] <= 5.2 + 1e-9 for r in rows)
    # 行为合理性：留出试次上软件至少抓到过真 mobility（管道没接反、没全 0）
    assert any(r["software_mobility_pipeline_s"] > 0 for r in rows), \
        "端到端管道里软件侧 mobility 恒为 0——规则或 bouts 没接上"


def test_determinism_same_input_same_output() -> None:
    a = L.lovo_cv(L.synthetic_lovo_trials(window_s=30.0, fps=10.0, seed=17),
                  theta_grid=_grid())
    b = L.lovo_cv(L.synthetic_lovo_trials(window_s=30.0, fps=10.0, seed=17),
                  theta_grid=_grid())
    assert a.theta_values == b.theta_values
    assert a.pooled_pearson_r == b.pooled_pearson_r
    assert a.ba_bias_s == b.ba_bias_s
    assert a.theta_cv_pct == b.theta_cv_pct


def test_pearson_degenerate_raises() -> None:
    try:
        L._pearson(np.array([1.0, 1.0, 1.0]), np.array([1.0, 2.0, 3.0]))
        assert False, "零方差时 r 无定义——必须报错而不是返回 0"
    except ValueError:
        pass
