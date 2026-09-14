"""结果页数据模型（纯标准库，不 import PySide6，也不 import 引擎包）。

架构 §3.4 规定外壳不许 import 引擎包。CSV 字段契约靠测试钉住：
测试层两边都能 import，写一条断言 CSV_FIELDS_EXPECTED == analyze.CSV_FIELDS。

这一层只做「读 + 摆」，一个运算符都不许出现在数字上（派工单 B4 §2.1）。
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ResultsError(Exception):
    """结果加载错误。"""
    pass


#: CSV 字段契约（抄自引擎 depressionplex/cli/analyze.CSV_FIELDS，顺序必须一致）。
#: 测试会断言 CSV_FIELDS_EXPECTED == analyze.CSV_FIELDS。
CSV_FIELDS_EXPECTED: tuple[str, ...] = (
    "trial_id", "assay", "fps", "recording_frames", "window_frames",
    "scorable_frames", "unknown_frames_window", "validity_status",
    "occupied_fraction", "scored", "immobility_s", "immobility_raw_s",
    "mobility_s", "mobility_bouts", "first_mobility_onset_s",
    "gate_messages"
)

#: 每个秒数列 → 它的分母列。
#: 判断依据（depressionplex/assay_core/trial_report.py）：
#:   L189: window_s = window_frames / fps
#:   L191-192: mirror_pipeline = window_s - mob.seconds_pipeline
#:             mirror_raw = window_s - mob.seconds_raw
#:   L253-259: 引擎报告同时印「占窗口」(÷window_seconds) 与「占可评分」(÷scorable_frames/fps)
#:   L60-61: first_onset_s 是相对窗口起点的秒数 (first_onset_frame / fps)
#:
#: - immobility_s / immobility_raw_s 是 window_frames/fps - mobility，
#:   不是从 scorable_frames 算的。需要 window_frames 和 fps 才知道窗口长度，
#:   需要 scorable_frames 来算占可评分比例（引擎两种占比都印）。
#: - mobility_s 是直接量出来的帧数 ÷ fps，window_frames 不是它的除数，
#:   但需要它来算占窗口比例。
#: - first_mobility_onset_s 只除 fps，但它是相对窗口起点的，需要 window_frames
#:   来标明窗口位置（FST 窗口从 120s 开始）。
DENOMINATORS: dict[str, tuple[str, ...]] = {
    "immobility_s": ("window_frames", "scorable_frames", "fps"),
    "immobility_raw_s": ("window_frames", "scorable_frames", "fps"),
    "mobility_s": ("window_frames", "scorable_frames", "fps"),
    "first_mobility_onset_s": ("window_frames", "fps"),
}


@dataclass
class ResultsRow:
    """结果表的一行（scored 行或报警行，同一个类型）。

    scored=True 的行：16 个字段原样带着（字符串就是字符串，不转 float）。
    scored=False / 根本没有 CSV 行的隔间：kind="alarm"，带 reason。
    """
    trial_id: str
    chamber: int | None  # 非隔间级的报警行用 None
    kind: str  # "scored" 或 "alarm"

    # scored 行的字段（CSV 的 16 个字段）
    assay: str | None = None
    fps: str | None = None
    recording_frames: str | None = None
    window_frames: str | None = None
    scorable_frames: str | None = None
    unknown_frames_window: str | None = None
    validity_status: str | None = None
    occupied_fraction: str | None = None
    scored: str | None = None
    immobility_s: str | None = None
    immobility_raw_s: str | None = None
    mobility_s: str | None = None
    mobility_bouts: str | None = None
    first_mobility_onset_s: str | None = None
    gate_messages: str | None = None

    # 报警行的字段
    reason: str | None = None


@dataclass
class ResultsTable:
    """一个视频段的结果表（CSV + run.json 的组合）。"""
    rows: list[ResultsRow]

    # 上下文信息（来自 run.json，CSV 故意不含这些）
    tool_version: str | None = None
    assay: str | None = None
    scoring_window_s: list[float] | None = None
    theta_mob: float | None = None
    video_name: str | None = None
    video_fps: float | None = None
    video_n_frames: int | None = None
    frame_count_source: str | None = None

    # 缺失文件的标记
    csv_missing: bool = False
    run_json_missing: bool = False


def _make_csv_row_fields(row_dict: dict[str, str]) -> dict[str, str]:
    """从 DictReader 行提取 16 个 CSV 字段（不含 trial_id）。"""
    return {
        "assay": row_dict["assay"],
        "fps": row_dict["fps"],
        "recording_frames": row_dict["recording_frames"],
        "window_frames": row_dict["window_frames"],
        "scorable_frames": row_dict["scorable_frames"],
        "unknown_frames_window": row_dict["unknown_frames_window"],
        "validity_status": row_dict["validity_status"],
        "occupied_fraction": row_dict["occupied_fraction"],
        "scored": row_dict["scored"],
        "immobility_s": row_dict["immobility_s"],
        "immobility_raw_s": row_dict["immobility_raw_s"],
        "mobility_s": row_dict["mobility_s"],
        "mobility_bouts": row_dict["mobility_bouts"],
        "first_mobility_onset_s": row_dict["first_mobility_onset_s"],
        "gate_messages": row_dict["gate_messages"],
    }


def _build_row_from_csv(
    row_dict: dict[str, str],
    trial_id: str,
    chamber: int,
) -> ResultsRow:
    """从已验证的 CSV 行构造 ResultsRow（处理 scored/alarm 分支）。

    scored 列已由调用方验证为 "true" 或 "false"（G5）。
    """
    fields = _make_csv_row_fields(row_dict)
    scored_str = row_dict["scored"].strip().lower()
    is_scored = (scored_str == "true")

    if is_scored:
        return ResultsRow(
            trial_id=trial_id,
            chamber=chamber,
            kind="scored",
            **fields,
        )
    else:
        gate_msg = row_dict["gate_messages"]
        reason = gate_msg if gate_msg else "CSV 标了未放行计分，但引擎没给原因"
        return ResultsRow(
            trial_id=trial_id,
            chamber=chamber,
            kind="alarm",
            reason=reason,
            **fields,
        )


def load_results(exp: dict, video_index: int) -> ResultsTable:
    """加载一个视频段的结果（CSV + run.json）。

    Args:
        exp: experiment.json 的全部内容
        video_index: 视频在 exp["videos"] 列表中的索引

    Returns:
        ResultsTable

    Raises:
        ResultsError: CSV 表头不符、run.json schema 不符、文件格式错误、
                      必写键缺失、trial_id 认不出等
    """
    # 导入 engine 来获取文件路径和前缀
    from desktop.app.services.engine import output_paths, trial_prefix

    paths = output_paths(exp, video_index)
    csv_path = paths["csv"]
    run_json_path = paths["run_json"]

    csv_exists = csv_path.exists()
    run_json_exists = run_json_path.exists()

    # 两个文件都不存在 → 报错
    if not csv_exists and not run_json_exists:
        raise ResultsError(
            f"CSV 和 run.json 都不存在：\n  {csv_path}\n  {run_json_path}"
        )

    rows: list[ResultsRow] = []
    context = ResultsTable(
        rows=rows,
        csv_missing=not csv_exists,
        run_json_missing=not run_json_exists,
    )

    # ── 加载 run.json（如果存在）──────────────────────────────────────────────
    run_data: dict[str, Any] | None = None
    # G1：chamber_roster 有三种状态：
    #   None          = 名册未知（run.json 不存在，不许做任何「名册外」判断）
    #   set()         = 名册已知但为空（run.json 存在但 chambers[] 是空列表）
    #   {1, 2, ...}   = 名册已知且非空
    chamber_roster: set[int] | None = None

    if run_json_exists:
        try:
            with run_json_path.open("r", encoding="utf-8") as f:
                run_data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise ResultsError(f"读取 run.json 失败：{run_json_path}\n  {e}") from e

        # 校验 schema_version
        if "schema_version" not in run_data:
            raise ResultsError(
                f"run.json 缺少必写键 'schema_version'：{run_json_path}"
            )
        schema_version = run_data["schema_version"]
        if schema_version != "1":
            raise ResultsError(
                f"run.json 的 schema_version 不是 '1'：{schema_version!r}\n"
                f"  文件：{run_json_path}"
            )

        # 校验顶层必写键（F5）
        required_top_keys = [
            "tool_version", "assay", "scoring_window_s", "rules",
            "video", "calib_indices", "chambers", "plan_warnings",
            "chamber_validity", "not_scored"
        ]
        for key in required_top_keys:
            if key not in run_data:
                raise ResultsError(
                    f"run.json 缺少必写键 '{key}'：{run_json_path}"
                )

        # 提取上下文信息
        context.tool_version = run_data["tool_version"]
        context.assay = run_data["assay"]
        context.scoring_window_s = run_data["scoring_window_s"]

        # rules.theta_mob 必须存在
        rules = run_data["rules"]
        if "theta_mob" not in rules:
            raise ResultsError(
                f"run.json 的 rules 缺少必写键 'theta_mob'：{run_json_path}"
            )
        context.theta_mob = rules["theta_mob"]

        # video 必写键
        video_info = run_data["video"]
        video_required_keys = ["name", "fps", "n_frames", "frame_count_source"]
        for key in video_required_keys:
            if key not in video_info:
                raise ResultsError(
                    f"run.json 的 video 缺少必写键 '{key}'：{run_json_path}"
                )
        context.video_name = video_info["name"]
        context.video_fps = video_info["fps"]
        context.video_n_frames = video_info["n_frames"]
        context.frame_count_source = video_info["frame_count_source"]

        # 提取名册（F1）：chambers[].index 是唯一权威来源
        # G1：run.json 存在时 roster 初始化为 set()（不是 None）
        chamber_roster = set()
        chambers_list = run_data["chambers"]
        for ch_obj in chambers_list:
            if "index" not in ch_obj:
                raise ResultsError(
                    f"run.json 的 chambers[] 条目缺少必写键 'index'：{run_json_path}"
                )
            idx = ch_obj["index"]
            # G6：名册里的 index 必须 ≥ 1（引擎隔间号 1 起，runner.py:284）
            if idx < 1:
                raise ResultsError(
                    f"run.json 的 chambers[] 条目 index={idx!r} 不合法"
                    f"（引擎隔间号从 1 起）：{run_json_path}"
                )
            chamber_roster.add(idx)

    else:
        # run.json 缺失：名册未知（G1：保持 None，不能当成空名册处理）
        # 添加一个全局警告（chamber=None，F7）
        rows.append(ResultsRow(
            trial_id="[上下文缺失]",
            chamber=None,
            kind="alarm",
            reason="上下文缺失：run.json 文件不存在，无法显示 tool_version、theta_mob 等信息",
        ))

    # ── 加载 CSV（如果存在）──────────────────────────────────────────────────
    # csv_rows: chamber → row_dict（已通过所有合法性检查）
    # csv_row_nums: chamber → 首次出现的 CSV 行号（G4 报错用）
    csv_rows: dict[int, dict[str, str]] = {}
    csv_row_nums: dict[int, int] = {}

    if csv_exists:
        try:
            with csv_path.open("r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)

                # 校验表头
                if reader.fieldnames is None:
                    raise ResultsError(f"CSV 没有表头：{csv_path}")

                actual_fields = tuple(reader.fieldnames)
                if actual_fields != CSV_FIELDS_EXPECTED:
                    missing = set(CSV_FIELDS_EXPECTED) - set(actual_fields)
                    extra = set(actual_fields) - set(CSV_FIELDS_EXPECTED)
                    raise ResultsError(
                        f"CSV 表头与契约不符：{csv_path}\n"
                        f"  缺字段：{sorted(missing) if missing else '无'}\n"
                        f"  多字段：{sorted(extra) if extra else '无'}\n"
                        f"  期望顺序：{CSV_FIELDS_EXPECTED}\n"
                        f"  实际顺序：{actual_fields}"
                    )

                # 读取所有行
                for row_num, row_dict in enumerate(reader, start=2):  # CSV 行号从 2 开始

                    # G3：检测超长行（DictReader 把多余的值放在 None 键下）
                    if None in row_dict:
                        raise ResultsError(
                            f"CSV 第 {row_num} 行的值多于表头（期望 {len(CSV_FIELDS_EXPECTED)} 列）：\n"
                            f"  文件：{csv_path}"
                        )

                    # G2：检测截短行（DictReader 把缺失的列填为 None）
                    # 注意：空串是引擎的合法输出，只有 None 才代表列缺失
                    for field in CSV_FIELDS_EXPECTED:
                        if row_dict[field] is None:
                            raise ResultsError(
                                f"CSV 第 {row_num} 行缺少字段 '{field}'"
                                f"（行被截短，期望 {len(CSV_FIELDS_EXPECTED)} 列）：\n"
                                f"  文件：{csv_path}"
                            )

                    # F6：从 trial_id 提取 chamber 号（格式：prefix-chN）
                    # 认不出就 ResultsError，不许 pass
                    trial_id_str = row_dict["trial_id"]

                    if "-ch" not in trial_id_str:
                        raise ResultsError(
                            f"CSV 第 {row_num} 行的 trial_id 格式不对（期望 'prefix-chN'）："
                            f"{trial_id_str!r}\n"
                            f"  文件：{csv_path}"
                        )
                    try:
                        chamber = int(trial_id_str.split("-ch")[-1])
                    except (ValueError, IndexError) as e:
                        raise ResultsError(
                            f"CSV 第 {row_num} 行的 trial_id 无法解析 chamber 号："
                            f"{trial_id_str!r}\n"
                            f"  文件：{csv_path}\n"
                            f"  错误：{e}"
                        ) from e

                    # G4：同一个 chamber 不许出现两行
                    if chamber in csv_rows:
                        first_row_num = csv_row_nums[chamber]
                        raise ResultsError(
                            f"CSV 里隔间 ch{chamber} 出现了两次："
                            f"第 {first_row_num} 行（{csv_rows[chamber]['trial_id']!r}）"
                            f"和第 {row_num} 行（{trial_id_str!r}）\n"
                            f"  文件：{csv_path}"
                        )

                    # G5：scored 列必须是 True 或 False（大小写不敏感，去空白）
                    scored_raw = row_dict["scored"].strip().lower()
                    if scored_raw not in ("true", "false"):
                        raise ResultsError(
                            f"CSV 第 {row_num} 行的 scored 值不合法"
                            f"（期望 True 或 False，实际 {row_dict['scored']!r}）：\n"
                            f"  文件：{csv_path}"
                        )

                    csv_rows[chamber] = row_dict
                    csv_row_nums[chamber] = row_num

        except (OSError, csv.Error) as e:
            raise ResultsError(f"读取 CSV 失败：{csv_path}\n  {e}") from e

    # 获取 trial_id 前缀（F4）
    prefix = trial_prefix(exp, video_index)

    # ── 构造结果行 ─────────────────────────────────────────────────────────────
    #
    # 两条分支取决于名册状态：
    #   chamber_roster is not None  → 名册已知（run.json 存在），按名册驱动
    #   chamber_roster is None      → 名册未知（run.json 缺失），CSV 行保持自己的 kind

    if chamber_roster is not None:
        # ── 分支 A：名册已知，按名册遍历 ────────────────────────────────────
        for chamber in sorted(chamber_roster):
            tid = f"{prefix}-ch{chamber}"

            if chamber in csv_rows:
                # CSV 有这个隔间的行（kind 由 scored 列决定，F3）
                rows.append(_build_row_from_csv(csv_rows[chamber], tid, chamber))

            else:
                # CSV 没有 → 从 run.json 的两个报警源查找原因
                reason_parts = []

                # 先查 not_scored
                not_scored_list = run_data["not_scored"]
                found_in_not_scored = False
                for item in not_scored_list:
                    if "chamber" not in item:
                        raise ResultsError(
                            f"run.json 的 not_scored[] 条目缺少必写键 'chamber'："
                            f"{run_json_path}"
                        )
                    if "reason" not in item:
                        raise ResultsError(
                            f"run.json 的 not_scored[] 条目缺少必写键 'reason'："
                            f"{run_json_path}"
                        )
                    if item["chamber"] == chamber:
                        reason_parts.append(item["reason"])
                        found_in_not_scored = True
                        break

                # 再查 chamber_validity
                chamber_validity_list = run_data["chamber_validity"]
                found_in_validity = False
                for cv in chamber_validity_list:
                    cv_required_keys = ["chamber", "status", "occupied_fraction",
                                        "unsegmentable_fraction", "note"]
                    for key in cv_required_keys:
                        if key not in cv:
                            raise ResultsError(
                                f"run.json 的 chamber_validity[] 条目缺少必写键 '{key}'："
                                f"{run_json_path}"
                            )
                    if cv["chamber"] == chamber:
                        status = cv["status"]
                        note = cv["note"]
                        reason_parts.append(status)
                        if note:
                            reason_parts.append(note)
                        found_in_validity = True
                        break

                # 如果两个报警源都没有，说明引擎在隔间层之前就停了
                if not found_in_not_scored and not found_in_validity:
                    reason = (
                        f"run.json 的名册里有本隔间，但产出与报警里都没有它"
                        f"——引擎在隔间层之前就停了或产出不完整"
                    )
                else:
                    reason = " | ".join(reason_parts)

                rows.append(ResultsRow(
                    trial_id=tid,
                    chamber=chamber,
                    kind="alarm",
                    reason=reason,
                ))

        # CSV 里有名册外的隔间（F1 后半段）
        for chamber in sorted(csv_rows.keys()):
            if chamber not in chamber_roster:
                tid = f"{prefix}-ch{chamber}"
                row_dict = csv_rows[chamber]
                rows.append(ResultsRow(
                    trial_id=tid,
                    chamber=chamber,
                    kind="alarm",
                    reason=(
                        f"CSV 里出现了名册外的隔间"
                        f"（run.json chambers[] 里没有 ch{chamber}）"
                    ),
                    **_make_csv_row_fields(row_dict),
                ))

    else:
        # ── 分支 B：名册未知（run.json 缺失）────────────────────────────────
        # G1：CSV 行保持自己的 kind，不许挂「名册外」这类 reason
        # 上下文缺失的 alarm 行（chamber=None）已经在 run.json 缺失时加入了
        for chamber in sorted(csv_rows.keys()):
            tid = f"{prefix}-ch{chamber}"
            rows.append(_build_row_from_csv(csv_rows[chamber], tid, chamber))

    # 按 chamber 排序（None 排最前）
    rows.sort(key=lambda r: (r.chamber is not None, r.chamber if r.chamber is not None else -1))

    return context
