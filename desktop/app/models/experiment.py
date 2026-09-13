"""experiment.json 契约与序列化（纯函数，不 import PySide6）。

本模块只用标准库，可被无 GUI 环境直接测试。Widget 只负责收集与调用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# experiment.json v1 契约的字段清单（锁住，加字段必须改对应测试）
SCHEMA_KEYS = (
    "schema_version",
    "created_at",
    "operator",
    "note",
    "assay",
    "n_chambers",
    "calib_frames",
    "body_area_prior",
    "output_dir",
    "videos",
)


@dataclass
class VideoEntry:
    """视频条目。"""

    path: Path  # 绝对路径
    trial_prefix: str | None  # 空 ⇒ None（不是 ""）


@dataclass
class ExperimentPlan:
    """实验计划（内存形式）。"""

    assay: str
    n_chambers: int
    calib_frames: int
    body_area_prior: float | None
    output_dir: Path  # 绝对路径
    videos: list[VideoEntry]
    operator: str | None
    note: str | None


def validate_experiment_plan(plan: ExperimentPlan) -> str | None:
    """验证实验计划。

    Returns:
        错误信息，无错误则返回 None
    """
    if plan.n_chambers < 1:
        return f"隔间数必须 ≥ 1，当前为 {plan.n_chambers}"

    if plan.calib_frames < 1:
        return f"标定帧数必须 ≥ 1，当前为 {plan.calib_frames}"

    if len(plan.videos) == 0:
        return "至少需要一个视频文件"

    # 检查视频路径是否重复
    paths = [v.path for v in plan.videos]
    if len(paths) != len(set(paths)):
        return "视频列表中存在重复路径"

    # 检查输出目录是否可写（尝试创建目录 + 写临时文件）
    try:
        plan.output_dir.mkdir(parents=True, exist_ok=True)
        test_file = plan.output_dir / ".write_test"
        test_file.write_text("test", encoding="utf-8")
        test_file.unlink()
    except (OSError, PermissionError) as e:
        return f"输出目录不可写：{e}"

    return None


def serialize_experiment_plan(plan: ExperimentPlan) -> dict[str, Any]:
    """将实验计划序列化为 dict（可直接 json.dump）。

    路径一律转为绝对路径字符串。空值一律为 None（不是 "" 或 0）。
    created_at 必须带时区偏移（ISO 8601）。
    """
    # 确保路径是绝对路径
    output_dir_abs = plan.output_dir.resolve()

    return {
        "schema_version": "1",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "operator": plan.operator,
        "note": plan.note,
        "assay": plan.assay,
        "n_chambers": plan.n_chambers,
        "calib_frames": plan.calib_frames,
        "body_area_prior": plan.body_area_prior,
        "output_dir": str(output_dir_abs),
        "videos": [
            {
                "path": str(v.path.resolve()),
                "trial_prefix": v.trial_prefix,
            }
            for v in plan.videos
        ],
    }


def write_experiment_json(
    plan: ExperimentPlan, target_path: Path | None = None
) -> Path:
    """写出 experiment.json。

    Args:
        plan: 实验计划
        target_path: 目标路径。为 None 时使用 plan.output_dir / "experiment.json"

    Returns:
        实际写入的文件路径（绝对路径）

    Raises:
        FileExistsError: 目标文件已存在
        OSError: 写入失败
    """
    if target_path is None:
        target_path = plan.output_dir / "experiment.json"

    target_path = target_path.resolve()

    # 已存在时报错不覆盖（静默覆盖等于抹掉别人的实验计划）
    if target_path.exists():
        raise FileExistsError(f"目标文件已存在：{target_path}")

    data = serialize_experiment_plan(plan)

    # 键顺序照契约写（diff 好读）
    with target_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")  # 末尾加换行

    return target_path
