"""标定文件契约：产品自己判断有没有资格报秒数（DP-100，架构文件 §4）。

**这个模块决定产品挂黄徽章还是绿徽章，是资质边界，不外派。**

三条硬规则，每条都有一个测试直接踩它：

1. **`gates.G11.threshold` 为 `null` ⇒ 一律 research**，哪怕文件里写着
   `mode: "validated"` 且 G7 / G8 都过。理由是 DP-097 那个实测反例：CSI 在我们
   这 27 个试次上 r = 0.842（过 G7）、偏差 +0.15 s（过 G8），却把一个只有水没有鼠
   的杯位报成 466.88 s 不动（99.923%）。**总量门全过 = 「达到 CSI 水平」，而 CSI
   水平包含「空杯 = 最重抑郁」。** 没有逐秒时间对齐门就不许签发资质。
2. **哈希由代码里的常量给，不是由文件自己给。** 文件自证哈希等于没有校验。
   `EXPECTED_CALIBRATION_SHA256` 是发布时由打包链写进代码的（= 进签名产物），
   为 `None` 表示这一版**没有**随包标定文件；此时目录里凭空出现一个
   `calibration.json` 反而是可疑的 ⇒ 红。
3. **没有任何入参能把模式抬到 validated。** 文件里的 `mode` 只会被**降级**，
   永不被采信抬高。想解锁只有一条路：跑通验收门、签发新的标定文件、重新打包。

失败时的行为：**降级 + 说明原因，不是报错退出**（DP-052 / DP-054 的教训是
「静默兜底比报错危险」，但对客户来说「装了打不开」比「黄徽章能用」更糟；
所以这里既不静默也不退出——降级并把原因显示出来）。

本模块**只用标准库**（`json` / `hashlib` / `dataclasses` / `enum` / `pathlib`）。
**不许 import PySide6，也不许 import 本仓的分析包**（架构文件 §3.4 的进程边界）：
前者让这套判定能在没有显示器的环境里被测试，后者是外壳的硬边界。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# 发布时由打包链填入随包 calibration.json 的 sha256。
# None = 这一版不带标定文件（研究版）。**不许在运行期改它。**
EXPECTED_CALIBRATION_SHA256: str | None = None

SCHEMA_VERSION = "1"

# 验收门阈值（架构文件 §4 的「解锁条件」表）。**改这里等于改验收标准，不许顺手改。**
G7_MIN_R = 0.818
G8_MAX_ABS_BIAS_S = 17.7


class Mode(str, Enum):
    RESEARCH = "research"
    VALIDATED = "validated"


class Badge(str, Enum):
    YELLOW = "yellow"   # 研究用途 · 未计量标定
    GREEN = "green"     # 计量模式
    RED = "red"         # 标定文件不可信（仍然降级到 research 继续可用）


@dataclass(frozen=True)
class GateReading:
    """一个门的读数与它当时生效的阈值。`threshold=None` = 门槛未定。"""

    value: float | None
    threshold: float | None


@dataclass(frozen=True)
class Calibration:
    """随包标定文件的内容（只读）。"""

    declared_mode: Mode   # 文件**声称**的模式。只会被降级，永不被采信抬高
    theta_mob: float
    batch: str
    dataset_sha256: str
    signed_at: str
    tool_version: str
    g7: GateReading
    g8: GateReading
    g11: GateReading
    g10a_false_positives: int
    g10a_prime_false_positives: int
    mae_s: float
    rmse_s: float
    per_trial_error_range_s: tuple[float, float]


@dataclass(frozen=True)
class CalibrationStatus:
    """启动自检的结论。`mode` 是产品实际生效的模式，不是文件声称的。"""

    mode: Mode
    badge: Badge
    reasons: tuple[str, ...] = field(default_factory=tuple)
    calibration: Calibration | None = None

    @property
    def may_report_metrology(self) -> bool:
        """有没有资格把秒数当计量结果报。**界面与导出只许问这一个属性。**"""
        return self.mode is Mode.VALIDATED


def _research(badge: Badge, *reasons: str,
              calibration: Calibration | None = None) -> CalibrationStatus:
    return CalibrationStatus(mode=Mode.RESEARCH, badge=badge,
                             reasons=tuple(reasons), calibration=calibration)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gate(raw: object, name: str) -> GateReading:
    if not isinstance(raw, dict):
        raise ValueError(f"gates.{name} 不是对象")
    for key in ("value", "threshold"):
        if key not in raw:
            raise ValueError(f"gates.{name} 缺 {key}（缺字段与 null 不是一回事）")
    def num(v: object, key: str) -> float | None:
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"gates.{name}.{key} 不是数字：{v!r}")
        return float(v)
    return GateReading(value=num(raw["value"], "value"),
                       threshold=num(raw["threshold"], "threshold"))


_KEYS = frozenset({
    "schema_version", "mode", "theta_mob", "batch", "dataset_sha256", "signed_at",
    "tool_version", "gates", "g10a_false_positives", "g10a_prime_false_positives",
    "mae_s", "rmse_s", "per_trial_error_range_s",
})


def _parse(raw: object) -> Calibration:
    """严格解析。**缺字段一律报错，不许填默认值** —— 默认值会把「这版没量」
    伪装成「量了且合格」。**未知键同样拒收**：这是签名产物，多出来的键说明
    它不是我们这套发布链签出来的（本仓在 `human_agreement` 上已用同一口径）。"""
    if not isinstance(raw, dict):
        raise ValueError("顶层不是对象")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version 不是 {SCHEMA_VERSION!r}："
                         f"{raw.get('schema_version')!r}")
    unknown = set(raw) - _KEYS
    if unknown:
        raise ValueError(f"未知键 {sorted(unknown)}——签名产物不许有多余字段")
    gates = raw.get("gates")
    if not isinstance(gates, dict):
        raise ValueError("缺 gates")
    for key in ("theta_mob", "batch", "dataset_sha256", "signed_at", "tool_version",
                "mae_s", "rmse_s", "per_trial_error_range_s"):
        if key not in raw:
            raise ValueError(f"缺字段 {key}")
    rng = raw["per_trial_error_range_s"]
    if not (isinstance(rng, list) and len(rng) == 2):
        raise ValueError("per_trial_error_range_s 必须是 [最小, 最大] 两项")
    for key in ("g10a_false_positives", "g10a_prime_false_positives"):
        if not isinstance(raw.get(key), int) or isinstance(raw.get(key), bool):
            raise ValueError(f"{key} 必须是整数（假阳性个数）")
    declared = raw.get("mode")
    if declared not in (Mode.RESEARCH.value, Mode.VALIDATED.value):
        raise ValueError(f"mode 只许是 research / validated：{declared!r}")
    return Calibration(
        declared_mode=Mode(declared),
        theta_mob=float(raw["theta_mob"]),
        batch=str(raw["batch"]),
        dataset_sha256=str(raw["dataset_sha256"]),
        signed_at=str(raw["signed_at"]),
        tool_version=str(raw["tool_version"]),
        g7=_gate(gates.get("G7"), "G7"),
        g8=_gate(gates.get("G8"), "G8"),
        g11=_gate(gates.get("G11"), "G11"),
        g10a_false_positives=int(raw["g10a_false_positives"]),
        g10a_prime_false_positives=int(raw["g10a_prime_false_positives"]),
        mae_s=float(raw["mae_s"]),
        rmse_s=float(raw["rmse_s"]),
        per_trial_error_range_s=(float(rng[0]), float(rng[1])),
    )


def _gate_failures(c: Calibration) -> list[str]:
    """文件声称 validated 时，逐条核它自己的读数。**读数不支持声明就是不可信。**"""
    bad: list[str] = []
    if c.g7.threshold != G7_MIN_R:
        bad.append(f"G7 门槛被改成 {c.g7.threshold}（应为 {G7_MIN_R}）")
    if c.g8.threshold != G8_MAX_ABS_BIAS_S:
        bad.append(f"G8 门槛被改成 {c.g8.threshold}（应为 {G8_MAX_ABS_BIAS_S}）")
    if c.g7.value is None or c.g7.value < G7_MIN_R:
        bad.append(f"G7 读数 {c.g7.value} 未达 {G7_MIN_R}")
    if c.g8.value is None or abs(c.g8.value) > G8_MAX_ABS_BIAS_S:
        bad.append(f"G8 读数 {c.g8.value} 超出 ±{G8_MAX_ABS_BIAS_S} s")
    if c.g11.value is None or c.g11.threshold is None or c.g11.value < c.g11.threshold:
        bad.append(f"G11 读数 {c.g11.value} 未达门槛 {c.g11.threshold}")
    if c.g10a_false_positives or c.g10a_prime_false_positives:
        bad.append(f"G10a/G10a' 有假阳性（{c.g10a_false_positives}"
                   f"/{c.g10a_prime_false_positives}）——空隔间报出了 immobility，"
                   "这是硬安全门")
    return bad


def evaluate_calibration(path: Path | None,
                         *, expected_sha256: str | None = EXPECTED_CALIBRATION_SHA256,
                         ) -> CalibrationStatus:
    """读随包标定文件，判定产品实际生效的发布态。

    **没有任何入参能把结果抬到 validated**：`expected_sha256` 只用于校验，
    文件里的 `mode` 只会被降级。想解锁只有跑通验收门、签发新标定、重新打包。
    """
    if expected_sha256 is None:
        if path is not None and path.exists():
            # 这一版不该带标定文件，却出现了一个 ⇒ 有人往安装目录里放东西。
            return _research(Badge.RED,
                             f"这一版未随包标定文件，但发现了 {path.name}——来源不可信，已忽略")
        return _research(Badge.YELLOW, "研究版：未随包标定文件（预期如此）")

    if path is None or not path.exists():
        return _research(Badge.RED,
                         "这一版应带标定文件，但没找到——被删了或安装不完整")

    actual = file_sha256(path)
    if actual != expected_sha256:
        return _research(Badge.RED,
                         f"标定文件哈希不符（期望 {expected_sha256[:12]}…，"
                         f"实际 {actual[:12]}…）——文件被改过，已忽略其内容")

    try:
        c = _parse(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError, json.JSONDecodeError) as e:
        return _research(Badge.RED, f"标定文件读不通：{e}")

    # 文件自己就说是研究版 ⇒ 照它说的办。**只降级不升级**：这条在 G11 与门读数
    # 之前，因为一份声明 research 的标定文件即使读数全过，也不该被我们「提拔」。
    if c.declared_mode is not Mode.VALIDATED:
        return _research(Badge.YELLOW,
                         f"标定文件声明为研究版（批次 {c.batch}）", calibration=c)

    # 规则 1（DP-097）：G11 门槛未定 ⇒ 一律 research。放在核门读数之前，
    # 因为这不是「没达标」，是「这个门还没有标准」——**不报 ≠ 过**。
    if c.g11.threshold is None:
        return _research(
            Badge.YELLOW,
            "G11（逐秒 Jaccard）门槛尚未定出（T1 精标未完成）⇒ 强制研究模式。"
            "只有总量门（G7/G8）不足以签发资质：CSI 同时通过 G7+G8，却把空杯"
            "报成 466.88 s 不动",
            calibration=c)

    bad = _gate_failures(c)
    if bad:
        return _research(Badge.RED,
                         "标定文件声称已验收，但它自己的读数不支持："
                         + "；".join(bad),
                         calibration=c)

    return CalibrationStatus(mode=Mode.VALIDATED, badge=Badge.GREEN,
                             reasons=(f"计量模式：验收批次 {c.batch}",),
                             calibration=c)
