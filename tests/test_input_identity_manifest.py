#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-133 守卫：共用输入身份清单（docs/共用输入身份清单_v1.csv）的自洽钉子。

这张表是 A 线（FST 独立研究链）与 B 线（CSI 定向验证）**共用**的输入身份清单。
它守的不是数值，是几条一旦悄悄失效就会让后面所有对照变成「拿甲比乙」的事实：

1. **文件名不是身份**。FST 与 TST 各有一段同名不同片（`10mg 2周.mp4`、`20mg 2周.mp4`），
   转码目录里还有与源片同名不同哈希的中间件。同名跨范式 ⇒ 哈希必须不同。
2. **MPEG-1 的 r_frame_rate 是 50/1（隔行场频），真实帧率是 25/1**，解码计数才与源片逐帧
   相等；容器时长比源片短半帧。谁拿 r_frame_rate 或容器时长当时基，
   「Min Length Thresh 15 帧」就会从 0.6 s 读成 0.3 s。这里把两个坑钉成测试。
3. **t0（入水时刻）在本项目不存在**（首帧鼠已在水中，DP-057）。缺失是声明出来的
   （`t0_status=missing_not_obtained`），不是漏填；哪天有人补上了 t0，这条会红，逼他改口径。
4. **没有任何几何标称「已人工确认」**、**没有任何素材标称「可当新盲测」**。
   手头的素材全部已被研究过（R3 全量对照、DP-053/055/059），再当盲测就是自欺。
5. **仓库夹具是客户侧原件的真拷贝**：逐字节重哈希三个小文件对表。
6. **EXE 哈希钉死逆向报告的基线**（R1 §目标哈希），防止取证材料被掉包。

表本身由 `scripts/dp133_build_input_identity.py` 生成；本测试只读表，不重哈希大文件
（三个 <2 KB 的夹具除外），CI 上没有素材也能跑。
"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs" / "共用输入身份清单_v1.csv"
MAPPING = ROOT / "docs" / "CSI输入视频映射表.csv"

EXPECTED_HEADER = [
    "material_id", "assay", "role", "path", "bytes", "sha256",
    "codec", "width", "height", "frames", "frames_source",
    "fps_rational", "fps_field_rate", "duration_s", "frames_x_fps_ok",
    "derives_from_sha256", "derives_from_status",
    "t0_source_s", "t0_status",
    "geometry_binding", "geometry_binding_status",
    "human_record_ref", "human_record_status",
    "usage_group", "evidence_status",
]

# 逆向报告（docs/逆向/2026-09-09…:23）的目标 EXE 基线，2026-09-18 在本机
# `unzip -p … | shasum -a 256` 独立复算一致。取证材料掉包即红。
EXE_SHA = {
    "ForcedSwimScan.exe":
        "341a5ba8d9bec53455234a7872ef2bb57cff837c04efaa2ce8d6f5533e2bd054",
    "TailSuspScan.exe":
        "aa85e490abd8c605ac0ac52f874a9c1a7f679b094e6ffb8a8da346690ecbc95c",
}

# 重复哈希（同一份字节出现在两行以上）只许落在三类**已有解释**里，逐类钉死数目：
#   transcode_alias      zztest01.mp4 ≡ 正常5+抑郁1-3.mp4（转码目录里的复制品）
#   repo_fixture_copy    6 个仓库夹具 ≡ 客户侧输出目录里的同名原件（夹具=真拷贝）
#   csi_out_same_delivery 同一批 CSI 输出被交付到两个目录 —— **不是两次运行**
# 出现第四类、或某一类数目变了 ⇒ 要么有新的复制品要登记，要么身份表串了，必须人来看。
DUP_CLASS_EXPECTED = {
    "transcode_alias": 1,
    "repo_fixture_copy": 6,
    "csi_out_same_delivery": 15,
}

# 客户侧四个 CSI 输出目录，按同哈希文件数配对：这两个数字是「有几份独立结果」的上界。
SAME_DELIVERY_PAIRS = {
    ("csi_out_ctrl", "csi_out_norm14"): 6,
    ("csi_out_fst", "csi_out_rest"): 12,
}


def _rows() -> list[dict]:
    with open(MANIFEST, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _mapping() -> list[dict]:
    # 带 BOM：裸 utf-8 会把首列名读成 '﻿范式'
    with open(MAPPING, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def test_header_is_pinned_schema() -> None:
    """表头就是契约：加减列必须改这里，逼出一次显式评审。"""
    with open(MANIFEST, newline="", encoding="utf-8-sig") as f:
        assert next(csv.reader(f)) == EXPECTED_HEADER


def test_row_floor_and_sha_format() -> None:
    rows = _rows()
    assert len(rows) >= 120, len(rows)          # 少了行说明某根目录没扫到
    for r in rows:
        assert len(r["sha256"]) == 64 and r["sha256"] == r["sha256"].lower()
        assert set(r["sha256"]) <= set("0123456789abcdef"), r["material_id"]


def _dup_class(ids: list[str]) -> str:
    if all(i.startswith("transcode_intermediate:") for i in ids):
        return "transcode_alias"
    if any(i.startswith("repo_fixture:") for i in ids):
        return "repo_fixture_copy"
    if all(i.startswith("csi_out:") for i in ids):
        return "csi_out_same_delivery"
    return "UNCLASSIFIED"


def test_aliasing_is_exhaustively_explained() -> None:
    """重复哈希只许出现在三类已登记的别名组里，且每类数目钉死。"""
    seen: dict[str, list[str]] = {}
    for r in _rows():
        seen.setdefault(r["sha256"], []).append(r["material_id"])
    dups = {h: ids for h, ids in seen.items() if len(ids) > 1}
    by_class: dict[str, list] = {}
    for h, ids in dups.items():
        by_class.setdefault(_dup_class(ids), []).append((h[:12], sorted(ids)))
    got = {k: len(v) for k, v in by_class.items()}
    assert got == DUP_CLASS_EXPECTED, sorted(by_class.get("UNCLASSIFIED", [])) or got
    flat = [i for ids in dups.values() for i in ids]
    assert any("zztest01" in i for i in flat), flat
    assert sum(1 for i in flat if i.startswith("repo_fixture:")) == 6, flat


def test_csi_output_dirs_are_copies_not_independent_runs() -> None:
    """四个 CSI 输出目录里有两对是**同一批交付的两次落盘**，不是四次独立运行。

    `csi_out_ctrl`（…_对照参数）与 `csi_out_norm14`（正常1-4对照数据）有 6 个文件逐字节相同，
    `csi_out_fst` 与 `csi_out_rest`（剩余游泳视频数据）有 12 个。谁拿目录数当「独立重复次数」，
    就是在数副本 —— 这条把上界钉住：**71 行 csi_out 记录只有 53 份互不相同的字节**。
    """
    per_root: dict[str, dict[str, str]] = {}
    n_csi_out = 0
    for r in _rows():
        if not r["material_id"].startswith("csi_out:"):
            continue
        n_csi_out += 1
        root = r["material_id"].split(":")[1]
        per_root.setdefault(root, {})[Path(r["path"]).name] = r["sha256"]
    for (a, b), want in SAME_DELIVERY_PAIRS.items():
        shared = {n for n, h in per_root[a].items()
                  if per_root[b].get(n) == h}
        assert len(shared) == want, (a, b, sorted(shared))
    distinct = {r["sha256"] for r in _rows()
                if r["material_id"].startswith("csi_out:")}
    assert n_csi_out == 71 and len(distinct) == 53, (n_csi_out, len(distinct))


def test_same_name_across_assays_never_same_hash() -> None:
    """`10mg 2周.mp4` 在游泳和悬尾是两段不同录像：同名跨范式 ⇒ 哈希必须不同。"""
    per_name: dict[str, dict[str, str]] = {}
    for r in _rows():
        if r["role"] in ("source_video", "csi_transcode"):
            per_name.setdefault(Path(r["path"]).name, {})[r["assay"]] = r["sha256"]
    for name, per_assay in per_name.items():
        if len(per_assay) > 1:
            assert len(set(per_assay.values())) == len(per_assay), name


def test_agrees_with_csi_mapping_table() -> None:
    """与 docs/CSI输入视频映射表.csv 逐行互核：前 16 位、帧数、时长、转码源哈希。"""
    rows = _rows()
    src = {(r["assay"], Path(r["path"]).name): r for r in rows
           if r["role"] == "source_video"}
    transc = {Path(r["path"]).name: r for r in rows
              if r["role"] == "csi_transcode"}
    for m in _mapping():
        s = src[(m["范式"], m["原视频"])]
        assert s["sha256"].startswith(m["原sha256前16"]), m["原视频"]
        assert s["frames"] == m["原帧数"], m["原视频"]
        assert abs(float(s["duration_s"]) - float(m["时长秒"])) < 0.005
        assert s["width"] + "x" + s["height"] == m["原尺寸"]
        t = transc[m["CSI用文件"]]
        assert t["frames"] == m["转换后帧数"], m["CSI用文件"]
        assert t["derives_from_sha256"] == s["sha256"], m["CSI用文件"]
        assert t["derives_from_status"] == "confirmed_by_mapping_table"


def test_mpeg1_field_rate_and_halfframe_truncation_pinned() -> None:
    """MPEG-1 转码件：r_frame_rate=50/1 是场频陷阱，avg=25/1 且解码计数==源帧数；
    容器时长只许比源片短、且短不超过半帧（时长不是身份键，帧数才是）。"""
    rows = _rows()
    # 键必须带 assay：`10mg 2周.mp4` 在 FST 和 TST 是两段不同录像（382.48 s / 386.44 s）。
    # 只按文件名建字典，后写的 TST 会盖掉 FST，于是 0.02 s 的半帧截断被读成 3.98 s 的差
    # —— 本测试自己就犯了这张表要防的那个错，2026-09-18 实测抓到，故键里带上 assay。
    src_dur = {(r["assay"], Path(r["path"]).name): float(r["duration_s"])
               for r in rows if r["role"] == "source_video"}
    n = 0
    for r in rows:
        if r["codec"] != "mpeg1video":
            continue
        n += 1
        assert r["fps_field_rate"] == "50/1", r["material_id"]
        assert r["fps_rational"] == "25/1", r["material_id"]
        assert r["frames_source"] == "decode_count", r["material_id"]
        key = _source_key_for(r)
        if key is None:
            continue           # 两个 fallback 不在映射表里，源片无从对起
        gap = (src_dur[key] - float(r["duration_s"])) * 25   # 换算成帧
        # 实测只有两种：短 0.5 帧（12 件）或短 1.5 帧（3 件）。多一个值就说明
        # 容器时长的行为变了，或者转码掉帧了 —— 两种都得人来看。
        assert round(gap, 3) in (0.5, 1.5), (r["material_id"], gap)
    assert n >= 15, n          # 14 主格式 + 1 备用 mpg；少了说明有转码件没扫到


def _source_key_for(r: dict) -> tuple[str, str] | None:
    """csi_in 文件名 → (范式, 源片名)（FST_ctrl_1to4_x2.mpg → (FST, 正常1-4.mp4)）。

    两个 fallback 件（`FST_ctrl_1to4_x1_fallback.mpg` / `…_x2_fallback.avi`）
    **不在映射表里**，返回 None；它们在清单里记 `unknown_provenance`。
    """
    table = {m["CSI用文件"]: (m["范式"], m["原视频"]) for m in _mapping()}
    return table.get(Path(r["path"]).name)


def test_unregistered_transcodes_are_pinned_at_two() -> None:
    """映射表之外的转码件只许是那 2 个 fallback。出现第 3 个 ⇒ 有人往 csi_in
    塞了没登记的文件，而它的源片身份是未知的（清单会写 unknown_provenance）。"""
    rows = [r for r in _rows() if r["role"] == "csi_transcode"]
    unreg = sorted(Path(r["path"]).name for r in rows
                   if _source_key_for(r) is None)
    assert unreg == ["FST_ctrl_1to4_x1_fallback.mpg",
                     "FST_ctrl_1to4_x2_fallback.avi"], unreg
    assert len(rows) == 16, len(rows)
    for r in rows:
        if _source_key_for(r) is None:
            assert r["derives_from_status"] == "unknown_provenance", r["material_id"]


def test_clips_derive_from_known_tst_sources() -> None:
    """27 个切片的源哈希必须落在 7 段 TST 源片里（cut_provenance 逐段记了全量哈希）。"""
    rows = _rows()
    tst_shas = {r["sha256"] for r in rows if r["assay"] == "TST"
                and r["role"] == "source_video"}
    clips = [r for r in rows if r["role"] == "tst_clip"]
    assert len(clips) == 27, len(clips)
    for r in clips:
        assert r["derives_from_status"] == "confirmed", r["material_id"]
        assert r["derives_from_sha256"] in tst_shas, r["material_id"]


def test_frames_x_fps_self_consistent_everywhere() -> None:
    bad = [r["material_id"] for r in _rows()
           if r["frames"] and r["frames_x_fps_ok"] != "是"]
    assert not bad, bad


def test_t0_declared_absent_not_silently_empty() -> None:
    """t0 全缺是**声明**出来的：视频行 missing_not_obtained，非视频行 na。"""
    rows = _rows()
    for r in rows:
        assert r["t0_source_s"] == "", r["material_id"]
        assert r["t0_status"] in ("missing_not_obtained", "na"), r["material_id"]
    video_rows = [r for r in rows if r["role"] in
                  ("source_video", "csi_transcode", "tst_clip")]
    assert video_rows and all(r["t0_status"] == "missing_not_obtained"
                              for r in video_rows)


def test_no_confirmed_geometry_and_no_fresh_blind_claim() -> None:
    """不许有「几何已人工确认」，也不许有素材自称可当新盲测。"""
    for r in _rows():
        assert r["geometry_binding_status"] in (
            "na", "bound_by_filename_only"), r["material_id"]
        assert r["usage_group"] in ("historical_or_dev_validation",
                                    "test_golden", "engineering_only",
                                    "forensic_never_execute"), r["material_id"]


def test_repo_fixtures_are_true_copies() -> None:
    """三个夹具当场重哈希，必须与表里客户侧原件的哈希一致（夹具=真拷贝）。"""
    rows = {r["material_id"]: r for r in _rows()}
    pairs = [
        ("tests/fixtures/csi_fst/10mg 2周.SET",
         "csi_out:csi_out_fst:10mg 2周.SET"),
        ("tests/fixtures/csi_fst/正常1-4对照更改.SET",
         "csi_out:csi_out_ctrl:正常1-4对照更改.SET"),
        ("tests/fixtures/csi_fst/正常1-4.CLB",
         "csi_out:csi_out_fst:正常1-4.CLB"),
    ]
    for rel, origin_id in pairs:
        p = ROOT / rel
        dig = hashlib.sha256(p.read_bytes()).hexdigest()
        assert rows[f"repo_fixture:na:{p.name}"]["sha256"] == dig, rel
        assert rows[origin_id]["sha256"] == dig, origin_id


def test_exe_hashes_match_reverse_report_baseline() -> None:
    rows = {r["material_id"].rsplit(":", 1)[-1]: r for r in _rows()
            if r["role"] == "csi_exe"}
    assert set(rows) == set(EXE_SHA), sorted(rows)
    for name, r in rows.items():
        assert r["sha256"] == EXE_SHA[name], name
        assert r["usage_group"] == "forensic_never_execute", name
        assert r["evidence_status"] == "static_recovered", name
