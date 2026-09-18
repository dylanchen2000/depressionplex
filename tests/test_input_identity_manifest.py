#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-133 守卫：共用输入身份清单（docs/共用输入身份清单_v1.csv）的自洽钉子。

这张表是 A 线（FST 独立研究链）与 B 线（CSI 定向验证）**共用**的输入身份清单。
它守的不是数值，是几条一旦悄悄失效就会让后面所有对照变成「拿甲比乙」的事实：

1. **身份键是内容哈希（sha256），不是文件名、也不是帧数。** FST 与 TST 各有一段同名
   不同片（`10mg 2周.mp4`、`20mg 2周.mp4`），转码目录里还有与源片同名不同哈希的中间件。
   同名跨范式 ⇒ 哈希必须不同。**帧数相同只支持「数量一致」**，不证明逐帧内容或时间等价；
   身份靠哈希加上可核验的源/转码/裁剪派生关系（返修 R112-02：撤回「帧数才是身份键」）。
2. **时间基准四件事分开记**：`fps_avg_frame_rate`（容器声称的平均帧率）、
   `fps_r_frame_rate`（ffprobe 的 `r_frame_rate`，官方定义是「能表达该流时间戳的
   **基础帧率估计**」）、解码帧数、逐包 `pts_time` 统计。本批 MPEG-1 转码件上
   `r_frame_rate=50/1` 而 `avg_frame_rate=25/1` —— 这个**差异是实测事实**，但
   「50/1 就是隔行场频」这个**解释被自己的数据否掉了**：`field_order` 实测 66 件是
   `progressive`、2 件 mjpeg 不上报，**没有一件是隔行**（返修 R112-02，撤回原结论）。
   坑仍然要钉：谁拿 `r_frame_rate` 或容器时长当时基，「Min Length Thresh 15 帧」
   就会从 0.6 s 读成 0.3 s —— 但钉的是「用 avg_frame_rate + pts」，不是「用场频解释」。
3. **转码件与源片的媒体时间映射是量出来的**：14 件已登记转码件的 `pts_count` 与源片
   帧数逐一相等，`pts_min` 一律 = 源片 + **0.54 s**；`pts_max` 11 件是 +0.54 s、
   3 件是 +0.50 s，而那 3 件**最后一个包的 `pts_time` 是 N/A**（pts_max 因此读早一帧），
   帧数并没有少。这条正是 Spec A §6.1「若存在转码时间变化，先验证映射」要的映射。
4. **t0（试次起点）在本次核对的材料里没找到**（FST 首帧鼠已在水中，DP-057）。缺失是
   **声明**出来的，不是漏填：FST 视频行 `t0_status=not_found_in_checked_materials`，
   TST 视频行另用 `na_tst_no_water_entry`（TST 没有「入水」这件事，不许套 FST 的理由）。
   口径限定：**「在本次核对的材料中未取得」≠「任何人的任何记录里都不可能有」**。
   哪天有人补上了 t0，这条会红，逼他改口径。
5. **没有任何几何标称「已人工确认」**、**没有任何素材标称「可当新盲测」**。
   手头的素材全部已被研究过（R3 全量对照、DP-053/055/059），再当盲测就是自欺。
6. **仓库夹具是客户侧原件的真拷贝**：逐字节重哈希三个小文件对表。
7. **EXE 哈希钉死逆向报告的基线**（R1 §目标哈希），防止取证材料被掉包。

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

#: 2026-09-19 起 32 列（原 25 列）。改名与新增都是返修 R112-02 要求的：
#: 列名一律用 **ffprobe 的原始字段名**，不带解释；时间戳校验单列成组。
EXPECTED_HEADER = [
    "material_id", "assay", "role", "path", "bytes", "sha256",
    "codec", "width", "height", "field_order",
    "frames", "frames_source",
    "fps_avg_frame_rate", "fps_r_frame_rate", "duration_s",
    "frames_vs_dur_x_avgfps",
    "pts_count", "pts_na", "pts_min_s", "pts_max_s",
    "pts_spacing_uniform", "pts_raw_order_monotonic",
    "derives_from_sha256", "derives_from_status",
    "t0_source_s", "t0_status",
    "geometry_binding", "geometry_binding_status",
    "human_record_ref", "human_record_status",
    "usage_group", "evidence_status",
]

#: ffprobe 表示**隔行**的 field_order 取值。本批一个都不该出现——
#: 出现了就说明「r_frame_rate=50/1 是场频」那个被撤回的解释又有人捡回来了。
INTERLACED_FIELD_ORDERS = {"tt", "bb", "tb", "bt"}

#: 转码件相对源片的实测起点偏移（秒）。14 件全部一致。
TRANSCODE_PTS_OFFSET_S = 0.54

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


#: 四个 CSI 输出目录里「互不相同的字节」按类型的分区。
#: **这张表以前是手填的，填错过一次**：写成 `3 SET + 5 CLB + 43 xlsx + 4 BMP = 55`，
#: 而 distinct 总数是 53 —— 一个和自身矛盾的分区（返修 R114-07 要求「从同一份清单
#: 重新生成分类，不要手工改数字」）。所以现在由 `test_…` 当场从清单算，
#: 这里只钉住算出来的值。2026-09-19 复算：3 + 8 + 34 + 8 = 53。
CSI_OUT_DISTINCT_BY_TYPE = {"set": 3, "clb": 8, "xlsx": 34, "bmp": 8}


def test_csi_out_distinct_bytes_partition_is_computed_not_handwritten() -> None:
    """71 行 CSI 输出 = 53 份互不相同的字节，且**按类型的分区必须自己加起来等于 53**。

    这条测试存在的理由就是那次手填事故：`3+5+43+4=55` 在数学上不可能是一个正确的
    distinct 分区，因为**没有任何一个 sha256 同时以两种扩展名出现**（这里也断言），
    所以各类型的 distinct 数相加**必然**等于总 distinct 数。凡是加起来不等的分区，
    一定是把「某个目录里的行数」当成了「全局去重数」。
    """
    rows = [r for r in _rows() if r["material_id"].startswith("csi_out:")]
    assert len(rows) == 71, len(rows)

    per_sha_ext: dict[str, set] = {}
    per_sha_ids: dict[str, list] = {}
    for r in rows:
        ext = Path(r["path"]).suffix.lstrip(".").lower()
        per_sha_ext.setdefault(r["sha256"], set()).add(ext)
        per_sha_ids.setdefault(r["sha256"], []).append(r["material_id"])
    distinct = set(per_sha_ext)
    assert len(distinct) == 53, len(distinct)

    # 跨扩展名的同一份字节 = 0：这是「分区之和必然等于 distinct 总数」的前提
    spanning = {h[:8]: sorted(e) for h, e in per_sha_ext.items() if len(e) > 1}
    assert not spanning, spanning
    assert set().union(*per_sha_ext.values()) == set(CSI_OUT_DISTINCT_BY_TYPE)

    got = {ext: sum(1 for e in per_sha_ext.values() if ext in e)
           for ext in CSI_OUT_DISTINCT_BY_TYPE}
    assert got == CSI_OUT_DISTINCT_BY_TYPE, got
    assert sum(got.values()) == len(distinct), (got, len(distinct))

    # 71 → 53 的 18 个差额全部是**同扩展名、跨目录**的逐字节重复，
    # 且只落在 SAME_DELIVERY_PAIRS 那两对目录里（= 同一批交付的两次落盘，不是两次运行）
    dup = {h: sorted(ids) for h, ids in per_sha_ids.items() if len(ids) > 1}
    assert sum(len(v) - 1 for v in dup.values()) == 71 - 53
    for h, ids in dup.items():
        roots = {i.split(":")[1] for i in ids}
        assert len(roots) == 2 and frozenset(roots) in \
            {frozenset(k) for k in SAME_DELIVERY_PAIRS}, (h[:8], ids)
        assert len({Path(i.split(":", 2)[2]).suffix for i in ids}) == 1, ids


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


def test_mpeg1_time_base_pinned() -> None:
    """MPEG-1 转码件的时基钉子（记观测，不记解释）。

    钉住的四件事：`r_frame_rate=50/1` 与 `avg_frame_rate=25/1` **并存**（这是实测差异，
    不是笔误）；帧数只能靠解码计数拿到；容器时长只许比源片短、且短 0.5 或 1.5 帧；
    `field_order` 是 `progressive` —— 所以 50/1 **不能**读成隔行场频。
    谁把时基建在 `r_frame_rate` 或容器时长上，「15 帧」就会从 0.6 s 读成 0.3 s。"""
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
        assert r["fps_r_frame_rate"] == "50/1", r["material_id"]
        assert r["fps_avg_frame_rate"] == "25/1", r["material_id"]
        assert r["frames_source"] == "decode_count", r["material_id"]
        # 撤回「50/1 = 隔行场频」的直接依据：这些流自己是 progressive
        assert r["field_order"] == "progressive", r["material_id"]
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


def test_frames_vs_duration_self_consistent_everywhere() -> None:
    """**数量**自洽：帧数 ≈ 容器时长 × avg_frame_rate。过了不等于逐帧等价。"""
    bad = [r["material_id"] for r in _rows()
           if r["frames"] and r["frames_vs_dur_x_avgfps"] != "是"]
    assert not bad, bad


def test_no_stream_is_interlaced_so_r_frame_rate_is_not_a_field_rate() -> None:
    """全批没有一件隔行流 ⇒ `r_frame_rate=50/1` 只能是「基础帧率估计」，不是场频。

    FFmpeg 对 `r_frame_rate` 的定义就是 a base frame rate estimate；把它叫 field rate
    是替它下了一个它没下的结论（返修 R112-02）。这条测试钉住撤回的依据本身：
    哪天真的进来一段隔行素材，这里会红，那时才需要重新讨论场频。
    """
    rows = [r for r in _rows() if r["frames_source"]]
    assert rows
    interlaced = [(r["material_id"], r["field_order"]) for r in rows
                  if r["field_order"] in INTERLACED_FIELD_ORDERS]
    assert not interlaced, interlaced
    # 只有两种取值：progressive（66）与 mjpeg 不上报（2）。出现第三种要人来看。
    assert {r["field_order"] for r in rows} <= {"progressive", ""}, \
        {r["field_order"] for r in rows}
    assert sum(1 for r in rows if r["field_order"] == "") == 2


def test_pts_columns_are_real_measurements_not_blanks() -> None:
    """时间戳六列不许整列空着——空列看着像「这项不适用」，其实是没测。

    并且钉住两条实测规律：源片 h264 的**原始包序不单调**（B 帧 ⇒ 解码序），
    所以间隔必须在排序后判；MPEG-1 转码件的 pts 起点不是 0。
    """
    rows = [r for r in _rows() if r["frames_source"]]
    for r in rows:
        for col in ("pts_count", "pts_na", "pts_min_s", "pts_max_s",
                    "pts_spacing_uniform", "pts_raw_order_monotonic"):
            assert r[col] != "", (r["material_id"], col)
        assert int(r["pts_count"]) == int(r["frames"]), r["material_id"]
        assert r["pts_spacing_uniform"] in ("是", "否")
        assert r["pts_raw_order_monotonic"] in ("是", "否")
    # 间隔不恒定的，必然有缺 pts 的包（空洞）；反过来不成立（末尾缺 pts 不造成空洞）
    for r in rows:
        if r["pts_spacing_uniform"] == "否":
            assert int(r["pts_na"]) > 0, r["material_id"]
    assert any(r["pts_raw_order_monotonic"] == "否" for r in rows)
    assert all(float(r["pts_min_s"]) > 0 for r in rows
               if r["codec"] == "mpeg1video")


def test_transcode_media_time_mapping_is_measured() -> None:
    """转码件 ↔ 源片的媒体时间映射：帧数逐一相等，起点一律 +0.54 s。

    这是 Spec A §6.1「若存在裁剪或转码时间变化，先验证映射」要的东西，
    也是把「原视频媒体时间」换算到「分析素材时间」的唯一实测依据。
    `pts_max` 有 3 件是 +0.50 s 而不是 +0.54 s —— 已查明原因是这 3 件**最后一个包的
    `pts_time` 是 N/A**，pts_max 因此读早了一帧；帧数与源片仍然逐一相等，不是掉帧。
    """
    rows = [r for r in _rows() if r["frames_source"]]
    by_sha = {r["sha256"]: r for r in rows}
    checked = 0
    for r in rows:
        if r["role"] != "csi_transcode" or not r["derives_from_sha256"]:
            continue
        s = by_sha.get(r["derives_from_sha256"])
        assert s is not None, r["material_id"]
        checked += 1
        assert r["pts_count"] == s["frames"], r["material_id"]      # 数量一致
        off_min = float(r["pts_min_s"]) - float(s["pts_min_s"])
        off_max = float(r["pts_max_s"]) - float(s["pts_max_s"])
        assert round(off_min, 4) == TRANSCODE_PTS_OFFSET_S, (r["material_id"], off_min)
        assert round(off_max, 4) in (0.54, 0.50), (r["material_id"], off_max)
        if round(off_max, 4) == 0.50:
            assert int(r["pts_na"]) > 0, r["material_id"]           # 末尾缺 pts，不是掉帧
        assert float(s["pts_min_s"]) == 0.0, s["material_id"]       # 源片从 0 起
    assert checked == 14, checked      # 16 件转码件里 2 个 fallback 无登记来源


#: t0 状态词。范围限定在「本次核对的材料」，不是「任何记录里都不可能有」。
T0_NOT_FOUND = "not_found_in_checked_materials"
T0_NA_TST = "na_tst_no_water_entry"


def test_t0_declared_absent_not_silently_empty() -> None:
    """t0 全缺是**声明**出来的，且**分范式给理由**：

    FST 视频行 = `not_found_in_checked_materials`（首帧鼠已在水中，DP-057）；
    TST 视频行 = `na_tst_no_water_entry`（悬尾没有「入水」这件事，
    把 FST 的理由套到 TST 上就是编了一个不存在的原因，返修 R114-03）；
    非视频行 = `na`。
    """
    rows = _rows()
    for r in rows:
        assert r["t0_source_s"] == "", r["material_id"]
        assert r["t0_status"] in (T0_NOT_FOUND, T0_NA_TST, "na"), r["material_id"]
    video_rows = [r for r in rows if r["role"] in
                  ("source_video", "csi_transcode", "tst_clip")]
    assert video_rows
    for r in video_rows:
        want = T0_NA_TST if r["assay"] == "TST" else T0_NOT_FOUND
        assert r["t0_status"] == want, (r["material_id"], r["assay"])
    assert {r["t0_status"] for r in video_rows} == {T0_NOT_FOUND, T0_NA_TST}


def test_no_confirmed_geometry_and_no_fresh_blind_claim() -> None:
    """不许有「几何已人工确认」，也不许有素材自称可当新盲测。"""
    for r in _rows():
        assert r["geometry_binding_status"] in (
            "na", "bound_by_filename_only"), r["material_id"]
        assert r["usage_group"] in ("historical_or_dev_validation",
                                    "test_golden", "engineering_only",
                                    "forensic_hash_only_no_run_scope"), \
            r["material_id"]


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
        # 词是精确的：只哈希、且**未查到覆盖「运行原 EXE」的许可**（Spec B:36 说当前
        # scope 是静态/只读）。这**不等于**「永久禁止运行」——永久禁止的是另一件事：
        # 把 CSI DT 加载进自建参考器/产品执行（Spec B:32）。两者混写就是把
        # 「这次没许可」说成「永远不可能有许可」（返修 R114-04）。
        assert r["usage_group"] == "forensic_hash_only_no_run_scope", name
        assert r["evidence_status"] == "static_recovered", name
