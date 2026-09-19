"""CLI 端到端测试：合成 mp4 → probe/解码 → 诊断记录 + 叠加短片 + 退出码。

用**标签合成**场景编一段真 mp4 再走完整 CLI：这条测试覆盖的是模块拼起来
以后还成不成立（probe 的 fps/帧数、frames_at 抽帧、yuv420p 往返后的阈值、
短片写出、记录落盘），合成输入在报告里必须标合成——这里测的是工程管线，
不是科学验证（Spec A §4 A1）。

退出码口径（cli 文档原话）：
0 = 记录产出且至少一杯看得见（或有申报空杯）；3 = 记录产出但没有一杯看得见；
2 = 守卫/用法拒绝；1 = 解码失败。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np

from depressionplex import video
from depressionplex.cli import fst_research as cli
from depressionplex.fst_research import overlay as ov

import fst_synth


def _ffmpeg_ok() -> bool:
    try:
        video._resolve_ffmpeg_tool(video.TOOL_FFMPEG)
        video._resolve_ffmpeg_tool(video.TOOL_FFPROBE)
        return True
    except Exception:
        return False


def _encode(frames, path: Path, fps: float = 25.0) -> Path:
    h, w = frames[0].shape
    return ov.encode_clip([ov.gray_to_rgb(f) for f in frames],
                          out_path=path, fps=fps, size=(w, h))


def _run(argv: list) -> int:
    return cli.main(argv)


def test_cli_synth_swim_exit0_and_artifacts() -> None:
    if not _ffmpeg_ok():
        print("  SKIP  test_cli_synth_swim_exit0_and_artifacts（无 ffmpeg）")
        return
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(30, cup=0)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vid = _encode(frames, tmp / "synth_swim.mp4")
        out = tmp / "diag"
        rc = _run([str(vid), "--out-dir", str(out), "--cups", "1",
                   "--step", "2", "--calib", "8", "--clip", "4"])
        assert rc == 0, f"退出码 {rc}（0 = 至少一杯看得见）"
        rec_path = out / "诊断_synth_swim.json"
        assert rec_path.exists()
        r = json.loads(rec_path.read_text(encoding="utf-8"))
        assert r["schema_version"] == "fst-research-v1"
        assert r["purpose"] == "research_diagnostics_only"
        assert r["must_not_enter_acceptance_paths"] is True
        # 时间未确认：t0 没给 ⇒ unknown，未套标准窗
        assert r["time_base"]["protocol_alignment"] == "unknown"
        assert r["window"]["applies_standard_window"] is False
        assert "protocol" in r["clocks"]["missing"]
        # 几何未确认：提案 confirmed=False，validate 报未确认（设计如此）
        assert r["geometry_confirmation"]["confirmed"] is False
        assert any("未确认" in p
                   for p in r["geometry_confirmation"]["validate_problems"])
        # 杯 0 看得见动物
        cup0 = r["cups"][0]
        assert cup0["quality_counts"]["observed"] > 0
        assert cup0["behavior_seconds"] is None
        assert cup0["behavior_seconds_semantics"] == \
            "research_diagnostics_no_window_alignment"
        assert cup0["motion_classification"]["performed"] is False
        assert cup0["features"]["local_motion_inside_vs_outside"] == \
            "not_implemented"
        # 叠加短片真的写出来了（非空 mp4，可再被 probe）
        clip = Path(cup0["overlay_clip"])
        assert clip.exists() and clip.stat().st_size > 100
        info = video.probe(clip)
        assert info.n_frames > 0
        # 几何提案件也落了盘，且仍未确认
        geo_path = out / "几何提案_synth_swim.json"
        assert geo_path.exists()
        assert json.loads(geo_path.read_text(encoding="utf-8"))["confirmed"] is False
        # 没给 --manifest：如实记"不做身份关联，不猜"
        assert r["manifest_lookup"]["found"] is False
        assert "不猜" in r["manifest_lookup"]["note"]


def test_cli_declared_empty_exit0_semantics() -> None:
    if not _ffmpeg_ok():
        print("  SKIP  test_cli_declared_empty_exit0_semantics（无 ffmpeg）")
        return
    scene = fst_synth.Scene(cups=2)
    frames = scene.swim_series(20, cup=0)          # 杯 1 始终没动物
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vid = _encode(frames, tmp / "synth_2cup.mp4")
        out = tmp / "diag"
        rc = _run([str(vid), "--out-dir", str(out), "--cups", "2",
                   "--step", "2", "--calib", "6", "--clip", "3",
                   "--declared-empty", "2"])
        assert rc == 0                             # 申报空杯算"有交代"
        r = json.loads((out / "诊断_synth_2cup.json").read_text(encoding="utf-8"))
        cup1 = r["cups"][1]
        assert cup1["declared_absent"] is True
        assert cup1["behavior_seconds"] is None    # 空杯：结果为空，不是 0 秒
        assert cup1["behavior_seconds_semantics"] == "empty_no_output"
        n = cup1["quality_counts"]["declared_absent"]
        assert n == cup1["analyzed_frames"] and n > 0


def test_cli_nothing_visible_exit3_record_still_written() -> None:
    if not _ffmpeg_ok():
        print("  SKIP  test_cli_nothing_visible_exit3_record_still_written（无 ffmpeg）")
        return
    scene = fst_synth.Scene(cups=1)
    frames = [scene.base() for _ in range(20)]     # 整段没有动物
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vid = _encode(frames, tmp / "synth_empty.mp4")
        out = tmp / "diag"
        rc = _run([str(vid), "--out-dir", str(out), "--cups", "1",
                   "--step", "2", "--calib", "6", "--clip", "3"])
        assert rc == 3, "没有一杯看得见动物 ⇒ 退出码必须是 3，不许 0 装绿"
        rec_path = out / "诊断_synth_empty.json"
        assert rec_path.exists()                   # 记录照写、照实报
        r = json.loads(rec_path.read_text(encoding="utf-8"))
        cup0 = r["cups"][0]
        assert cup0["quality_counts"]["observed"] == 0
        assert cup0["behavior_seconds"] is None
        joined = json.dumps(cup0["top_reasons"], ensure_ascii=False)
        assert "无候选" in joined or "long_absence" in joined


def test_cli_guards_exit2() -> None:
    if not _ffmpeg_ok():
        print("  SKIP  test_cli_guards_exit2（无 ffmpeg）")
        return
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(12, cup=0)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vid = _encode(frames, tmp / "synth_guard.mp4")
        # 1) t0 给了没给依据 ⇒ 拒
        assert _run([str(vid), "--out-dir", str(tmp / "o1"),
                     "--t0-source-s", "10"]) == 2
        # 2) 落点在仓库里 ⇒ 拒（且什么都不写）
        assert _run([str(vid), "--out-dir",
                     str(ov.REPO_ROOT / "data" / "diag")]) == 2
        assert not (ov.REPO_ROOT / "data" / "diag").exists()
        # 3) 申报空杯号超过找到的杯数 ⇒ 拒
        assert _run([str(vid), "--out-dir", str(tmp / "o2"), "--cups", "1",
                     "--step", "3", "--calib", "4", "--clip", "2",
                     "--declared-empty", "9"]) == 2
        # 4) 视频文件不存在 ⇒ 解码/探测失败
        assert _run([str(tmp / "no_such.mp4"), "--out-dir", str(tmp / "o3")]) == 1


def test_cli_self_check_mode() -> None:
    rc = _run(["ignored.mp4", "--out-dir", "/tmp/ignored", "--self-check"])
    assert rc == 0                                 # 静态闸干净（另见 isolation 测试）


def test_cli_t0_known_sets_window() -> None:
    if not _ffmpeg_ok():
        print("  SKIP  test_cli_t0_known_sets_window（无 ffmpeg）")
        return
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(20, cup=0)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vid = _encode(frames, tmp / "synth_t0.mp4")
        out = tmp / "diag"
        rc = _run([str(vid), "--out-dir", str(out), "--cups", "1",
                   "--step", "2", "--calib", "6", "--clip", "3",
                   "--t0-source-s", "-118.0", "--t0-evidence",
                   "测试用假想 t0：合成素材无真实入水时刻"])
        assert rc == 0
        r = json.loads((out / "诊断_synth_t0.json").read_text(encoding="utf-8"))
        assert r["time_base"]["protocol_alignment"] == "known_t0"
        assert r["window"]["applies_standard_window"] is True
        assert r["time_base"]["t0_source_s"] == -118.0
        # t0=-118 ⇒ 窗口 (120,360) 协议秒 = 媒体 2..242 s；素材只有 0.8 s
        # ⇒ 截断必须如实报，不许静默
        assert r["window"]["truncated"] is True
        assert r["coverage"]["coverage_frac"] < 1.0
