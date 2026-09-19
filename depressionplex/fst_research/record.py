"""诊断记录组装：分区闭合、空杯语义、以及"这份记录不是什么"。

三件在本层落的事：

1. **分区闭合**——每杯四个可见性状态的帧数加起来必须等于分析帧数。
   不闭合就是丢帧或状态机漏了分支，**raise**，不许带着一笔糊涂账出报告。
2. **空杯语义**——人工申报空杯的杯子，记录里 `behavior_seconds = None`
   且 `behavior_seconds_semantics = "empty_no_output"`：结果为空，
   **不是 0 秒、也不是整窗不动**（Spec A §5.1 S16 / §6.2 第 3 行）。
   未申报的杯子同样是 None，但语义是"研究诊断不套标准窗、不出行为秒数"——
   两种 None 必须写不同的 semantics，混在一起就读错了。
3. **活动 vs 不动不分类**——observed 帧里哪些算活动哪些算不动，
   是一条**新的科学口径**（运动判据/阈值），没有批准就不做。
   本层只出可见性分区时长，并把"没做"和原因写进记录。

身份关联走 DP-133 共用清单（两条线共用一张表，不另建真值表）：
按 sha256 查登记行；**多个别名就全列**、`material_id` 写 null（同 DP-135 的规矩），
查不到就写 `manifest_lookup: null` 并说明，不猜。
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import SCHEMA_VERSION, PURPOSE
from .cup_perception import (QUALITY_DECLARED_ABSENT, QUALITY_LOST_SHORT,
                             QUALITY_OBSERVED, QUALITY_UNCLEAR, QUALITIES, FrameDiag)
from .overlay import refuse_in_repo

BEHAVIOR_SECONDS_EMPTY = "empty_no_output"
BEHAVIOR_SECONDS_NO_WINDOW = "research_diagnostics_no_window_alignment"

MOTION_CLASSIFICATION_REASON = (
    "observed 帧内的活动/不动分类是一条新的科学口径（运动判据与阈值），"
    "未经批准不做；本记录只出可见性分区时长（Spec A §6.2）。")


def sha256_of(path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def manifest_lookup(manifest_path, sha: str) -> dict | None:
    """按 sha256 查共用身份清单。**多别名全列**，不挑一个当"已核对来源"。"""
    p = Path(manifest_path)
    if not p.exists():
        return None
    hits = []
    with open(p, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if (row.get("sha256") or "").strip().lower() == sha.lower():
                hits.append(row)
    if not hits:
        return {"found": False, "aliases": [], "material_id": None,
                "note": "sha256 不在共用身份清单里：不猜身份，按未登记素材处理"}
    ids = sorted({r["material_id"] for r in hits})
    return {
        "found": True,
        "aliases": [{"material_id": r["material_id"], "role": r["role"],
                     "path": r["path"], "t0_status": r.get("t0_status", ""),
                     "t0_source_s": r.get("t0_source_s", "")} for r in hits],
        # 同一份字节多个登记位置 ≠ 多个来源核对过：身份写 null，别名全列
        "material_id": ids[0] if len(ids) == 1 else None,
        "distinct_material_ids": ids,
    }


def check_partition(counts: dict[str, int], total_frames: int) -> list[str]:
    """分区闭合校验。返回问题列表；空列表 = 闭合。"""
    problems: list[str] = []
    unknown_keys = sorted(set(counts) - set(QUALITIES))
    if unknown_keys:
        problems.append(f"出现未知可见性状态: {unknown_keys}")
    missing = sorted(set(QUALITIES) - set(counts))
    if missing:
        problems.append(f"缺少状态计数（按 0 记但不许不出现）: {missing}")
    got = sum(counts.get(q, 0) for q in QUALITIES)
    if got != total_frames:
        problems.append(f"分区不闭合：四状态合计 {got} ≠ 分析帧数 {total_frames}"
                        "——丢帧或状态机漏分支，不许带着出报告")
    return problems


def counts_of(diags: list[FrameDiag]) -> dict[str, int]:
    out = {q: 0 for q in QUALITIES}
    for d in diags:
        out[d.quality] = out.get(d.quality, 0) + 1
    return out


def top_reasons(diags: list[FrameDiag], limit: int = 6) -> list[dict]:
    """非 observed 帧的原因按频次排序。读报告的人先看这里，不看汇总绿。"""
    tally: dict[str, int] = {}
    for d in diags:
        if d.quality == QUALITY_OBSERVED:
            continue
        for r in d.reasons:
            key = r.split("：")[0].split("（")[0]
            tally[key] = tally.get(key, 0) + 1
    rows = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [{"reason": k, "frames": v} for k, v in rows]


def cup_record(*, cup_index: int, diags: list[FrameDiag], fps: float,
               declared_absent: bool, geometry: dict,
               features_summary: dict, spatial_scale_px: float | None) -> dict:
    counts = counts_of(diags)
    problems = check_partition(counts, len(diags))
    if problems:
        raise ValueError(f"杯 {cup_index} 诊断分区不闭合: {problems}")
    if declared_absent:
        semantics = BEHAVIOR_SECONDS_EMPTY
    else:
        semantics = BEHAVIOR_SECONDS_NO_WINDOW
    return {
        "cup": cup_index,
        "declared_absent": declared_absent,
        "geometry": geometry,
        "spatial_scale_px": spatial_scale_px,
        "analyzed_frames": len(diags),
        "quality_counts": counts,
        "observable_durations_s": {q: counts[q] / fps for q in QUALITIES},
        "behavior_seconds": None,
        "behavior_seconds_semantics": semantics,
        "motion_classification": {"performed": False,
                                  "reason": MOTION_CLASSIFICATION_REASON},
        "features": features_summary,
        "top_reasons": top_reasons(diags),
        "partition_problems": problems,
    }


def build_record(*, source: dict, time_base: dict, clock_ledger: dict,
                 window: dict, coverage: dict, geometry_confirmation: dict,
                 cups: list[dict], manifest: dict | None,
                 limits: list[str]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "purpose": PURPOSE,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "manifest_lookup": manifest,
        "time_base": time_base,
        "clocks": clock_ledger,
        "window": window,
        "coverage": coverage,
        "geometry_confirmation": geometry_confirmation,
        "cups": cups,
        "must_not_enter_acceptance_paths": True,
        "limits": limits,
    }


def write_record(record: dict, path) -> Path:
    """原子落盘 + 仓库外守卫。半成品记录不许留在磁盘上被人当结论读。"""
    out = refuse_in_repo(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    return out
