#!/usr/bin/env python3
"""DP-042 切片映射核验 + DP-032 隔间定位对照（只读，不改任何素材）。

做四件事，全部机器判定，不依赖文件名可信：
  1) 用 sha256 核对 cut_provenance.csv 记的源视频（provenance 是否可独立复现）
  2) 首帧模板匹配定每个切片在源画面里的真实 x 偏移（切片高=源高，只搜 x）
  3) 由 x 偏移排出「左起第几个」，与文件名里的 chN 对照
  4) 由源首帧的列强度剖面找隔间分隔线，与切片边界对照 —— 这是 DP-032 的关键：
     切片是等分四列切的，软件按面板几何定隔间，两者差多少直接决定
     「隔间 4 判空」是不是「看错了地方」

用法：python3 scripts/dp042_verify_slice_mapping.py [切片目录] [源视频目录]
"""
import hashlib
import re
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

CLIPS = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Downloads/tst_30clips"
SRC = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.home() / "Work/heavy/depression/悬尾"


def probe(p):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,nb_frames", "-of", "csv=p=0", str(p)],
        capture_output=True, text=True, check=True).stdout.strip()
    w, h, n = out.split(",")[:3]
    return int(w), int(h), int(n)


def first_frame_gray(p, w, h):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(p), "-vframes", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(raw[:w * h], np.uint8).reshape(h, w)


def sha256(p):
    hh = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            hh.update(blk)
    return hh.hexdigest()


def read_provenance(path):
    """cut_provenance.csv 行内有换行，不能用 csv 模块直读。"""
    text = path.read_text(encoding="utf-8")
    rows = re.findall(
        r'([^\s,]+),"([^"]+)",(\d+),(\d+),([a-z0-9]+),(\d+),(\d+),(\d+/\d+)\s+([\d.]+),([0-9a-f]{64})',
        text)
    return {r[0]: {"source": r[1], "t0": int(r[2]), "t1": int(r[3]),
                   "w": int(r[5]), "h": int(r[6]), "sha256": r[9]} for r in rows}


def dividers(frame):
    """列强度剖面找竖直分隔结构。背光下动物暗、背景亮、隔板/框暗。"""
    prof = frame.mean(axis=0)
    k = 5
    sm = np.convolve(prof, np.ones(k) / k, mode="same")
    thr = sm.mean() - 1.0 * sm.std()
    dark = sm < thr
    runs, start = [], None
    for x, d in enumerate(dark):
        if d and start is None:
            start = x
        elif not d and start is not None:
            runs.append((start, x - 1))
            start = None
    if start is not None:
        runs.append((start, len(dark) - 1))
    return [(a, b, (a + b) // 2) for a, b in runs if b - a >= 1]


def main():
    prov = read_provenance(CLIPS / "cut_provenance.csv")
    print(f"cut_provenance 行数 = {len(prov)}")

    src_by_sha = {}
    print("\n=== 1) 源视频 sha256 核对 ===")
    for p in sorted(SRC.glob("*.mp4")):
        s = sha256(p)
        src_by_sha[s] = p
        print(f"  {s[:12]}  {p.name}")

    clips = sorted(CLIPS.glob("*.mp4"))
    print(f"\n切片数 = {len(clips)}")

    groups = {}
    print("\n=== 2) 首帧模板匹配定 x 偏移 ===")
    print(f"{'切片':34s} {'源视频':30s} {'切片WxH':10s} {'帧数':6s} "
          f"{'x偏移':6s} {'NCC':7s} {'次峰':7s} {'判定'}")
    for c in clips:
        tid = c.stem
        pr = prov.get(tid)
        if pr is None:
            print(f"  {tid}: provenance 缺失，跳过")
            continue
        srcp = src_by_sha.get(pr["sha256"])
        if srcp is None:
            print(f"  {tid}: sha256 对不上任何源视频")
            continue
        cw, ch, cn = probe(c)
        sw, sh, sn = probe(srcp)
        cf = first_frame_gray(c, cw, ch)
        sf = first_frame_gray(srcp, sw, sh)
        if ch != sh:
            note = f"高不等({ch}!={sh})，非纯横切"
            print(f"  {tid}: {note}")
            continue
        res = cv2.matchTemplate(sf, cf, cv2.TM_CCOEFF_NORMED)[0]
        x = int(res.argmax())
        best = float(res[x])
        m = res.copy()
        lo, hi = max(0, x - cw // 2), min(len(m), x + cw // 2 + 1)
        m[lo:hi] = -1
        second = float(m.max()) if len(m) > hi - lo else float("nan")
        ok = "唯一峰" if best > 0.9 and best - second > 0.05 else "峰不锐利"
        groups.setdefault(srcp, []).append((tid, x, cw, best, cn))
        print(f"  {tid:32s} {srcp.name:28s} {cw}x{ch:<6d} {cn:<6d} "
              f"{x:<6d} {best:.4f}  {second:.4f}  {ok}")

    print("\n=== 3) chN 与「左起第几个」对照 + 4) 隔间分隔线对照 ===")
    for srcp, items in sorted(groups.items(), key=lambda kv: kv[0].name):
        sw, sh, sn = probe(srcp)
        sf = first_frame_gray(srcp, sw, sh)
        items.sort(key=lambda t: t[1])
        print(f"\n--- {srcp.name}  源 {sw}x{sh}  {sn} 帧 ---")
        cov = []
        for rank, (tid, x, cw, best, cn) in enumerate(items, 1):
            label = tid.rsplit("-ch", 1)[-1]
            agree = "一致" if label == str(rank) else f"不一致(chN={label})"
            print(f"  左起第 {rank}  x=[{x:3d},{x + cw - 1:3d}]  宽{cw}  {tid:32s} {agree}")
            cov.append((x, x + cw - 1))
        used = sum(b - a + 1 for a, b in cov)
        print(f"  切片覆盖 {used}/{sw} px，未覆盖 {sw - used} px")
        gaps = []
        prev = 0
        for a, b in cov:
            if a > prev:
                gaps.append((prev, a - 1))
            prev = b + 1
        if prev < sw:
            gaps.append((prev, sw - 1))
        print(f"  未被任何切片覆盖的 x 区段: {gaps if gaps else '无'}")
        dv = dividers(sf)
        print(f"  列剖面暗带（隔板/框候选）: "
              f"{[(a, b) for a, b, _ in dv] if dv else '未检出'}")


if __name__ == "__main__":
    main()
