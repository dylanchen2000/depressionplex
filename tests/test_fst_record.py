"""诊断记录层测试：分区闭合、空杯语义、身份关联、落盘纪律。

钉死的口径（Spec A §5.1 S16 / §6.2 + R2-115 T1）：
- 四态分区不闭合 ⇒ **raise**，不许带着一笔糊涂账出报告；
- 申报空杯 ⇒ behavior_seconds=None + "empty_no_output"（不是 0 秒）；
  未申报 ⇒ 同样 None 但语义是"不套标准窗"——两种 None 必须写不同 semantics；
- T1：计数叫 sampled_*（抽样记录数，不是连续帧数）；时长只出显式命名的
  时间加权估计（区间权重 = 实际源帧号差 / fps，尾处理写明，不叫"秒数统计"）；
- 身份查清单：多别名全列且 material_id=null，查不到写"不猜"；
- 记录不进仓库，落盘原子（不留 .tmp 半成品）。
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

from depressionplex.fst_research import overlay as ov
from depressionplex.fst_research import record as rec
from depressionplex.fst_research.cup_perception import (
    QUALITY_DECLARED_ABSENT, QUALITY_LOST_SHORT, QUALITY_OBSERVED,
    QUALITY_UNCLEAR, FrameDiag)


def _diag(frame: int, quality: str, reasons: tuple = ()) -> FrameDiag:
    return FrameDiag(frame, quality, reasons)


def _mixed(n_obs=5, n_unc=2, n_lost=1) -> list:
    diags = [_diag(i, QUALITY_OBSERVED) for i in range(n_obs)]
    diags += [_diag(100 + i, QUALITY_UNCLEAR,
                    ("连续 2 帧无候选且无法归为短暂丢失（long_absence_unexplained）",))
              for i in range(n_unc)]
    diags += [_diag(200 + i, QUALITY_LOST_SHORT,
                    ("连续 1 帧无候选，前后均可见（短暂分割失败）",))
              for i in range(n_lost)]
    return diags


def test_check_partition_closed_and_broken() -> None:
    counts = {QUALITY_OBSERVED: 5, QUALITY_UNCLEAR: 2,
              QUALITY_LOST_SHORT: 1, QUALITY_DECLARED_ABSENT: 0}
    assert rec.check_partition(counts, 8) == []
    assert rec.check_partition(counts, 9) != []          # 合计≠帧数
    broken = dict(counts)
    broken["weird_state"] = 1
    problems = rec.check_partition(broken, 9)
    assert any("未知可见性状态" in p for p in problems)
    partial = {QUALITY_OBSERVED: 5}
    assert any("缺少状态计数" in p
               for p in rec.check_partition(partial, 5))


def test_counts_of_covers_every_frame() -> None:
    counts = rec.counts_of(_mixed())
    assert counts == {QUALITY_OBSERVED: 5, QUALITY_UNCLEAR: 2,
                      QUALITY_LOST_SHORT: 1, QUALITY_DECLARED_ABSENT: 0}
    assert sum(counts.values()) == 8


def test_top_reasons_skips_observed_and_limits() -> None:
    diags = _mixed(n_obs=3, n_unc=4, n_lost=2)
    rows = rec.top_reasons(diags, limit=1)
    assert len(rows) == 1
    assert rows[0]["frames"] == 4                        # unclear 的原因最多
    # 原因键取"："/"（"前的前缀：报告里先看到人话，详情在 frame_rows
    assert rows[0]["reason"] == "连续 2 帧无候选且无法归为短暂丢失"
    # observed 帧不贡献原因
    only_obs = [_diag(i, QUALITY_OBSERVED) for i in range(3)]
    assert rec.top_reasons(only_obs) == []


def test_cup_record_refuses_nonclosing_partition() -> None:
    diags = _mixed() + [_diag(9, "not_a_quality")]       # 状态机漏分支
    try:
        rec.cup_record(cup_index=0, diags=diags, fps=25.0, declared_absent=False,
                       geometry={}, features_summary={}, spatial_scale_px=10.0)
    except ValueError as e:
        assert "分区不闭合" in str(e)
    else:
        raise AssertionError("分区不闭合居然出了记录")


def test_cup_record_empty_vs_no_window_semantics() -> None:
    geo = {"interior": [74, 17, 106, 93], "confirmed": False}
    declared = [_diag(i, QUALITY_DECLARED_ABSENT,
                      ("人工申报空杯：结果为空，不输出 0 秒",)) for i in range(4)]
    r = rec.cup_record(cup_index=1, diags=declared, fps=2.0, declared_absent=True,
                       geometry=geo, features_summary={}, spatial_scale_px=77.0)
    assert r["behavior_seconds"] is None                 # 不是 0！
    assert r["behavior_seconds_semantics"] == rec.BEHAVIOR_SECONDS_EMPTY
    assert r["sampled_frames"] == 4
    assert r["sampled_state_counts"][QUALITY_DECLARED_ABSENT] == 4
    # R2-115 T1：申报空杯 ⇒ 时间加权估计全 None，绝不输出 0 秒
    assert all(v is None for v in r["time_weighted_sampled_s"].values())
    assert r["time_weighting"]["computed"] is False
    assert "0 秒" in r["time_weighting"]["reason"]

    r2 = rec.cup_record(cup_index=0, diags=_mixed(), fps=2.0, declared_absent=False,
                        geometry=geo, features_summary={}, spatial_scale_px=77.0)
    assert r2["behavior_seconds"] is None
    assert r2["behavior_seconds_semantics"] == rec.BEHAVIOR_SECONDS_NO_WINDOW
    assert r2["behavior_seconds_semantics"] != r["behavior_seconds_semantics"]
    assert r2["sampled_frames"] == 8                     # 抽样记录数，不叫"帧数"
    assert r2["time_weighting"]["computed"] is True


# ---------------------------------------------------------------------------
# R2-115 T1：时间加权估计量——区间权重、尾处理、拒绝坏输入
# ---------------------------------------------------------------------------

def test_time_weighted_interval_weights_and_tail() -> None:
    """权重 = 实际源帧号差 / fps；尾记录按一个步长假设计（写明在 provenance）。"""
    diags = [_diag(0, QUALITY_OBSERVED), _diag(5, QUALITY_UNCLEAR),
             _diag(10, QUALITY_LOST_SHORT)]
    out = rec.time_weighted_seconds(diags, fps=25.0, tail_spacing_frames=5,
                                    declared_absent=False)
    s = out["seconds"]
    assert abs(s[QUALITY_OBSERVED] - 0.2) < 1e-12   # 帧 0→5 = 5 帧 / 25
    assert abs(s[QUALITY_UNCLEAR] - 0.2) < 1e-12    # 帧 5→10
    assert abs(s[QUALITY_LOST_SHORT] - 0.2) < 1e-12  # 尾 = 步长 5 帧（假设）
    prov = out["provenance"]
    assert prov["computed"] is True
    assert "帧号差" in prov["method"]
    assert "假设" in prov["tail_handling"] and "5" in prov["tail_handling"]
    assert "不是连续逐帧统计" in prov["note"]


def test_time_weighted_uneven_intervals_not_averaged() -> None:
    """不等距抽样（缺口后的记录间隔大）⇒ 权重跟着变大，不是按条数摊平。"""
    diags = [_diag(0, QUALITY_OBSERVED), _diag(2, QUALITY_OBSERVED),
             _diag(52, QUALITY_OBSERVED)]      # 中间隔了 50 帧的缺口段
    s = rec.time_weighted_seconds(diags, fps=25.0, tail_spacing_frames=2,
                                  declared_absent=False)["seconds"]
    assert abs(s[QUALITY_OBSERVED] - (2 + 50 + 2) / 25.0) < 1e-12


def test_time_weighted_empty_and_declared_are_none_not_zero() -> None:
    empty = rec.time_weighted_seconds([], fps=25.0, tail_spacing_frames=5,
                                      declared_absent=False)
    assert all(v is None for v in empty["seconds"].values())
    assert "为空" in empty["provenance"]["reason"]
    dec = rec.time_weighted_seconds([_diag(0, QUALITY_OBSERVED)], fps=25.0,
                                    tail_spacing_frames=5, declared_absent=True)
    assert all(v is None for v in dec["seconds"].values())


def test_time_weighted_rejects_bad_input() -> None:
    d = [_diag(0, QUALITY_OBSERVED)]
    for kw in (dict(fps=0.0, tail_spacing_frames=5),
               dict(fps=-1.0, tail_spacing_frames=5),
               dict(fps=25.0, tail_spacing_frames=0)):
        try:
            rec.time_weighted_seconds(d, declared_absent=False, **kw)
        except ValueError:
            pass
        else:
            raise AssertionError(f"没拒绝坏参数: {kw}")
    # 帧号不严格递增 ⇒ raise（区间权重会变成负数）
    bad = [_diag(5, QUALITY_OBSERVED), _diag(5, QUALITY_UNCLEAR)]
    try:
        rec.time_weighted_seconds(bad, fps=25.0, tail_spacing_frames=1,
                                  declared_absent=False)
    except ValueError as e:
        assert "递增" in str(e)
    else:
        raise AssertionError("帧号重复没被拒绝")


def test_cup_record_motion_classification_not_performed() -> None:
    r = rec.cup_record(cup_index=0, diags=_mixed(), fps=25.0, declared_absent=False,
                       geometry={}, features_summary={}, spatial_scale_px=10.0)
    assert r["motion_classification"]["performed"] is False
    assert "科学口径" in r["motion_classification"]["reason"]
    assert r["partition_problems"] == []


def test_build_record_carries_flags() -> None:
    sampling = {"step_frames": 5, "sampled_frames": 100}
    prov = {"run_id": "run_x", "code": {"vcs": "git", "commit": "c" * 40},
            "command_line": "python -m depressionplex.cli.fst_research --step 5"}
    params = {"step_frames": 5, "cup_min_width_px": 40,
              "lost_short_max_gap_frames": 11}
    record = rec.build_record(
        source={"sha256": "x"}, time_base={}, clock_ledger={}, window={},
        coverage={}, sampling=sampling, geometry_confirmation={"confirmed": False},
        cups=[], manifest=None, limits=["研究诊断"],
        provenance=prov, research_params=params)
    assert record["must_not_enter_acceptance_paths"] is True
    assert record["purpose"] == "research_diagnostics_only"
    assert record["schema_version"] == "fst-research-v1"
    assert record["limits"] == ["研究诊断"]
    assert record["generated_at"]
    # R2-115 T1：抽样口径是记录的独立一节，不藏在 coverage 里
    assert record["sampling"] == sampling
    # R2-115 P组：代码出处与完整研究配置也是记录的独立节
    assert record["provenance"] == prov
    assert record["research_params"] == params


def test_write_record_atomic_and_repo_refused() -> None:
    record = {"a": [1, 2], "中文": "值"}
    with tempfile.TemporaryDirectory() as td:
        out = rec.write_record(record, Path(td) / "sub" / "诊断_x.json")
        assert out.exists()
        assert json.loads(out.read_text(encoding="utf-8")) == record
        assert not list(Path(td).rglob("*.tmp"))         # 半成品不留
    try:
        rec.write_record(record, ov.REPO_ROOT / "data" / "诊断.json")
    except ValueError:
        pass
    else:
        raise AssertionError("write_record 接受了仓库内落点")


# ---------------------------------------------------------------------------
# R2-115 P组：研究证据不被下一轮覆盖
# ---------------------------------------------------------------------------

def test_write_record_refuses_overwrite() -> None:
    """旧记录是上一轮的证据，不是草稿：第二次写同一目标 ⇒ 拒绝，旧字节不动。"""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "诊断_x.json"
        rec.write_record({"v": 1}, out)
        old_bytes = out.read_bytes()
        try:
            rec.write_record({"v": 2}, out)
        except FileExistsError as e:
            assert "拒绝覆盖" in str(e)
        else:
            raise AssertionError("write_record 覆盖了已有研究证据")
        assert out.read_bytes() == old_bytes


def test_new_run_dir_unique_and_refuses_collision() -> None:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td).resolve() / "out"     # 守卫会 resolve（macOS /var → /private/var）
        d1, rid1 = rec.new_run_dir(base, "正常1", run_id="run_x")
        assert d1 == base / "正常1_run_x" and d1.is_dir()
        assert rid1 == "run_x"
        # 撞名 ⇒ 拒绝混写（不 exist_ok）
        try:
            rec.new_run_dir(base, "正常1", run_id="run_x")
        except FileExistsError:
            pass
        else:
            raise AssertionError("new_run_dir 复用了已存在的 run 目录")
        # 自动 run_id：同一秒内重复运行也不撞（时间戳 + 随机 hex）
        d2, rid2 = rec.new_run_dir(base, "正常1")
        d3, rid3 = rec.new_run_dir(base, "正常1")
        assert rid2 != rid3 and d2 != d3 and d2.is_dir() and d3.is_dir()
        assert d1.is_dir()                       # 旧 run 目录不动
    # 仓库内落点照旧拒绝
    try:
        rec.new_run_dir(ov.REPO_ROOT / "data", "x")
    except ValueError:
        pass
    else:
        raise AssertionError("new_run_dir 接受了仓库内落点")


def test_make_run_id_format() -> None:
    rid = rec.make_run_id(datetime(2026, 9, 19, 14, 5, 6))
    assert rid.startswith("run_20260919-140506_")
    tail = rid.rsplit("_", 1)[-1]
    assert len(tail) == 8
    int(tail, 16)                                # 随机段是 hex


def test_code_provenance_git_and_non_git() -> None:
    prov = rec.code_provenance(ov.REPO_ROOT)     # 本仓库工作树
    assert prov["vcs"] == "git"
    assert len(prov["commit"]) == 40
    int(prov["commit"], 16)
    assert isinstance(prov["dirty"], bool)       # dirty 标记必须存在
    assert isinstance(prov["dirty_file_count"], int)
    with tempfile.TemporaryDirectory() as td:    # 非 git 目录 ⇒ 明说，不猜不编
        prov2 = rec.code_provenance(td)
        assert prov2["vcs"] in ("not_a_git_repo", "git_unavailable")


def test_completion_manifest_written_last_with_hashes() -> None:
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td).resolve()          # 守卫会 resolve
        a = run_dir / "诊断_x.json"
        a.write_text('{"v": 1}', encoding="utf-8")
        b = run_dir / "叠加_x_杯1.mp4"
        b.write_bytes(b"\x00\x01\x02")
        mp = rec.write_completion_manifest(
            run_dir, run_id="run_x", input_sha256="ab" * 32,
            code={"vcs": "git", "commit": "c" * 40},
            artifacts={"诊断记录": a, "叠加短片_杯1": b})
        assert mp.name == rec.MANIFEST_NAME and mp.parent == run_dir
        m = json.loads(mp.read_text(encoding="utf-8"))
        assert m["schema"] == rec.MANIFEST_SCHEMA
        assert m["run_id"] == "run_x" and m["status"] == "complete"
        assert m["input_sha256"] == "ab" * 32
        assert m["code"]["commit"] == "c" * 40
        assert m["completed_at"]
        assert set(m["files"]) == {"诊断记录", "叠加短片_杯1"}
        f = m["files"]["诊断记录"]
        assert f["path"] == "诊断_x.json"
        assert f["bytes"] == a.stat().st_size
        assert f["sha256"] == rec.sha256_of(a) and len(f["sha256"]) == 64
        assert "未完成" in m["note"]


def test_completion_manifest_refuses_missing_artifact() -> None:
    """清单不许替不存在的文件背书；失败时连清单文件都不该出现。"""
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        try:
            rec.write_completion_manifest(
                run_dir, run_id="run_x", input_sha256="ab" * 32,
                code={}, artifacts={"诊断记录": run_dir / "不存在.json"})
        except FileNotFoundError as e:
            assert "不存在" in str(e)
        else:
            raise AssertionError("完成清单列了不存在的产物")
        assert not (run_dir / rec.MANIFEST_NAME).exists()


def test_sha256_of_known_vector() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "abc.bin"
        p.write_bytes(b"abc")
        assert rec.sha256_of(p) == (
            "ba7816bf8f01cfea414140de5dae2223b00361a3"
            "96177a9cb410ff61f20015ad")


def _manifest(td: Path, rows: list, *, bom=True, name="清单.csv") -> Path:
    p = td / name
    header = "material_id,assay,role,path,sha256,t0_source_s,t0_status\n"
    lines = [header] + [",".join(r) + "\n" for r in rows]
    p.write_text("".join(lines), encoding="utf-8-sig" if bom else "utf-8")
    return p


def test_manifest_lookup_single_hit() -> None:
    sha = "ab" * 32
    with tempfile.TemporaryDirectory() as td:
        m = _manifest(Path(td), [
            ["FST-V01", "FST", "source_video", "游泳/正常1.mp4", sha, "",
             "not_found_in_checked_materials"]])
        got = rec.manifest_lookup(m, sha.upper())        # 大小写不敏感
    assert got["found"] is True
    assert got["material_id"] == "FST-V01"
    assert got["aliases"][0]["t0_status"] == "not_found_in_checked_materials"


def test_manifest_lookup_multi_alias_id_null() -> None:
    sha = "cd" * 32
    with tempfile.TemporaryDirectory() as td:
        m = _manifest(Path(td), [
            ["FST-V01", "FST", "source_video", "游泳/正常1.mp4", sha, "", "x"],
            ["FST-V01-T", "FST", "transcode", "trans/正常1.mkv", sha, "", "x"]])
        got = rec.manifest_lookup(m, sha)
    assert got["found"] is True
    assert got["material_id"] is None                    # 多别名 ≠ 已核对来源
    assert got["distinct_material_ids"] == ["FST-V01", "FST-V01-T"]
    assert len(got["aliases"]) == 2


def test_manifest_lookup_miss_and_missing_file() -> None:
    with tempfile.TemporaryDirectory() as td:
        m = _manifest(Path(td), [["FST-V01", "FST", "source_video", "a.mp4",
                                  "ee" * 32, "", "x"]])
        got = rec.manifest_lookup(m, "ff" * 32)
        assert got["found"] is False
        assert "不猜" in got["note"]
        assert rec.manifest_lookup(Path(td) / "没有.csv", "ff" * 32) is None
