"""PDF 渲染器（B6，DP-110）。

架构约束（派工单 §0.5）：
- 渲染前按 微软雅黑 → 宋体 → Noto Sans CJK SC 顺序找字体，
  用 QRawFont 实测一个汉字能不能渲。
- 三个都不行 ⇒ **拒绝导出 PDF**，不生成任何 .pdf 文件。
- 字体决策放在纯函数 select_cjk_font() 里（输入「哪些字体可用」，
  输出「用哪个或拒绝」），Qt 只负责问系统；这样没有 PySide6 的沙箱
  也能测这个纯函数（派工单 §3 第 11 条）。
- 渲染层**只摆不算**：不含 +/-/*/÷/round()。
- **不引入第三方 PDF 库**：使用 QPdfWriter + QTextDocument（均在 PySide6.QtGui 里，
  无 QtPrintSupport 插件依赖，也不牵 CUPS）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from desktop.app.models.report import Report

# ---------------------------------------------------------------------------
# 字体决策（纯函数，沙箱可测，不 import Qt）
# ---------------------------------------------------------------------------

CJK_FONT_CANDIDATES: tuple[str, ...] = (
    "Microsoft YaHei",  # 微软雅黑（startswith 匹配，含 "Microsoft YaHei UI" 等变体）
    "SimSun",           # 宋体
    "Noto Sans CJK SC", # Noto Sans CJK SC
)

_TEST_CHAR = "中"  # 用来测字体能不能渲中文


class FontUnavailableError(RuntimeError):
    """本机缺中文字体，拒绝生成 PDF。"""
    pass


def _check_font_renders(family: str) -> bool:
    """用 QRawFont 实测字体能否渲中文字符，是字体可渲性的唯一判据。

    只用 QRawFont.fromFont(QFont(family)).supportsCharacter("中")，
    不用 QFontDatabase() 实例也不调 addApplicationFont("")。

    Args:
        family: 字体 family 名

    Returns:
        True 表示该字体可渲中文

    Raises:
        FontUnavailableError: 当底层 Qt 调用抛出特定异常时，把原异常内容包进来抛出
    """
    from PySide6.QtGui import QRawFont, QFont
    try:
        raw = QRawFont.fromFont(QFont(family))
        return raw.supportsCharacter(_TEST_CHAR)
    except (TypeError, RuntimeError, AttributeError) as e:
        raise FontUnavailableError(
            f"检查字体 {family!r} 时内部出错: {e}"
        ) from e


def _get_renderable_families() -> list[str]:
    """枚举系统全部 family，过滤出通过 _check_font_renders 的那些。

    Returns:
        能渲中文的 family 名列表（空列表 = 本机没有中文字体）
    """
    from PySide6.QtGui import QFontDatabase
    renderable: list[str] = []
    for f in QFontDatabase.families():
        try:
            if _check_font_renders(f):
                renderable.append(f)
        except FontUnavailableError:
            pass  # 内部错误，跳过这个字体
    return renderable


def select_cjk_font(renderable_families: Sequence[str]) -> str:
    """从「已实测可渲中文」的 family 列表里选字体（纯函数）。

    按 CJK_FONT_CANDIDATES 优先级：找到第一个 renderable family 名以候选名为前缀
    （大小写不敏感），返回它；找不到则返回列表第一个。
    列表为空抛 FontUnavailableError。

    Args:
        renderable_families: 已验证能渲中文的 family 名列表

    Returns:
        选中的 family 名

    Raises:
        FontUnavailableError: renderable_families 为空时
    """
    if not renderable_families:
        raise FontUnavailableError(
            "本机缺中文字体（无任何已安装字体通过中文渲染测试）；"
            "xlsx 与审计包不受影响，可以先导那两个。"
        )
    for candidate in CJK_FONT_CANDIDATES:
        cand_lower = candidate.lower()
        for fam in renderable_families:
            if fam.lower().startswith(cand_lower):
                return fam
    return renderable_families[0]


def _select_font_with_qt() -> str:
    """枚举可渲中文的字体，再选优先级最高的。失败抛 FontUnavailableError（含枚举总数）。"""
    from PySide6.QtGui import QFontDatabase
    all_families = list(QFontDatabase.families())
    renderable = _get_renderable_families()
    if not renderable:
        raise FontUnavailableError(
            f"本机缺中文字体，已枚举 {len(all_families)} 个字体均不可渲中文；"
            "xlsx 与审计包不受影响，可以先导那两个。"
        )
    return select_cjk_font(renderable)


def _build_html(report: Report) -> str:
    """把 Report 转成 HTML 字符串（QPdfWriter 通过 QTextDocument 渲染）。

    渲染层只摆不算，所有数字来自 Report。
    """
    import html as html_lib

    def esc(s: str | None) -> str:
        if s is None or s == "":
            return "—"
        return html_lib.escape(str(s))

    lines: list[str] = []
    lines.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
    lines.append("<style>")
    lines.append("body { font-family: FONT_PLACEHOLDER, sans-serif; font-size: 10pt; }")
    lines.append("h1 { font-size: 14pt; }")
    lines.append("h2 { font-size: 12pt; }")
    lines.append("table { border-collapse: collapse; width: 100%; }")
    lines.append("th { background: #4F81BD; color: white; padding: 4px 6px; }")
    lines.append("td { border: 1px solid #ccc; padding: 3px 6px; }")
    lines.append("tr.alarm { background: #FFFF99; }")
    lines.append("tr.summary { background: #E0E0E0; font-style: italic; }")
    lines.append("pre { white-space: pre-wrap; font-size: 9pt; }")
    lines.append("</style></head><body>")

    # 声明（第一页）
    lines.append("<h1>研究用途声明</h1>")
    decl_html = html_lib.escape(report.declaration).replace("\n", "<br>")
    lines.append(f"<pre>{decl_html}</pre>")
    lines.append("<hr>")

    # 逐试次表
    lines.append("<h2>逐试次结果表</h2>")
    SECONDS_FIELDS = ["immobility_s", "immobility_raw_s", "mobility_s", "first_mobility_onset_s"]
    base_cols = [("trial_id", "Trial ID"), ("chamber", "隔间"), ("assay", "范式"),
                 ("validity_status", "有效性"), ("scored", "已计分")]
    sec_denom_cols: list[tuple[str, str]] = []
    seen: set[str] = set()
    for sf in SECONDS_FIELDS:
        if sf not in seen:
            seen.add(sf)
            label = sf.replace("_", " ")
            if sf == "first_mobility_onset_s":
                label += "（相对计分窗起点）"
            sec_denom_cols.append((sf, label))
        for df in DENOMINATORS.get(sf, ()):
            if df not in seen:
                seen.add(df)
                sec_denom_cols.append((df, df.replace("_", " ") + "（分母）"))
    other_cols = [("mobility_bouts", "Mobility Bouts"), ("occupied_fraction", "在场占比"),
                  ("reason", "报警原因")]
    all_cols = base_cols + sec_denom_cols + other_cols

    lines.append("<table><thead><tr>")
    for _, label in all_cols:
        lines.append(f"<th>{esc(label)}</th>")
    lines.append("</tr></thead><tbody>")

    for trial_row in report.trial_rows:
        cls = " class='alarm'" if trial_row.kind == "alarm" else ""
        lines.append(f"<tr{cls}>")
        for field, _ in all_cols:
            if field == "chamber":
                val = str(trial_row.chamber) if trial_row.chamber is not None else "—"
            else:
                val = getattr(trial_row, field, None)
                val = "—" if (val is None or val == "") else str(val)
            lines.append(f"<td>{esc(val)}</td>")
        lines.append("</tr>")

    # 合计行
    if report.trial_summary:
        s = report.trial_summary
        lines.append(f"<tr class='summary'><td colspan='{len(all_cols)}'>{esc(s.source_description)}</td></tr>")
        field_to_idx = {f: i for i, (f, _) in enumerate(all_cols)}
        cells = ["—"] * len(all_cols)
        cells[0] = "均值（scored 行）"
        for fname, mean_val in [
            ("immobility_s", s.immobility_s_mean),
            ("immobility_raw_s", s.immobility_raw_s_mean),
            ("mobility_s", s.mobility_s_mean),
        ]:
            if fname in field_to_idx and mean_val is not None:
                cells[field_to_idx[fname]] = mean_val
        lines.append("<tr class='summary'>")
        for c in cells:
            lines.append(f"<td>{esc(c)}</td>")
        lines.append("</tr>")

    lines.append("</tbody></table>")

    # 运行上下文表
    lines.append("<h2>运行上下文</h2>")
    lines.append("<table><thead><tr><th>项目</th><th>值</th></tr></thead><tbody>")
    for entry in report.context_rows:
        lines.append(f"<tr><td>{esc(entry.key)}</td><td>{esc(entry.value)}</td></tr>")
    lines.append("</tbody></table>")

    # 发布态与验证读数表
    lines.append("<h2>发布态与验证读数（我们验证批次的读数，不是您这批数据的误差）</h2>")
    lines.append("<table><thead><tr><th>项目</th><th>值</th><th>说明</th></tr></thead><tbody>")
    for entry in report.validation_rows:
        lines.append(f"<tr><td>{esc(entry.key)}</td><td>{esc(entry.value)}</td><td>{esc(entry.note)}</td></tr>")
    lines.append("</tbody></table>")

    lines.append("</body></html>")
    return "\n".join(lines)


def render_pdf(report: Report, out_path: Path) -> None:
    """把 Report 渲染到 PDF 文件（需要 PySide6）。

    §0.5：渲染前必须找到可渲中文的字体，否则拒绝生成。

    Args:
        report: 内容层 Report 对象
        out_path: 输出路径

    Raises:
        FontUnavailableError: 本机缺中文字体（不生成任何 .pdf 文件）
    """
    # 字体检查（拒绝生成，不降级）
    font_family = _select_font_with_qt()  # 缺字体时直接抛异常

    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QPageSize, QPdfWriter, QTextDocument

    html = _build_html(report)
    # 把 FONT_PLACEHOLDER 替换成实际字体
    html = html.replace("FONT_PLACEHOLDER", font_family)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # QPdfWriter 在 QtGui 里，无插件依赖，离屏渲染稳定（无 QtPrintSupport / CUPS）
    writer = QPdfWriter(str(out_path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))

    doc = QTextDocument()
    doc.setHtml(html)
    doc.print_(writer)
