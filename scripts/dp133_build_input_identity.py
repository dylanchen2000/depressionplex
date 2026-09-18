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
⇒ 文件名在本项目**不是身份**，只有全量 sha256 是。本脚本把身份一次性量出来落表。

**不做什么**（与 Spec A §3 / Spec B §9 对齐）：
- 不建第二套人工真值表。人工记录在本表里只写**指针**（指向拥有它的 manifest），
  不复制 holds、不复制评分、不复制复评归属；缺的哈希列去 DP-082 入库侧补，不在这里另立一本。
- 不改任何冻结常量、不写素材目录、不运行 CSI 原程序。
- 缺失的身份（t0、正式几何标定、FSR3/TSR1 样例、CSI TST 输出）**不写成空行假装存在**，
  记在 `docs/备忘_共用输入身份清单_v1.md` 的缺失清单里，带明确状态。

**时间基准的两个实测坑**（本表刻意分开记，供守卫测试钉住）：
- MPEG-1 转码件的 `r_frame_rate` 报 **50/1**（隔行场频），真实帧率是 `avg_frame_rate`=25/1，
  解码计数 `nb_read_frames` 与源片帧数逐帧相等。谁拿 `r_frame_rate` 当帧率，
  就会把「Min Length Thresh 15 帧」从 0.6 s 读成 0.3 s。
- MPEG-1 容器时长比源片短 0.02 s（半帧截断），帧数却一致 ⇒ **时长不是身份键，帧数才是**。

用法（只读本机授权目录，输出一份 CSV）：

    /tmp/dpx311/bin/python scripts/dp133_build_input_identity.py \\
        --out docs/共用输入身份清单_v1.csv

任一授权根目录缺失 ⇒ 直接报错退出，不产半张表（静默缺行比报错危险，见 WORKFLOW §4）。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from pathlib import Path

HOME = Path.home()
ROOT = Path(__file__).resolve().parent.parent

HEADER = [
    "material_id", "assay", "role", "path", "bytes", "sha256",
    "codec", "width", "height", "frames", "frames_source",
    "fps_rational", "fps_field_rate", "duration_s", "frames_x_fps_ok",
    "derives_from_sha256", "derives_from_status",
    "t0_source_s", "t0_status",
    "geometry_binding", "geometry_binding_status",
    "human_record_ref", "human_record_status",
    "usage_group", "evidence_status",
]

VIDEO_SUFFIX = (".mp4", ".mpg", ".avi")

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

# 仓库夹具 ↔ 客户侧目录里的同名原件（守卫测试验二者同哈希 ⇒ 夹具就是真拷贝）。
FIXTURE_ORIGINS = {
    "10mg 2周.SET": "csi_out_fst",
    "正常1-4对照更改.SET": "csi_out_ctrl",
    "正常1-4.CLB": "csi_out_fst",
}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_in_zip(zip_path: Path, inner: str) -> tuple[str, int]:
    """管道取 zip 内文件哈希，不落盘、不解包整包。"""
    out = subprocess.run(["unzip", "-p", str(zip_path), inner],
                         capture_output=True, check=True)
    return hashlib.sha256(out.stdout).hexdigest(), len(out.stdout)


def probe(p: Path) -> dict:
    """只取流元数据；容器无帧数（MPEG-1/AVI）时用 -count_frames 解码计数。"""
    keys = "codec_name,width,height,nb_frames,r_frame_rate,avg_frame_rate,duration"
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", f"stream={keys}", "-of", "json", str(p)]
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
                f"stream={keys},nb_read_frames", "-of", "json", str(p)]
        info2 = json.loads(subprocess.run(cmd2, capture_output=True,
                                          check=True).stdout)["streams"][0]
        frames = info2.get("nb_read_frames") or ""
        frames_source = "decode_count"
    return {
        "codec": info.get("codec_name", ""),
        "width": info.get("width", ""),
        "height": info.get("height", ""),
        "frames": frames,
        "frames_source": frames_source,
        "fps_rational": info.get("avg_frame_rate", ""),
        "fps_field_rate": info.get("r_frame_rate", ""),
        "duration_s": info.get("duration", ""),
    }


def frames_ok(frames: str, duration_s: str, fps_rational: str,
              frames_source: str = "container") -> str:
    """帧数与「容器时长×帧率」是否自洽。容差分两档，都是实测出来的，不是放宽了事：

    - `container`（源片 mp4 / mjpeg avi）：容器就写着帧数，容差半帧。
    - `decode_count`（MPEG-1 转码件）：容器**没有**帧数，时长还系统性偏短——
      15 件里 12 件短 0.5 帧（0.02 s）、3 件短 1.5 帧（0.06 s）。
      所以判据是「解码帧数 ≥ 时长×帧率，且多出来的不超过 1.5 帧」，
      即 `0 ≤ n − d·fps ≤ 1.5`。**方向是有意义的**：反过来（帧数少于时长×帧率）
      说明掉帧，那是转码事故，必须红。
    """
    try:
        n = int(frames)
        d = float(duration_s)
        num, den = fps_rational.split("/")
        fps = float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        return ""
    if frames_source == "decode_count":
        return "是" if 0.0 <= n - d * fps <= 1.5 else "否"
    return "是" if abs(n - d * fps) <= 0.5 else "否"


def base_row(mid: str, assay: str, role: str, p: Path, usage: str,
             evidence: str = "artifact_checked") -> dict:
    return {
        "material_id": mid, "assay": assay, "role": role,
        "path": "~/" + str(p.relative_to(HOME)),
        "bytes": p.stat().st_size, "sha256": sha256_file(p),
        "codec": "", "width": "", "height": "", "frames": "",
        "frames_source": "", "fps_rational": "", "fps_field_rate": "",
        "duration_s": "", "frames_x_fps_ok": "",
        "derives_from_sha256": "", "derives_from_status": "na",
        # t0（入水时刻）在本项目所有素材上都不存在：首帧鼠已在水中（DP-057），
        # 素材里没有、记录里也没有。这里刻意留空并给状态：缺失是声明出来的，不是漏填。
        "t0_source_s": "", "t0_status": "missing_not_obtained",
        "geometry_binding": "", "geometry_binding_status": "na",
        "human_record_ref": "", "human_record_status": "na",
        "usage_group": usage, "evidence_status": evidence,
    }


def video_row(mid: str, assay: str, role: str, p: Path, usage: str,
              evidence: str = "artifact_checked", **kw) -> dict:
    row = base_row(mid, assay, role, p, usage, evidence)
    row.update(probe(p))
    row["frames_x_fps_ok"] = frames_ok(row["frames"], row["duration_s"],
                                       row["fps_rational"],
                                       row["frames_source"])
    row.update(kw)
    return row


def plain_row(mid: str, assay: str, role: str, p: Path, usage: str,
              evidence: str = "artifact_checked", **kw) -> dict:
    row = base_row(mid, assay, role, p, usage, evidence)
    row["codec"] = p.suffix.lstrip(".")
    row["t0_status"] = "na"
    row.update(kw)
    return row


def load_manifest_videos() -> dict[str, set[str]]:
    """data/human_scores/manifests 里被点名过的 video_filename（只取名字集合做指针判定）。"""
    out: dict[str, set[str]] = {}
    d = ROOT / "data/human_scores/manifests"
    for key, name in (("FST", "FST全程_视频清单_给评分员.csv"),
                      ("TST", "T1精标_视频清单_给评分员.csv")):
        # utf-8-sig：映射表带 BOM，裸 utf-8 会把首列名读成 '﻿范式'
        with open(d / name, newline="", encoding="utf-8-sig") as f:
            out[key] = {r["video_filename"] for r in csv.DictReader(f)}
    return out


def load_mapping_table() -> dict[str, dict[str, str]]:
    """docs/CSI输入视频映射表.csv：CSI 用文件 ↔ 源片的官方对应（含源 sha256 前 16 位）。"""
    out: dict[str, dict[str, str]] = {}
    with open(ROOT / "docs/CSI输入视频映射表.csv", newline="",
              encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            out[r["CSI用文件"]] = r
    return out


def build() -> list[dict]:
    missing = [k for k, p in ROOTS.items()
               if not (p.is_file() if k == "exe_zip" else p.is_dir())]
    if missing:
        raise SystemExit(f"授权根目录缺失，拒产半张表：{missing}")

    manifest_videos = load_manifest_videos()
    mapping = load_mapping_table()
    rows: list[dict] = []

    # 1) 源片：FST 7 + TST 7。人工真值的拥有者是 data/human_scores/manifests，这里只给指针。
    for key, assay in (("fst_source", "FST"), ("tst_source", "TST")):
        for p in sorted(ROOTS[key].glob("*.mp4")):
            listed = p.name in manifest_videos[assay]
            rows.append(video_row(
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
        for p in sorted(ROOTS["csi_in"].glob(f"*{suf}")):
            m = mapping.get(p.name)
            src = src_sha.get((m["范式"], m["原视频"]), "") if m else ""
            rows.append(video_row(
                f"csi_transcode:{m['范式'] if m else 'na'}:{p.name}",
                m["范式"] if m else "na", "csi_transcode", p,
                usage="historical_or_dev_validation",
                derives_from_sha256=src,
                derives_from_status=("confirmed_by_mapping_table"
                                     if src else "unknown_provenance")))

    # 3) TST 切片：cut_provenance.csv 逐段记了源 sha256 全量与切窗，直接对得上。
    prov: dict[str, str] = {}
    raw = (ROOTS["tst_clips"] / "cut_provenance.csv").read_text().splitlines()
    for i in range(0, len(raw) - 1, 2):
        a = next(csv.reader([raw[i]]))
        b = next(csv.reader([raw[i + 1]]))
        prov[a[0] + ".mp4"] = b[1]
    for p in sorted(ROOTS["tst_clips"].glob("*.mp4")):
        listed = p.name in manifest_videos["TST"]
        rows.append(video_row(
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
    for p in sorted(ROOTS["fst_transcode_intermediate"].iterdir()):
        mid = f"transcode_intermediate:FST:{p.name}"
        if p.suffix.lower() in VIDEO_SUFFIX:
            rows.append(video_row(mid, "FST", "transcode_intermediate", p,
                                  usage="engineering_only",
                                  derives_from_status="unknown_provenance"))
        else:
            rows.append(plain_row(mid, "FST", "transcode_intermediate", p,
                                  usage="engineering_only",
                                  derives_from_status="unknown_provenance"))

    # 5) CSI 输出（SET/CLB/xlsx/BMP）：参数与几何的真身。CLB 目前只按文件名挂视频。
    for key in ("csi_out_fst", "csi_out_ctrl", "csi_out_rest",
                "csi_out_norm14"):
        for p in sorted(ROOTS[key].iterdir()):
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
    for p in sorted(ROOTS["ui_shots"].glob("*.png")):
        rows.append(plain_row(f"ui_screenshot:na:{p.name}", "na",
                              "ui_screenshot", p,
                              usage="historical_or_dev_validation"))

    # 7) 仓库夹具：真 CSI 文件拷进来的金标准（守卫测试验它与客户侧原件同哈希）。
    fix = ROOT / "tests/fixtures/csi_fst"
    for p in sorted(fix.iterdir()):
        if p.is_file():
            rows.append(plain_row(f"repo_fixture:na:{p.name}", "na",
                                  "repo_fixture", p, usage="test_golden",
                                  evidence="independently_reproduced"))

    # 8) 登记表本身：钉住本表是照哪个版本的登记表生成的。
    for p in (ROOT / "docs/CSI输入视频映射表.csv",
              ROOTS["tst_clips"] / "cut_provenance.csv",
              ROOTS["tst_clips"] / "manifest.csv",
              ROOTS["ui_shots"] / "CSI参数与文件格式_实录_2026-09-10.md"):
        rows.append(plain_row(f"registry_doc:na:{p.name}", "na",
                              "registry_doc", p, usage="engineering_only"))

    # 9) 原 EXE：取证材料。架构 §3.3 裁决永不执行、不加载 DT；把裁决写进行里。
    #    zip 内路径带安装目录前缀，裸文件名 unzip 匹配不到（实测教训）。
    for inner in ("Depressionscan  HR/ForcedSwimScan-Ver2/HR-V2.0/ForcedSwimScan.exe",
                  "Depressionscan  HR/TailSuspScan-Ver2/HR-V2.0/TailSuspScan.exe"):
        dig, size = sha256_in_zip(ROOTS["exe_zip"], inner)
        row = base_row(f"csi_exe:na:{Path(inner).name}", "na", "csi_exe",
                       ROOTS["exe_zip"], "forensic_never_execute",
                       evidence="static_recovered")
        row.update({"path": f"~/{ROOTS['exe_zip'].relative_to(HOME)}!{inner}",
                    "bytes": size, "sha256": dig, "codec": "exe",
                    "t0_status": "na"})
        rows.append(row)

    return rows


def self_check(rows: list[dict]) -> None:
    """同名源片跨范式必须不同哈希；否则这张表自己就在用文件名冒充身份。"""
    by_name: dict[str, dict[str, str]] = {}
    for r in rows:
        if r["role"] == "source_video":
            by_name.setdefault(Path(r["path"]).name, {})[r["assay"]] = r["sha256"]
    for name, per_assay in by_name.items():
        if len(per_assay) > 1 and len(set(per_assay.values())) == 1:
            raise SystemExit(f"同名源片哈希相同，身份表失效：{name}")
    n_video = sum(1 for r in rows if r["frames"])
    bad = [r["material_id"] for r in rows
           if r["frames"] and r["frames_x_fps_ok"] == "否"]
    if bad:
        raise SystemExit(f"帧数与时长×帧率不自洽（先查，不要直接放行）：{bad}")
    # 探针跑过（有帧率）却没有帧数 ⇒ 一定是 ffprobe 的键名/选项写错了，
    # 不是「这个文件没有帧数」。这一条就是为那次静默失败加的。
    silent = [r["material_id"] for r in rows
              if r["fps_rational"] and not r["frames"]]
    if silent:
        raise SystemExit(f"探针跑过却没拿到帧数（查 probe()，不要放行空值）：{silent}")
    if n_video != 68:
        raise SystemExit(f"视频行数 {n_video} != 68（14 源片 + 16 转码件 + 27 切片"
                         f" + 11 中间件），某根目录没扫全")
    print(f"自检通过：{len(rows)} 行，其中视频行 {n_video}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs/共用输入身份清单_v1.csv"))
    args = ap.parse_args()

    rows = build()
    self_check(rows)
    out = Path(args.out)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)
    print(f"写出 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
