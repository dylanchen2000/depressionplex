"""CLI 退出码契约：未获准出数不得返回成功（DP-053）。

约定（`cli/analyze.py` 顶部 docstring）：
  0 = 至少一个隔间 `scored=True`（含合法 0 秒不动）
  2 = 一个都没获准出数（含 reports 空、或仅有 scored=False 拦截行）
  1 = 解码/探测失败

历史缺陷：成功判据写成 `if not reports`——字典里全是拦截报告时仍返回 0。
本模块用可控报告驱动 `main`，不改科学阈值与真值数据。
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest import mock

from depressionplex import runner as R
from depressionplex import video as V
from depressionplex.assay_core import trial_report, validity
from depressionplex.cli import analyze as A


def _info() -> V.VideoInfo:
    return V.VideoInfo(
        path=Path("/tmp/合成-exit-contract.mp4"), fps=10.0, n_frames=60,
        width=400, height=268, frame_count_source="packets")


def _plan(*chamber_indices: int) -> R.TrialPlan:
    chambers = tuple(
        R.ChamberPlan(index=i, col_range=((i - 1) * 100, i * 100 - 1),
                      corridor=None, suspension=(50.0, 10.0),
                      source="exit-contract-fixture")
        for i in chamber_indices)
    cvs = tuple(
        validity.ChamberValidity(
            chamber=i, status="valid", max_area=100.0, ref_body_area=90.0,
            body_threshold=45.0, ever_had_body=True, note="",
            occupied_fraction=1.0, unsegmentable_fraction=0.0)
        for i in chamber_indices)
    return R.TrialPlan(
        chambers=chambers,
        trial_validity=validity.TrialValidity(chambers=cvs),
        calib_indices=(0, 10, 20),
        warnings=())


def _report(*, chamber: int, scored: bool,
            immobility_s: float | None = 12.0) -> trial_report.TrialReport:
    """最小 TrialReport。`scored=False` 时数字必须是 None（与真闸门一致）。"""
    if scored:
        assert immobility_s is not None
        cats: dict[str, trial_report.CategoryStat] = {}
        if immobility_s == 0.0:
            # 合法零值：窗口内全是活动 ⇒ 镜像不动 0；仍须获准出数
            cats["Mobility"] = trial_report.CategoryStat(
                name="Mobility", bouts=1, frames=60,
                seconds_pipeline=6.0, seconds_raw=6.0,
                pct_of_window=100.0, pct_of_scorable=100.0,
                first_onset_s=0.0, segments_s=((0.0, 6.0),))
        return trial_report.TrialReport(
            trial_id=f"合成-ch{chamber}", assay="TST", fps=10.0,
            recording_frames=60, window_start_s=0.0, window_end_s=6.0,
            window_frames=60, unknown_frames_window=0, scorable_frames_window=60,
            unknown_frames_recording=0, validity_status="valid",
            occupied_fraction=1.0, scored=True,
            immobility_mirror_pipeline_s=immobility_s,
            immobility_mirror_raw_s=immobility_s,
            categories=cats, gate_messages=())
    return trial_report.TrialReport(
        trial_id=f"合成-ch{chamber}", assay="TST", fps=10.0,
        recording_frames=60, window_start_s=0.0, window_end_s=6.0,
        window_frames=60, unknown_frames_window=0, scorable_frames_window=60,
        unknown_frames_recording=0, validity_status="never_occupied",
        occupied_fraction=0.0, scored=False,
        immobility_mirror_pipeline_s=None, immobility_mirror_raw_s=None,
        categories={}, gate_messages=("排除态不放行计分",))


def _run_main(reports: dict[int, trial_report.TrialReport],
              skipped: dict[int, str] | None = None) -> int:
    """绕过真实解码：ffmpeg 解析与 analyze_video 全部替掉，只测退出码判据。"""
    info = _info()
    plan = _plan(*sorted(set(reports) | set(skipped or {})) or (1,))
    skipped = skipped or {}

    def fake_analyze(path, **kwargs):
        return info, plan, reports, skipped

    with mock.patch.object(V, "_resolve_ffmpeg_tool",
                           return_value=("/fake/ffmpeg", "test")), \
         mock.patch.object(R, "analyze_video", side_effect=fake_analyze), \
         mock.patch("sys.stdout", new_callable=io.StringIO), \
         mock.patch("sys.stderr", new_callable=io.StringIO):
        return A.main(["/tmp/合成-exit-contract.mp4", "--assay", "TST"])


def test_all_blocked_reports_exit_two_not_zero() -> None:
    """未获准出数：reports 非空但全部 scored=False ⇒ 退出码 2，不许假成功。"""
    reports = {
        1: _report(chamber=1, scored=False),
        2: _report(chamber=2, scored=False),
    }
    assert _run_main(reports) == 2


def test_partial_emit_exits_zero() -> None:
    """部分出数：有的放行、有的拦截 ⇒ 至少一个获准 ⇒ 退出码 0。"""
    reports = {
        1: _report(chamber=1, scored=True, immobility_s=18.5),
        2: _report(chamber=2, scored=False),
    }
    assert _run_main(reports) == 0


def test_legitimate_zero_immobility_exits_zero() -> None:
    """合法零值：scored=True 且不动秒数 = 0 ⇒ 仍是成功读数，退出码 0。

    若有人把成功判据写成「秒数真假值」或 `immobility_s or …`，本条必须红。
    """
    reports = {1: _report(chamber=1, scored=True, immobility_s=0.0)}
    assert reports[1].scored is True
    assert reports[1].immobility_mirror_pipeline_s == 0.0
    assert _run_main(reports) == 0


def test_empty_reports_exit_two() -> None:
    """一个报告都没有（全进 skipped）⇒ 退出码 2。"""
    assert _run_main({}, skipped={1: "没有帧（帧源为空）"}) == 2
