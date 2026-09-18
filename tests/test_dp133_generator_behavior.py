#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-133 守卫（生成器**行为**）：`scripts/dp133_build_input_identity.py`。

`test_input_identity_manifest.py` 守的是**产物**——那张 CSV 里的自洽钉子。产物守得住，
但守得**太晚**：等它红的时候，坏清单已经写进 `docs/`、已经被别的分支引用了。
返修 R112-03 的要求就是把这几条挪到**发布之前**：缺根目录、缺登记表、
重复 material_id、非法帧率/帧数、与映射表的哈希前缀对不上——一律在生成器里当场停机，
不靠 CI 事后读到一份坏 CSV 才发现。

R112-01 的另一半是**输出保护**：这是个只读盘点脚本，它不该有能力写坏它正在盘点的素材，
也不该悄悄盖掉上一版清单。所以：素材/证据根目录与仓库源码目录是禁止输出区（跟随软链接）；
默认不覆盖；更新要显式 `--update` + 旧文件的 `--expect-sha256`；先写临时文件、
校验通过再发布，旧版留快照。

**这些测试全部用小临时样例 / 受控替身**：不跑 ffprobe、不解 zip、不碰任何客户素材，
CI 上没有那 68 段视频也能跑（返修 R112-01 明确要求测试不依赖客户全量视频）。
"""
from __future__ import annotations

import csv
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "dp133_build_input_identity.py"

_spec = importlib.util.spec_from_file_location("dp133_gen", SCRIPT)
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)


def _exit_msg(fn, *a, **kw) -> str:
    """断言 fn 以 SystemExit 停机，返回它的消息。

    不用 pytest：本仓库自带 `run_tests.py` 正是因为沙箱里 pytest 的 site-packages
    有 I/O 故障（见 run_tests.py 开头），测试文件不许把它变成硬依赖。
    """
    try:
        fn(*a, **kw)
    except SystemExit as e:
        return str(e)
    raise AssertionError(f"应当停机（SystemExit），却正常返回了：{fn}")


# ------------------------------------------------------------------ 替身树

def _fake_tree(tmp: Path) -> tuple[dict, Path]:
    """搭一棵最小的假素材树 + 假仓库。**不是**客户素材的副本，是受控替身：
    文件内容随便（`b"v"`），只为让 `build(do_probe=False, include_exe=False)` 走完全程。
    """
    roots = {}
    for k in ("fst_source", "tst_source", "csi_in", "tst_clips",
              "fst_transcode_intermediate", "csi_out_fst", "csi_out_ctrl",
              "csi_out_rest", "csi_out_norm14", "ui_shots"):
        roots[k] = tmp / k
        roots[k].mkdir()
    roots["exe_zip"] = tmp / "exe.zip"
    roots["exe_zip"].write_bytes(b"not-a-real-zip")

    (roots["fst_source"] / "A.mp4").write_bytes(b"vA")
    (roots["fst_source"] / "B.mp4").write_bytes(b"vB")
    (roots["tst_source"] / "C.mp4").write_bytes(b"vC")
    (roots["csi_in"] / "A.mpg").write_bytes(b"tA")
    (roots["csi_in"] / "Z_fallback.mpg").write_bytes(b"tZ")
    (roots["tst_clips"] / "clipA.mp4").write_bytes(b"cA")
    (roots["fst_transcode_intermediate"] / "A.mp4").write_bytes(b"iA")
    (roots["fst_transcode_intermediate"] / "notes.txt").write_text("x")
    for name in ("10mg 2周.SET", "正常1-4.CLB", "r.xlsx", "s.bmp"):
        (roots["csi_out_fst"] / name).write_bytes(b"csi")
    (roots["ui_shots"] / "shot.png").write_bytes(b"png")
    (roots["ui_shots"] / "CSI参数与文件格式_实录_2026-09-10.md").write_text("实录")

    # cut_provenance.csv：无表头、每段两行，第二行第 2 列是源片 sha256
    (roots["tst_clips"] / "cut_provenance.csv").write_text(
        "clipA,0.0,3.0\nclipA,3.0," + "c" * 64 + "\n", encoding="utf-8")
    (roots["tst_clips"] / "manifest.csv").write_text(
        "trial_id,video_filename\nT1,clipA.mp4\n", encoding="utf-8")

    repo = tmp / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "data/human_scores/manifests").mkdir(parents=True)
    (repo / "tests/fixtures/csi_fst").mkdir(parents=True)
    (repo / "tests/fixtures/csi_fst" / "A.SET").write_bytes(b"fix")

    # 两份「给评分员」的清单：本表只取 video_filename 集合做**指针**判定，
    # 绝不复制 holds/评分/复评归属（Spec A §3）。
    (repo / "data/human_scores/manifests/FST全程_视频清单_给评分员.csv").write_text(
        "trial_id,video_filename,assay,declared_empty\nF1,A.mp4,FST,否\n",
        encoding="utf-8")
    (repo / "data/human_scores/manifests/T1精标_视频清单_给评分员.csv").write_text(
        "trial_id,video_filename\nT1,C.mp4\n", encoding="utf-8")

    # 映射表带 BOM：裸 utf-8 会把首列名读成 '﻿范式'（踩过一次）
    src_sha = g.sha256_file(roots["fst_source"] / "A.mp4")
    with open(repo / "docs/CSI输入视频映射表.csv", "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["范式", "原视频", "CSI用文件", "原尺寸", "转换后", "原帧数",
                    "转换后帧数", "帧数一致", "时长秒", "原sha256前16", "输出MB"])
        w.writerow(["FST", "A.mp4", "A.mpg", "472x266", "944x532", "100",
                    "100", "是", "4.00", src_sha[:16], "1.0"])
    return roots, repo


def _fake_rows() -> list[dict]:
    """一份**合法**的两行清单（1 源片 + 1 转码件），给 self_check 当基准。"""
    src = {c: "" for c in g.HEADER}
    src.update({
        "material_id": "source:FST:A.mp4", "assay": "FST", "role": "source_video",
        "path": "~/素材/A.mp4", "bytes": "10", "sha256": "963c4a670628b28f" + "0" * 48,
        "codec": "h264", "width": "472", "height": "266",
        "field_order": "progressive", "frames": "100", "frames_source": "container",
        "fps_avg_frame_rate": "25/1", "fps_r_frame_rate": "25/1",
        "duration_s": "4.0", "frames_vs_dur_x_avgfps": "是",
        "pts_count": "100", "pts_na": "0", "pts_min_s": "0", "pts_max_s": "3.96",
        "pts_spacing_uniform": "是", "pts_raw_order_monotonic": "否",
        "derives_from_status": "na", "t0_status": g.T0_NOT_FOUND,
        "geometry_binding_status": "na", "human_record_status": "pointer_only_joins_by_filename",
        "usage_group": "historical_or_dev_validation",
        "evidence_status": "artifact_checked",
    })
    tr = dict(src)
    tr.update({
        "material_id": "csi_transcode:FST:A.mpg", "role": "csi_transcode",
        "path": "~/csi_in/A.mpg", "sha256": "b" * 64, "codec": "mpeg1video",
        "fps_r_frame_rate": "50/1", "frames_source": "decode_count",
        "pts_min_s": "0.54", "pts_max_s": "4.5", "pts_raw_order_monotonic": "是",
        "derives_from_sha256": src["sha256"],
        "derives_from_status": "confirmed_by_mapping_table",
    })
    return [src, tr]


def _mapping_ok() -> dict:
    return {"A.mpg": {"范式": "FST", "原视频": "A.mp4",
                      "原sha256前16": "963c4a670628b28f",
                      "原帧数": "100", "转换后帧数": "100"}}


# ------------------------------------------------------- 输入侧：拒产半张表

def test_build_walks_the_whole_fake_tree() -> None:
    """基准：假树能走完全程，每类角色都产出行（18 = 3 源片 + 2 转码 + 1 切片
    + 2 中间件 + 4 CSI 输出 + 1 截图 + 1 夹具 + 4 登记表）。"""
    with tempfile.TemporaryDirectory() as d:
        roots, repo = _fake_tree(Path(d))
        rows = g.build(roots, repo, do_probe=False, include_exe=False)
    assert len(rows) == 18, [(r["role"], r["material_id"]) for r in rows]
    roles = {r["role"] for r in rows}
    assert {"source_video", "csi_transcode", "tst_clip", "transcode_intermediate",
            "csi_set", "csi_clb", "csi_xlsx", "csi_bmp", "ui_screenshot",
            "repo_fixture", "registry_doc"} <= roles, roles
    ids = [r["material_id"] for r in rows]
    assert len(set(ids)) == len(ids), "material_id 必须唯一"
    # t0 状态分范式：TST 没有「入水」这件事，不许套 FST 的理由（R114-03）
    per = {(r["assay"], r["t0_status"]) for r in rows if r["role"] in
           ("source_video", "csi_transcode", "tst_clip")}
    assert ("TST", g.T0_NA_TST) in per and ("FST", g.T0_NOT_FOUND) in per, per
    assert ("TST", g.T0_NOT_FOUND) not in per, per


def test_missing_material_root_refuses_before_writing() -> None:
    """少一根授权根目录 ⇒ 当场停机。静默少一批行比报错危险（WORKFLOW §4）。"""
    with tempfile.TemporaryDirectory() as d:
        roots, repo = _fake_tree(Path(d))
        for victim in ("fst_source", "csi_out_norm14"):
            for p in roots[victim].iterdir():
                p.unlink()
            roots[victim].rmdir()
            msg = _exit_msg(g.build, roots, repo, do_probe=False,
                            include_exe=False)
            assert "拒产半张表" in msg and victim in msg, msg


def test_missing_registry_docs_refuse() -> None:
    """缺人工清单 / 映射表 / 切片来源记录 ⇒ 拒产。这三样是本表指针与派生关系的依据。"""
    with tempfile.TemporaryDirectory() as d:
        roots, repo = _fake_tree(Path(d))
        for victim in (repo / "data/human_scores/manifests/T1精标_视频清单_给评分员.csv",
                       repo / "docs/CSI输入视频映射表.csv",
                       roots["tst_clips"] / "cut_provenance.csv"):
            v = Path(victim)
            v.unlink()
            msg = _exit_msg(g.build, roots, repo, do_probe=False,
                            include_exe=False)
            assert "拒产半张表" in msg, msg


# --------------------------------------------- self_check：发布前拦住坏清单

def test_self_check_accepts_the_baseline() -> None:
    g.self_check(_fake_rows(), _mapping_ok(), expect_videos=2, expect_rows_min=1)


def test_self_check_rejects_duplicate_material_id() -> None:
    """主键重复 ⇒ 任何按 id 取行的代码会静默拿到最后一条。"""
    rows = _fake_rows()
    rows[1]["material_id"] = rows[0]["material_id"]
    msg = _exit_msg(g.self_check, rows, _mapping_ok(), expect_videos=2,
                    expect_rows_min=1)
    assert "material_id 重复" in msg, msg


def test_self_check_rejects_illegal_fps_frames_duration() -> None:
    """帧数不是整数、avg_frame_rate 是 0 分母或负数、时长解析不了 ⇒ 一律拦下。

    这一条防的是 `frames_ok()` 的老毛病：解析不了就返回空串，空串看着像「这项不适用」，
    于是一整列 quietly 变成没检查过。
    """
    for bad in ({"frames": ""}, {"frames": "N/A"}, {"fps_avg_frame_rate": ""},
                {"fps_avg_frame_rate": "25/0"}, {"fps_avg_frame_rate": "0/1"},
                {"fps_avg_frame_rate": "abc"}, {"duration_s": ""}):
        rows = _fake_rows()
        rows[0].update(bad)
        msg = _exit_msg(g.self_check, rows, _mapping_ok(), expect_videos=2,
                        expect_rows_min=1)
        assert "非法帧数/帧率/时长" in msg, (bad, msg)


def test_self_check_rejects_empty_or_stale_verdict_column() -> None:
    """判定列为空 = 这一列根本没检查过；与重算不符 = 有人手改过 CSV。两种都拦。"""
    for bad in ("", "是是", "yes"):
        rows = _fake_rows()
        rows[0]["frames_vs_dur_x_avgfps"] = bad
        msg = _exit_msg(g.self_check, rows, None, expect_videos=2,
                        expect_rows_min=1)
        assert "判定列为空或与重算不符" in msg, (bad, msg)
    rows = _fake_rows()
    rows[0]["frames_vs_dur_x_avgfps"] = "否"        # 重算是「是」⇒ 值过期/被改
    msg = _exit_msg(g.self_check, rows, None, expect_videos=2, expect_rows_min=1)
    assert "判定列为空或与重算不符" in msg, msg


def test_self_check_rejects_hash_prefix_mismatch_with_mapping() -> None:
    """与仓库映射表逐行互核：源片哈希前 16 位对不上 ⇒ 说明表串了或素材被换过。"""
    rows = _fake_rows()
    m = _mapping_ok()
    m["A.mpg"]["原sha256前16"] = "ffffffffffffffff"
    msg = _exit_msg(g.self_check, rows, m, expect_videos=2, expect_rows_min=1)
    assert "前 16 位不符" in msg, msg


def test_self_check_rejects_broken_derivation_and_counts() -> None:
    rows = _fake_rows()
    m = _mapping_ok()
    m["A.mpg"]["转换后帧数"] = "99"                       # 转码件帧数与映射表不符
    msg = _exit_msg(g.self_check, rows, m, expect_videos=2, expect_rows_min=1)
    assert "转码件帧数与映射表不符" in msg, msg

    rows = _fake_rows()
    rows[1]["derives_from_sha256"] = "d" * 64            # 来源哈希没指向源片
    msg = _exit_msg(g.self_check, rows, _mapping_ok(), expect_videos=2,
                    expect_rows_min=1)
    assert "来源哈希" in msg, msg

    rows = _fake_rows()
    rows[1]["sha256"] = "B" * 64                         # 大写十六进制
    msg = _exit_msg(g.self_check, rows, None, expect_videos=2,
                    expect_rows_min=1)
    assert "sha256 格式不对" in msg, msg

    msg = _exit_msg(g.self_check, _fake_rows(), _mapping_ok(),  # 批次钉子漂了
                    expect_videos=3, expect_rows_min=1)
    assert "视频行数" in msg and "--expect-videos" in msg, msg


# ---------------------------------------------- 输出保护（R112-01 的核心）

def test_refuses_to_write_into_material_or_source_roots() -> None:
    """禁止输出区：素材根目录、仓库源码目录。跟随软链接——链接指过去也算。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        roots, repo = _fake_tree(d)
        safe = d / "safe"
        safe.mkdir()
        kw = dict(roots=roots, repo_root=repo)

        for target in (roots["fst_source"] / "清单.csv",
                       roots["csi_out_fst"] / "清单.csv",
                       repo / "scripts" / "清单.csv",
                       repo / "tests" / "清单.csv",
                       repo / "depressionplex" / "清单.csv"):
            msg = _exit_msg(g.resolve_out, target, **kw)
            assert "拒绝把清单写进" in msg, (target, msg)
            assert not Path(target).exists(), target

        # 已存在的素材文件（真 zip 的替身）：拒绝之外，还得证明它没被动过
        before = roots["exe_zip"].read_bytes()
        msg = _exit_msg(g.resolve_out, roots["exe_zip"], **kw)
        assert "拒绝把清单写进" in msg, msg
        assert roots["exe_zip"].read_bytes() == before

        # 软链接绕过：safe/ 是允许的位置，但链接指向素材根目录
        link = safe / "link.csv"
        os.symlink(roots["fst_source"] / "偷写.csv", link)
        msg = _exit_msg(g.resolve_out, link, **kw)
        assert "拒绝把清单写进" in msg, msg
        assert not (roots["fst_source"] / "偷写.csv").exists()

        # 允许的位置照样放行
        assert g.resolve_out(safe / "ok.csv", **kw) == (safe / "ok.csv").resolve()


def test_default_refuses_to_overwrite_and_update_needs_old_identity() -> None:
    """默认不覆盖；`--update` 要旧哈希对得上、且目标真是一份清单（表头核对）。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        roots, repo = _fake_tree(d)
        (d / "safe").mkdir()
        out = d / "safe" / "清单.csv"
        out.write_text("旧的但重要的内容\n", encoding="utf-8")
        kw = dict(roots=roots, repo_root=repo)

        msg = _exit_msg(g.resolve_out, out, **kw)              # 默认拒绝覆盖
        assert "默认不覆盖" in msg, msg
        assert out.read_text(encoding="utf-8") == "旧的但重要的内容\n"

        msg = _exit_msg(g.resolve_out, out, update=True, **kw)   # 表头不符
        assert "不是一份输入身份清单" in msg, msg
        assert out.read_text(encoding="utf-8") == "旧的但重要的内容\n"

        # 表头对得上就放行（上一版 25 列表头也算，见 PREVIOUS_HEADERS）
        out.write_text(",".join(g.HEADER) + "\n", encoding="utf-8")
        assert g.resolve_out(out, update=True, **kw) == out.resolve()
        for prev in g.PREVIOUS_HEADERS:
            out.write_text(",".join(prev) + "\n", encoding="utf-8")
            assert g.resolve_out(out, update=True, **kw) == out.resolve()


def test_publish_snapshots_old_version_and_never_damages_it() -> None:
    """发布路径：临时文件校验 → 旧版留快照 → 原子替换。哈希对不上就什么都不动。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        out = d / "清单.csv"
        out.write_text("OLD\n", encoding="utf-8")
        old_sha = g.sha256_file(out)
        snaps = d / "snaps"
        rows = _fake_rows()

        # a) 期望哈希不对 ⇒ 拒绝更新，旧文件一个字节没变，不建快照，临时文件清掉
        tmp = d / "t1.csv"
        tmp.write_text(g.render_csv(rows), encoding="utf-8")
        msg = _exit_msg(g.publish, tmp, out, snapshot_dir=snaps,
                        expect_sha="f" * 64)
        assert "拒绝更新" in msg, msg
        assert out.read_text(encoding="utf-8") == "OLD\n"
        assert not snaps.exists() and not tmp.exists()

        # b) 哈希对得上 ⇒ 快照 + 发布
        tmp = d / "t2.csv"
        tmp.write_text(g.render_csv(rows), encoding="utf-8")
        snap = g.publish(tmp, out, snapshot_dir=snaps, expect_sha=old_sha)
        assert snap and snap.is_file() and snap.read_text(encoding="utf-8") == "OLD\n"
        assert snap.parent == snaps, "快照默认落在仓库外，不许污染工作树"
        assert not tmp.exists(), "发布后临时文件应已被 replace 掉"
        with open(out, newline="", encoding="utf-8") as f:
            assert next(csv.reader(f)) == g.HEADER


def test_validate_written_catches_a_broken_file_before_publish() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.csv"
        p.write_text(g.render_csv(_fake_rows()), encoding="utf-8")
        g.validate_written(p, 2)
        p.write_text(",".join(g.HEADER) + "\n" + "a,b\n", encoding="utf-8")
        msg = _exit_msg(g.validate_written, p, 2)
        assert "行数" in msg, msg


# ------------------------------------------------------- 纯函数：时间列口径

def test_parse_pts_sorts_decode_order_and_counts_missing() -> None:
    """`pts_time` 原样读出来是**解码序**（h264 有 B 帧 ⇒ 会出现负间隔）。
    所以「间隔是否恒定」必须排序后判，「原始包序是否单调」单独记一列——
    它不是错误，它正是「包序 ≠ 展示序」的证据。"""
    # 展示序 0/0.04/0.08/0.12，按解码序打乱着给
    out = g.parse_pts("0.080000\n0.000000\n0.120000\n0.040000\n")
    assert out == {"pts_count": 4, "pts_na": 0, "pts_min_s": "0",
                   "pts_max_s": "0.12", "pts_spacing_uniform": "是",
                   "pts_raw_order_monotonic": "否"}, out

    # 已经单调的（MPEG-1 无 B 帧）+ 末尾缺 pts 的包
    out = g.parse_pts("0.540000\n0.580000\nN/A\n")
    assert out["pts_raw_order_monotonic"] == "是"
    assert out["pts_na"] == 1 and out["pts_count"] == 3
    assert out["pts_max_s"] == "0.58", "缺 pts 的包不参与 min/max"
    assert out["pts_spacing_uniform"] == "是"

    # 真空洞（中间缺 pts）⇒ 出现第二种间隔 ⇒ 「否」，这是要人来看的信号
    assert g.parse_pts("0.0\n0.04\nN/A\n0.12\n")["pts_spacing_uniform"] == "否"
    # 全是 N/A / 完全空 ⇒ 六列都空，不猜
    for txt in ("N/A\nN/A\n", ""):
        out = g.parse_pts(txt)
        assert out["pts_min_s"] == "" and out["pts_spacing_uniform"] == ""
        assert out["pts_count"] == (2 if txt else 0)


def test_frames_ok_direction_matters() -> None:
    """帧数 vs 时长×avg_frame_rate 只判**数量**自洽；decode_count 那条是有方向的。

    转码件的容器时长系统性偏短（本批 12 件短 0.5 帧、3 件短 1.5 帧），所以判据是
    `0 ≤ n − d·fps ≤ 1.5`。反过来（帧数**少**于时长×帧率）说明掉帧，那是转码事故，必须红。
    """
    assert g.frames_ok("100", "4.0", "25/1", "container") == "是"
    # 容器时长系统性**偏短** ⇒ d < n/fps ⇒ n − d·fps > 0。实测只有两档：短 0.5 / 1.5 帧
    assert g.frames_ok("100", "3.98", "25/1", "decode_count") == "是"   # 短 0.5 帧
    assert g.frames_ok("100", "3.94", "25/1", "decode_count") == "是"   # 短 1.5 帧
    assert g.frames_ok("100", "3.90", "25/1", "decode_count") == "否"   # 短 2.5 帧 ⇒ 反常
    assert g.frames_ok("100", "4.02", "25/1", "decode_count") == "否"   # 时长比帧数还长
    assert g.frames_ok("99", "4.0", "25/1", "decode_count") == "否"     # 掉帧方向
    assert g.frames_ok("101", "4.0", "25/1", "container") == "否"
    for bad in (("N/A", "4.0", "25/1"), ("100", "", "25/1"),
                ("100", "4.0", "25/0"), ("100", "4.0", "abc")):
        assert g.frames_ok(*bad, "container") == "", bad


def test_display_path_never_leaks_home_and_never_crashes_outside() -> None:
    assert g.display_path(g.HOME / "Work" / "a.mp4") == "~/Work/a.mp4"
    assert g.display_path(Path("/tmp/outside/a.mp4")) == "/tmp/outside/a.mp4"


# --------------------------------------------- 子进程级：真实 CLI 的拒绝路径

def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=str(ROOT))


def test_cli_refuses_dangerous_out_before_touching_any_material() -> None:
    """这三条都在 `build()` 之前返回，所以既快又安全：不哈希、不跑 ffprobe、
    不碰任何素材目录。顺序本身就是保护的一部分。"""
    # a) 写进素材根目录
    target = g.ROOTS["fst_source"] / "绝不该出现的清单.csv"
    r = _cli("--out", str(target))
    assert r.returncode != 0 and "拒绝把清单写进" in (r.stdout + r.stderr)
    assert not target.exists()

    # b) 默认不覆盖已有清单
    r = _cli("--out", str(ROOT / "docs" / g.MANIFEST_NAME))
    assert r.returncode != 0 and "默认不覆盖" in (r.stdout + r.stderr)

    # c) --update 不给旧哈希 ⇒ 拒绝（「我要更新的那一版」必须能点名）
    r = _cli("--out", str(ROOT / "docs" / g.MANIFEST_NAME), "--update")
    assert r.returncode != 0 and "--expect-sha256" in (r.stdout + r.stderr)
