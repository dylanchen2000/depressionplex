"""测试进度回调与 run.json（DP-099 / B3a）。

验收判据（五条，每条都要能独立失败）：
1. 同一批合成帧，progress=None 与 progress=<收集器> 跑出的报告逐位相同
2. 收集器收到的最后一次调用的第一个参数 == 帧数
3. 回调抛异常时异常会往上抛（证明没被吞）
4. run.json 的键与 CSV_FIELDS 的交集只有 assay / fps
5. 存在未产出数字的隔间时，run.json 的 not_scored 里有它和原因原文
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from depressionplex import runner as R
from depressionplex.cli import analyze

# 复用 test_runner.py 的合成帧生成器
H = 268
W_CH = 95
W_PILLAR = 4
N_CH = 4
FPS = 10.0

MOVE, STILL, EMPTY = "move", "still", "empty"


def _chamber(kind: str, i: int) -> np.ndarray:
    g = np.full((H, W_CH), 250.0)
    g[0:70, :] = 10.0
    g[H - 9:, :] = 10.0
    g[70:150, 44:50] = 15.0
    if kind == EMPTY:
        return g
    if kind == MOVE:
        top = 150 if i % 2 == 0 else 158
        col = 36 + 3 * (i % 6)
        g[top:190, col:col + 12] = 20.0
    else:
        g[152:182, 18:30] = 20.0
    return g


def _frame(kinds: tuple[str, ...], i: int) -> np.ndarray:
    w = N_CH * W_CH + (N_CH + 1) * W_PILLAR
    g = np.full((H, w), 10.0)
    for k, kind in enumerate(kinds):
        c0 = W_PILLAR + k * (W_CH + W_PILLAR)
        g[:, c0:c0 + W_CH] = _chamber(kind, i)
    return g


def _frames(kinds: tuple[str, ...], n: int) -> list[np.ndarray]:
    return [_frame(kinds, i) for i in range(n)]


# ---- 判据 1：progress=None 与有回调时报告逐位相同 -------------------------


def test_progress_does_not_change_results() -> None:
    """同一批帧，带/不带 progress 跑出的报告必须逐位相同。"""
    kinds = (MOVE, STILL, MOVE, STILL)
    calib = _frames(kinds, 8)
    frames = _frames(kinds, 20)

    # 不带 progress
    plan1, reports1, skipped1 = R.analyze_frames(
        calib, frames, fps=FPS, assay="TST", trial_prefix="test1", n_chambers=4)

    # 带 progress（收集调用）
    calls = []

    def collect(current: int, total: int) -> None:
        calls.append((current, total))

    plan2, reports2, skipped2 = R.analyze_frames(
        calib, frames, fps=FPS, assay="TST", trial_prefix="test2", n_chambers=4,
        progress=collect)

    # 计划必须相同
    assert len(plan1.chambers) == len(plan2.chambers)
    assert len(plan1.warnings) == len(plan2.warnings)

    # 报告数字必须相同（逐隔间比对）
    assert set(reports1.keys()) == set(reports2.keys())
    for k in reports1:
        r1, r2 = reports1[k], reports2[k]
        assert r1.immobility_mirror_pipeline_s == r2.immobility_mirror_pipeline_s
        assert r1.scorable_frames_window == r2.scorable_frames_window
        assert r1.validity_status == r2.validity_status

    # skipped 必须相同
    assert skipped1 == skipped2

    # 确保回调确实被调用了
    assert len(calls) > 0, "进度回调一次都没被调用"


# ---- 判据 2：最后一次调用的 current == 帧数 --------------------------------


def test_progress_last_call_reports_total_frames() -> None:
    """收集器收到的最后一次调用的第一个参数（已处理帧数）== 帧数。"""
    kinds = (MOVE, STILL, MOVE, STILL)
    calib = _frames(kinds, 8)
    n_frames = 15
    frames = _frames(kinds, n_frames)

    calls = []

    def collect(current: int, total: int) -> None:
        calls.append((current, total))

    R.analyze_frames(
        calib, frames, fps=FPS, assay="TST", trial_prefix="test",
        n_chambers=4, progress=collect)

    assert len(calls) > 0, "进度回调一次都没被调用"
    last_current, _ = calls[-1]
    assert last_current == n_frames, \
        f"最后一次调用报告 current={last_current}，期望 {n_frames}"


# ---- 判据 3：回调抛异常时异常会往上抛 --------------------------------------


def test_progress_exception_propagates() -> None:
    """回调抛异常时，异常会往上抛（不会被 try/except: pass 吞掉）。"""
    kinds = (MOVE, STILL, MOVE, STILL)
    calib = _frames(kinds, 8)
    frames = _frames(kinds, 10)

    class TestException(RuntimeError):
        pass

    def explode(current: int, total: int) -> None:
        if current >= 3:
            raise TestException("故意抛出的测试异常")

    try:
        R.analyze_frames(
            calib, frames, fps=FPS, assay="TST", trial_prefix="test",
            n_chambers=4, progress=explode)
    except TestException:
        pass  # 符合预期
    else:
        raise AssertionError("回调抛出的异常被静默吞掉了")


# ---- 判据 4：run.json 键与 CSV_FIELDS 交集只有 assay / fps ----------------


def test_runjson_no_csv_overlap() -> None:
    """run.json 的键（递归展开）与 CSV_FIELDS 的交集只允许 assay / fps。

    这条硬约束防止"一个数字两个序列化器 = 迟早漂移"（DP-054）。
    """
    # 构造一个最小的 run.json 样本
    from depressionplex import video
    from depressionplex.assay_core import segment, validity

    # 构造假的 info/plan
    info = video.VideoInfo(
        path=Path("/fake.mp4"), fps=25.0, n_frames=100,
        width=400, height=268, frame_count_source="nb_frames")

    # 构造假的 plan（单隔间，走廊失败 → 会进 skipped）
    ch = R.ChamberPlan(
        index=1, col_range=(0, 95), corridor=None, suspension=None,
        source="test")
    cv = validity.ChamberValidity(
        chamber=1, status="valid", max_area=100.0, ref_body_area=90.0,
        body_threshold=45.0, ever_had_body=True, note="", occupied_fraction=1.0,
        unsegmentable_fraction=0.0)
    tv = validity.TrialValidity(chambers=(cv,))
    plan = R.TrialPlan(
        chambers=(ch,), trial_validity=tv, calib_indices=(0, 10, 20),
        warnings=("test_warning",))

    skipped = {1: "悬挂点不可估（走廊标定失败 ⇒ 悬挂点不可估（不猜））⇒ 拒绝产出数字"}

    run_data = analyze._build_run_json(info, plan, "TST", skipped)

    # 递归提取所有键名
    def extract_keys(obj, prefix=""):
        keys = set()
        if isinstance(obj, dict):
            for k, v in obj.items():
                full_key = f"{prefix}.{k}" if prefix else k
                keys.add(full_key)
                keys.update(extract_keys(v, full_key))
        elif isinstance(obj, list):
            for item in obj:
                keys.update(extract_keys(item, prefix))
        return keys

    run_keys = extract_keys(run_data)
    csv_fields = set(analyze.CSV_FIELDS)

    # 找出最外层键与 CSV_FIELDS 的交集
    top_level_keys = {k.split(".")[0] for k in run_keys}
    overlap = top_level_keys & csv_fields

    # 只允许 assay 和 fps（这两个是标识不是指标）
    allowed = {"assay", "fps"}
    forbidden = overlap - allowed

    assert not forbidden, \
        f"run.json 出现了 CSV 里已有的键：{forbidden}。" \
        "一个数字两个序列化器 = 迟早漂移（DP-054）"


# ---- 判据 5：not_scored 包含未产出数字的隔间 -------------------------------


def test_runjson_not_scored_present() -> None:
    """存在未产出数字的隔间时，run.json 的 not_scored 里有它和原因原文。"""
    # 直接构造一个有 skipped 的场景（模拟走廊标定失败）
    from depressionplex import video
    from depressionplex.assay_core import validity

    # 构造假的 info
    info = video.VideoInfo(
        path=Path("/fake.mp4"), fps=FPS, n_frames=100,
        width=400, height=H, frame_count_source="nb_frames")

    # 构造一个走廊为 None 的隔间（会导致悬挂点为 None → 跳过）
    ch1 = R.ChamberPlan(
        index=1, col_range=(0, 95), corridor=None, suspension=None,
        source="test")
    ch2 = R.ChamberPlan(
        index=2, col_range=(100, 195),
        corridor=None, suspension=None,
        source="test")

    cv1 = validity.ChamberValidity(
        chamber=1, status="valid", max_area=100.0, ref_body_area=90.0,
        body_threshold=45.0, ever_had_body=True, note="",
        occupied_fraction=1.0, unsegmentable_fraction=0.0)
    cv2 = validity.ChamberValidity(
        chamber=2, status="valid", max_area=100.0, ref_body_area=90.0,
        body_threshold=45.0, ever_had_body=True, note="",
        occupied_fraction=1.0, unsegmentable_fraction=0.0)

    tv = validity.TrialValidity(chambers=(cv1, cv2))
    plan = R.TrialPlan(
        chambers=(ch1, ch2), trial_validity=tv, calib_indices=(0, 10, 20),
        warnings=())

    # 模拟 skipped（悬挂点不可估）
    skipped = {
        1: "test-ch1：悬挂点不可估（走廊标定失败 ⇒ 悬挂点不可估（不猜））⇒ 拒绝产出数字",
        2: "test-ch2：悬挂点不可估（走廊标定失败 ⇒ 悬挂点不可估（不猜））⇒ 拒绝产出数字",
    }

    run_data = analyze._build_run_json(info, plan, "TST", skipped)

    # not_scored 必须存在且非空
    assert "not_scored" in run_data, "run.json 缺少 not_scored 键"
    not_scored = run_data["not_scored"]
    assert isinstance(not_scored, list), "not_scored 必须是列表"
    assert len(not_scored) == 2, f"not_scored 应该包含 2 个跳过的隔间，实际 {len(not_scored)}"

    # 检查是否包含原因原文
    not_scored_chambers = {item["chamber"] for item in not_scored}
    assert not_scored_chambers == {1, 2}, "not_scored 应包含隔间 1 和 2"

    for item in not_scored:
        assert "chamber" in item and "reason" in item
        assert item["chamber"] in skipped
        assert item["reason"] == skipped[item["chamber"]]


# ---- 补充：not_scored 为空时也要有这个键 -----------------------------------


def test_runjson_not_scored_empty_but_present() -> None:
    """not_scored 为空时也要有这个键（写 []），不许省略。"""
    # 全部正常隔间（MOVE 和 STILL）
    kinds = (MOVE, STILL, MOVE, STILL)
    calib = _frames(kinds, 8)
    frames = _frames(kinds, 20)

    plan, reports, skipped = R.analyze_frames(
        calib, frames, fps=FPS, assay="TST", trial_prefix="test", n_chambers=4)

    # 应该没有 skipped
    assert len(skipped) == 0, "所有隔间应该都产出数字"

    from depressionplex import video
    info = video.VideoInfo(
        path=Path("/fake.mp4"), fps=FPS, n_frames=len(frames),
        width=400, height=H, frame_count_source="nb_frames")

    run_data = analyze._build_run_json(info, plan, "TST", skipped)

    # not_scored 必须存在
    assert "not_scored" in run_data, \
        "run.json 缺少 not_scored 键（为空时也要有，写 []）"
    assert run_data["not_scored"] == [], "not_scored 应该是空列表"
