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
        # 几何提案件落盘为**确认件 wrapper 的提案态**（G4）：binding 已预填、
        # envelope.confirmed=False、确认人留空——人工核对后改它即成确认件。
        geo_path = out / "几何提案_synth_swim.json"
        assert geo_path.exists()
        geo = json.loads(geo_path.read_text(encoding="utf-8"))
        assert geo["schema"] == "fst-confirmed-geometry-v1"
        assert geo["status"] == "proposal_unconfirmed"
        assert geo["envelope"]["confirmed"] is False
        assert geo["binding"]["confirmed_by"] == ""        # 留给人填
        assert geo["binding"]["video_sha256"] == r["source"]["sha256"]  # 脚本预绑
        # 记录里的几何确认段：提案态、带问题清单
        assert r["geometry_confirmation"]["source"] == "proposal"
        assert r["geometry_confirmation"]["geometry_file"] is None
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


def test_cli_geometry_confirmed_file_roundtrip() -> None:
    """G4 端到端：提案模板 → 人工翻 confirmed/填确认人 → --geometry 吃回去。

    模板态（未确认）当确认件用必须被拒；人工确认后的文件加载成功，
    记录里 source=human_confirmed_file、带确认件 sha256、validate 干净。
    """
    if not _ffmpeg_ok():
        print("  SKIP  test_cli_geometry_confirmed_file_roundtrip（无 ffmpeg）")
        return
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(20, cup=0)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vid = _encode(frames, tmp / "synth_geo.mp4")
        out1 = tmp / "diag1"
        rc = _run([str(vid), "--out-dir", str(out1), "--cups", "1",
                   "--step", "2", "--calib", "6", "--clip", "3"])
        assert rc == 0
        tmpl_path = out1 / "几何提案_synth_geo.json"
        tmpl = json.loads(tmpl_path.read_text(encoding="utf-8"))
        assert tmpl["status"] == "proposal_unconfirmed"
        # 模板直接当确认件 ⇒ 拒（confirmed_by 空 + envelope 未确认）
        out2 = tmp / "diag2"
        assert _run([str(vid), "--out-dir", str(out2), "--cups", "1",
                     "--step", "2", "--calib", "6", "--clip", "3",
                     "--geometry", str(tmpl_path)]) == 2
        # 人工翻 confirmed + 填确认人/时间 ⇒ 成确认件
        tmpl["envelope"]["confirmed"] = True
        for pr in tmpl["envelope"]["primitives"]:
            pr["confirmed"] = True
        tmpl["binding"]["confirmed_by"] = "测试确认人"
        tmpl["binding"]["confirmed_at"] = "2026-09-19T00:00:00Z"
        tmpl["binding"]["confirmed_basis"] = "看了叠加短片"
        conf = tmp / "confirmed.json"
        conf.write_text(json.dumps(tmpl, ensure_ascii=False), encoding="utf-8")
        out3 = tmp / "diag3"
        rc = _run([str(vid), "--out-dir", str(out3), "--cups", "1",
                   "--step", "2", "--calib", "6", "--clip", "3",
                   "--geometry", str(conf)])
        assert rc == 0
        r = json.loads((out3 / "诊断_synth_geo.json").read_text(encoding="utf-8"))
        gc = r["geometry_confirmation"]
        assert gc["confirmed"] is True
        assert gc["source"] == "human_confirmed_file"
        assert gc["geometry_file"] == str(conf)
        assert gc["geometry_file_sha256"] and len(gc["geometry_file_sha256"]) == 64
        assert gc["binding"]["confirmed_by"] == "测试确认人"
        assert gc["validate_problems"] == []
        # 确认件的几何标 confirmed=True（不再是提案）
        assert r["cups"][0]["geometry"]["confirmed"] is True


def test_cli_geometry_wrong_video_sha_refused() -> None:
    """G4：确认件绑定的不是这段素材（sha 不符）⇒ 拒绝，退出码 2。"""
    if not _ffmpeg_ok():
        print("  SKIP  test_cli_geometry_wrong_video_sha_refused（无 ffmpeg）")
        return
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(16, cup=0)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vid = _encode(frames, tmp / "synth_sha.mp4")
        out1 = tmp / "diag1"
        assert _run([str(vid), "--out-dir", str(out1), "--cups", "1",
                     "--step", "2", "--calib", "6", "--clip", "3"]) == 0
        tmpl = json.loads((out1 / "几何提案_synth_sha.json").read_text(encoding="utf-8"))
        tmpl["envelope"]["confirmed"] = True
        for pr in tmpl["envelope"]["primitives"]:
            pr["confirmed"] = True
        tmpl["binding"]["confirmed_by"] = "测试确认人"
        tmpl["binding"]["confirmed_at"] = "2026-09-19T00:00:00Z"
        tmpl["binding"]["video_sha256"] = "00" * 32     # 绑到别的视频
        conf = tmp / "wrong.json"
        conf.write_text(json.dumps(tmpl, ensure_ascii=False), encoding="utf-8")
        out2 = tmp / "diag2"
        rc = _run([str(vid), "--out-dir", str(out2), "--cups", "1",
                   "--step", "2", "--calib", "6", "--clip", "3",
                   "--geometry", str(conf)])
        assert rc == 2


def test_cli_declared_empty_refused_when_cup_count_ambiguous() -> None:
    """G3 端到端：期望 2 杯但只找到 1 杯 ⇒ 物理杯号有歧义 ⇒ 拒绝应用申报。

    跑不中断（不是 exit 2），但记录里 declared_empty_binding.status=
    refused_ambiguous、applied 为空，那杯**不**被当空杯处理（保持未决）。
    """
    if not _ffmpeg_ok():
        print("  SKIP  test_cli_declared_empty_refused_when_cup_count_ambiguous（无 ffmpeg）")
        return
    scene = fst_synth.Scene(cups=1)                 # 画面里只有 1 个杯
    frames = scene.swim_series(20, cup=0)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vid = _encode(frames, tmp / "synth_amb.mp4")
        out = tmp / "diag"
        # 申报杯 1 空，但 --cups 说应有 2 杯 ⇒ 找到 1 杯 ≠ 2 ⇒ 绑定有歧义
        rc = _run([str(vid), "--out-dir", str(out), "--cups", "2",
                   "--step", "2", "--calib", "6", "--clip", "3",
                   "--declared-empty", "1"])
        # 杯 1 看得见动物（observed）⇒ 退出码 0；拒绝申报只影响"空杯结论"，
        # 不影响可见性口径。
        assert rc == 0
        r = json.loads((out / "诊断_synth_amb.json").read_text(encoding="utf-8"))
        deb = r["geometry_confirmation"]["declared_empty_binding"]
        assert deb["status"] == "refused_ambiguous"
        assert deb["applied_cup_ids"] == []
        assert deb["problems"]
        # 那杯保持未决：不是 declared_absent
        assert r["cups"][0]["declared_absent"] is False
