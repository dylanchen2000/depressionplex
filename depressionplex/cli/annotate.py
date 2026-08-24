"""原子原语标注工具（规划文档 §3.5 / §6.2 轨 2）。

多标签原语勾选（不是互斥类别）。一次标注导出学术 / CSI 兼容 / 我方精细
三套口径。双人独立标注 + 逐帧 Cohen's κ（§6.3）。

用法：
    # 建标注文件（可用规则引擎输出做主动学习种子，标 suggested_by）
    python3 -m depressionplex.cli.annotate init out.json --trial v1 \
        --assay TST --annotator ann1 --n-frames 9661

    # 交互式编辑（人选 bout、逐原语勾选）
    python3 -m depressionplex.cli.annotate edit out.json

    # 导出某套口径的逐帧标签 + CSI 兼容 bout 统计
    python3 -m depressionplex.cli.annotate export out.json --rubric academic_tst

    # 双人一致性（逐原语 κ）
    python3 -m depressionplex.cli.annotate agree a.json b.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from ..assay_core import primitives as P
from ..assay_core import bouts

SCHEMA = 1


def _load(path: Path) -> dict:
    doc = json.loads(path.read_text())
    if doc.get("schema") != SCHEMA:
        raise SystemExit(f"标注文件 schema 不符: {doc.get('schema')} != {SCHEMA}")
    return doc


def _save(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1))


def _prims_for(assay: str) -> list[str]:
    return [pid for pid, d in P.PRIMITIVES.items() if assay in d["assay"]]


def cmd_init(args: argparse.Namespace) -> int:
    doc = {
        "schema": SCHEMA,
        "trial": args.trial,
        "assay": args.assay,
        "annotator": args.annotator,
        "fps": args.fps,
        "n_frames": args.n_frames,
        "bouts": [],
    }
    if args.from_events:
        # 主动学习种子：机器事件转建议 bout，人工确认/修改；不替代人工。
        ev = json.loads(Path(args.from_events).read_text())
        for b in ev.get("bouts", []):
            doc["bouts"].append(
                {
                    "start": b["start"],
                    "end": b["end"],
                    "primitives": b.get("primitives", {}),
                    "axis_orient": b.get("axis_orient", "down"),
                    "suggested_by": "rules",
                }
            )
    _save(Path(args.out), doc)
    print(f"已创建 {args.out}（{len(doc['bouts'])} 个建议 bout）")
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    path = Path(args.file)
    doc = _load(path)
    assay = doc["assay"]
    prims = _prims_for(assay)
    print(f"trial={doc['trial']} annotator={doc['annotator']} 原语: {prims}")
    while True:
        print(f"\nbouts ({len(doc['bouts'])}):")
        for i, b in enumerate(doc["bouts"]):
            on = sorted(k for k, v in b["primitives"].items() if v)
            tag = " [建议]" if b.get("suggested_by") else ""
            print(f"  {i}: [{b['start']},{b['end']}] {on} orient={b.get('axis_orient')}{tag}")
        cmd = input("命令 (a=加 bout / d=删 / <i>=编辑 / s=保存退出 / q=不保存退出): ").strip()
        if cmd in ("q", "s"):
            if cmd == "s":
                _save(path, doc)
                print("已保存")
            return 0
        if cmd == "a":
            rng = input("start end: ").split()
            doc["bouts"].append(
                {"start": int(rng[0]), "end": int(rng[1]), "primitives": {},
                 "axis_orient": "down"}
            )
            continue
        if cmd == "d":
            i = int(input("删哪个: "))
            del doc["bouts"][i]
            continue
        try:
            i = int(cmd)
        except ValueError:
            continue
        b = doc["bouts"][i]
        for pid in prims:
            cur = bool(b["primitives"].get(pid, False))
            ans = input(f"  {pid} [{P.PRIMITIVES[pid]['label']}] 当前={cur} (y/n/回车跳过): ").strip()
            if ans in ("y", "n"):
                b["primitives"][pid] = ans == "y"
        ans = input(f"  axis_orient 当前={b.get('axis_orient')} (up/level/down/回车跳过): ").strip()
        if ans in ("up", "level", "down"):
            b["axis_orient"] = ans
        b.pop("suggested_by", None)  # 人工确认过即不再是"建议"


def cmd_export(args: argparse.Namespace) -> int:
    doc = _load(Path(args.file))
    n = doc["n_frames"]
    fps = doc["fps"]
    bouts_list = [P.Bout(b["start"], b["end"], b["primitives"]) for b in doc["bouts"]]
    out: dict = {"rubric": args.rubric, "per_frame": {}, "summary": {}}
    # 逐帧类别：先展开原语再套规则表
    prim_frames = {
        pid: P.expand_to_frames(bouts_list, n, pid) for pid in _prims_for(doc["assay"])
    }
    labels: list[str] = []
    for i in range(n):
        d = {pid: bool(arr[i]) for pid, arr in prim_frames.items()}
        for b in doc["bouts"]:
            if b["start"] <= i <= b["end"]:
                d["axis_orient"] = b.get("axis_orient", "down")
        hits = P.export_rubric(d, args.rubric)
        labels.append(hits[0] if hits else "none")
    out["per_frame_count"] = {
        c: int(sum(1 for l in labels if l == c)) for c in sorted(set(labels))
    }
    for cls in sorted(set(labels)):
        if cls == "none":
            continue
        s = bouts.score(labels, cls, params=bouts.CSI_TST_STARTING_POINT, fps=fps)
        out["summary"][cls] = {
            "bouts": s.bouts,
            "seconds": round(s.seconds, 2),
            "duration_pct": round(s.duration_pct, 2),
        }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def cmd_agree(args: argparse.Namespace) -> int:
    a = _load(Path(args.a))
    b = _load(Path(args.b))
    if a["n_frames"] != b["n_frames"]:
        raise SystemExit("两文件帧数不符")
    n = a["n_frames"]
    ba = [P.Bout(x["start"], x["end"], x["primitives"]) for x in a["bouts"]]
    bb = [P.Bout(x["start"], x["end"], x["primitives"]) for x in b["bouts"]]
    print(f"逐原语逐帧 Cohen's κ（{a['annotator']} vs {b['annotator']}）:")
    for pid in _prims_for(a["assay"]):
        if P.PRIMITIVES[pid]["kind"] != "bool":
            continue
        ka = P.expand_to_frames(ba, n, pid)
        kb = P.expand_to_frames(bb, n, pid)
        print(f"  {pid}: κ={P.Cohen_kappa(ka, kb):.3f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("out")
    p.add_argument("--trial", required=True)
    p.add_argument("--assay", required=True, choices=("TST", "FST"))
    p.add_argument("--annotator", required=True)
    p.add_argument("--n-frames", type=int, required=True)
    p.add_argument("--fps", type=float, default=25.0)
    p.add_argument("--from-events", default=None, help="规则引擎建议 bout 的 JSON")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("edit")
    p.add_argument("file")
    p.set_defaults(fn=cmd_edit)

    p = sub.add_parser("export")
    p.add_argument("file")
    p.add_argument("--rubric", required=True, choices=sorted(P.RUBRICS))
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("agree")
    p.add_argument("a")
    p.add_argument("b")
    p.set_defaults(fn=cmd_agree)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
