"""导出入口：EXPORT_SUFFIXES 单一来源 + 三个导出函数（B6，DP-110）。

架构约束（派工单 §0.3）：
- 导出文件名后缀（含 "_research" / ".xlsx" / ".pdf" / "_审计包"）**只许出现在本文件**。
- "_research" 必须由 calibration.Mode 的取值拼出，不许写字面量。
- AST 守卫会扫 services/ 下其他文件，若找到这些字面量即红。
"""

from __future__ import annotations

from pathlib import Path

from desktop.app.models.report import Report, build_report
from desktop.app.models.results import load_results
from desktop.app.services.calibration import (
    G7_MIN_R,
    G8_MAX_ABS_BIAS_S,
    Mode,
    evaluate_calibration,
)
from desktop.app.services.engine import output_paths as engine_output_paths

# ---------------------------------------------------------------------------
# 导出文件名模板 — 唯一来源
# "_research" 来自 Mode.RESEARCH.value，不许写字面量
# ---------------------------------------------------------------------------
_MODE_RESEARCH = Mode.RESEARCH.value  # == "research"

EXPORT_SUFFIXES: dict[str, str] = {
    "xlsx":      "{stem}_" + _MODE_RESEARCH + ".xlsx",
    "pdf":       "{stem}_" + _MODE_RESEARCH + ".pdf",
    "audit_zip": "{stem}_" + _MODE_RESEARCH + "_审计包.zip",
}


def export_paths(exp: dict, video_index: int, mode: Mode) -> dict[str, Path]:
    """给定 experiment + 视频索引 + 发布态，返回三个导出文件的绝对路径。

    注意：文件名里的 _research / _validated 后缀由 mode.value 拼出，
    全仓只此一处，守卫会扫别处的字面量。
    """
    paths = engine_output_paths(exp, video_index)
    stem = paths["csv"].stem  # 与引擎 OUTPUT_SUFFIXES 的 stem 保持一致
    out_dir = paths["csv"].parent
    mode_str = mode.value  # "research" 或 "validated"

    return {
        "xlsx":      out_dir / f"{stem}_{mode_str}.xlsx",
        "pdf":       out_dir / f"{stem}_{mode_str}.pdf",
        "audit_zip": out_dir / f"{stem}_{mode_str}_审计包.zip",
    }


def _prepare_report(exp: dict, video_index: int, calib_path: Path | None) -> Report:
    """内部辅助：加载结果 + 标定状态 + 构造 Report 对象。"""
    results = load_results(exp, video_index)
    calib_status = evaluate_calibration(calib_path)
    calib = calib_status.calibration
    batch = calib.batch if calib else None

    e_paths = engine_output_paths(exp, video_index)

    report = build_report(
        results=results,
        calib_mode=calib_status.mode.value,
        calib_badge=calib_status.badge.value,
        calib_batch=batch,
        g7_threshold=G7_MIN_R,
        g8_threshold_s=G8_MAX_ABS_BIAS_S,
        engine_output_paths_dict=e_paths,
    )
    return report


def export_xlsx(
    exp: dict,
    video_index: int,
    calib_path: Path | None,
    out_path: Path,
) -> None:
    """导出 xlsx 报告。

    Args:
        exp: experiment.json 内容
        video_index: 视频段索引
        calib_path: calibration.json 路径（None = 研究版）
        out_path: 输出文件路径（应当由 export_paths() 给出）
    """
    from desktop.app.services.export_xlsx import render_xlsx
    report = _prepare_report(exp, video_index, calib_path)
    render_xlsx(report, out_path)


def export_pdf(
    exp: dict,
    video_index: int,
    calib_path: Path | None,
    out_path: Path,
) -> None:
    """导出 PDF 报告（需要 PySide6，沙箱里 CI 验证）。

    Raises:
        RuntimeError: 本机缺中文字体时拒绝生成（§0.5），弹提示后不生成 .pdf。
    """
    from desktop.app.services.export_pdf import render_pdf
    report = _prepare_report(exp, video_index, calib_path)
    render_pdf(report, out_path)


def export_audit(
    exp: dict,
    video_index: int,
    calib_path: Path | None,
    out_path: Path,
) -> None:
    """导出审计包（zip，标准库实现，沙箱可验）。"""
    from desktop.app.services.export_audit import render_audit_zip
    report = _prepare_report(exp, video_index, calib_path)
    render_audit_zip(report, out_path)
