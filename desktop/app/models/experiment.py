"""experiment.json 契约与序列化（纯函数，不 import PySide6）。

只用标准库 + `desktop.app.assays`（那份表也只有标准库），所以可以在没有 PySide6 的
环境里直接测——本仓的测试套件正是这种环境。Widget 只负责收集与调用。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from desktop.app.assays import ASSAY_WINDOWS_UI

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


def probe_writable(target: Path) -> str | None:
    """探 `target` 落得下去吗，**不建任何目录**。

    往上找到第一个已存在的祖先，在那里写一个临时探针再删掉。
    为什么不用 `os.access(p, os.W_OK)`：Windows 上它只看只读属性、不看 ACL，
    会把「其实写不进去」报成可写——产品要装在 Windows 上，这个假阳性接受不了。

    为什么不再像原来那样 `mkdir(parents=True)` 之后再探：**验证是诊断**。
    校验失败（或用户看完提示又反悔）时，客户机上已经凭空多出一串空目录了；
    而且这么写的话 `validate` 就不是可重复调用的纯检查，将来加一个「预检」
    按钮就等于每点一下建一棵树。同型教训见 `paths.py` 的守卫（规则 8）。
    """
    p = target
    while not p.exists():
        parent = p.parent
        if parent == p:
            return f"输出目录没有任何已存在的上级，无处可探：{target}"
        p = parent
    if not p.is_dir():
        return f"输出目录的上级 {p} 不是目录"
    probe = p / f".dpx_write_probe_{os.getpid()}"
    try:
        probe.write_text("probe", encoding="utf-8")
    except OSError as e:
        return f"输出目录不可写（探的是已存在的上级 {p}）：{e}"
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
    return None


def validate_experiment_plan(plan: ExperimentPlan) -> str | None:
    """验证实验计划。**只读不写**：不建目录、不留文件（探针即写即删）。

    Returns:
        错误信息，无错误则返回 None
    """
    # 范式必须是认识的那几个。引擎侧 `--assay` 是 `choices=sorted(ASSAY_WINDOWS)`，
    # 写进契约文件的未知范式最终会被 argparse 拦下——但那要等到队列把它交给 CLI，
    # 隔了两层，报错离出错的地方太远。窗口表就在手边，这里当场判。
    if plan.assay not in ASSAY_WINDOWS_UI:
        return f"未知范式 {plan.assay!r}：只认 {sorted(ASSAY_WINDOWS_UI)}"

    if plan.n_chambers < 1:
        return f"隔间数必须 ≥ 1，当前为 {plan.n_chambers}"

    if plan.calib_frames < 1:
        return f"标定帧数必须 ≥ 1，当前为 {plan.calib_frames}"

    if len(plan.videos) == 0:
        return "至少需要一个视频文件"

    # 视频路径查重要**按解析后的绝对路径**比：序列化时写的是 `resolve()` 的结果，
    # 拿没解析的 Path 比就会漏掉「同一个文件两种写法」（相对/绝对、符号链接），
    # 于是同一段录像在一次实验里被跑两遍、输出名还撞车。
    resolved = [v.path.resolve() for v in plan.videos]
    if len(resolved) != len(set(resolved)):
        dup = sorted({str(p) for p in resolved if resolved.count(p) > 1})
        return f"视频列表中存在重复路径（按绝对路径比）：{dup}"

    return probe_writable(plan.output_dir)


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

    target_path = Path(target_path).resolve()

    # 已存在时报错不覆盖（静默覆盖等于抹掉别人的实验计划）
    if target_path.exists():
        raise FileExistsError(f"目标文件已存在：{target_path}")

    data = serialize_experiment_plan(plan)

    # 目录在**这里**建（`validate` 只诊断不留痕，见 `probe_writable`）
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # 键顺序照契约写（diff 好读）。
    # 写一半失败必须把半截文件删掉：留着它的后果不是「文件坏了」而是
    # **重试被自己堵死**——上面那条「已存在就不覆盖」会把半截文件当成别人的实验计划，
    # 用户看到的是「文件已存在」，而真正的错误（磁盘满、盘被拔）已经看不见了。
    try:
        with target_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")  # 末尾加换行
    except BaseException:
        target_path.unlink(missing_ok=True)
        raise

    return target_path
