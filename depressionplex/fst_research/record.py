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
   本层只出**抽样记录**的可见性分区计数与显式命名的时间加权估计
   （R2-115 T1：抽样计数不是连续时长，`time_weighted_seconds` 的权重、
   尾处理与"这是估计"都写进记录），并把"没做"和原因写进记录。

身份关联走 DP-133 共用清单（两条线共用一张表，不另建真值表）：
按 sha256 查登记行；**多个别名就全列**、`material_id` 写 null（同 DP-135 的规矩），
查不到就写 `manifest_lookup: null` 并说明，不猜。

4. **研究证据不被下一轮覆盖**（R2-115 P组）——每次运行开独立 run 目录
   （`new_run_dir`），记录/几何提案/短片全落在里面；`write_record` 对已存在
   目标**拒绝覆盖**；全部产物成功才写 `_完成清单.json`（含逐件 sha256），
   没有清单的 run 目录视为未完成。记录带代码 SHA/dirty/实际命令行
   （`code_provenance`），失败清理只删本次 run 目录，旧证据只读。
"""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import SCHEMA_VERSION, PURPOSE
from .cup_perception import (QUALITY_DECLARED_ABSENT, QUALITY_LOST_SHORT,
                             QUALITY_OBSERVED, QUALITY_UNCLEAR, QUALITIES, FrameDiag)
from .overlay import REPO_ROOT, refuse_in_repo

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


# ---------------------------------------------------------------------------
# R2-115 P组：run 目录 / 代码出处 / 完成清单——研究证据不被下一轮覆盖
# ---------------------------------------------------------------------------

MANIFEST_NAME = "_完成清单.json"
MANIFEST_SCHEMA = "fst-research-run-manifest-v1"


def make_run_id(now: datetime | None = None) -> str:
    """run 标识：时间戳 + 8 位随机 hex（同一秒内重复运行也不会撞）。"""
    now = now or datetime.now()
    return f"run_{now:%Y%m%d-%H%M%S}_{uuid.uuid4().hex[:8]}"


def new_run_dir(out_dir, stem: str, run_id: str | None = None) -> tuple[Path, str]:
    """开本次运行的独立产物目录（暂存区）：旧证据只读，绝不覆盖。

    目录名 `<stem>_<run_id>`；已存在即 raise（不 exist_ok）——撞名说明
    生成逻辑出了问题，宁可拒绝也不混写。仓库内落点照旧拒绝。
    """
    base = refuse_in_repo(out_dir)
    rid = run_id or make_run_id()
    d = base / f"{stem}_{rid}"
    if d.exists():
        raise FileExistsError(f"run 目录已存在，拒绝混写: {d}")
    base.mkdir(parents=True, exist_ok=True)
    d.mkdir()                       # 不 exist_ok：撞名即失败
    return d, rid


def code_provenance(repo_root=REPO_ROOT) -> dict:
    """代码出处：git commit + dirty 标记；非 git 状态**明说**，不猜不编。"""
    root = Path(repo_root)

    def _git(*a: str):
        return subprocess.run(["git", *a], cwd=root, capture_output=True,
                              text=True, timeout=15)
    try:
        head = _git("rev-parse", "HEAD")
        if head.returncode != 0:
            return {"vcs": "not_a_git_repo",
                    "note": f"git rev-parse 失败：{head.stderr.strip()[:200]}"}
        branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        status = _git("status", "--porcelain")
        dirty_files = [l.strip() for l in status.stdout.splitlines() if l.strip()]
        return {"vcs": "git",
                "commit": head.stdout.strip(),
                "branch": branch or None,
                "dirty": bool(dirty_files),
                "dirty_file_count": len(dirty_files),
                "dirty_files_sample": dirty_files[:20]}
    except (OSError, subprocess.SubprocessError) as e:
        return {"vcs": "git_unavailable", "note": f"git 不可用：{e}"}


def _atomic_write_json(payload: dict, path) -> Path:
    """JSON 原子落盘（tmp → replace）+ 仓库守卫。半成品不留盘。"""
    out = refuse_in_repo(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    return out


def write_completion_manifest(run_dir, *, run_id: str, input_sha256: str,
                              code: dict, artifacts: dict) -> Path:
    """全部产物成功后才写完成清单：逐件 sha256 + 输入哈希 + 代码出处。

    没有这份清单的 run 目录 = 未完成（失败/中断），里面的文件不得当
    交付证据用。列出的产物必须真实存在——清单不许替不存在的文件背书。
    """
    files = {}
    for name, p in artifacts.items():
        p = Path(p)
        if not p.exists():
            raise FileNotFoundError(f"完成清单拒绝列不存在的产物: {name} → {p}")
        files[name] = {"path": p.name, "bytes": p.stat().st_size,
                       "sha256": sha256_of(p)}
    payload = {
        "schema": MANIFEST_SCHEMA,
        "run_id": run_id,
        "status": "complete",
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input_sha256": input_sha256,
        "code": code,
        "files": files,
        "note": "本清单只在本次运行全部产物成功写出后生成；没有清单的 run 目录"
                "视为未完成，其中的文件不得当作交付证据",
    }
    return _atomic_write_json(payload, Path(run_dir) / MANIFEST_NAME)


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


def time_weighted_seconds(diags: list[FrameDiag], *, fps: float,
                          tail_spacing_frames: int,
                          declared_absent: bool,
                          analysis_end_frames: int) -> dict:
    """状态时长的**时间加权估计**（R2-115 T1）：显式命名、区间权重、尾处理写明。

    抽样计数不是连续时长——旧版 `counts/fps` 把每条抽样记录当 1/fps 秒，
    step=5 时低估 5 倍还叫"秒"。这里改成明确的估计量：
    - 权重 = 相邻抽样记录的**实际源帧号差** / fps（区间权重，不假设等距）；
    - 尾记录没有"下一条"：区间 = [末抽样帧, 分析末端)，取
      `min(tail_spacing_frames, analysis_end_frames - 末帧)`——**最后一个区间
      不得超过实际视频/分析范围末端**（R3-115 ②；总帧数不整除 step 时尾区间
      不足一个步长，按剩余帧计），写进 provenance，不静默；
    - 申报空杯 / 空序列 ⇒ 全 None：结果为空，不是 0 秒。
    """
    if declared_absent:
        return {"seconds": {q: None for q in QUALITIES},
                "provenance": {"computed": False,
                               "reason": "申报空杯：结果为空，不输出 0 秒"}}
    if not diags:
        return {"seconds": {q: None for q in QUALITIES},
                "provenance": {"computed": False, "reason": "抽样序列为空：结果为空"}}
    if fps <= 0:
        raise ValueError(f"fps 必须为正: {fps}")
    if tail_spacing_frames < 1:
        raise ValueError(f"尾处理步长必须 ≥ 1 帧: {tail_spacing_frames}")
    if analysis_end_frames <= diags[-1].frame:
        raise ValueError(
            f"分析末端 {analysis_end_frames} 必须晚于末抽样帧 {diags[-1].frame}"
            "（尾区间不能是空的或倒的）")
    total = {q: 0.0 for q in QUALITIES}
    for i, d in enumerate(diags):
        if i + 1 < len(diags):
            span = diags[i + 1].frame - d.frame
            if span <= 0:
                raise ValueError(
                    f"抽样记录帧号必须严格递增: {d.frame} → {diags[i + 1].frame}")
        else:
            # R3-115 ②：尾区间 = [末抽样帧, 分析末端)，不许越过末端
            span = min(tail_spacing_frames, analysis_end_frames - d.frame)
        total[d.quality] = total.get(d.quality, 0.0) + span / fps
    tail = min(tail_spacing_frames, analysis_end_frames - diags[-1].frame)
    return {"seconds": {q: total[q] for q in QUALITIES},
            "provenance": {
                "computed": True,
                "method": "时间加权估计：权重 = 相邻抽样记录的实际源帧号差 / fps",
                "tail_handling": (f"尾记录区间 = [末抽样帧 {diags[-1].frame}, 分析末端 "
                                  f"{analysis_end_frames})，共 {tail} 帧"
                                  f"（{tail / fps:.3f} s）= min(一个抽样步长 "
                                  f"{tail_spacing_frames} 帧, 末端剩余帧)——"
                                  "不超过实际视频/分析范围末端"),
                "note": "这是抽样帧上的估计量，不是连续逐帧统计"}}


def cup_record(*, cup_index: int, diags: list[FrameDiag], fps: float,
               declared_absent: bool, geometry: dict,
               features_summary: dict, spatial_scale_px: float | None,
               sample_step_frames: int = 1,
               analysis_end_frames: int) -> dict:
    counts = counts_of(diags)
    problems = check_partition(counts, len(diags))
    if problems:
        raise ValueError(f"杯 {cup_index} 诊断分区不闭合: {problems}")
    if declared_absent:
        semantics = BEHAVIOR_SECONDS_EMPTY
    else:
        semantics = BEHAVIOR_SECONDS_NO_WINDOW
    tw = time_weighted_seconds(diags, fps=fps,
                               tail_spacing_frames=sample_step_frames,
                               declared_absent=declared_absent,
                               analysis_end_frames=analysis_end_frames)
    return {
        "cup": cup_index,
        "declared_absent": declared_absent,
        "geometry": geometry,
        "spatial_scale_px": spatial_scale_px,
        # R2-115 T1：这些是**抽样记录**的计数，不是连续时长/帧数
        "sampled_frames": len(diags),
        "sampled_state_counts": counts,
        "time_weighted_sampled_s": tw["seconds"],
        "time_weighting": tw["provenance"],
        "behavior_seconds": None,
        "behavior_seconds_semantics": semantics,
        "motion_classification": {"performed": False,
                                  "reason": MOTION_CLASSIFICATION_REASON},
        "features": features_summary,
        "top_reasons": top_reasons(diags),
        "partition_problems": problems,
    }


def build_record(*, source: dict, time_base: dict, clock_ledger: dict,
                 window: dict, coverage: dict, sampling: dict,
                 geometry_confirmation: dict,
                 cups: list[dict], manifest: dict | None,
                 limits: list[str],
                 provenance: dict, research_params: dict) -> dict:
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
        # R2-115 T1：抽样口径与实际消费的帧号（计数是抽样记录数，不是连续帧数）
        "sampling": sampling,
        "geometry_confirmation": geometry_confirmation,
        # R2-115 P组：run 标识、代码 SHA/dirty、实际命令行、研究参数与起点值出处
        "provenance": provenance,
        "research_params": research_params,
        "cups": cups,
        "must_not_enter_acceptance_paths": True,
        "limits": limits,
    }


def write_record(record: dict, path) -> Path:
    """原子落盘 + 仓库外守卫 + **拒绝覆盖已有证据**（R2-115 P组）。

    半成品记录不许留在磁盘上被人当结论读；旧记录是上一轮的证据，
    不是草稿——新一轮运行请开新的 run 目录，而不是覆盖它。
    """
    out = refuse_in_repo(path)
    if out.exists():
        raise FileExistsError(
            f"拒绝覆盖已有研究证据: {out}（新一轮运行请写新的 run 目录）")
    return _atomic_write_json(record, out)
