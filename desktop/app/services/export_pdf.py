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
    "Microsoft YaHei",  # 微软雅黑
    "SimSun",           # 宋体
    "Noto Sans CJK SC", # Noto Sans CJK SC
)

_TEST_CHAR = "中"  # 用来测字体能不能渲中文


class FontUnavailableError(RuntimeError):
    """本机缺中文字体，拒绝生成 PDF。"""
    pass


def select_cjk_font(available_families: Sequence[str]) -> str:
    """从候选字体列表里选第一个可用的，全不可用则抛 FontUnavailableError。

    这是纯函数，输入「哪些字体可用」，输出「用哪个」。
    Qt 只负责问系统可用字体列表，然后调这个函数。
    沙箱测试可以直接用字符串列表模拟输入。

    Args:
        available_families: 系统已安装的字体 family 名列表（大小写不敏感匹配）

    Returns:
        选中的字体 family 名

    Raises:
        FontUnavailableError: 三个候选字体都不在 available_families 里
    """
    available_lower = {f.lower() for f in available_families}
    for candidate in CJK_FONT_CANDIDATES:
        if candidate.lower() in available_lower:
            return candidate
    raise FontUnavailableError(
        f"本机缺中文字体，已找过这三个：{', '.join(CJK_FONT_CANDIDATES)}；"
        "xlsx 与审计包不受影响，可以先导那两个。"
    )


def _check_font_renders(family: str) -> bool:
    """用 QRawFont 实测字体能否渲染测试汉字。返回 True 表示可以渲染。

    需要 PySide6，只在真机/CI 里调用。
    """
    try:
        from PySide6.QtGui import QRawFont, QFontDatabase
        from PySide6.QtCore import QByteArray

        db = QFontDatabase()
        # 从系统字体数据库载入
        font_id = db.addApplicationFont("")  # 触发初始化
        raw = QRawFont.fromFont(
            __import__("PySide6.QtGui", fromlist=["QFont"]).QFont(family)
        )
        return raw.isValid() and raw.supportsCharacter(_TEST_CHAR)
    except Exception:
        return False


def _get_qt_font_families() -> list[str]:
    """从 Qt 查询系统字体列表，并实测中文可渲。"""
    from PySide6.QtGui import QFontDatabase
    return list(QFontDatabase.families())


def _select_font_with_qt() -> str:
    """Qt 问字体 + 纯函数选字体。失败抛 FontUnavailableError。"""
    families = _get_qt_font_families()
    # 先用纯函数选候选字体（看字体是否在列表里）
    chosen = select_cjk_font(families)
    # 再实测这个字体能否渲中文
    if not _check_font_renders(chosen):
        # 第一个候选不行，继续往下找
        remaining = [f for f in CJK_FONT_CANDIDATES if f != chosen]
        for candidate in remaining:
            if candidate.lower() in {f.lower() for f in families}:
                if _check_font_renders(candidate):
                    return candidate
        raise FontUnavailableError(
            f"本机缺中文字体，已找过这三个：{', '.join(CJK_FONT_CANDIDATES)}；"
            "xlsx 与审计包不受影响，可以先导那两个。"
        )
    return chosen


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
