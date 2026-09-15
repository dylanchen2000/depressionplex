"""采集自检结果模型（DP-109 B7）。

纯标准库读 JSON。键集缺一即抛（不许 .get(键, 默认值)）；
读不到的读数是 None（测不出时引擎写 null），**不是 0**——
未知不许长成数字的样子（本仓成文的规矩）。

页面代码里不许出现门槛字面量——GUI 只显示 JSON 说的，
门槛值全靠本模型从 JSON 里读出来传给页面。
"""

from __future__ import annotations

import json
from pathlib import Path


# ── 键集常量（守卫 13 双向对账用，**同时是 _require_keys 的唯一来源**）───────
# 只声明不使用 = 注释：第三轮复核实测，六个 _require_keys 调用点原本各写一份行内
# 字面量元组，于是「把行内元组少要一个键」（产品真行为变了）双向镜子全绿，
# 而「只改这里的常量」（产品行为没变）反而变红 —— 镜子照的是一份产品不看的名册。
# 现在 _require_keys 只许收这几个常量，AST 守卫盯着（见 test_acq_check.py 守卫 13b）。
_MODEL_JSON_KEYS = ("generated_at", "video", "sampling", "gates", "chambers", "reference")
_MODEL_GATES_KEYS = ("contrast", "noise_floor", "area_jitter")
_MODEL_CONTRAST_KEYS = ("value", "ratio", "threshold_abs", "threshold_ratio", "passed")
_MODEL_NOISE_FLOOR_KEYS = ("value", "n_frames")
_MODEL_AREA_JITTER_KEYS = ("value", "threshold", "passed", "chamber", "chamber_residual", "moving")
_MODEL_SAMPLING_KEYS = ("n_windows", "frames_per_window", "frame_indices")


class AcqCheckError(ValueError):
    """acq_check JSON 键集不合契约——引擎产物，键缺失说明契约破裂。"""


class AcqCheckResult:
    """acq_check CLI JSON 输出的解析结果。

    所有属性用 [] 直接访问 JSON 键，缺键即 KeyError（→ AcqCheckError）。
    值为 None 是引擎明确写入的 null（读数未能测出），不是默认值。
    """

    def __init__(self, data: dict) -> None:
        _require_keys(data, _MODEL_JSON_KEYS, "顶层")

        gates = data["gates"]
        _require_keys(gates, _MODEL_GATES_KEYS, "gates")

        contrast = gates["contrast"]
        _require_keys(contrast, _MODEL_CONTRAST_KEYS, "gates.contrast")

        noise_floor = gates["noise_floor"]
        _require_keys(noise_floor, _MODEL_NOISE_FLOOR_KEYS, "gates.noise_floor")

        area_jitter = gates["area_jitter"]
        _require_keys(area_jitter, _MODEL_AREA_JITTER_KEYS, "gates.area_jitter")

        sampling = data["sampling"]
        _require_keys(sampling, _MODEL_SAMPLING_KEYS, "sampling")

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

def conclusion_lines(result: "AcqCheckResult") -> list[str]:
    """从 AcqCheckResult 生成「人话结论」文本块（每块一段，永不返回空列表）。

    三态组合（contrast_passed × area_jitter_passed × area_jitter_moving）共 27 种，
    每种都能产出至少一条人话，调用方用 "\\n\\n".join(...) 组装。

    兜底「全部指标通过」只在三门都是明确 True 且动物不在动时出现。

    Returns:
        list[str]: 至少含一条说明，调用方直接 join 即可。
    """
    lines: list[str] = []
    r = result

    # ── 无面板带：完全测不出 ────────────────────────────────────────────────────
    if r.contrast_value is None:
        lines.append(
            "【无法测量】没找到背光面板行带，说明画面结构与我们假设的不同"
            "（亮背板横贯全宽、动物在其下方）。"
            "这不是「不达标」，是「量不了」——请把一帧截图发给我们。"
        )
        return lines  # 无面板时其余门都测不到，直接返回

    # ── 对比度门有值但判定未知（has value, no conclusion） ────────────────────
    if r.contrast_value is not None and r.contrast_passed is None:
        lines.append(
            "【对比度量不了结论】有对比度读数但无法得出通过/不通过判断。"
            "这是内部状态异常，请联系技术支持。"
        )

    # ── 对比度不达标 ─────────────────────────────────────────────────────────────
    if r.contrast_passed is False:
        lines.append(
            "【对比度不达标】背光不足或曝光不当。"
            "我们的分割靠亮背板 + 黑剪影，"
            f"绝对差 < {r.contrast_threshold_abs:.0f} 灰阶时阈值分割会不稳。"
            "建议加背光板或调曝光后重录一段再自检。"
        )

    # ── 动物持续运动 ─────────────────────────────────────────────────────────────
    if r.area_jitter_moving is True:
        lines.append(
            "【动物持续运动】本次抽到的帧里动物都在动，抖动读数含真实形变，"
            "不能当噪声底看。换一段有静止时段的素材再自检。"
        )

    # ── 面积抖动不达标（且不是因为动物在动导致的）──────────────────────────────
    if r.area_jitter_passed is False and r.area_jitter_moving is not True:
        lines.append(
            "【面积抖动超标】分割噪声底偏高，"
            f"实测 {r.area_jitter_value:.4f}，门槛 ≤{r.area_jitter_threshold:.4f}，"
            "可能影响 immobility 判定精度。建议改善采集条件后重试。"
        )

    # ── 面积抖动门无法测量（找不到隔间）─────────────────────────────────────
    if r.area_jitter_passed is None and r.area_jitter_moving is None:
        lines.append(
            "【面积抖动量不了】找不到任何动物隔间，面积抖动读数无法获取。"
            "请确认视频中有清晰的隔间边界。"
        )
    elif r.area_jitter_passed is None and r.area_jitter_moving is not None:
        lines.append(
            "【面积抖动量不了】面积抖动门无法得出通过/不通过判断。"
            "这是内部状态异常，请联系技术支持。"
        )

    # ── 全部指标明确通过（无任何报警）────────────────────────────────────────
    all_known_pass = (
        r.contrast_passed is True
        and r.area_jitter_passed is True
        and r.area_jitter_moving is not True
    )
    if all_known_pass:
        lines.append("全部指标通过，可以开始正式分析。")

    # ── 安全网：不应触达（所有路径都已覆盖）──────────────────────────────────
    if not lines:
        lines.append(
            "【状态未知】当前指标组合未能产出结论，请联系技术支持（内部错误）。"
        )

    return lines


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
