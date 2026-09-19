"""诊断记录层测试：分区闭合、空杯语义、身份关联、落盘纪律。

钉死的口径（Spec A §5.1 S16 / §6.2）：
- 四态分区不闭合 ⇒ **raise**，不许带着一笔糊涂账出报告；
- 申报空杯 ⇒ behavior_seconds=None + "empty_no_output"（不是 0 秒）；
  未申报 ⇒ 同样 None 但语义是"不套标准窗"——两种 None 必须写不同 semantics；
- 身份查清单：多别名全列且 material_id=null，查不到写"不猜"；
- 记录不进仓库，落盘原子（不留 .tmp 半成品）。
"""

from __future__ import annotations

import json
import tempfile
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
    assert r["quality_counts"][QUALITY_DECLARED_ABSENT] == 4
    assert r["observable_durations_s"][QUALITY_DECLARED_ABSENT] == 2.0

    r2 = rec.cup_record(cup_index=0, diags=_mixed(), fps=2.0, declared_absent=False,
                        geometry=geo, features_summary={}, spatial_scale_px=77.0)
    assert r2["behavior_seconds"] is None
    assert r2["behavior_seconds_semantics"] == rec.BEHAVIOR_SECONDS_NO_WINDOW
    assert r2["behavior_seconds_semantics"] != r["behavior_seconds_semantics"]


def test_cup_record_motion_classification_not_performed() -> None:
    r = rec.cup_record(cup_index=0, diags=_mixed(), fps=25.0, declared_absent=False,
                       geometry={}, features_summary={}, spatial_scale_px=10.0)
    assert r["motion_classification"]["performed"] is False
    assert "科学口径" in r["motion_classification"]["reason"]
    assert r["partition_problems"] == []


def test_build_record_carries_flags() -> None:
    record = rec.build_record(
        source={"sha256": "x"}, time_base={}, clock_ledger={}, window={},
        coverage={}, geometry_confirmation={"confirmed": False},
        cups=[], manifest=None, limits=["研究诊断"])
    assert record["must_not_enter_acceptance_paths"] is True
    assert record["purpose"] == "research_diagnostics_only"
    assert record["schema_version"] == "fst-research-v1"
    assert record["limits"] == ["研究诊断"]
    assert record["generated_at"]


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
