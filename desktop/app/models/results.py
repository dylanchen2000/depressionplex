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
#: 判断依据（引擎 cli/analyze.py 的 _row() 函数，第64行）：
#: - immobility_s / immobility_raw_s：来自 r.immobility_mirror_pipeline_s / raw_s，
#:   这些是在 scorable_frames_window 范围内计算的（见 trial_report.py）→ 分母 scorable_frames
#: - mobility_s：来自 mob.seconds_pipeline，是窗口内的总秒数 → 分母 window_frames + fps
#: - first_mobility_onset_s：来自 mob.first_onset_s，是从录像起点算的绝对时间 → 分母 fps
DENOMINATORS: dict[str, tuple[str, ...]] = {
    "immobility_s": ("scorable_frames",),
    "immobility_raw_s": ("scorable_frames",),
    "mobility_s": ("window_frames", "fps"),
    "first_mobility_onset_s": ("fps",),
}


def trial_id(stem: str, chamber: int) -> str:
    """生成 trial_id（与引擎 runner.py:284 同一个形状）。

    只许有一个派生器，测试会对着引擎产物核对。
    """
    return f"{stem}-ch{chamber}"


@dataclass
class ResultsRow:
    """结果表的一行（scored 行或报警行，同一个类型）。

    scored=True 的行：16 个字段原样带着（字符串就是字符串，不转 float）。
    scored=False / 根本没有 CSV 行的隔间：kind="alarm"，带 reason。
    """
    trial_id: str
    chamber: int
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


def load_results(exp: dict, video_index: int) -> ResultsTable:
    """加载一个视频段的结果（CSV + run.json）。

    Args:
        exp: experiment.json 的全部内容
        video_index: 视频在 exp["videos"] 列表中的索引

    Returns:
        ResultsTable

    Raises:
        ResultsError: CSV 表头不符、run.json schema 不符、文件格式错误等
    """
    # 导入 engine.output_paths 来获取文件路径
    # 注意：不能 import depressionplex，但可以 import desktop.app.services
    from desktop.app.services.engine import output_paths

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

    # 加载 run.json（如果存在）
    run_data: dict[str, Any] | None = None
    if run_json_exists:
        try:
            with run_json_path.open("r", encoding="utf-8") as f:
                run_data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise ResultsError(f"读取 run.json 失败：{run_json_path}\n  {e}") from e

        # 校验 schema_version
        schema_version = run_data.get("schema_version")
        if schema_version != "1":
            raise ResultsError(
                f"run.json 的 schema_version 不是 '1'：{schema_version!r}\n"
                f"  文件：{run_json_path}"
            )

        # 提取上下文信息
        context.tool_version = run_data.get("tool_version")
        context.assay = run_data.get("assay")
        context.scoring_window_s = run_data.get("scoring_window_s")

        rules = run_data.get("rules", {})
        context.theta_mob = rules.get("theta_mob")

        video_info = run_data.get("video", {})
        context.video_name = video_info.get("name")
        context.video_fps = video_info.get("fps")
        context.video_n_frames = video_info.get("n_frames")
        context.frame_count_source = video_info.get("frame_count_source")

    # 加载 CSV（如果存在）
    csv_rows: dict[int, dict[str, str]] = {}  # chamber → row
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
                for row_dict in reader:
                    trial_id_str = row_dict.get("trial_id", "")
                    # 从 trial_id 提取 chamber 号（格式：stem-chN）
                    if "-ch" in trial_id_str:
                        try:
                            chamber = int(trial_id_str.split("-ch")[-1])
                            csv_rows[chamber] = row_dict
                        except (ValueError, IndexError):
                            # trial_id 格式不对，跳过这一行
                            pass
        except (OSError, csv.Error) as e:
            raise ResultsError(f"读取 CSV 失败：{csv_path}\n  {e}") from e

    # 构造结果行
    # 情况1：有 CSV → 从 CSV 构造 scored 行
    for chamber, row_dict in csv_rows.items():
        rows.append(ResultsRow(
            trial_id=row_dict["trial_id"],
            chamber=chamber,
            kind="scored",
            assay=row_dict["assay"],
            fps=row_dict["fps"],
            recording_frames=row_dict["recording_frames"],
            window_frames=row_dict["window_frames"],
            scorable_frames=row_dict["scorable_frames"],
            unknown_frames_window=row_dict["unknown_frames_window"],
            validity_status=row_dict["validity_status"],
            occupied_fraction=row_dict["occupied_fraction"],
            scored=row_dict["scored"],
            immobility_s=row_dict["immobility_s"],
            immobility_raw_s=row_dict["immobility_raw_s"],
            mobility_s=row_dict["mobility_s"],
            mobility_bouts=row_dict["mobility_bouts"],
            first_mobility_onset_s=row_dict["first_mobility_onset_s"],
            gate_messages=row_dict["gate_messages"],
        ))

    # 情况2：有 run.json → 添加未产出数字的隔间（报警行）
    if run_data is not None:
        # 从 not_scored 添加报警行
        not_scored_list = run_data.get("not_scored", [])
        for item in not_scored_list:
            chamber = item.get("chamber")
            reason = item.get("reason", "未产出数字")
            if chamber is not None and chamber not in csv_rows:
                stem = Path(exp["videos"][video_index]["path"]).stem
                rows.append(ResultsRow(
                    trial_id=trial_id(stem, chamber),
                    chamber=chamber,
                    kind="alarm",
                    reason=reason,
                ))

        # 从 chamber_validity 添加报警行（这些隔间也没有 CSV 行）
        chamber_validity_list = run_data.get("chamber_validity", [])
        for cv in chamber_validity_list:
            chamber = cv.get("chamber")
            status = cv.get("status", "unknown")
            note = cv.get("note")
            if chamber is not None and chamber not in csv_rows:
                # 组合 status 和 note 作为 reason
                reason_parts = [status]
                if note:
                    reason_parts.append(note)
                reason = " | ".join(reason_parts)

                stem = Path(exp["videos"][video_index]["path"]).stem
                # 避免重复添加（可能已经在 not_scored 里）
                if not any(r.chamber == chamber for r in rows):
                    rows.append(ResultsRow(
                        trial_id=trial_id(stem, chamber),
                        chamber=chamber,
                        kind="alarm",
                        reason=reason,
                    ))

    # 情况3：CSV 不存在但 run.json 存在 → 所有隔间都是报警行
    # （这已经被上面的逻辑覆盖了）

    # 按 chamber 排序
    rows.sort(key=lambda r: r.chamber)

    # 如果 run.json 缺失，添加一个警告标记
    if not run_json_exists and csv_exists:
        # 添加一个特殊的报警行（chamber=-1表示全局警告）
        rows.insert(0, ResultsRow(
            trial_id="[上下文缺失]",
            chamber=-1,
            kind="alarm",
            reason="上下文缺失：run.json 文件不存在，无法显示 tool_version、theta_mob 等信息",
        ))

    return context
