"""PDF 渲染器（B6，DP-110）。

架构约束（派工单 §0.5）：
- 渲染前必须找到能渲一个汉字的字体，顺序是 **随包字体 → 系统字体 → 报错**。
  随包在前：它是唯一我们能保证在客户机器上存在、且不依赖平台插件的那一份。
  系统字体按 微软雅黑 → 宋体 → Noto Sans CJK SC 优先，用 QRawFont 实测能不能渲。
- 都不行 ⇒ **拒绝导出 PDF**，不生成任何 .pdf 文件；报错文案必须说清是三种原因里的
  哪一种（随包漏了 / 平台插件不给字体库 / 这台机器真没中文字体），见
  _font_failure_message()。把三件事说成一句话是最难查的一类失败。
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

from desktop.app.models.report import Report, DENOMINATORS
from desktop.app.utils.paths import BUNDLED_FONT_FILENAME, bundled_font_dir

# ---------------------------------------------------------------------------
# 字体决策（纯函数，沙箱可测，不 import Qt）
# ---------------------------------------------------------------------------

CJK_FONT_CANDIDATES: tuple[str, ...] = (
    "Microsoft YaHei",  # 微软雅黑（startswith 匹配，含 "Microsoft YaHei UI" 等变体）
    "SimSun",           # 宋体
    "Noto Sans CJK SC", # Noto Sans CJK SC
)

_TEST_CHAR = "中"  # 用来测字体能不能渲中文

# 随包中文字体（DP-110）。文件与许可由 packaging/fetch_font.py 拉到 vendor/fonts/，
# 由 packaging/build_windows.spec 的 datas 打进 <bundle>/fonts/。
# 二进制不进 git，所以源码运行前要先跑一次 fetch_font.py。
# 文件名与目录名在 utils/paths.py（那边是 PyInstaller 内部 API 的唯一入口点），
# 这里只留 family 名——它是**字体文件里的元数据**，不是路径。
# 注册后 Qt 报出来的 family 名是 "Noto Sans SC"（fontTools 实测），
# **不是** CJK_FONT_CANDIDATES 里那个 "Noto Sans CJK SC"（那是 Linux 上系统包的名字）。
BUNDLED_FONT_FAMILY = "Noto Sans SC"


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


def _renders_ok(family: str) -> bool:
    """_check_font_renders 的吞异常版：单个字体检查内部出错 ⇒ 当它不可渲，跳过。

    只用在「逐个筛一批字体」的场合。一个字体探测失败不该让整次导出失败——
    最终「一个都没有」才是失败，那一步由调用方判定并给出归因文案。
    """
    try:
        return _check_font_renders(family)
    except FontUnavailableError:
        return False


def _get_renderable_families() -> list[str]:
    """枚举系统全部 family，过滤出通过 _check_font_renders 的那些。

    Returns:
        能渲中文的 family 名列表。空列表有两种可能——本机真没有中文字体，
        或者平台插件根本不提供字体库（offscreen 在 Windows 上实测枚举到 0 个）。
        **这两件事必须由调用方分开报**，见 _font_failure_message()。
    """
    from PySide6.QtGui import QFontDatabase
    return [f for f in QFontDatabase.families() if _renders_ok(f)]


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


def _register_bundled_font(font_path: Path) -> list[str]:
    """把随包字体注册进 Qt，返回其中实测能渲中文的 family 名（失败返回空列表）。

    走 addApplicationFont 而不是靠系统字体，是因为实测（run 34932358712）
    Qt 6.7.3 的 offscreen 平台插件在 windows-latest 上 QFontDatabase.families()
    返回 **0** 个——而那台机器上 msyh.ttc 明明在位、原生 windows 插件能枚举到 154 个。
    同样条件下 addApplicationFont 返回 id 0、注册出 2 个 family、两个都能渲「中」。
    所以这是唯一不依赖平台插件字体库的机制。

    返回顺序里把 BUNDLED_FONT_FAMILY 排在最前：同一份字体文件下，
    PDF 里写的 font-family 必须是确定的，不许随枚举顺序变。
    """
    from PySide6.QtGui import QFontDatabase
    if not font_path.is_file():
        return []
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    if font_id < 0:
        return []
    families = [f for f in QFontDatabase.applicationFontFamilies(font_id)
                if _renders_ok(f)]
    families.sort(key=lambda f: (f != BUNDLED_FONT_FAMILY, f))
    return families


def _font_failure_message(font_path: Path, enumerated_count: int) -> str:
    """三种失败原因分开说（纯函数，无 Qt，可在沙箱里测）。

    旧版把「一个字体都没枚举到」和「没有中文字体」说成同一句话，于是 CI 上那条
    「本机缺中文字体，已枚举 0 个」是**一句关于机器的假话**：机器上有字体，
    是平台插件不给字体库。报错文案本身就是归因结论，含混的文案会把排查带偏。

    Args:
        font_path: 随包字体**应该**在的位置
        enumerated_count: Qt 这次枚举到的系统 family 总数
    """
    if not font_path.is_file():
        return (
            f"随包中文字体不在位：{font_path}——这是打包或部署漏了文件，"
            "不是这台机器缺字体（源码运行时先跑一次 python3 packaging/fetch_font.py）；"
            "xlsx 与审计包不受影响，可以先导那两个。"
        )
    if enumerated_count == 0:
        return (
            f"随包中文字体 {font_path.name} 在位但注册失败，且 Qt 一个系统字体都没枚举到"
            "（0 个）——这通常是**平台插件不提供字体库**（offscreen 在 Windows 上实测就是"
            "0 个），**不等于这台机器没有中文字体**；请换原生平台插件重试，"
            "或检查该字体文件是否损坏。xlsx 与审计包不受影响，可以先导那两个。"
        )
    return (
        f"随包中文字体 {font_path.name} 注册失败，且已枚举的 {enumerated_count} 个系统字体"
        "没有一个能渲中文——**这台机器确实缺中文字体**。"
        "xlsx 与审计包不受影响，可以先导那两个。"
    )


def _select_font_with_qt(font_dir: Path | None = None) -> str:
    """选一个能渲中文的字体：随包 → 系统实测 → 报错。

    Args:
        font_dir: 随包字体所在目录；None ⇒ bundled_font_dir()。
            留这个入参是为了让 CI 能真的走到「随包字体不在位」那条分支
            （指一个空目录），而不是把那条分支写成可跳过的测试。

    Raises:
        FontUnavailableError: 三条路都不通，消息说清是哪一种原因
    """
    from PySide6.QtGui import QFontDatabase
    if font_dir is None:
        font_dir = bundled_font_dir()
    font_path = font_dir / BUNDLED_FONT_FILENAME

    bundled = _register_bundled_font(font_path)
    if bundled:
        return bundled[0]

    enumerated = list(QFontDatabase.families())
    renderable = _get_renderable_families()
    if renderable:
        return select_cjk_font(renderable)
    raise FontUnavailableError(_font_failure_message(font_path, len(enumerated)))


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


def render_pdf(report: Report, out_path: Path, font_dir: Path | None = None) -> None:
    """把 Report 渲染到 PDF 文件（需要 PySide6）。

    §0.5：渲染前必须找到可渲中文的字体，否则拒绝生成。

    Args:
        report: 内容层 Report 对象
        out_path: 输出路径
        font_dir: 随包字体目录；None ⇒ 用 bundled_font_dir()。产品调用不传，
            只有 CI 的冒烟测试会传一个空目录来逼出系统字体那条分支。

    Raises:
        FontUnavailableError: 找不到可渲中文的字体（不生成任何 .pdf 文件）
    """
    # 字体检查（拒绝生成，不降级）
    font_family = _select_font_with_qt(font_dir)  # 缺字体时直接抛异常

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
