#!/usr/bin/env python3
"""把被 4 KiB 粘贴框截断的秒表工具审计 JSON 抢救成 CSV。

背景（DP-010）：评分员张的导出报错，只能从浏览器控制台贴 localStorage 内容，
粘贴框在第 4096 个字符处**静默截断**，JSON 因此不完整、`json.loads` 直接失败。

本工具的唯一职责是**如实抢救**：
- 只输出 `done` 数组里**完整闭合**的记录；
- 尾部那条被截断的记录**必须丢弃并显式报出**，不得修补、不得补全 holds、不得置 0；
- `mobile_seconds` 一律丢弃，改用 `union(sorted(holds))` 重算（工具每按键多算约 0.121 s 墙钟）；
- 无法抢救的信息（`order` 之外的 24 个未评试次）如实报"缺失"，不写占位行。

"能救几条就是几条"——把 3 条当 14 条用，比没有数据更危险。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys

from . import _stdio
from pathlib import Path

DONE_KEY = '"done":['


def _split_top_level_objects(text: str) -> tuple[list[str], str]:
    """按顶层花括号切分 `done` 数组内容，返回（完整对象列表, 残余尾巴）。

    自己走括号深度而不是找 `},{`，因为 holds 里全是嵌套 `[]`，
    而字符串里可能出现括号（评分员备注栏是自由文本）。
    """
    objects: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                objects.append(text[start : i + 1])
                start = -1
    tail = text[start:] if start >= 0 else ""
    return objects, tail


def union_seconds(holds: list) -> tuple[float, int, bool, int]:
    """并集时长、按键段数、是否乱序、零长段个数。

    **必须先排序再取并集**：43 个试次里 12 个 holds 数组乱序自嵌套，
    朴素求和最多虚高 +54.8 s（+30%）。
    """
    pairs = [(float(a), float(b)) for a, b in holds]
    unsorted = any(pairs[i][0] > pairs[i + 1][0] for i in range(len(pairs) - 1))
    zero_len = sum(1 for a, b in pairs if b <= a)
    total = 0.0
    cur_a = cur_b = None
    for a, b in sorted(pairs):
        if b <= a:
            continue
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += cur_b - cur_a
    return total, len(pairs), unsorted, zero_len


def salvage(path: Path) -> tuple[list[dict], dict]:
    raw = path.read_text()
    idx = raw.find(DONE_KEY)
    if idx < 0:
        raise SystemExit(f"找不到 {DONE_KEY}，这个文件不是秒表工具的内部状态：{path}")
    objects, tail = _split_top_level_objects(raw[idx + len(DONE_KEY) :])

    order: list[str] = []
    head = raw[:idx]
    o = head.find('"order":[')
    if o >= 0:
        end = head.find("]", o)
        if end > 0:
            order = json.loads(head[o + len('"order":') : end + 1])

    rows = []
    for text in objects:
        rec = json.loads(text)
        total, n_holds, unsorted, zero_len = union_seconds(rec.get("holds") or [])
        rows.append(
            {
                "trial_id": rec.get("trial_id", ""),
                "mobile_union_s": round(total, 2),
                "immobility_s": round(360.0 - total, 2),
                "n_hold_segments": n_holds,
                "holds_unsorted": unsorted,
                "zero_length_segments": zero_len,
                "playback_rate": rec.get("playback_rate", ""),
                "tail_climbing": rec.get("tail_climbing", ""),
                "unscoreable": rec.get("unscoreable", ""),
                "presentation_order": rec.get("presentation_order", ""),
                "scored_at": rec.get("scored_at", ""),
                "note": rec.get("note", ""),
                "mobile_seconds_DISCARDED": rec.get("mobile_seconds", ""),
            }
        )

    partial_id = ""
    if tail:
        t = tail.find('"trial_id":"')
        if t >= 0:
            partial_id = tail[t + len('"trial_id":"') :].split('"', 1)[0]

    report = {
        "file_chars": len(raw),
        "salvaged": len(rows),
        "partial_dropped": bool(tail),
        "partial_trial_id": partial_id,
        "order_len": len(order),
        "never_scored": max(0, len(order) - len(rows) - (1 if tail else 0)),
    }
    return rows, report


def main() -> int:
    _stdio.force_utf8()

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    args = ap.parse_args()

    rows, report = salvage(args.input)
    with args.output.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"输入 {report['file_chars']} 字符（4096 = 粘贴框上限，已截断）")
    print(f"抢救出完整记录 **{report['salvaged']}** 条 → {args.output}")
    if report["partial_dropped"]:
        print(f"丢弃尾部被截断的 1 条：{report['partial_trial_id']}（不修补、不置 0）")
    print(f"`order` 里共 {report['order_len']} 个试次，其中 {report['never_scored']} 个本次导出中完全没有记录")
    print("注意：`mobile_seconds` 已丢弃，表里 `mobile_union_s` 由 union(sorted(holds)) 重算")
    return 0


if __name__ == "__main__":
    sys.exit(main())
