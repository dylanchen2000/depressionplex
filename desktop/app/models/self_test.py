"""采集自检结果模型（DP-109 B7）。

纯标准库读 JSON。键集缺一即抛（不许 .get(键, 默认值)）；
读不到的读数是 None（测不出时引擎写 null），**不是 0**——
未知不许长成数字的样子（本仓成文的规矩）。

页面代码里不许出现门槛字面量 100 / 2.0 / 0.02——GUI 只显示 JSON 说的，
门槛值全靠本模型从 JSON 里读出来传给页面。
"""

from __future__ import annotations

import json
from pathlib import Path


class AcqCheckError(ValueError):
    """acq_check JSON 键集不合契约——引擎产物，键缺失说明契约破裂。"""


class AcqCheckResult:
    """acq_check CLI JSON 输出的解析结果。

    所有属性用 [] 直接访问 JSON 键，缺键即 KeyError（→ AcqCheckError）。
    值为 None 是引擎明确写入的 null（读数未能测出），不是默认值。
    """

    def __init__(self, data: dict) -> None:
        _require_keys(data, (
            "generated_at", "video", "sampling", "gates", "chambers", "reference",
        ), "顶层")

        gates = data["gates"]
        _require_keys(gates, ("contrast", "noise_floor", "area_jitter"), "gates")

        contrast = gates["contrast"]
        _require_keys(contrast, (
            "value", "ratio", "threshold_abs", "threshold_ratio", "passed",
        ), "gates.contrast")

        noise_floor = gates["noise_floor"]
        _require_keys(noise_floor, ("value", "n_frames"), "gates.noise_floor")

        area_jitter = gates["area_jitter"]
        _require_keys(area_jitter, (
            "value", "threshold", "passed", "chamber", "chamber_residual", "moving",
        ), "gates.area_jitter")

        sampling = data["sampling"]
        _require_keys(sampling, (
            "n_windows", "frames_per_window", "frame_indices",
        ), "sampling")

        self._data = data

    # ── 对比度门 ──────────────────────────────────────────────────────────────

    @property
    def contrast_value(self) -> float | None:
        """绝对灰阶差（灰阶）；None = 无法测量（无面板带）。"""
        return self._data["gates"]["contrast"]["value"]

    @property
    def contrast_ratio(self) -> float | None:
        """背景/暗侧均值比值；None = 无法测量。"""
        return self._data["gates"]["contrast"]["ratio"]

    @property
    def contrast_threshold_abs(self) -> float:
        """绝对差门槛（来自引擎 JSON，不许在页面里写死）。"""
        return self._data["gates"]["contrast"]["threshold_abs"]

    @property
    def contrast_threshold_ratio(self) -> float:
        """比值门槛（来自引擎 JSON，不许在页面里写死）。"""
        return self._data["gates"]["contrast"]["threshold_ratio"]

    @property
    def contrast_passed(self) -> bool | None:
        """对比度是否通过；None = 无法测量。"""
        return self._data["gates"]["contrast"]["passed"]

    # ── 分割噪声底 ────────────────────────────────────────────────────────────

    @property
    def noise_floor_value(self) -> float | None:
        """面积抖动 |Δ| 均值（px）；None = 无法测量。诊断量，无 passed 字段。"""
        return self._data["gates"]["noise_floor"]["value"]

    @property
    def noise_floor_n_frames(self) -> int:
        """参与噪声底计算的帧数。"""
        return self._data["gates"]["noise_floor"]["n_frames"]

    # ── 面积抖动门 ────────────────────────────────────────────────────────────

    @property
    def area_jitter_value(self) -> float | None:
        """最静隔间面积抖动 p90 / BL²；None = 无法测量。"""
        return self._data["gates"]["area_jitter"]["value"]

    @property
    def area_jitter_threshold(self) -> float:
        """面积抖动门槛（来自引擎 JSON，不许在页面里写死）。"""
        return self._data["gates"]["area_jitter"]["threshold"]

    @property
    def area_jitter_passed(self) -> bool | None:
        """面积抖动是否通过；None = 无法测量。"""
        return self._data["gates"]["area_jitter"]["passed"]

    @property
    def area_jitter_chamber(self) -> int | None:
        """被选中作为判定依据的隔间索引（RAD 残差最低者）；None = 无法测量。"""
        return self._data["gates"]["area_jitter"]["chamber"]

    @property
    def area_jitter_chamber_residual(self) -> float | None:
        """被选中隔间的 RAD 残差；None = 无法测量。"""
        return self._data["gates"]["area_jitter"]["chamber_residual"]

    @property
    def area_jitter_moving(self) -> bool | None:
        """被选中隔间的动物是否在动（残差超过阈值）；None = 无法测量。"""
        return self._data["gates"]["area_jitter"]["moving"]

    # ── 诊断表 ────────────────────────────────────────────────────────────────

    @property
    def chambers(self) -> list[dict]:
        """每个隔间的诊断数据（index / area_jitter_p90 / rad_residual）。"""
        return self._data["chambers"]

    # ── 参考素材 ──────────────────────────────────────────────────────────────

    @property
    def reference(self) -> dict | None:
        """参考素材（2026-08-24 实测）数据；None = p2_reference.json 不存在。"""
        return self._data["reference"]

    # ── 抽帧信息 ──────────────────────────────────────────────────────────────

    @property
    def sampling_frame_indices(self) -> list[int]:
        """实际采样的帧号列表（复现读数的必要信息）。"""
        return self._data["sampling"]["frame_indices"]

    @property
    def sampling_n_windows(self) -> int:
        return self._data["sampling"]["n_windows"]

    @property
    def sampling_frames_per_window(self) -> int:
        return self._data["sampling"]["frames_per_window"]

    # ── 工厂方法 ──────────────────────────────────────────────────────────────

    @classmethod
    def from_json_str(cls, text: str) -> "AcqCheckResult":
        """从 JSON 字符串加载（用于测试和进程间读取）。"""
        return cls(json.loads(text))

    @classmethod
    def from_json_file(cls, path: Path) -> "AcqCheckResult":
        """从 JSON 文件加载。"""
        return cls.from_json_str(path.read_text(encoding="utf-8"))


def _require_keys(d: dict, keys: tuple[str, ...], location: str) -> None:
    """验证 d 包含所有必须的键；缺一即抛 AcqCheckError（而不是 KeyError）。

    AcqCheckError 说明这是"契约破裂"，而不是"代码写错了用了错误的键名"。
    """
    missing = [k for k in keys if k not in d]
    if missing:
        raise AcqCheckError(
            f"acq_check JSON 的 {location} 缺少键 {missing}。"
            "引擎产物，键缺失说明契约破裂（版本不匹配或 JSON 被改动）。"
        )
