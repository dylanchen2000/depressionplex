"""DP-013：LOVO-CV 框架测试（合成真值）。

这里验证的是**管道正确性**：留一是否干净、每折 θ 是否输出、G2 是否可算、
bout 参数是否真的没被拟合。合成数据上的 r / CV 数值本身不是 G2/G7 的证据
（那些要等人工数据接进来的 DP-014 报告），断言因此只钉结构不变量与
宽松的物理合理性，不钉具体统计值。
"""

from __future__ import annotations

import dataclasses
import math
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


# ---------------------------------------------------------------- DP-037 目标函数


def test_default_objective_is_total_immobility_untouched() -> None:
    """默认口径 = total_immobility，加 onset 后一个字都没变（口径断裂护栏）。"""
    assert L.OBJECTIVE_TOTAL == "total_immobility"
    samples = L.synthetic_lovo_trials(window_s=30.0, fps=10.0, seed=23)
    implicit = L.lovo_cv(samples, theta_grid=_grid())
    explicit = L.lovo_cv(samples, theta_grid=_grid(), objective=L.OBJECTIVE_TOTAL)
    assert implicit.theta_values == explicit.theta_values
    assert implicit.pooled_pearson_r == explicit.pooled_pearson_r
    assert implicit.objective == L.OBJECTIVE_TOTAL
    assert "total_immobility" in implicit.summary()
    fit = L.fit_theta_mob(samples, grid=_grid())
    assert fit.objective == L.OBJECTIVE_TOTAL, "ThetaFit 必须记录用的哪个目标"
    try:
        L.lovo_cv(samples, objective="software_output")   # 未知目标必须拒
        assert False, "未知目标函数不许静默接受"
    except ValueError:
        pass


def test_onset_target_requires_truth_segs_no_silent_fallback() -> None:
    """onset 缺 truth_mobile_segs ⇒ 报错，绝不静默退化成总量差目标。"""
    s = L.synthetic_lovo_trials(window_s=10.0, fps=10.0, seed=5)[0]
    bare = dataclasses.replace(s, truth_mobile_segs=None)
    try:
        L.fit_theta_mob([bare], grid=_grid(), objective=L.OBJECTIVE_ONSET)
        assert False, "缺真值段的 onset 目标未定义——必须拒跑"
    except ValueError as e:
        assert "truth_mobile_segs" in str(e)


def test_onset_match_error_semantics() -> None:
    # 完美对齐 ⇒ 0
    assert L.onset_match_error([(1.0, 3.0)], [(1.0, 3.0)]) == 0.0
    # 起始差被量到；**结束差被无视**（这正是目标：人只在起始侧可靠）
    assert abs(L.onset_match_error([(1.0, 3.0)], [(2.0, 3.0)]) - 1.0) < 1e-12
    w = [(0.0, 10.0), (20.0, 22.0)]
    c = [(1.0, 12.0), (21.0, 30.0)]   # 两对起始差都是 1，结束差 −2/−8 各不同
    assert abs(L.onset_match_error(w, c) - 1.0) < 1e-12
    # 互为最佳（不是一对多）：长软件段只配它的最佳真值段，别的不计入
    err = L.onset_match_error([(0.0, 10.0)], [(1.0, 4.0), (6.0, 20.0)])
    assert abs(err - 6.0) < 1e-12, "一对多匹配会把未最佳段也平均进来（834% 虚高教训）"
    # 无配对 ⇒ inf（最坏行为必须被看见），不是 NaN
    assert math.isinf(L.onset_match_error([(0.0, 1.0)], [(5.0, 6.0)]))
    assert math.isinf(L.onset_match_error([], [(5.0, 6.0)]))
    # 真值无运动：软件也无 ⇒ 0（判对不罚）；软件凭空判出 ⇒ inf（幻影更糟）
    assert L.onset_match_error([], []) == 0.0
    assert math.isinf(L.onset_match_error([(1.0, 2.0)], []))


def test_truth_segs_validation() -> None:
    base = L.synthetic_lovo_trials(window_s=10.0, fps=10.0, seed=5)[0]
    for bad, why in (
        (((0.0, 0.0),), "零长段"),
        (((5.0, 3.0),), "负长段"),
        (((2.0, 4.0), (1.0, 2.5)), "重叠/未排序"),
        (((0.0, 10.5),), "越窗"),
    ):
        try:
            dataclasses.replace(base, truth_mobile_segs=bad)
            assert False, f"{why}的真值段必须拒收"
        except ValueError:
            pass


def test_lovo_onset_runs_and_reports() -> None:
    samples = L.synthetic_lovo_trials(window_s=30.0, fps=10.0, seed=29)
    assert all(s.truth_mobile_segs is not None for s in samples), \
        "合成生成器必须同时产出真值段（onset 目标的原料）"
    res = L.lovo_cv(samples, theta_grid=_grid(), objective=L.OBJECTIVE_ONSET)
    assert len(res.theta_values) == 7
    assert all(L.THETA_SEARCH_MIN <= t <= L.THETA_SEARCH_MAX
               for t in res.theta_values), "onset 目标的 θ 也必须在声明网格内"
    assert res.objective == L.OBJECTIVE_ONSET
    assert "onset_match" in res.summary()
    th = np.asarray(res.theta_values)
    assert res.theta_cv_pct == round(100.0 * th.std(ddof=1) / th.mean(), 2), \
        "onset 组也带自己的 G2，口径与 total 组一致"


# ---------------------------------------------------------------- DP-035 G11 门


def test_jaccard_segs_semantics() -> None:
    # 完全一致 ⇒ 1；完全不相交 ⇒ 0；部分重叠按**时长加权**（与人工-人工基线同式）
    assert L.jaccard_segs([(1.0, 5.0)], [(1.0, 5.0)]) == 1.0
    assert L.jaccard_segs([(0.0, 4.0)], [(10.0, 14.0)]) == 0.0
    j = L.jaccard_segs([(0.0, 10.0)], [(5.0, 15.0)])
    assert abs(j - 5.0 / 15.0) < 1e-12
    # 一侧全空 ⇒ 0（幻影/漏判都要被看见）；双方皆空 ⇒ 1.0（零分歧，判对不罚）
    assert L.jaccard_segs([], [(1.0, 2.0)]) == 0.0
    assert L.jaccard_segs([(1.0, 2.0)], []) == 0.0
    assert L.jaccard_segs([], []) == 1.0
    # 多段并集口径：交集复用 scorer_disagreement 的归并扫描。
    # A=[0,4]+[6,8] 总 6；B=[2,7] 总 5；交 (2,4)=2+(6,7)=1=3；并 = 6+5−3 = 8 ⇒ 3/8
    assert abs(L.jaccard_segs([(0.0, 4.0), (6.0, 8.0)], [(2.0, 7.0)])
               - 3.0 / 8.0) < 1e-12


def test_truth_segs_and_total_must_share_one_ledger() -> None:
    """段合计 ≠ truth_mobile_s ⇒ 两套账，拒收（G11 与总量不许各说各话）。"""
    base = L.synthetic_lovo_trials(window_s=10.0, fps=10.0, seed=5)[0]
    try:
        dataclasses.replace(base, truth_mobile_s=1.0,
                            truth_mobile_segs=((0.0, 6.0),))
        assert False, "段合计 6 s 而总量 1 s——必须拒收"
    except ValueError as e:
        assert "两套账" in str(e)


def test_g11_travels_in_summary_and_rows() -> None:
    samples = L.synthetic_lovo_trials(window_s=30.0, fps=10.0, seed=41)
    res = L.lovo_cv(samples, theta_grid=_grid())
    assert res.g11_mean_jaccard is not None, "合成生成器带真值段，G11 必须可算"
    assert 0.0 <= res.g11_mean_jaccard <= 1.0
    txt = res.summary()
    for needle in ("G11", "0.738", "G7", "G8", "任一不过即不过", "拖累项"):
        assert needle in txt, f"summary 缺 {needle!r}——G7/G8/G11 必须同报告"
    # 逐试次升序：前一行数值 ≤ 后一行
    listed = [float(l.split()[0]) for l in txt.splitlines()
              if l.startswith("      ") and "." in l.split()[0]]
    assert len(listed) == res.n_predictions == 27
    assert listed == sorted(listed)
    rows = L.prediction_rows(res)
    assert all(0.0 <= r["g11_jaccard"] <= 1.0 for r in rows)
    assert abs(sum(r["g11_jaccard"] for r in rows) / len(rows)
               - res.g11_mean_jaccard) < 1e-3


def test_g11_both_empty_trials_excluded_from_denominator() -> None:
    """DP-045：双方皆空的试次剔出 G11 分母——不记 1.0（白送分）也不记 0（冤枉）。

    真实来源：`20mg_3周-ch4` 是**真的空隔间**，徐乐彤与张咸明独立都给 0。
    人工侧那条被 DP-012 拒收、G11 基线按 n=13 算；软件侧若把同一条记 1.0，
    两边就不是一套账了（DP-035 明令禁止），而且靠"隔间是空的"把 G11 抬到
    门槛上等于造假通过。
    """
    samples = L.synthetic_lovo_trials(window_s=30.0, fps=10.0, seed=41)
    res = L.lovo_cv(samples, theta_grid=_grid())
    preds = [p for f in res.folds for p in f.predictions]
    base = L.g11_mean(preds)
    assert base is not None and abs(base - res.g11_mean_jaccard) < 1e-12

    # 掺入一个双方皆空试次（Jaccard=1.0）：不打标记会把均值抬高，打标记后不动
    empty_unflagged = dataclasses.replace(preds[0], jaccard_vs_truth=1.0,
                                          jaccard_both_empty=False)
    empty_flagged = dataclasses.replace(empty_unflagged, jaccard_both_empty=True)
    assert L.g11_mean(preds + [empty_unflagged]) > base, "对照：不剔就是会被抬高"
    assert abs(L.g11_mean(preds + [empty_flagged]) - base) < 1e-12

    # 全是双方皆空 ⇒ 算不出，不是 1.0 满分通过
    assert L.g11_mean([empty_flagged, empty_flagged]) is None

    # 标记由 evaluate 自己打，不靠调用方记得传
    assert all(p.jaccard_both_empty is False for p in preds), \
        "合成样本都有运动段，不该有双方皆空"

    # 分母必须写进报告（DP-045：永远报分母）
    txt = L.LovoResult(
        folds=res.folds, bout_params=res.bout_params,
        theta_values=res.theta_values, theta_cv_pct=res.theta_cv_pct,
        n_predictions=res.n_predictions, pooled_pearson_r=res.pooled_pearson_r,
        ba_bias_s=res.ba_bias_s, ba_loa_low_s=res.ba_loa_low_s,
        ba_loa_high_s=res.ba_loa_high_s, truth_sources=res.truth_sources,
        objective=res.objective, g11_mean_jaccard=res.g11_mean_jaccard,
    ).summary()
    assert "分母 n=" in txt


def test_g11_missing_segs_is_not_silently_passing() -> None:
    """缺真值段 ⇒ G11 算不出 ⇒ 捆绑判定按不过处理，且报告写明。"""
    samples = L.synthetic_lovo_trials(window_s=30.0, fps=10.0, seed=43)
    bare = [dataclasses.replace(s, truth_mobile_segs=None) for s in samples]
    res = L.lovo_cv(bare, theta_grid=_grid())          # total 目标不要求段，能跑
    assert res.g11_mean_jaccard is None
    txt = res.summary()
    assert "算不出" in txt and "不报≠过" in txt
    assert "**不过**" in txt                            # 捆绑判定不许绿
    rows = L.prediction_rows(res)
    assert all(r["g11_jaccard"] is None for r in rows)


def test_gate_bundle_flags_synthetic_as_pipeline_only() -> None:
    """合成真值上的捆绑判定必须自带口径声明——管道演示不得对外作验收证据。"""
    res = L.lovo_cv(L.synthetic_lovo_trials(window_s=30.0, fps=10.0, seed=47),
                    theta_grid=_grid())
    txt = res.summary()
    assert "管道演示" in txt and "DP-014" in txt
    # 门槛常量写死且来源是人工侧（纪律护栏：改这三个数需要 SPEC 依据，不需要新代码）
    assert L.G7_MIN_R == 0.818 and L.G8_MAX_BIAS_S == 28.6
    assert L.G11_MIN_JACCARD == 0.738


def test_objective_comparison_prints_both_G2s() -> None:
    samples = L.synthetic_lovo_trials(window_s=30.0, fps=10.0, seed=31)
    comp = L.lovo_cv_objective_comparison(samples, theta_grid=_grid())
    assert set(comp) == set(L.OBJECTIVES)
    direct = L.lovo_cv(samples, theta_grid=_grid())
    assert comp[L.OBJECTIVE_TOTAL].theta_values == direct.theta_values, \
        "对照里的 total 组必须与单独跑 total 完全一致（同函数同参，不是第二套实现）"
    txt = L.format_objective_comparison(comp)
    for needle in (L.OBJECTIVE_TOTAL, L.OBJECTIVE_ONSET, "G2", "Δmean", "判读"):
        assert needle in txt
    assert "θ=" in txt
    # 再跑一遍必须逐位一致（确定性）
    assert L.format_objective_comparison(
        L.lovo_cv_objective_comparison(samples, theta_grid=_grid())) == txt
