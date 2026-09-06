"""trial 级输出串联测试（DP-041）。

钉死的口径：
- 计分窗两范式不许抹平（FST 后 4 min / TST 全程 6 min，窗口表逐位钉死）；
- 每个 immobility 数字旁必须有分母（可评分/总帧、unknown 占比、有效性）；
- 排除态隔间数字不产出，但幻影候选值必须出现在报警里（N1/N3 端到端）；
- 短录像截断必须带警告；窗口完全在录像外直接 raise；
- 秒→帧换算只依赖 fps（同一时间形态换帧率，秒与占比不变）。
"""

from __future__ import annotations

import numpy as np

from depressionplex import human_agreement
from depressionplex.assay_core import trial_report as TR
from depressionplex.assay_core import validity
from depressionplex.assay_core.rules import TstEventLabels

FPS = 10.0
N = 3600          # 360 s 整


def _labels(mob=(), imm=(), unknown=(), n=N):
    """按 (起秒, 止秒) 闭区间列表铺 True。"""
    def fill(spans):
        a = np.zeros(n, dtype=bool)
        for s0, s1 in spans:
            a[int(s0 * FPS):int(s1 * FPS) + 1] = True
        return a
    return TstEventLabels(
        mobility=fill(mob), immobility=fill(imm),
        passive_swing=np.zeros(n, dtype=bool),
        tail_climbing=np.zeros(n, dtype=bool),
        forelimb_only=np.zeros(n, dtype=bool),
        unknown=fill(unknown),
    )


def _cv(status, frac=None):
    return validity.ChamberValidity(
        chamber=4, status=status, max_area=0.0 if status == "never_occupied" else 50.0,
        ref_body_area=188.0, body_threshold=94.0,
        ever_had_body=(status == validity.STATUS_VALID),
        note="测试口径", occupied_fraction=frac)


# ---- 窗口纪律：不许抹平 --------------------------------------------------------

def test_window_table_pinned_and_single_ledger():
    # 逐位钉死：有人把 FST 起点改 0（抹平）即变红
    assert TR.ASSAY_WINDOWS == {"TST": (0.0, 360.0), "FST": (120.0, 360.0)}
    # 与人工侧窗口常量同源（一本账护栏）
    assert TR.ASSAY_WINDOWS["TST"][1] == human_agreement.TST_WINDOW_S


def test_tst_and_fst_never_collapse():
    # 活动全在前 2 min：TST 看得见，FST 看不见——两数必须不同
    lab = _labels(mob=[(0.0, 99.9)])
    t = TR.build_trial_report(lab, fps=FPS, assay="TST", trial_id="t")
    f = TR.build_trial_report(lab, fps=FPS, assay="FST", trial_id="t")
    assert t.window_seconds == 360.0 and f.window_seconds == 240.0
    assert t.immobility_mirror_pipeline_s == 260.0   # 360 − 100 s 活动
    assert f.immobility_mirror_pipeline_s == 240.0   # 窗口内零活动
    assert f.immobility_mirror_pipeline_s != t.immobility_mirror_pipeline_s
    assert f.categories["Mobility"].bouts == 0       # 前 2 min 完全不进 FST 账


def test_unknown_assay_raises():
    for bad in ("fst", "TST ", "", "OF", None):
        try:
            TR.build_trial_report(_labels(), fps=FPS, assay=bad, trial_id="t")
        except ValueError:
            continue
        raise AssertionError(f"assay={bad!r} 应 raise 而未 raise（禁止静默默认）")


# ---- 分母 ----------------------------------------------------------------------

def test_denominators_exact():
    # 窗口内 unknown 20 帧 + 窗外（360–400 s 段不存在，用尾部 2 s）：
    # 录像 360 s 全在窗内 ⇒ 用窗口内 10 帧 + 制造 4000 帧录像把 unknown 放窗外
    n2 = 4000
    lab = _labels(mob=[(10.0, 59.9)], unknown=[(300.0, 300.9), (380.0, 380.9)], n=n2)
    t = TR.build_trial_report(lab, fps=FPS, assay="TST", trial_id="d")
    assert t.window_frames == 3600
    assert t.unknown_frames_window == 10          # 380 s 那个在窗尾之外，不进窗口账
    assert t.scorable_frames_window == 3590
    assert t.unknown_frames_recording == 20       # 全录像账
    assert abs(t.unknown_fraction_window - 10 / 3600) < 1e-12
    assert abs(t.unknown_fraction_recording - 20 / 4000) < 1e-12
    cs = t.categories["Mobility"]
    assert cs.frames == 500
    assert cs.pct_of_window == 100.0 * 500 / 3600
    assert cs.pct_of_scorable == 100.0 * 500 / 3590   # 两口径并列、不静默挑一


def test_first_onset_relative_to_window():
    lab = _labels(mob=[(150.0, 159.9)])
    t = TR.build_trial_report(lab, fps=FPS, assay="TST", trial_id="o")
    f = TR.build_trial_report(lab, fps=FPS, assay="FST", trial_id="o")
    assert t.categories["Mobility"].first_onset_s == 150.0
    assert f.categories["Mobility"].first_onset_s == 30.0   # 相对窗口起点 120 s


def test_seconds_invariant_to_fps():
    # 同一时间形态 10→20 fps：秒与占比逐位不变（换算只走 fps，无帧常数）
    lab10 = _labels(mob=[(10.0, 109.9)], unknown=[(200.0, 201.9)])
    # 同一时间形态按 20 fps 铺：[10s,110s) 与 [200s,202s) 半开区间，与 10fps 逐帧等秒
    a20 = np.zeros(7200, dtype=bool); a20[10 * 20:110 * 20] = True
    u20 = np.zeros(7200, dtype=bool); u20[200 * 20:202 * 20] = True
    lab20 = TstEventLabels(mobility=a20, immobility=np.zeros(7200, bool),
                           passive_swing=np.zeros(7200, bool),
                           tail_climbing=np.zeros(7200, bool),
                           forelimb_only=np.zeros(7200, bool), unknown=u20)
    t10 = TR.build_trial_report(lab10, fps=10.0, assay="TST", trial_id="s")
    t20 = TR.build_trial_report(lab20, fps=20.0, assay="TST", trial_id="s")
    assert t10.immobility_mirror_pipeline_s == t20.immobility_mirror_pipeline_s
    assert t10.immobility_mirror_raw_s == t20.immobility_mirror_raw_s
    assert t10.unknown_fraction_window == t20.unknown_fraction_window
    assert (t10.scorable_frames_window / t10.fps
            == t20.scorable_frames_window / t20.fps)   # 可评分秒数不变
    assert t10.categories["Mobility"].bouts == t20.categories["Mobility"].bouts


def test_mismatched_series_lengths_rejected():
    lab = _labels(mob=[(10.0, 20.0)])
    lab = TstEventLabels(mobility=lab.mobility,
                         immobility=np.zeros(N - 5, dtype=bool),
                         passive_swing=lab.passive_swing,
                         tail_climbing=lab.tail_climbing,
                         forelimb_only=lab.forelimb_only,
                         unknown=lab.unknown)
    try:
        TR.build_trial_report(lab, fps=FPS, assay="TST", trial_id="x")
        raise AssertionError("长度不齐应 raise")
    except ValueError as e:
        assert "两套账" in str(e) or "不同源" in str(e)


# ---- 有效性闸门（N1/N3 端到端） ------------------------------------------------

def test_excluded_chamber_produces_no_number_but_alarms():
    # 空场隔间：流水线必然把全窗判成 immobility=360 s——幻影必须响，数字必须拦
    lab = _labels()
    t = TR.build_trial_report(lab, fps=FPS, assay="TST", trial_id="v4ch4",
                              chamber_validity=_cv(validity.STATUS_NEVER_OCCUPIED, 0.0))
    assert not t.scored
    assert t.categories == {}
    assert t.immobility_mirror_pipeline_s is None
    assert any("PHANTOM-IMMOBILITY" in m for m in t.gate_messages)
    assert any("360.0" in m for m in t.gate_messages)     # 候选值随报警入账
    # 分母仍在（拦截时更要看得见）
    assert t.scorable_frames_window == 3600
    txt = TR.trial_report_text(t)
    assert "不产出" in txt and "PHANTOM-IMMOBILITY" in txt
    assert "主口径 = 窗口" not in txt                      # 不渲染正常数字行


def test_detached_blocks_with_occupied_fraction():
    t = TR.build_trial_report(_labels(), fps=FPS, assay="TST", trial_id="x",
                              chamber_validity=_cv(validity.STATUS_DETACHED, 0.42))
    assert not t.scored and t.occupied_fraction == 0.42
    assert any("在场帧占比 0.42" in m for m in t.gate_messages)


def test_valid_chamber_passes_cleanly():
    t = TR.build_trial_report(_labels(mob=[(5.0, 60.0)]), fps=FPS, assay="TST",
                              trial_id="ok", chamber_validity=_cv(validity.STATUS_VALID, 1.0))
    assert t.scored and t.gate_messages == () and t.validity_status == "valid"
    # 活动段 [5.0, 60.0] 闭秒铺帧 = 551 帧 = 55.1 s ⇒ 镜像 360 − 55.1
    assert abs(t.immobility_mirror_pipeline_s - 304.9) < 1e-9


def test_missing_validity_warns_not_silent():
    t = TR.build_trial_report(_labels(mob=[(5.0, 60.0)]), fps=FPS, assay="TST", trial_id="w")
    assert t.validity_status == "not_assessed" and t.scored
    assert any("validity_not_assessed" in w for w in t.warnings)
    assert "不是放行凭据" in TR.trial_report_text(t)


# ---- 短录像 / 边界 ---------------------------------------------------------------

def test_short_recording_clips_with_warning():
    lab = _labels(mob=[(130.0, 159.9)], n=3000)   # 300 s 录像，FST 窗 [120,360)
    f = TR.build_trial_report(lab, fps=FPS, assay="FST", trial_id="short")
    assert f.window_frames == 1800                 # 截断到实际
    assert f.window_end_s == 360.0                 # 名义值保留在报告里
    assert any("recording_shorter_than_window" in w for w in f.warnings)
    assert f.immobility_mirror_pipeline_s == 180.0 - 30.0
    assert "截断" in TR.trial_report_text(f)


def test_recording_before_window_raises():
    lab = _labels(n=600)                           # 60 s 录像，FST 窗从 120 s 起
    try:
        TR.build_trial_report(lab, fps=FPS, assay="FST", trial_id="early")
        raise AssertionError("窗口完全在录像外应 raise")
    except ValueError as e:
        assert "人工" in str(e)


def test_bad_fps_raises():
    for bad in (0.0, -5.0):
        try:
            TR.build_trial_report(_labels(), fps=bad, assay="TST", trial_id="f")
            raise AssertionError(f"fps={bad} 应 raise")
        except ValueError:
            pass


# ---- 渲染器：分母永远在数字旁 ------------------------------------------------------

def test_renderer_always_pairs_numbers_with_denominators():
    for assay in ("TST", "FST"):
        txt = TR.trial_report_text(
            TR.build_trial_report(_labels(mob=[(200.0, 260.0)]), fps=FPS,
                                  assay=assay, trial_id="r",
                                  chamber_validity=_cv(validity.STATUS_VALID, 0.9)))
        imm_lines = [ln for ln in txt.splitlines() if ln.startswith("Immobility")]
        assert len(imm_lines) == 1 and "占可评分" in imm_lines[0]
        assert any(ln.startswith("分母：可评分帧") for ln in txt.splitlines())
        assert any(ln.startswith("有效性：valid") for ln in txt.splitlines())
        if assay == "FST":
            assert "后 4 min" in txt and "前 2 min 适应不计" in txt
        else:
            assert "全程 6 min" in txt
