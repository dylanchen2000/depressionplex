#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-133：生成 A/B 两线共用的「输入身份清单」（研发侧补充件）。

**为什么**：下一阶段两条线都要按身份引用素材——A 线（FST 独立研究链）要拿真素材进独立
路径，B 线（CSI 定向验证）要拿真素材做受控差分与读回。但现有登记只有三处、且各自不全：

1. `docs/CSI输入视频映射表.csv`：14 段源片，只有**源 sha256 前 16 位**、尺寸、帧数、时长；
   没有转码件自身的哈希、没有 t0、没有几何绑定。
2. `~/Downloads/tst_30clips/{manifest,cut_provenance}.csv`：以**文件名**为键；cut_provenance
   无表头、每段占两行（第二行是时长+源 sha256 全量）。
3. `data/human_scores/manifests/*.csv`：以 `trial_id,video_filename` 为键，**无视频哈希列**。

而 FST 与 TST 各有一段**同名不同片**（`10mg 2周.mp4` / `20mg 2周.mp4`），转码目录里还有
与源片同名不同哈希的中间件、以及 `zztest01.mp4` ≡ `正常5+抑郁1-3.mp4` 的同哈希异名。
⇒ 文件名在本项目**不是身份**。**身份依据是内容哈希（sha256），加上可核验的
源/转码/裁剪派生关系**；帧数、时长、时间戳都只作**一致性检查**，不作身份键。

**不做什么**（与 Spec A §3 / Spec B §9 对齐）：
- 不建第二套人工真值表。人工记录在本表里只写**指针**（指向拥有它的 manifest），
  不复制 holds、不复制评分、不复制复评归属；缺的哈希列去 DP-082 入库侧补，不在这里另立一本。
- 不改任何冻结常量、不写素材目录、不运行 CSI 原程序。
- 缺失的身份（t0、正式几何标定、FSR3/TSR1 样例、CSI TST 输出）**不写成空行假装存在**，
  记在 `docs/备忘_共用输入身份清单_v1.md` 的缺失清单里，带明确状态。

**时间基准：这一版记的是观测值，不是解释**（2026-09-19 返修，见备忘 §五.2）：

- `fps_avg_frame_rate` = ffprobe 的 `avg_frame_rate`（容器声称的平均帧率）。
- `fps_r_frame_rate` = ffprobe 的 `r_frame_rate`。**按 FFmpeg 官方定义，它是「能表达该流
  时间戳的基础帧率估计」，不保证等于隔行场频**。本批 MPEG-1 转码件上它是 50/1 而
  `avg_frame_rate` 是 25/1，这个**差异是实测事实**；把它解释成「隔行场频」需要
  `field_order` 与逐帧时间戳另行佐证，本表把 `field_order` 原样记下（实测源片是
  `progressive`、mjpeg AVI 是 `unknown`），**不代替解释**。
- `frames` + `frames_source`：容器写着帧数就用容器的（`container`），没有就解码计数
  （`decode_count`，落在 `nb_read_frames`）。**帧数相同只支持「数量一致」**，
  不证明逐帧内容或时间完全等价；重采样、裁剪、填边、可变帧率都还没有验证过。
- `pts_*` 六列：逐包 `pts_time` 的实测统计（包数、缺 pts 的包数、最小/最大、
  排序后间隔是否恒定、**原始包序**是否单调）。源片 h264 的包序**不是**单调的
  （B 帧 ⇒ 解码序），排序后间隔恒为 0.04 s；MPEG-1 转码件的 `pts_min` 实测是
  **0.54 s 而不是 0**，间隔同样恒为 0.04 s、跨度与源片逐帧相等。
  ⇒ 「原视频媒体时间 → 分析素材时间」的映射有一个实测的 0.54 s 起点偏移，
  这条正是 Spec A §6.1 要求「若存在裁剪或转码时间变化，先验证映射」的对象。
- `frames_vs_dur_x_avgfps`：帧数与「容器时长 × avg_frame_rate」是否数量自洽
  （容差分两档，见 `frames_ok`）。**容器时长系统性偏短**（本批 12 件短 0.5 帧、
  3 件短 1.5 帧），所以时长不能当时间基准，只作一致性检查。

**输出保护**（2026-09-19 返修 R112-01）：这是一个**只读盘点**脚本，它不该有能力
破坏它所盘点的素材，也不该悄悄盖掉上一版清单。所以：素材/证据根目录与仓库源码目录
是禁止输出区；默认不覆盖已有文件；更新已有清单要显式 `--update` 并给出旧文件的
`--expect-sha256`；先写临时文件、**校验通过再发布**，旧版另存快照（默认落在仓库外）。

用法（只读本机授权目录，输出一份 CSV）：

    /tmp/dpx311/bin/python scripts/dp133_build_input_identity.py \\
        --out docs/共用输入身份清单_v1.csv            # 首次生成（目标已存在则拒绝）
    /tmp/dpx311/bin/python scripts/dp133_build_input_identity.py \\
        --update --expect-sha256 <旧清单 sha256>      # 显式更新，旧版留快照

任一授权根目录缺失 ⇒ 直接报错退出，不产半张表（静默缺行比报错危险，见 WORKFLOW §4）。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOME = Path.home()
ROOT = Path(__file__).resolve().parent.parent

HEADER = [
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

VIDEO_SUFFIX = (".mp4", ".mpg", ".avi")

#: 本批次的实测计数。**这是「这一批材料量到多少」的快照钉子，不是项目永久规则**：
#: 新素材到来时按 `--expect-videos` 显式改，并在备忘里开一个新版本（v2），
#: 不要把「68 段视频 / 没有 t0 / 几何未确认」写成永远不能更新的科学结论。
EXPECTED_BATCH_VIDEOS = 68
EXPECTED_BATCH_ROWS_MIN = 120

#: t0（试次起点）状态词。**限定在「本次核对的材料」这个范围内**：
#: 未取得 ≠ 任何人的任何记录里都不可能有（返修 R114-03）。
T0_NOT_FOUND = "not_found_in_checked_materials"
#: TST 没有「入水」这件事，不能套用 FST 缺 t0 的原因。
T0_NA_TST = "na_tst_no_water_entry"
T0_NA = "na"

#: 原 EXE 的用途标记。**不是「永久禁止运行」**：Spec B:32 永久禁止的是
#: 「在自建参考器/产品里加载执行 CSI DT」；Spec B:36/38 说的是**当前 scope 是静态/只读**，
#: 启动原 EXE 需要已有许可或显式 scope 扩展，授权范围内作黑盒对照是另一件事。
#: 所以这里如实写「只哈希、未查到覆盖运行的许可」。
USAGE_EXE = "forensic_hash_only_no_run_scope"

# 授权素材根目录。全部是仓库文档已点名过的本地路径（见备忘 §二）。
# 换机器跑不了是正常的：这些素材本来就不在 CI 上。
ROOTS = {
    "fst_source": HOME / "Work/heavy/depression/游泳",
    "tst_source": HOME / "Work/heavy/depression/悬尾",
    "csi_in": HOME / "Work/csi_in",
    "tst_clips": HOME / "Downloads/tst_30clips",
    "fst_transcode_intermediate": HOME / "Work/heavy/depression/游泳/转码_forCSI",
    "csi_out_fst": HOME / "Work/depression抑郁绝望/9月10日集中标注/CSI分析强迫游泳数据",
    "csi_out_ctrl": HOME / "Work/depression抑郁绝望/9月10日集中标注/CSI分析强迫游泳数据_对照参数",
    "csi_out_rest": HOME / "Work/depression抑郁绝望/9月10日集中标注/剩余游泳视频数据",
    "csi_out_norm14": HOME / "Work/depression抑郁绝望/9月10日集中标注/正常1-4对照数据",
    "ui_shots": HOME / "Work/depression抑郁绝望/9月10日集中标注",
    "exe_zip": HOME / "Downloads/Depressionscan  HR.zip",
}

#: 仓库内不许被当输出的目录：源码与测试。`docs/` 不在里面——清单本身就住在那儿，
#: 但更新它必须走 `--update` + `--expect-sha256` + 表头核对（见 `resolve_out`）。
PROTECTED_REPO_SUBDIRS = ("depressionplex", "scripts", "tests", ".git")

# 仓库夹具 ↔ 客户侧目录里的同名原件（守卫测试验二者同哈希 ⇒ 夹具就是真拷贝）。
FIXTURE_ORIGINS = {
    "10mg 2周.SET": "csi_out_fst",
    "正常1-4对照更改.SET": "csi_out_ctrl",
    "正常1-4.CLB": "csi_out_fst",
}

MANIFEST_NAME = "共用输入身份清单_v1.csv"

#: 上一版表头（2026-09-18，25 列）。留着是为了让**这一次** schema 迁移能走 `--update`：
#: `resolve_out` 只认这张表里的表头，指到别的 CSV 上照样拒绝。
#: 迁移完成后这一项应清空——留着就等于永远允许把任意旧版当更新目标。
PREVIOUS_HEADERS = (
    ["material_id", "assay", "role", "path", "bytes", "sha256",
     "codec", "width", "height", "frames", "frames_source",
     "fps_rational", "fps_field_rate", "duration_s", "frames_x_fps_ok",
     "derives_from_sha256", "derives_from_status",
     "t0_source_s", "t0_status",
     "geometry_binding", "geometry_binding_status",
     "human_record_ref", "human_record_status",
     "usage_group", "evidence_status"],
)
ACCEPTED_HEADERS = (HEADER, *PREVIOUS_HEADERS)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_in_zip(zip_path: Path, inner: str) -> tuple[str, int]:
    """管道取 zip 内文件哈希，不落盘、不解包整包、**不执行**。"""
    out = subprocess.run(["unzip", "-p", str(zip_path), inner],
                         capture_output=True, check=True)
    return hashlib.sha256(out.stdout).hexdigest(), len(out.stdout)


_PROBE_KEYS = ("codec_name,width,height,field_order,nb_frames,"
               "r_frame_rate,avg_frame_rate,duration")


def probe(p: Path) -> dict:
    """只取流元数据；容器无帧数（MPEG-1/AVI）时用 -count_frames 解码计数。

    字段名一律用 **ffprobe 的原始名**（`fps_avg_frame_rate` / `fps_r_frame_rate`），
    不改成带解释的名字：`r_frame_rate` 是「基础帧率估计」，不是「场频」，
    把它叫成 field rate 就是替 FFmpeg 下了一个它没下的结论（返修 R112-02）。
    """
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", f"stream={_PROBE_KEYS}", "-of", "json", str(p)]
    info = json.loads(subprocess.run(cmd, capture_output=True,
                                     check=True).stdout)["streams"][0]
    frames = info.get("nb_frames") or ""
    frames_source = "container"
    if frames in ("", "N/A"):
        # -count_frames 是全局选项，必须放在 -select_streams 之前；
        # 且计数落在 **nb_read_frames**，不 ask 它就永远读不到（实测踩过：
        # 16 行转码件帧数全空、自检却一声不响，因为自检只查有帧数的行）。
        cmd2 = ["ffprobe", "-v", "error", "-count_frames",
                "-select_streams", "v:0", "-show_entries",
                f"stream={_PROBE_KEYS},nb_read_frames", "-of", "json", str(p)]
        info2 = json.loads(subprocess.run(cmd2, capture_output=True,
                                          check=True).stdout)["streams"][0]
        frames = info2.get("nb_read_frames") or ""
        frames_source = "decode_count"
    return {
        "codec": info.get("codec_name", ""),
        "width": info.get("width", ""),
        "height": info.get("height", ""),
        "field_order": info.get("field_order", ""),
        "frames": frames,
        "frames_source": frames_source,
        "fps_avg_frame_rate": info.get("avg_frame_rate", ""),
        "fps_r_frame_rate": info.get("r_frame_rate", ""),
        "duration_s": info.get("duration", ""),
    }


def parse_pts(csv_text: str, spacing: float = 0.04) -> dict:
    """把 `ffprobe -show_entries packet=pts_time` 的输出算成六项观测（纯函数，可单测）。

    **为什么要排序**：源片是 h264，包序是**解码序**，`pts_time` 原样读出来会有负间隔
    （实测 `正常1-4.mp4` 的原始包序里 0.08 / −0.04 / −0.08 三种间隔混着）。
    所以「间隔是否恒定」必须在**排序后**判；「原始包序是否单调」单独记一列，
    因为它正是「包序 ≠ 展示序」的证据，不是错误。
    """
    raw = [x.strip() for x in csv_text.splitlines() if x.strip()]
    vals = [float(x) for x in raw if x != "N/A"]
    if not vals:
        return {"pts_count": len(raw), "pts_na": len(raw), "pts_min_s": "",
                "pts_max_s": "", "pts_spacing_uniform": "",
                "pts_raw_order_monotonic": ""}
    srt = sorted(vals)
    gaps = {round(b - a, 6) for a, b in zip(srt, srt[1:])}
    return {
        "pts_count": len(raw),
        "pts_na": len(raw) - len(vals),
        "pts_min_s": f"{srt[0]:.6f}".rstrip("0").rstrip("."),
        "pts_max_s": f"{srt[-1]:.6f}".rstrip("0").rstrip("."),
        # 间隔恒定 = 固定帧率的证据；出现第二种间隔通常就是缺 pts 的那几个包造成的空洞
        "pts_spacing_uniform": "是" if gaps == {spacing} else "否",
        "pts_raw_order_monotonic":
            "是" if all(b >= a for a, b in zip(vals, vals[1:])) else "否",
    }


def ts_probe(p: Path) -> dict:
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(p)]
    out = subprocess.run(cmd, capture_output=True, check=True)
    return parse_pts(out.stdout.decode("utf-8", "replace"))


def frames_ok(frames: str, duration_s: str, fps_avg: str,
              frames_source: str = "container") -> str:
    """帧数与「容器时长×avg_frame_rate」是否**数量**自洽。容差分两档，都是实测出来的：

    - `container`（源片 mp4 / mjpeg avi）：容器就写着帧数，容差半帧。
    - `decode_count`（MPEG-1 转码件）：容器**没有**帧数，时长还系统性偏短——
      16 件里 12 件短 0.5 帧（0.02 s）、3 件短 1.5 帧（0.06 s）。
      所以判据是「解码帧数 ≥ 时长×帧率，且多出来的不超过 1.5 帧」，
      即 `0 ≤ n − d·fps ≤ 1.5`。**方向是有意义的**：反过来（帧数少于时长×帧率）
      说明掉帧，那是转码事故，必须红。

    注意这条检查**只说数量**：过了不等于逐帧内容或时间戳等价（那看 `pts_*` 六列）。
    解析不了就返回 `""`（`self_check` 会把空值当非法输入拦下，不静默放行）。
    """
    try:
        n = int(frames)
        d = float(duration_s)
        num, den = fps_avg.split("/")
        fps = float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        return ""
    if fps <= 0:
        return ""
    if frames_source == "decode_count":
        return "是" if 0.0 <= n - d * fps <= 1.5 else "否"
    return "是" if abs(n - d * fps) <= 0.5 else "否"


def display_path(p: Path) -> str:
    """表里 `path` 列的写法：HOME 下的写 `~/…`（不把家目录绝对路径散到公开仓库里），
    HOME 外的照原样写绝对路径（测试替身、外挂盘、`zip!inner` 都归这一类）。"""
    try:
        return "~/" + str(p.relative_to(HOME))
    except ValueError:
        return str(p)


def base_row(mid: str, assay: str, role: str, p: Path, usage: str,
             evidence: str = "artifact_checked") -> dict:
    return {
        "material_id": mid, "assay": assay, "role": role,
        "path": display_path(p),
        "bytes": p.stat().st_size, "sha256": sha256_file(p),
        "codec": "", "width": "", "height": "", "field_order": "",
        "frames": "", "frames_source": "",
        "fps_avg_frame_rate": "", "fps_r_frame_rate": "",
        "duration_s": "", "frames_vs_dur_x_avgfps": "",
        "pts_count": "", "pts_na": "", "pts_min_s": "", "pts_max_s": "",
        "pts_spacing_uniform": "", "pts_raw_order_monotonic": "",
        "derives_from_sha256": "", "derives_from_status": "na",
        # t0（试次起点）在**本次核对的材料里**没找到。FST 与 TST 的原因不同，
        # 不许把「首帧鼠已在水中」这条 FST 的观察套到 TST 或非视频材料上（返修 R114-03）。
        "t0_source_s": "", "t0_status": T0_NA,
        "geometry_binding": "", "geometry_binding_status": "na",
        "human_record_ref": "", "human_record_status": "na",
        "usage_group": usage, "evidence_status": evidence,
    }


def video_row(mid: str, assay: str, role: str, p: Path, usage: str,
              evidence: str = "artifact_checked", **kw) -> dict:
    row = base_row(mid, assay, role, p, usage, evidence)
    row.update(probe(p))
    row.update(ts_probe(p))
    row["frames_vs_dur_x_avgfps"] = frames_ok(row["frames"], row["duration_s"],
                                              row["fps_avg_frame_rate"],
                                              row["frames_source"])
    row["t0_status"] = T0_NA_TST if assay == "TST" else T0_NOT_FOUND
    row.update(kw)
    return row


def plain_row(mid: str, assay: str, role: str, p: Path, usage: str,
              evidence: str = "artifact_checked", **kw) -> dict:
    row = base_row(mid, assay, role, p, usage, evidence)
    row["codec"] = p.suffix.lstrip(".")
    row.update(kw)
    return row


def load_manifest_videos(dirpath: Path) -> dict[str, set[str]]:
    """data/human_scores/manifests 里被点名过的 video_filename（只取名字集合做指针判定）。"""
    out: dict[str, set[str]] = {}
    for key, name in (("FST", "FST全程_视频清单_给评分员.csv"),
                      ("TST", "T1精标_视频清单_给评分员.csv")):
        f = dirpath / name
        if not f.is_file():
            raise SystemExit(f"人工清单缺失，拒产半张表：{f}")
        # utf-8-sig：映射表带 BOM，裸 utf-8 会把首列名读成 '﻿范式'
        with open(f, newline="", encoding="utf-8-sig") as fh:
            out[key] = {r["video_filename"] for r in csv.DictReader(fh)}
    return out


def load_mapping_table(path: Path) -> dict[str, dict[str, str]]:
    """docs/CSI输入视频映射表.csv：CSI 用文件 ↔ 源片的官方对应（含源 sha256 前 16 位）。"""
    if not path.is_file():
        raise SystemExit(f"映射表缺失，拒产半张表：{path}")
    out: dict[str, dict[str, str]] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["CSI用文件"] in out:
                raise SystemExit(f"映射表里 CSI用文件 重复：{r['CSI用文件']}")
            out[r["CSI用文件"]] = r
    return out


def check_roots(roots: dict[str, Path]) -> None:
    missing = [k for k, p in roots.items()
               if not (p.is_file() if k == "exe_zip" else p.is_dir())]
    if missing:
        raise SystemExit(f"授权根目录缺失，拒产半张表：{missing}")


def build(roots: dict[str, Path] | None = None, repo_root: Path = ROOT,
          do_probe: bool = True, include_exe: bool = True) -> list[dict]:
    """扫全部授权根目录，产出清单行。

    `roots` / `repo_root` 可注入，是为了让守卫测试能用**小临时样例**打到异常路径
    （缺根目录、缺映射表、重复 material_id……），不必依赖客户的全量视频。
    `do_probe=False` 跳过 ffprobe，`include_exe=False` 跳过原 EXE 的 zip 内哈希
    ——两个都是给测试用的替身开关，**正式生成一律用默认值**（`main` 里就是默认值）。
    """
    roots = dict(ROOTS if roots is None else roots)
    check_roots(roots)

    manifest_videos = load_manifest_videos(repo_root / "data/human_scores/manifests")
    mapping = load_mapping_table(repo_root / "docs/CSI输入视频映射表.csv")
    vrow = video_row if do_probe else _no_probe_video_row
    rows: list[dict] = []

    # 1) 源片：FST 7 + TST 7。人工真值的拥有者是 data/human_scores/manifests，这里只给指针。
    for key, assay in (("fst_source", "FST"), ("tst_source", "TST")):
        for p in sorted(roots[key].glob("*.mp4")):
            listed = p.name in manifest_videos[assay]
            rows.append(vrow(
                f"source:{assay}:{p.name}", assay, "source_video", p,
                usage="historical_or_dev_validation",
                geometry_binding=f"{p.stem}.CLB",
                geometry_binding_status="bound_by_filename_only",
                human_record_ref=(f"data/human_scores/manifests/"
                                  f"{'FST全程' if assay == 'FST' else 'T1精标'}"
                                  f"_视频清单_给评分员.csv") if listed else "",
                human_record_status=("pointer_only_joins_by_filename"
                                     if listed else "none_recorded")))
    src_sha = {(r["assay"], Path(r["path"]).name): r["sha256"]
               for r in rows if r["role"] == "source_video"}

    # 2) CSI 转码输入：14 主格式 + 2 备用。源身份按仓库映射表填全量哈希（表里有前 16 位可对）。
    for suf in (".mpg", ".avi"):
        for p in sorted(roots["csi_in"].glob(f"*{suf}")):
            m = mapping.get(p.name)
            src = src_sha.get((m["范式"], m["原视频"]), "") if m else ""
            rows.append(vrow(
                f"csi_transcode:{m['范式'] if m else 'na'}:{p.name}",
                m["范式"] if m else "na", "csi_transcode", p,
                usage="historical_or_dev_validation",
                derives_from_sha256=src,
                derives_from_status=("confirmed_by_mapping_table"
                                     if src else "unknown_provenance")))

    # 3) TST 切片：cut_provenance.csv 逐段记了源 sha256 全量与切窗，直接对得上。
    prov_path = roots["tst_clips"] / "cut_provenance.csv"
    if not prov_path.is_file():
        raise SystemExit(f"切片来源记录缺失，拒产半张表：{prov_path}")
    prov: dict[str, str] = {}
    raw = prov_path.read_text().splitlines()
    for i in range(0, len(raw) - 1, 2):
        a = next(csv.reader([raw[i]]))
        b = next(csv.reader([raw[i + 1]]))
        prov[a[0] + ".mp4"] = b[1]
    for p in sorted(roots["tst_clips"].glob("*.mp4")):
        if p.name == "cut_provenance.csv":
            continue
        listed = p.name in manifest_videos["TST"]
        rows.append(vrow(
            f"tst_clip:TST:{p.name}", "TST", "tst_clip", p,
            usage="historical_or_dev_validation",
            derives_from_sha256=prov.get(p.name, ""),
            derives_from_status=("confirmed" if p.name in prov
                                 else "unknown_provenance"),
            human_record_ref=("data/human_scores/manifests/"
                              "T1精标_视频清单_给评分员.csv") if listed else "",
            human_record_status=("pointer_only_joins_by_filename"
                                 if listed else "none_recorded")))

    # 4) 转码中间件：来源与转码参数无任何记录，只报哈希，不许当已确证的转码产物。
    for p in sorted(roots["fst_transcode_intermediate"].iterdir()):
        mid = f"transcode_intermediate:FST:{p.name}"
        if p.suffix.lower() in VIDEO_SUFFIX:
            rows.append(vrow(mid, "FST", "transcode_intermediate", p,
                             usage="engineering_only",
                             derives_from_status="unknown_provenance"))
        else:
            rows.append(plain_row(mid, "FST", "transcode_intermediate", p,
                                  usage="engineering_only",
                                  derives_from_status="unknown_provenance"))

    # 5) CSI 输出（SET/CLB/xlsx/BMP）：参数与几何的真身。CLB 目前只按文件名挂视频。
    for key in ("csi_out_fst", "csi_out_ctrl", "csi_out_rest",
                "csi_out_norm14"):
        for p in sorted(roots[key].iterdir()):
            if not p.is_file():
                continue
            role = {"set": "csi_set", "clb": "csi_clb", "xlsx": "csi_xlsx",
                    "bmp": "csi_bmp"}.get(p.suffix.lower().lstrip("."),
                                          "csi_other")
            rows.append(plain_row(
                f"csi_out:{key}:{p.name}", "FST", role, p,
                usage="historical_or_dev_validation",
                geometry_binding=(p.name.split("（")[0]
                                  if p.suffix == ".CLB" else ""),
                geometry_binding_status=("bound_by_filename_only"
                                         if p.suffix == ".CLB" else "na")))

    # 6) Settings 面板截图 7 页：此前只被备忘文字引用、B0 记为「缺件」；实物在本机。
    for p in sorted(roots["ui_shots"].glob("*.png")):
        rows.append(plain_row(f"ui_screenshot:na:{p.name}", "na",
                              "ui_screenshot", p,
                              usage="historical_or_dev_validation"))

    # 7) 仓库夹具：真 CSI 文件拷进来的金标准（守卫测试验它与客户侧原件同哈希）。
    fix = repo_root / "tests/fixtures/csi_fst"
    if fix.is_dir():
        for p in sorted(fix.iterdir()):
            if p.is_file():
                rows.append(plain_row(f"repo_fixture:na:{p.name}", "na",
                                      "repo_fixture", p, usage="test_golden",
                                      evidence="independently_reproduced"))

    # 8) 登记表本身：钉住本表是照哪个版本的登记表生成的。
    for p in (repo_root / "docs/CSI输入视频映射表.csv",
              roots["tst_clips"] / "cut_provenance.csv",
              roots["tst_clips"] / "manifest.csv",
              roots["ui_shots"] / "CSI参数与文件格式_实录_2026-09-10.md"):
        if p.is_file():
            rows.append(plain_row(f"registry_doc:na:{p.name}", "na",
                                  "registry_doc", p, usage="engineering_only"))

    # 9) 原 EXE：取证材料，**只哈希、不执行**。当前 scope 是静态/只读（Spec B:36），
    #    未查到覆盖「运行原 EXE」的许可；永久禁止的是另一件事——把 CSI DT 加载进
    #    自建参考器/产品执行（Spec B:32）。两者不许混写（返修 R114-04）。
    #    zip 内路径带安装目录前缀，裸文件名 unzip 匹配不到（实测教训）。
    for inner in (("Depressionscan  HR/ForcedSwimScan-Ver2/HR-V2.0/ForcedSwimScan.exe",
                   "Depressionscan  HR/TailSuspScan-Ver2/HR-V2.0/TailSuspScan.exe")
                  if include_exe else ()):
        dig, size = sha256_in_zip(roots["exe_zip"], inner)
        row = base_row(f"csi_exe:na:{Path(inner).name}", "na", "csi_exe",
                       roots["exe_zip"], USAGE_EXE,
                       evidence="static_recovered")
        row.update({"path": f"{display_path(roots['exe_zip'])}!{inner}",
                    "bytes": size, "sha256": dig, "codec": "exe"})
        rows.append(row)

    return rows


def _no_probe_video_row(mid: str, assay: str, role: str, p: Path, usage: str,
                        evidence: str = "artifact_checked", **kw) -> dict:
    """`do_probe=False` 时的替身：不跑 ffprobe，只留空的时间列。"""
    row = base_row(mid, assay, role, p, usage, evidence)
    row["t0_status"] = T0_NA_TST if assay == "TST" else T0_NOT_FOUND
    row.update(kw)
    return row


def self_check(rows: list[dict], mapping: dict[str, dict[str, str]] | None = None,
               expect_videos: int = EXPECTED_BATCH_VIDEOS,
               expect_rows_min: int = EXPECTED_BATCH_ROWS_MIN) -> None:
    """发布**之前**把能自查的全查一遍。

    返修 R112-03 的要求是「把这些检查放在发布清单之前，不等 CI 读到已写坏的 CSV
    才发现」，所以这里不止查内容自洽，也查**结构**（重复 material_id、非法帧率/帧数、
    与映射表的哈希前缀对不上）。每条都写清它防的是什么事故。
    """
    # a) material_id 是这张表的主键。重复 ⇒ 后面任何按 id 取行的代码会静默拿到最后一条。
    seen: dict[str, int] = {}
    for r in rows:
        seen[r["material_id"]] = seen.get(r["material_id"], 0) + 1
    dup = sorted(k for k, n in seen.items() if n > 1)
    if dup:
        raise SystemExit(f"material_id 重复（主键失效）：{dup}")

    # b) sha256 必须是 64 位小写十六进制。半截哈希比没有哈希危险。
    bad_sha = [r["material_id"] for r in rows
               if len(r["sha256"]) != 64
               or set(r["sha256"]) - set("0123456789abcdef")]
    if bad_sha:
        raise SystemExit(f"sha256 格式不对：{bad_sha}")

    # c) 同名源片跨范式必须不同哈希；否则这张表自己就在用文件名冒充身份。
    by_name: dict[str, dict[str, str]] = {}
    for r in rows:
        if r["role"] == "source_video":
            by_name.setdefault(Path(r["path"]).name, {})[r["assay"]] = r["sha256"]
    for name, per_assay in by_name.items():
        if len(per_assay) > 1 and len(set(per_assay.values())) == 1:
            raise SystemExit(f"同名源片哈希相同，身份表失效：{name}")

    # d) 视频行的时间列必须是**能解析的数**：帧数是整数、avg_frame_rate 是 a/b 且 b≠0。
    #    frames_ok() 解析不了会返回空串——空串在这里当非法输入拦下，不当「这项不适用」。
    vids = [r for r in rows if r["role"] in
            ("source_video", "csi_transcode", "tst_clip", "transcode_intermediate")
            and r["frames_source"]]
    illegal = []
    for r in vids:
        try:
            int(r["frames"])
            num, den = r["fps_avg_frame_rate"].split("/")
            if float(den) == 0 or float(num) <= 0:
                raise ValueError
            float(r["duration_s"])
        except (ValueError, ZeroDivisionError):
            illegal.append((r["material_id"], r["frames"],
                            r["fps_avg_frame_rate"], r["duration_s"]))
    if illegal:
        raise SystemExit(f"非法帧数/帧率/时长（不要放行空值或 0 分母）：{illegal}")

    # d2) 判定列本身也不许是空的或过期的。`frames_ok()` 解析不了会返回 ""，
    #     而 "" 看着像「这项不适用」——于是一整列 quietly 变成没检查过。
    #     重算一遍并对表：空值、被手改过的值、以及「探针没跑过」三种都会在这里现形。
    stale = []
    for r in vids:
        want = frames_ok(r["frames"], r["duration_s"], r["fps_avg_frame_rate"],
                         r["frames_source"])
        if r["frames_vs_dur_x_avgfps"] != want:
            stale.append((r["material_id"], r["frames_vs_dur_x_avgfps"] or "（空）",
                          want or "（算不出）"))
    if stale:
        raise SystemExit(f"帧数×时长判定列为空或与重算不符：{stale}")

    # e) 帧数与容器时长×avg_frame_rate 数量自洽。
    bad = [r["material_id"] for r in rows
           if r["frames"] and r["frames_vs_dur_x_avgfps"] == "否"]
    if bad:
        raise SystemExit(f"帧数与时长×帧率不自洽（先查，不要直接放行）：{bad}")

    # f) 探针跑过（有帧率）却没有帧数 ⇒ 一定是 ffprobe 的键名/选项写错了，
    #    不是「这个文件没有帧数」。这一条就是为那次静默失败加的。
    silent = [r["material_id"] for r in rows
              if r["fps_avg_frame_rate"] and not r["frames"]]
    if silent:
        raise SystemExit(f"探针跑过却没拿到帧数（查 probe()，不要放行空值）：{silent}")

    # g) 与仓库映射表逐行互核（哈希前 16 位、帧数、尺寸、时长）。
    if mapping:
        src = {(r["assay"], Path(r["path"]).name): r for r in rows
               if r["role"] == "source_video"}
        transc = {Path(r["path"]).name: r for r in rows
                  if r["role"] == "csi_transcode"}
        for name, m in mapping.items():
            s = src.get((m["范式"], m["原视频"]))
            if s is None:
                raise SystemExit(f"映射表点名的源片不在清单里：{m['范式']}/{m['原视频']}")
            if not s["sha256"].startswith(m["原sha256前16"]):
                raise SystemExit(
                    f"源片哈希与映射表前 16 位不符：{m['原视频']} "
                    f"{s['sha256'][:16]} != {m['原sha256前16']}")
            if s["frames"] != m["原帧数"]:
                raise SystemExit(f"源片帧数与映射表不符：{m['原视频']}")
            t = transc.get(name)
            if t is None:
                raise SystemExit(f"映射表点名的转码件不在清单里：{name}")
            if t["frames"] != m["转换后帧数"]:
                raise SystemExit(f"转码件帧数与映射表不符：{name}")
            if t["derives_from_sha256"] != s["sha256"]:
                raise SystemExit(f"转码件的来源哈希没填成源片哈希：{name}")

    # h) 本批次的视频行数。**这是批次快照钉子，不是项目永久规则**：
    #    数变了要么是没扫全，要么是来了新素材（那就显式改 --expect-videos 并开新版本）。
    n_video = sum(1 for r in rows if r["frames"])
    if n_video != expect_videos:
        raise SystemExit(
            f"视频行数 {n_video} != 本批预期 {expect_videos}"
            f"（本批 = 14 源片 + 16 转码件 + 27 切片 + 11 中间件）。"
            f"少了说明某根目录没扫全；多了说明来了新素材 —— 那种情况要显式改 "
            f"--expect-videos 并在备忘里开新版本，不许默默放行。")
    if len(rows) < expect_rows_min:
        raise SystemExit(f"总行数 {len(rows)} < {expect_rows_min}")
    print(f"自检通过：{len(rows)} 行，其中视频行 {n_video}")


# ---------------------------------------------------------------- 输出保护

def protected_zones(roots: dict[str, Path] | None = None,
                    repo_root: Path = ROOT) -> list[tuple[str, Path]]:
    """禁止当输出的区域：素材/证据根目录（只读盘点的对象）+ 仓库源码目录。"""
    roots = dict(ROOTS if roots is None else roots)
    zones = [(f"素材根目录 {k}", Path(v).resolve()) for k, v in roots.items()]
    zones += [(f"仓库源码 {s}", (repo_root / s).resolve())
              for s in PROTECTED_REPO_SUBDIRS]
    return zones


def resolve_out(raw: str | Path, *, roots: dict[str, Path] | None = None,
                repo_root: Path = ROOT, update: bool = False,
                dry_run: bool = False) -> Path:
    """把 `--out` 规范化（跟随软链接）并挡下三类危险目标。

    1. 落在素材/证据根目录或仓库源码目录里 ⇒ 拒绝。**只读盘点脚本不该有能力
       写坏它正在盘点的素材**（返修 R112-01）。
    2. 目标已存在而没给 `--update` ⇒ 拒绝（默认不覆盖）。
    3. 给了 `--update` 但目标不是一份清单（表头对不上）⇒ 拒绝，
       这样就不会把 `--update` 指到别的 CSV 上把它毁掉。

    `dry_run=True` 只跳过第 2、3 条（本来就不写文件，「已存在」不是危险），
    **禁止输出区那条照样查**——路径本身合不合法与写不写无关。
    """
    out = Path(raw).expanduser()
    resolved = out.resolve()          # 跟随软链接：链接指到素材目录也一样拦
    for label, zone in protected_zones(roots, repo_root):
        if resolved == zone or zone in resolved.parents:
            raise SystemExit(
                f"拒绝把清单写进{label}：{resolved}\n"
                "这是个只读盘点脚本，输出不许落在它盘点的素材里，也不许落在源码目录。")
    if (resolved.exists() or resolved.is_symlink()) and not dry_run:
        if not update:
            raise SystemExit(
                f"目标已存在，默认不覆盖：{resolved}\n"
                "更新已有清单要显式 --update 并给出旧文件的 --expect-sha256。")
        with open(resolved, newline="", encoding="utf-8-sig") as f:
            head = next(csv.reader(f), None)
        if head not in ACCEPTED_HEADERS:
            raise SystemExit(
                f"--update 的目标不是一份输入身份清单（表头不符）：{resolved}\n"
                f"  实际表头 = {head}\n  期望表头 = {HEADER}")
    return resolved


def render_csv(rows: list[dict]) -> str:
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=HEADER, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def validate_written(path: Path, expect_rows: int) -> None:
    """发布前把刚写出的文件**读回来**验一遍：表头、行数、每行 sha 格式。"""
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.reader(f)
        head = next(rd, None)
        body = list(rd)
    if head != HEADER:
        raise SystemExit(f"写出的表头不对：{head}")
    if len(body) != expect_rows:
        raise SystemExit(f"写出的行数 {len(body)} != 预期 {expect_rows}")
    for r in body:
        if len(r) != len(HEADER):
            raise SystemExit(f"有一行列数不对（{len(r)}）：{r[:2]}")


def publish(tmp_csv: Path, out: Path, *, snapshot_dir: Path,
            expect_sha: str | None) -> Path | None:
    """临时文件校验通过后发布；`--update` 时先把旧版另存快照（默认落在仓库外）。

    返回快照路径（没有更新就返回 None）。失败只清理本次创建的文件，
    绝不动旧清单与素材。
    """
    snapshot = None
    if out.exists():
        old_sha = sha256_file(out)
        if expect_sha and old_sha != expect_sha:
            tmp_csv.unlink(missing_ok=True)
            raise SystemExit(
                f"旧清单哈希对不上，拒绝更新：\n  实际 {old_sha}\n  期望 {expect_sha}\n"
                "你以为在更新的那一版不是磁盘上这一版（可能别人已经改过）。")
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot = snapshot_dir / f"{out.stem}.prev-{old_sha[:8]}{out.suffix}"
        if snapshot.exists():
            snapshot.unlink()
        shutil.copy2(out, snapshot)     # 先留旧版，再发布新版
    os.replace(tmp_csv, out)
    return snapshot


def default_snapshot_dir(repo_root: Path = ROOT) -> Path:
    """快照默认落在**仓库外**：留在 docs/ 里会变成未跟踪文件，把收工守卫弄红。"""
    return Path(os.environ.get("DP133_SNAPSHOT_DIR")
                or (HOME / ".cache" / "dp133_manifest_snapshots"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="生成 A/B 两线共用的输入身份清单（只读盘点，不写素材目录）")
    ap.add_argument("--out", default=str(ROOT / "docs" / MANIFEST_NAME))
    ap.add_argument("--update", action="store_true",
                    help="显式更新已有清单（默认拒绝覆盖）")
    ap.add_argument("--expect-sha256", default=None,
                    help="更新时旧清单的 sha256（旧版身份核对，对不上就拒绝）")
    ap.add_argument("--snapshot-dir", default=None,
                    help="旧版快照目录，默认仓库外 ~/.cache/dp133_manifest_snapshots")
    ap.add_argument("--expect-videos", type=int, default=EXPECTED_BATCH_VIDEOS,
                    help=f"本批视频行数钉子，默认 {EXPECTED_BATCH_VIDEOS}"
                         "（新素材到来时显式改，并在备忘开新版本）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只扫描+自检，不写任何文件")
    args = ap.parse_args(argv)

    if args.update and not args.expect_sha256:
        raise SystemExit(
            "--update 必须同时给出 --expect-sha256（旧清单的 sha256）。\n"
            "「我要更新的那一版」必须是能点名的一版，否则可能盖掉别人刚生成的清单。")
    out = resolve_out(args.out, update=args.update, dry_run=args.dry_run)
    rows = build()
    mapping = load_mapping_table(ROOT / "docs/CSI输入视频映射表.csv")
    self_check(rows, mapping, expect_videos=args.expect_videos)
    text = render_csv(rows)

    if args.dry_run:
        print(f"--dry-run：自检通过，{len(rows)} 行，未写文件（目标将是 {out}）")
        return 0

    if out.exists() and not args.update:
        raise SystemExit(f"目标已存在，默认不覆盖：{out}（更新要 --update）")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkstemp(prefix=out.name + ".tmp-", dir=str(out.parent))[1])
    created = [tmp]
    try:
        tmp.write_text(text, encoding="utf-8")
        validate_written(tmp, len(rows))
        snap_dir = Path(args.snapshot_dir).expanduser() if args.snapshot_dir \
            else default_snapshot_dir()
        snapshot = publish(tmp, out, snapshot_dir=snap_dir,
                           expect_sha=args.expect_sha256)
        created = []                      # 发布成功，临时文件已不存在
    except BaseException:
        for p in created:                 # 只清理本次创建的东西
            Path(p).unlink(missing_ok=True)
        raise
    print(f"写出 → {out}  sha256={sha256_file(out)[:16]}…")
    if snapshot:
        print(f"旧版快照 → {snapshot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
