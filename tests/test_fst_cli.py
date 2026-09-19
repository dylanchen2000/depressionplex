"""CLI 端到端测试：合成 mp4 → probe/解码 → 诊断记录 + 叠加短片 + 退出码。

用**标签合成**场景编一段真 mp4 再走完整 CLI：这条测试覆盖的是模块拼起来
以后还成不成立（probe 的 fps/帧数、frames_at 抽帧、yuv420p 往返后的阈值、
短片写出、记录落盘），合成输入在报告里必须标合成——这里测的是工程管线，
不是科学验证（Spec A §4 A1）。

退出码口径（cli 文档原话）：
0 = 记录产出且至少一杯看得见（或有申报空杯）；3 = 记录产出但没有一杯看得见；
2 = 守卫/用法拒绝；1 = 解码/编码失败（run 目录已清理）；
4 = 产物写出失败（run 目录已清理，旧证据未动）。

R2-115 P组：所有产物落在本次运行独立 run 目录 `out/<名>_run_*/`，全部成功
才写 `_完成清单.json`；失败清理只删本次 run 目录，旧证据分毫不动。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np

from depressionplex import video
from depressionplex.cli import fst_research as cli
from depressionplex.fst_research import overlay as ov
from depressionplex.fst_research import record as rec_mod

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


def _run_dirs(out: Path, stem: str) -> list[Path]:
    """out 下该素材的全部 run 目录（P组），按名排序。"""
    return sorted(out.glob(f"{stem}_run_*"))


def _run_dir(out: Path, stem: str) -> Path:
    dirs = _run_dirs(out, stem)
    assert dirs, f"{out} 下没有 {stem} 的 run 目录"
    return dirs[-1]


def _record(out: Path, stem: str) -> dict:
    p = _run_dir(out, stem) / f"诊断_{stem}.json"
    assert p.exists(), f"诊断记录不存在: {p}"
    return json.loads(p.read_text(encoding="utf-8"))


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
        run_dir = _run_dir(out, "synth_swim")
        rec_path = run_dir / "诊断_synth_swim.json"
        assert rec_path.exists()
        r = json.loads(rec_path.read_text(encoding="utf-8"))
        assert r["schema_version"] == "fst-research-v1"
        assert r["purpose"] == "research_diagnostics_only"
        assert r["must_not_enter_acceptance_paths"] is True
        # 时间未确认：t0 没给 ⇒ unknown，未套标准窗
        assert r["time_base"]["protocol_alignment"] == "unknown"
        assert r["time_base"]["t0_evidence"] is None
        # R2-115 T3：没给清单 ⇒ 角色 unverified，不声称"原视频"
        assert r["time_base"]["media_role"] == "unverified"
        assert "未经清单核实" in r["clocks"]["missing"]["analysis_media"]
        assert r["window"]["applies_standard_window"] is False
        assert "protocol" in r["clocks"]["missing"]
        # R2-115 T3：套窗标记 ≠ 统计已按窗截取
        assert r["window"]["stats_are_windowed"] is False
        assert "不表示统计已按窗" in r["window"]["stats_scope"]
        # 几何未确认：提案 confirmed=False，validate 报未确认（设计如此）
        assert r["geometry_confirmation"]["confirmed"] is False
        assert any("未确认" in p
                   for p in r["geometry_confirmation"]["validate_problems"])
        # R2-115 T1：抽样口径独立成节，计数是抽样记录数、帧号逐条列出
        s = r["sampling"]
        assert s["step_frames"] == 2
        assert abs(s["sample_spacing_s"] - 2 / 25.0) < 1e-9
        assert s["frame_indices"] == list(range(0, 30, 2))
        assert s["sampled_frames"] == len(s["frame_indices"]) == 15
        assert abs(s["sample_fraction"] - 0.5) < 1e-9
        assert "不是连续逐帧统计" in s["semantics"]
        assert "不补帧" in s["tail_note"]
        # 覆盖三分：解码完整（样点全消费），密度另报
        assert r["coverage"]["decode"]["complete"] is True
        assert abs(r["coverage"]["sampling"]["sample_fraction"] - 0.5) < 1e-9
        # 杯 0 看得见动物
        cup0 = r["cups"][0]
        assert cup0["sampled_state_counts"]["observed"] > 0
        assert cup0["sampled_frames"] == 15
        assert cup0["behavior_seconds"] is None
        assert cup0["behavior_seconds_semantics"] == \
            "research_diagnostics_no_window_alignment"
        assert cup0["motion_classification"]["performed"] is False
        assert cup0["features"]["local_motion_inside_vs_outside"] == \
            "not_implemented"
        # 叠加短片真的写出来了（非空 mp4，可再被 probe），且在本次 run 目录里
        clip = Path(cup0["overlay_clip"])
        assert clip.exists() and clip.stat().st_size > 100
        # 守卫会 resolve（macOS /var → /private/var），比较前先对齐
        assert clip.parent.resolve() == run_dir.resolve()
        info = video.probe(clip)
        assert info.n_frames > 0
        # 几何提案件落盘为**确认件 wrapper 的提案态**（G4）：binding 已预填、
        # envelope.confirmed=False、确认人留空——人工核对后改它即成确认件。
        geo_path = run_dir / "几何提案_synth_swim.json"
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

        # ---- R2-115 P组：完整研究配置与代码出处随记录落盘 ----
        prov = r["provenance"]
        assert prov["generated_by"] == "depressionplex.cli.fst_research"
        assert prov["run_id"] and prov["run_id"] in prov["run_dir"]
        assert Path(prov["run_dir"]).resolve() == rec_path.parent.resolve()
        assert "--step" in prov["command_line"]
        assert "synth_swim.mp4" in prov["command_line"]
        code = prov["code"]
        assert code["vcs"] in ("git", "not_a_git_repo", "git_unavailable")
        if code["vcs"] == "git":
            assert len(code["commit"]) == 40
            int(code["commit"], 16)
            assert isinstance(code["dirty"], bool)   # dirty 标记必须在场
        rp = r["research_params"]
        assert rp["input_sha256"] == r["source"]["sha256"]
        assert rp["step_frames"] == 2 and rp["calib_frames"] == 8
        assert rp["clip_frames"] == 4 and rp["cups_expected"] == 1
        assert rp["declared_empty_requested"] == []
        # 起点值逐项在场且写明出处（评审点名：cup min_width=40 等）
        assert rp["lost_short_max_gap_frames"] == 11
        assert rp["cup_min_width_px"] == 40
        assert rp["roi_above_margin_px"] == 30
        assert rp["cup_proposal_min_area_px"] == 200
        assert rp["dark"] == 96.0 and rp["bright"] == 150.0
        assert "起点值" in rp["provenance"]["cup_min_width_px"]
        assert "起点值" in rp["provenance"]["dark_bright"]
        assert rp["geometry_source"] == "proposal"
        assert rp["geometry_file_sha256"] is None
        # 完成清单：全部产物成功 ⇒ 最后落盘，逐件 sha256
        man_path = run_dir / "_完成清单.json"
        assert man_path.exists()
        man = json.loads(man_path.read_text(encoding="utf-8"))
        assert man["schema"] == "fst-research-run-manifest-v1"
        assert man["run_id"] == prov["run_id"]
        assert man["status"] == "complete"
        assert man["input_sha256"] == r["source"]["sha256"]
        assert man["code"] == code
        assert set(man["files"]) == {"诊断记录", "几何提案", "叠加短片_杯1"}
        for f in man["files"].values():
            assert len(f["sha256"]) == 64 and f["bytes"] > 0
            fp = run_dir / f["path"]
            assert fp.exists()
            assert rec_mod.sha256_of(fp) == f["sha256"]   # 清单说的哈希是真的


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
        r = _record(out, "synth_2cup")
        cup1 = r["cups"][1]
        assert cup1["declared_absent"] is True
        assert cup1["behavior_seconds"] is None    # 空杯：结果为空，不是 0 秒
        assert cup1["behavior_seconds_semantics"] == "empty_no_output"
        n = cup1["sampled_state_counts"]["declared_absent"]
        assert n == cup1["sampled_frames"] and n > 0
        # R2-115 T1：申报空杯 ⇒ 时间加权估计全 None（结果为空，不是 0 秒）
        assert all(v is None for v in cup1["time_weighted_sampled_s"].values())
        assert cup1["time_weighting"]["computed"] is False
        # P组：申报请求随研究配置落盘（物理杯号，1 起）
        assert r["research_params"]["declared_empty_requested"] == [2]


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
        rec_path = _run_dir(out, "synth_empty") / "诊断_synth_empty.json"
        assert rec_path.exists()                   # 记录照写、照实报
        r = json.loads(rec_path.read_text(encoding="utf-8"))
        cup0 = r["cups"][0]
        assert cup0["sampled_state_counts"]["observed"] == 0
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
        # 拒绝都发生在建 run 目录之前：o1/o2 里不许有 run 目录
        for name in ("o1", "o2"):
            d = tmp / name
            assert not d.exists() or not _run_dirs(d, "synth_guard")


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
        r = _record(out, "synth_t0")
        assert r["time_base"]["protocol_alignment"] == "known_t0"
        assert r["window"]["applies_standard_window"] is True
        assert r["time_base"]["t0_source_s"] == -118.0
        # R2-115 T3：t0 依据字符串落进记录，不只打在终端
        assert r["time_base"]["t0_evidence"] == \
            "测试用假想 t0：合成素材无真实入水时刻"
        # t0=-118 ⇒ 窗口 (120,360) 协议秒 = 媒体 2..242 s；素材只有 0.8 s
        # ⇒ 截断必须如实报，不许静默
        assert r["window"]["truncated"] is True
        assert r["coverage"]["sampling"]["sample_fraction"] < 1.0
        # R2-115 T3：applies_standard_window=True **不**表示统计已按窗截取
        assert r["window"]["stats_are_windowed"] is False
        assert any("stats_are_windowed=False" in l for l in r["limits"])


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
        tmpl_path = _run_dir(out1, "synth_geo") / "几何提案_synth_geo.json"
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
        r = _record(out3, "synth_geo")
        gc = r["geometry_confirmation"]
        assert gc["confirmed"] is True
        assert gc["source"] == "human_confirmed_file"
        assert gc["geometry_file"] == str(conf)
        assert gc["geometry_file_sha256"] and len(gc["geometry_file_sha256"]) == 64
        assert gc["binding"]["confirmed_by"] == "测试确认人"
        assert gc["validate_problems"] == []
        # 确认件的几何标 confirmed=True（不再是提案）
        assert r["cups"][0]["geometry"]["confirmed"] is True
        # P组：给了确认件 ⇒ 不再写几何提案，cups_expected 记 None（由 binding 核）
        run3 = _run_dir(out3, "synth_geo")
        assert not (run3 / "几何提案_synth_geo.json").exists()
        assert r["research_params"]["cups_expected"] is None
        assert r["research_params"]["geometry_file_sha256"] == gc["geometry_file_sha256"]
        man = json.loads((run3 / "_完成清单.json").read_text(encoding="utf-8"))
        assert set(man["files"]) == {"诊断记录", "叠加短片_杯1"}


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
        tmpl_path = _run_dir(out1, "synth_sha") / "几何提案_synth_sha.json"
        tmpl = json.loads(tmpl_path.read_text(encoding="utf-8"))
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
        # 拒绝发生在建 run 目录之前：out2 里不许有产物
        assert not out2.exists() or not _run_dirs(out2, "synth_sha")


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
        r = _record(out, "synth_amb")
        deb = r["geometry_confirmation"]["declared_empty_binding"]
        assert deb["status"] == "refused_ambiguous"
        assert deb["applied_cup_ids"] == []
        assert deb["problems"]
        # 那杯保持未决：不是 declared_absent
        assert r["cups"][0]["declared_absent"] is False
        # P组：被拒绝的申报请求也照实落盘（请求了 [1]，没应用）
        assert r["research_params"]["declared_empty_requested"] == [1]


# ---------------------------------------------------------------------------
# R2-115 T3：坏数在写任何文件之前拒绝；素材角色只认清单登记行
# ---------------------------------------------------------------------------

def test_cli_numeric_guards_refuse_before_writing_files() -> None:
    """step/门/面积/负边距/NaN/Inf ⇒ exit 2，且**不写任何文件**（不探测视频）。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vid = str(tmp / "不存在.mp4")            # 守卫在 probe 之前 ⇒ 无需 ffmpeg
        bad_argv = [
            [vid, "--out-dir", str(tmp / "g1"), "--step", "0"],
            [vid, "--out-dir", str(tmp / "g2"), "--step", "-3"],
            [vid, "--out-dir", str(tmp / "g3"), "--lost-short-max-gap-frames", "0"],
            [vid, "--out-dir", str(tmp / "g4"), "--min-area", "0"],
            [vid, "--out-dir", str(tmp / "g5"), "--roi-above-margin", "-1"],
            [vid, "--out-dir", str(tmp / "g6"), "--t0-source-s", "nan",
             "--t0-evidence", "x"],
            [vid, "--out-dir", str(tmp / "g7"), "--t0-source-s", "inf",
             "--t0-evidence", "x"],
            [vid, "--out-dir", str(tmp / "g8"), "--dark", "nan"],
            [vid, "--out-dir", str(tmp / "g9"), "--bright", "inf"],
            [vid, "--out-dir", str(tmp / "g10"), "--clip-at", "nan"],
        ]
        for argv in bad_argv:
            assert _run(argv) == 2, f"没拒绝坏参数: {argv}"
        # 一个输出目录都没建：拒绝发生在写文件之前
        assert not any(p.name.startswith("g") and p.is_dir()
                       for p in tmp.iterdir())


def _mini_manifest(tmp: Path, sha: str, *roles: str) -> Path:
    """写一份最小清单：同一 sha256 登记成给定角色（测角色映射用）。"""
    p = tmp / "清单.csv"
    header = "material_id,assay,role,path,sha256,t0_source_s,t0_status\n"
    rows = [f"M{i},FST,{r},x/正常1.mp4,{sha},,not_found\n"
            for i, r in enumerate(roles)]
    p.write_text(header + "".join(rows), encoding="utf-8-sig")
    return p


def test_cli_transcode_role_switches_clock_and_refuses_known_t0() -> None:
    """清单登记转码件 ⇒ analysis_media 钟；再给 known t0（偏移未查到）⇒ 拒。"""
    if not _ffmpeg_ok():
        print("  SKIP  test_cli_transcode_role_switches_clock_and_refuses_known_t0（无 ffmpeg）")
        return
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(20, cup=0)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vid = _encode(frames, tmp / "synth_trans.mp4")
        sha = rec_mod.sha256_of(vid)
        man = _mini_manifest(tmp, sha, "csi_transcode")
        # 转码件 + known t0：t0 定义在源媒体时间上、偏移未查到 ⇒ 拒绝（不静默跳过减法）
        out1 = tmp / "o1"
        assert _run([str(vid), "--out-dir", str(out1), "--cups", "1",
                     "--step", "2", "--calib", "6", "--clip", "3",
                     "--manifest", str(man),
                     "--t0-source-s", "10", "--t0-evidence", "测试依据"]) == 2
        assert not out1.exists()                     # 拒绝在写文件之前
        # 不给 t0 ⇒ 照跑，但时间轴按转码件解释，账本如实记偏移未查到
        out2 = tmp / "o2"
        rc = _run([str(vid), "--out-dir", str(out2), "--cups", "1",
                   "--step", "2", "--calib", "6", "--clip", "3",
                   "--manifest", str(man)])
        assert rc == 0
        r = _record(out2, "synth_trans")
        assert r["time_base"]["clock"] == "analysis_media"
        assert r["time_base"]["media_role"] == "transcode"
        assert "不许填 0" in r["clocks"]["missing"]["analysis_media"]
        assert r["time_base"]["protocol_alignment"] == "unknown"


def test_cli_source_role_from_manifest() -> None:
    """清单登记 source_video ⇒ 才允许说"直接读原视频"；角色随记录落盘。"""
    if not _ffmpeg_ok():
        print("  SKIP  test_cli_source_role_from_manifest（无 ffmpeg）")
        return
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(20, cup=0)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vid = _encode(frames, tmp / "synth_src.mp4")
        sha = rec_mod.sha256_of(vid)
        man = _mini_manifest(tmp, sha, "source_video")
        out = tmp / "o"
        rc = _run([str(vid), "--out-dir", str(out), "--cups", "1",
                   "--step", "2", "--calib", "6", "--clip", "3",
                   "--manifest", str(man)])
        assert rc == 0
        r = _record(out, "synth_src")
        assert r["time_base"]["clock"] == "source_media"
        assert r["time_base"]["media_role"] == "source_video"
        assert "直接读原视频" in r["clocks"]["missing"]["analysis_media"]
        assert r["manifest_lookup"]["found"] is True
        # P组：清单路径进研究配置
        assert r["research_params"]["manifest"] == str(man)


def test_cli_ambiguous_manifest_role_exit2() -> None:
    """同一 sha 既登记原视频又登记转码件 ⇒ 角色歧义，拒绝猜，exit 2。"""
    if not _ffmpeg_ok():
        print("  SKIP  test_cli_ambiguous_manifest_role_exit2（无 ffmpeg）")
        return
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(20, cup=0)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vid = _encode(frames, tmp / "synth_amb2.mp4")
        sha = rec_mod.sha256_of(vid)
        man = _mini_manifest(tmp, sha, "source_video", "csi_transcode")
        out = tmp / "o"
        rc = _run([str(vid), "--out-dir", str(out), "--cups", "1",
                   "--step", "2", "--calib", "6", "--clip", "3",
                   "--manifest", str(man)])
        assert rc == 2
        assert not out.exists()


def test_cli_unregistered_sha_is_unverified_not_source() -> None:
    """给了清单但 sha 没登记 ⇒ unverified：不声称原视频，也不拒跑。"""
    if not _ffmpeg_ok():
        print("  SKIP  test_cli_unregistered_sha_is_unverified_not_source（无 ffmpeg）")
        return
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(20, cup=0)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vid = _encode(frames, tmp / "synth_unreg.mp4")
        man = _mini_manifest(tmp, "ab" * 32, "source_video")   # 登记的是别的素材
        out = tmp / "o"
        rc = _run([str(vid), "--out-dir", str(out), "--cups", "1",
                   "--step", "2", "--calib", "6", "--clip", "3",
                   "--manifest", str(man)])
        assert rc == 0
        r = _record(out, "synth_unreg")
        assert r["time_base"]["media_role"] == "unverified"
        assert r["manifest_lookup"]["found"] is False
        assert "不猜" in r["manifest_lookup"]["note"]


def test_cli_lost_short_gate_arg_reaches_record() -> None:
    """--lost-short-max-gap-frames 是研究配置：改了要能在记录 limits 里追到。"""
    if not _ffmpeg_ok():
        print("  SKIP  test_cli_lost_short_gate_arg_reaches_record（无 ffmpeg）")
        return
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(20, cup=0)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vid = _encode(frames, tmp / "synth_gate.mp4")
        out = tmp / "o"
        rc = _run([str(vid), "--out-dir", str(out), "--cups", "1",
                   "--step", "2", "--calib", "6", "--clip", "3",
                   "--lost-short-max-gap-frames", "21"])
        assert rc == 0
        r = _record(out, "synth_gate")
        assert any("lost_short_max_gap_frames=21" in l for l in r["limits"])
        # P组：改过的门随研究配置落盘（不再只是代码默认值）
        assert r["research_params"]["lost_short_max_gap_frames"] == 21


# ---------------------------------------------------------------------------
# R2-115 P组：重复运行 / 编码器失败 / JSON 失败 / 第二个短片失败——
# 旧证据始终未变，失败只清理本次 run 目录，没有完成清单就没有可交付状态
# ---------------------------------------------------------------------------

def _decoy(out: Path) -> Path:
    """out_dir 里放一个"上一轮的旧证据"哨兵文件：每个失败测试都核它没被动。"""
    out.mkdir(parents=True, exist_ok=True)
    d = out / "旧一轮的证据.txt"
    d.write_bytes("上一轮产物，一个字节都不许变".encode("utf-8"))
    return d


def test_cli_repeat_run_same_out_dir_old_evidence_untouched() -> None:
    """评审用例①：重复运行同一 out-dir ⇒ 各得独立 run 目录，旧证据逐字节不变。"""
    if not _ffmpeg_ok():
        print("  SKIP  test_cli_repeat_run_same_out_dir_old_evidence_untouched（无 ffmpeg）")
        return
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(20, cup=0)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vid = _encode(frames, tmp / "synth_rep.mp4")
        out = tmp / "diag"
        argv = [str(vid), "--out-dir", str(out), "--cups", "1",
                "--step", "2", "--calib", "6", "--clip", "3"]
        assert _run(argv) == 0
        rd1 = _run_dir(out, "synth_rep")
        before = {p.name: rec_mod.sha256_of(p) for p in rd1.iterdir()}
        r1 = json.loads((rd1 / "诊断_synth_rep.json").read_text(encoding="utf-8"))
        assert _run(argv) == 0                     # 第二轮：同目录、同参数
        dirs = _run_dirs(out, "synth_rep")
        assert len(dirs) == 2, f"重复运行没各得其所: {[d.name for d in dirs]}"
        # 第一轮 run 目录逐字节未变（sha256 全同，没多没少）
        after = {p.name: rec_mod.sha256_of(p) for p in rd1.iterdir()}
        assert after == before, "第二轮动了第一轮的证据"
        # 两轮各有完成清单，run_id 不同
        rd2 = [d for d in dirs if d != rd1][0]
        assert (rd2 / "_完成清单.json").exists()
        r2 = json.loads((rd2 / "诊断_synth_rep.json").read_text(encoding="utf-8"))
        assert r1["provenance"]["run_id"] != r2["provenance"]["run_id"]
        assert r1["source"]["sha256"] == r2["source"]["sha256"]   # 同一素材


def test_cli_encoder_failure_cleans_run_dir_old_evidence_untouched() -> None:
    """评审用例②：编码器失败（close 断管）⇒ exit 1，run 目录清干净，旧证据未动。"""
    if not _ffmpeg_ok():
        print("  SKIP  test_cli_encoder_failure_cleans_run_dir_old_evidence_untouched（无 ffmpeg）")
        return
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(20, cup=0)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vid = _encode(frames, tmp / "synth_enc.mp4")
        out = tmp / "diag"
        decoy = _decoy(out)
        decoy_bytes = decoy.read_bytes()

        def _boom(self):
            raise video.VideoError("编码器断管（测试注入）")
        orig = ov.ClipWriter.close
        ov.ClipWriter.close = _boom                # 函数级补丁
        try:
            rc = _run([str(vid), "--out-dir", str(out), "--cups", "1",
                       "--step", "2", "--calib", "6", "--clip", "3"])
        finally:
            ov.ClipWriter.close = orig
        assert rc == 1, f"编码失败该 exit 1，得到 {rc}"
        # 本次 run 目录整个清掉：不留半截 mp4 / 半截 JSON 冒充证据
        assert _run_dirs(out, "synth_enc") == []
        assert decoy.read_bytes() == decoy_bytes   # 旧证据一个字节没动


def test_cli_second_clip_broken_pipe_exit4_cleans_all() -> None:
    """评审用例③：第二个短片写出中途断管 ⇒ exit 4，两杯的半成品都不留。"""
    if not _ffmpeg_ok():
        print("  SKIP  test_cli_second_clip_broken_pipe_exit4_cleans_all（无 ffmpeg）")
        return
    scene = fst_synth.Scene(cups=2)
    frames = scene.swim_series(20, cup=0)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vid = _encode(frames, tmp / "synth_pipe.mp4")
        out = tmp / "diag"
        decoy = _decoy(out)
        decoy_bytes = decoy.read_bytes()

        orig = ov.ClipWriter
        state = {"n": 0}

        class Flaky(orig):                         # 第 2 个实例写第 2 帧时断管
            def __init__(self, *a, **kw):
                state["n"] += 1
                self._which = state["n"]
                super().__init__(*a, **kw)

            def write(self, rgb):
                if self._which >= 2 and self._n >= 1:
                    raise BrokenPipeError("ffmpeg stdin 断管（测试注入）")
                super().write(rgb)

        ov.ClipWriter = Flaky                      # 函数级补丁
        try:
            rc = _run([str(vid), "--out-dir", str(out), "--cups", "2",
                       "--step", "2", "--calib", "6", "--clip", "3"])
        finally:
            ov.ClipWriter = orig
        assert rc == 4, f"产物写出失败该 exit 4，得到 {rc}"
        assert state["n"] >= 2, "注入没生效：第二个短片根本没开写"
        # run 目录整个清掉：杯 1 已写好的短片也不留——异常不得留混杂产物
        assert _run_dirs(out, "synth_pipe") == []
        assert decoy.read_bytes() == decoy_bytes


def test_cli_record_write_failure_exit4_cleans_run_dir() -> None:
    """评审用例④：JSON 记录写出失败 ⇒ exit 4，run 目录清掉，旧证据未动。"""
    if not _ffmpeg_ok():
        print("  SKIP  test_cli_record_write_failure_exit4_cleans_run_dir（无 ffmpeg）")
        return
    scene = fst_synth.Scene(cups=1)
    frames = scene.swim_series(20, cup=0)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vid = _encode(frames, tmp / "synth_json.mp4")
        out = tmp / "diag"
        decoy = _decoy(out)
        decoy_bytes = decoy.read_bytes()

        def _boom(record, path):
            raise RuntimeError("JSON 写出失败（测试注入）")
        orig = rec_mod.write_record
        rec_mod.write_record = _boom               # 函数级补丁
        try:
            rc = _run([str(vid), "--out-dir", str(out), "--cups", "1",
                       "--step", "2", "--calib", "6", "--clip", "3"])
        finally:
            rec_mod.write_record = orig
        assert rc == 4, f"记录写出失败该 exit 4，得到 {rc}"
        assert _run_dirs(out, "synth_json") == []  # 短片成功了也不留：没有清单就没有可交付状态
        assert decoy.read_bytes() == decoy_bytes


def test_clipwriter_refuses_existing_target() -> None:
    """ClipWriter 对已存在目标拒绝覆盖（不 spawn ffmpeg，所以无需工具在场）。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "已有短片.mp4"
        p.write_bytes(b"\x00old-evidence")
        try:
            ov.ClipWriter(p, fps=25.0, size=(8, 8))
        except FileExistsError as e:
            assert "拒绝覆盖" in str(e)
        else:
            raise AssertionError("ClipWriter 要覆盖已有短片")
        assert p.read_bytes() == b"\x00old-evidence"
