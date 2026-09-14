"""报告内容层（纯标准库，不 import PySide6，不 import 引擎包）。

架构约束（B6 派工单 §0.1 / SPEC §3.4）：
- 本模块**只许用标准库**，无 openpyxl、无 PySide6。
- **一个 +、一个 /、一个 round() 都不许出现在渲染层**；合计/均值只许在这里算。
- 数字唯一来源：B4 的 load_results()；声明文案唯一来源：DECLARATION_TEMPLATE。
- DENOMINATORS 直接 import 自 B4 的 models/results.py，不许再抄一份。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 直接 import B4 的 DENOMINATORS，不许再抄一份（派工单 §3 第 6 条守卫）
from desktop.app.models.results import DENOMINATORS, ResultsTable, ResultsRow

# 找到 data/validation_readings.json 的路径（相对仓根）
# 本文件在 desktop/app/models/report.py，parents[3] 是仓根
_REPO_ROOT = Path(__file__).resolve().parents[3]
VALIDATION_READINGS_PATH = _REPO_ROOT / "data" / "validation_readings.json"

# ---------------------------------------------------------------------------
# 声明文案（模板，{占位符} 由 data/validation_readings.json 填）。
# 守卫要断言：模板文本里不出现那些读数的数字——改了 JSON 声明照旧印旧数是最难发现的错法。
# 三处（xlsx / PDF / 审计包）都从这个常量取同一份字符串。
# ---------------------------------------------------------------------------

# 发布态标签映射（唯一来源）。计量版文案未定，M3 前禁止生成。
_MODE_LABELS: dict[str, str] = {
    "research":  "研究版",
    "validated": "计量版",
}

DECLARATION_TEMPLATE = """\
### 研究用途声明

本报告由 DEPRESSION-PLEX **{version_label}**生成。本软件当前**没有计量资质**，
报告中的秒数**不得**作为计量结果、申报材料或合规证据使用。

**为什么是研究版**：签发计量资质的前置条件之一是「逐秒时间对齐门」（G11）
必须有一个实数门槛，而它现在**未定**（待人工精标档 T1 定标）。
只有总量门（相关系数 G7 门槛 {g7_threshold}、总体偏差 G8 门槛 {g8_threshold_s} 秒）
全过**不足以**签发资质 —— 我们实测过一个反例：同类商业软件在
{csi_n} 个**强迫游泳**试次上相关系数 {csi_r}、总体偏差 {csi_bias_s} 秒
（两个总量门都过），平均绝对误差却有 {csi_mae_s} 秒，
并且把一个**只有水、没有动物**的杯位报成 {csi_empty_cup_s} 秒不动
（占全窗 {csi_empty_cup_pct}%）。**总量对、时间错的软件能过总量门，
过不了任何科学审视。**

**我们自己量到的一致性（这是我们验证批次的读数，不是您这批数据的误差）**：
在 {own_n_trials} 个**悬尾**试次（可配对 {own_n_paired} 个）、
人工评分为常规档（T2）与对标档（T0）混合的条件下 ——
软件与人工的**总体偏差 {own_bias_s} 秒**（几乎不偏，回归斜率 {own_slope}），
但**逐试次绝对偏差平均 {own_mae_s} 秒**（范围 {own_err_min_s} … {own_err_max_s} 秒），
逐试次达标（≤{g8_threshold_s} 秒）**{own_pass_n} / {own_n_paired}**。
同一批数据上，**两位人工评分员之间**的差异极差中位 {rater_median_s} 秒、
最大 {rater_max_s} 秒 —— 尺子本身也有这么宽。

**怎么用这份数字才是安全的**：
1. **组间趋势、批内比较**可以用；**单个试次的秒数不要单独作为结论依据**。
2. 发表或对外报告时，请**同时报出**本声明中的偏差数字，以及您自己的人工标定结果。
3. 报告中每个秒数都带着它的**分母**（窗口帧数、可评分帧数、帧率）与
   `validity_status`。**分母不看，秒数没有意义。**
4. **没有产出数字的隔间会在表里占一行并写明原因。**
   那一行不是「零不动」，是「这一只没有测到」—— 两者在数据上完全不同。
5. 判定阈值 θ_mob = {theta_mob} 是**冻结值**，标定来源见「运行上下文」表。
   它当前的标定锚在混合档人工标签上，**待 T1 精标档重新定标**。

**本软件不做的事**：不做跨录像/跨批次统计，不做组间显著性检验，
不替您判断某只动物该不该排除。这些都需要先定清纳入排除规则，属于分析而非测量。\
"""


def _load_validation_readings(readings_path: "Path | None" = None) -> dict[str, Any]:
    """从 data/validation_readings.json 加载验证读数。

    Args:
        readings_path: 显式指定 JSON 路径（None = 用 VALIDATION_READINGS_PATH）。
                       测试可以传 tmpdir 下的副本，避免修改仓库文件。
    """
    path = readings_path if readings_path is not None else VALIDATION_READINGS_PATH
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    # 过滤掉 __comment 和 __sources 这两个注释键
    return {k: v for k, v in raw.items() if not k.startswith("__")}


def render_declaration(
    g7_threshold: Any,
    g8_threshold_s: Any,
    theta_mob: Any,
    mode: str = "research",
    readings_path: "Path | None" = None,
) -> str:
    """用运行时读数填充声明模板，返回最终声明文本。

    g7_threshold / g8_threshold_s：来自 calibration.py 的门槛值
    theta_mob：来自本次 run.json 的 rules.theta_mob
    mode：发布态（"research" 或 "validated"）；validated 时抛 NotImplementedError
    readings_path：显式指定验证读数 JSON 路径（None = VALIDATION_READINGS_PATH）

    G11 门槛 None ⇒ 印「未定」。
    """
    if mode == "validated":
        raise NotImplementedError(
            "计量版声明文案未定，M3 前不许生成计量版报告"
        )
    readings = _load_validation_readings(readings_path=readings_path)
    ctx = dict(readings)
    ctx["g7_threshold"] = g7_threshold
    ctx["g8_threshold_s"] = g8_threshold_s
    ctx["theta_mob"] = theta_mob if theta_mob is not None else "未知"
    ctx["version_label"] = _MODE_LABELS.get(mode, mode)
    return DECLARATION_TEMPLATE.format(**ctx)


# ---------------------------------------------------------------------------
# 报告数据模型（内容层）
# ---------------------------------------------------------------------------

@dataclass
class TrialTableRow:
    """逐试次表的一行，含秒数字段与其分母字段。"""
    trial_id: str
    chamber: int | None
    kind: str  # "scored" or "alarm"
    reason: str | None

    # 所有 16 个 CSV 字段（字符串原值）
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


@dataclass
class TrialTableSummary:
    """逐试次表的末行合计/均值（仅 scored 行参与，注明了 N 行）。"""
    n_scored: int  # 参与计算的行数（N）
    immobility_s_mean: str | None  # 均值（字符串表示，内容层算，渲染层不算）
    immobility_raw_s_mean: str | None
    mobility_s_mean: str | None
    source_description: str  # 例如「由本页 5 行 scored 行算得」


@dataclass
class ContextTableEntry:
    """运行上下文表的一行（键-值对）。"""
    key: str
    value: str


@dataclass
class ValidationTableEntry:
    """发布态与验证读数表的一行。"""
    key: str
    value: str
    note: str = ""


@dataclass
class Report:
    """报告内容层的顶层容器（纯 Python，无渲染依赖）。

    三个渲染器（xlsx / PDF / 审计包）从这个对象取内容，**只摆不算**。
    """
    # 声明文本（已填好占位符，三处渲染用的同一份字符串）
    declaration: str

    # 逐试次表
    trial_rows: list[TrialTableRow]
    trial_summary: TrialTableSummary | None  # 有 scored 行才有合计

    # 运行上下文表
    context_rows: list[ContextTableEntry]

    # 发布态与验证读数表
    validation_rows: list[ValidationTableEntry]

    # 报告元数据
    tool_version: str | None
    export_mode: str  # calibration.Mode.value，例如 "research"
    badge: str  # calibration.Badge.value，例如 "yellow"
    video_name: str | None
    assay: str | None

    # B7 自检（若未跑则为 None）
    self_test_summary: str | None = None

    # 四个引擎产出文件的路径（export_audit.py 用）
    engine_output_paths: dict[str, Path] = field(default_factory=dict)

    # 采集自检 JSON 路径（若有）
    self_test_json_path: Path | None = None


def _fmt(val: str | None) -> str:
    """空值格式化为「—」，永远不许印 0。"""
    if val is None or val == "":
        return "—"
    return val


def _mean_of_seconds(values: list[str]) -> str | None:
    """对一组秒数字符串算均值，返回保留两位小数的字符串。
    只在内容层调用，渲染层不许再算。
    """
    floats = []
    for v in values:
        if v is None or v == "":
            continue
        try:
            floats.append(float(v))
        except ValueError:
            pass
    if not floats:
        return None
    return f"{sum(floats) / len(floats):.2f}"


def _rows_from_results(results: ResultsTable) -> list[TrialTableRow]:
    """把 ResultsTable.rows 转成 TrialTableRow 列表（一对一，不丢行）。"""
    out: list[TrialTableRow] = []
    for r in results.rows:
        out.append(TrialTableRow(
            trial_id=r.trial_id,
            chamber=r.chamber,
            kind=r.kind,
            reason=r.reason,
            assay=r.assay,
            fps=r.fps,
            recording_frames=r.recording_frames,
            window_frames=r.window_frames,
            scorable_frames=r.scorable_frames,
            unknown_frames_window=r.unknown_frames_window,
            validity_status=r.validity_status,
            occupied_fraction=r.occupied_fraction,
            scored=r.scored,
            immobility_s=r.immobility_s,
            immobility_raw_s=r.immobility_raw_s,
            mobility_s=r.mobility_s,
            mobility_bouts=r.mobility_bouts,
            first_mobility_onset_s=r.first_mobility_onset_s,
            gate_messages=r.gate_messages,
        ))
    return out


def _build_summary(trial_rows: list[TrialTableRow]) -> TrialTableSummary | None:
    """只对 scored 行算合计，注明 N 行。没有 scored 行返回 None。"""
    scored = [r for r in trial_rows if r.kind == "scored"]
    if not scored:
        return None
    n = len(scored)

    def mean_field(field_name: str) -> str | None:
        return _mean_of_seconds([getattr(r, field_name) for r in scored])

    return TrialTableSummary(
        n_scored=n,
        immobility_s_mean=mean_field("immobility_s"),
        immobility_raw_s_mean=mean_field("immobility_raw_s"),
        mobility_s_mean=mean_field("mobility_s"),
        source_description=f"由本页 {n} 行 scored 行算得",
    )


def _build_context_rows(
    results: ResultsTable,
    calib_mode: str,
    calib_badge: str,
    calib_batch: str | None,
    g7_threshold: Any,
    g8_threshold_s: Any,
    decoder_info: str | None,
) -> list[ContextTableEntry]:
    """构造运行上下文表（来自 run.json + calibration，不重算任何科学量）。"""
    rows: list[ContextTableEntry] = []

    def add(key: str, val: Any) -> None:
        rows.append(ContextTableEntry(key=key, value=str(val) if val is not None else "未知"))

    add("tool_version", results.tool_version)
    add("assay（范式）", results.assay)

    if results.scoring_window_s is not None:
        add("计分窗口（秒）", f"{results.scoring_window_s[0]} – {results.scoring_window_s[1]}")
    else:
        add("计分窗口（秒）", None)

    theta = results.theta_mob
    if theta is not None:
        add("θ_mob（冻结值）", theta)
    else:
        add("θ_mob（冻结值）", None)

    theta_source = "run.json rules.theta_mob" if theta is not None else "未知"
    add("θ_mob 标定来源", theta_source)

    add("视频文件名", results.video_name)

    fps_str = f"{results.video_fps}" if results.video_fps is not None else "未知"
    add("帧率（fps）", fps_str)

    if results.video_n_frames is not None:
        add("总帧数", results.video_n_frames)
    else:
        add("总帧数", None)

    add("帧数来源（frame_count_source）", results.frame_count_source)

    # plan_warnings 在 ResultsTable 不直接存储，但架构规定要印
    # 这里通过对象字段获取（B4 的 ResultsTable 暂不含此字段，留 TODO）
    plan_warnings = getattr(results, "plan_warnings", None)
    if plan_warnings:
        add("plan_warnings", "; ".join(plan_warnings))
    else:
        add("plan_warnings", "（无）")

    # 解码器身份（B10 会加 decoder 块，现在可能未知）
    add("解码器身份", decoder_info if decoder_info else "未知")

    add("发布态", calib_mode)
    add("徽章", calib_badge)
    if calib_batch:
        add("标定批次", calib_batch)

    add("G7 门槛", g7_threshold)
    add("G8 门槛（秒）", g8_threshold_s)
    add("G11 门槛", "未定")

    return rows


def _build_validation_rows(
    g7_threshold: Any,
    g8_threshold_s: Any,
    readings: dict[str, Any],
) -> list[ValidationTableEntry]:
    """构造发布态与验证读数表。"""
    rows: list[ValidationTableEntry] = []

    def add(key: str, val: Any, note: str = "") -> None:
        rows.append(ValidationTableEntry(key=key, value=str(val), note=note))

    add("G7 门槛（Pearson r）", g7_threshold, "来自 calibration.py")
    add("G8 门槛（秒）", g8_threshold_s, "来自 calibration.py")
    add("G11 门槛", "未定", "待 T1 精标档定标")
    add("CSI 试次数（强迫游泳）", readings["csi_n"],
        "这是 CSI 的强迫游泳数据，不是本批数据")
    add("CSI Pearson r", readings["csi_r"])
    add("CSI 总体偏差（秒）", readings["csi_bias_s"])
    add("CSI 平均绝对误差（秒）", readings["csi_mae_s"])
    add("CSI 空杯不动时长（秒）", readings["csi_empty_cup_s"],
        "只有水没有动物，报成此值")
    add("CSI 空杯占全窗（%）", readings["csi_empty_cup_pct"])
    add("我们的试次数（悬尾）", readings["own_n_trials"],
        "这是我们自己的验证批次，不是客户数据")
    add("我们的可配对数", readings["own_n_paired"])
    add("我们的总体偏差（秒）", readings["own_bias_s"])
    add("我们的回归斜率", readings["own_slope"])
    add("我们的平均绝对误差（秒）", readings["own_mae_s"])
    add("我们的逐试次误差范围（秒）",
        f"{readings['own_err_min_s']} … {readings['own_err_max_s']}")
    add(f"逐试次达标（≤{g8_threshold_s}秒）",
        f"{readings['own_pass_n']} / {readings['own_n_paired']}")
    add("评分员间差异中位极差（秒）", readings["rater_median_s"])
    add("评分员间差异最大极差（秒）", readings["rater_max_s"])

    return rows


def build_report(
    results: ResultsTable,
    calib_mode: str,
    calib_badge: str,
    calib_batch: str | None,
    g7_threshold: Any,
    g8_threshold_s: Any,
    engine_output_paths_dict: dict[str, Path] | None = None,
    self_test_json_path: Path | None = None,
    self_test_summary: str | None = None,
) -> Report:
    """构造报告内容层（纯 Python，无渲染依赖）。

    Args:
        results: B4 的 load_results() 返回的 ResultsTable
        calib_mode: calibration.Mode.value（"research" 或 "validated"）
        calib_badge: calibration.Badge.value（"yellow" / "green" / "red"）
        calib_batch: 标定批次号（研究版为 None）
        g7_threshold: 来自 calibration.G7_MIN_R
        g8_threshold_s: 来自 calibration.G8_MAX_ABS_BIAS_S
        engine_output_paths_dict: services/engine.output_paths() 的结果
        self_test_json_path: B7 自检 JSON 路径（若有）
        self_test_summary: B7 自检摘要（若有）

    Returns:
        Report
    """
    readings = _load_validation_readings()

    # 从 run.json 取 theta_mob（不许写死）
    theta_mob = results.theta_mob

    # 从 run.json 取 decoder 块（B10 还没加，容忍缺失）
    decoder_info: str | None = getattr(results, "decoder_info", None)

    # 声明文本（模板填充）
    declaration = render_declaration(
        g7_threshold=g7_threshold,
        g8_threshold_s=g8_threshold_s,
        theta_mob=theta_mob,
        mode=calib_mode,
    )

    # 逐试次表
    trial_rows = _rows_from_results(results)
    trial_summary = _build_summary(trial_rows)

    # 运行上下文表
    context_rows = _build_context_rows(
        results=results,
        calib_mode=calib_mode,
        calib_badge=calib_badge,
        calib_batch=calib_batch,
        g7_threshold=g7_threshold,
        g8_threshold_s=g8_threshold_s,
        decoder_info=decoder_info,
    )

    # 发布态与验证读数表
    validation_rows = _build_validation_rows(
        g7_threshold=g7_threshold,
        g8_threshold_s=g8_threshold_s,
        readings=readings,
    )

    return Report(
        declaration=declaration,
        trial_rows=trial_rows,
        trial_summary=trial_summary,
        context_rows=context_rows,
        validation_rows=validation_rows,
        tool_version=results.tool_version,
        export_mode=calib_mode,
        badge=calib_badge,
        video_name=results.video_name,
        assay=results.assay,
        self_test_summary=self_test_summary,
        engine_output_paths=engine_output_paths_dict or {},
        self_test_json_path=self_test_json_path,
    )
