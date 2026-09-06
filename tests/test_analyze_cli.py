"""`cli/analyze.py` 的输出格式化测试。

**为什么单开一支**：第一次拿真素材跑，整条链（9000 帧分割 + 报告）全跑完了，
却死在打印那一行——`ChamberValidity.reason` 不存在（真名是 `note`）。
格式化代码碰的全是别的模块的字段名，没有测试就等于没有护栏，而它的失败
时机最坏：所有算力都花完了才炸。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from depressionplex import runner as R, video as V
from depressionplex.cli import analyze as A
from test_runner import EMPTY, FPS, KINDS4, MOVE, N_FRAMES, STILL, _frame, _run


def _fixture(kinds: tuple[str, ...] = KINDS4):
    """复用 `test_runner._run` 的缓存——全链一次要算 4 隔间 × 60 帧的 RAD，
    两个测试模块各跑一遍纯属浪费。"""
    plan, reports, skipped = _run(kinds)
    h, w = _frame(kinds, 0).shape
    info = V.VideoInfo(path=Path("/tmp/合成.mp4"), fps=FPS, n_frames=N_FRAMES,
                       width=w, height=h, frame_count_source="packets")
    return info, plan, reports, skipped


def test_plan_text_touches_every_field_it_prints() -> None:
    """真正跑一遍格式化——字段名写错在这里就红，不用等 9000 帧算完。"""
    info, plan, _, _ = _fixture()
    txt = A._plan_text(info, plan)
    assert "合成.mp4" in txt
    assert "读自文件" in txt, "fps 必须标明来源（不猜纪律）"
    assert "packets" in txt, "帧数来源要能追溯（容器头 vs 逐包计数）"
    assert "非人工确认几何" in txt, "几何量必须自带来源标签"
    assert "有效性 ch1" in txt and "不可分割帧占比" in txt
    for ch in plan.chambers:
        assert f"ch{ch.index}" in txt


def test_plan_text_reports_warnings_and_failed_calibration() -> None:
    """标定失败与隔间数不符必须印出来，不许只在数据结构里躺着。"""
    info, plan, _, _ = _fixture(kinds=(MOVE, EMPTY, MOVE, STILL))
    txt = A._plan_text(info, plan)
    assert "有效性 ch2" in txt
    broken = R.TrialPlan(
        chambers=(R.ChamberPlan(index=1, col_range=(0, 94), corridor=None,
                                suspension=None, source="走廊标定失败"),),
        trial_validity=plan.trial_validity,
        calib_indices=plan.calib_indices,
        warnings=("chamber1_band_unsealed：走廊带底未收口",))
    t2 = A._plan_text(info, broken)
    assert "走廊未标定" in t2
    assert "band_unsealed" in t2 and "[警告]" in t2


def test_csv_row_covers_all_fields() -> None:
    info, plan, reports, skipped = _fixture()
    assert reports, skipped
    row = A._row(reports[1])
    assert set(row) == set(A.CSV_FIELDS), (
        set(A.CSV_FIELDS) ^ set(row))
    assert row["trial_id"] == "合成-ch1"
    # 分母必须在同一行里——没有分母的 immobility 不可审计
    assert row["window_frames"] == N_FRAMES
    assert isinstance(row["scorable_frames"], int)
    assert row["immobility_s"] != ""


def test_csv_leaves_blocked_numbers_blank_not_zero() -> None:
    """未放行计分 ⇒ 留空。**填 0 会被下游读成"一秒都没不动"**，
    而这正好是最强抑郁表型的反面——两个方向的假信号都不许造。"""
    info, plan, reports, _ = _fixture()
    blocked = dataclasses.replace(
        reports[1], scored=False, categories={},
        immobility_mirror_pipeline_s=None, immobility_mirror_raw_s=None,
        gate_messages=("排除态不放行计分",))
    row = A._row(blocked)
    assert row["immobility_s"] == ""
    assert row["immobility_raw_s"] == ""
    assert row["mobility_s"] == "" and row["mobility_bouts"] == ""
    assert row["scored"] is False
    assert "不放行" in row["gate_messages"]


def test_assay_choices_are_the_two_paradigms() -> None:
    """`--assay` 只认 TST/FST，且不给默认值——默认值会让人忘了两范式窗口不同。"""
    from depressionplex.assay_core import trial_report
    assert sorted(trial_report.ASSAY_WINDOWS) == ["FST", "TST"]
    try:
        A.main(["/tmp/不存在.mp4"])
    except SystemExit as e:
        assert e.code == 2, "缺 --assay 必须被 argparse 拒绝"
    else:
        raise AssertionError("--assay 必须是必填项")


def test_missing_video_exits_one_not_zero() -> None:
    """素材不存在 ⇒ 退出码 1。静默返回 0 会让批处理脚本以为这一段跑过了。"""
    assert A.main(["/tmp/绝对不存在的素材-dp053.mp4", "--assay", "TST"]) == 1
