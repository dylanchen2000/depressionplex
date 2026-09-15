"""模式徽章的内容层：把 `CalibrationStatus` 变成「屏幕上写什么、什么颜色」。

**这一层是资质边界的显示端**（架构文件 §4）。`services/calibration.py` 负责判
*有没有资格*，本模块负责*怎么把这件事说给用户*，`widgets/badge.py` 只负责摆。

三条硬规则：

1. **文案与颜色只有这一份。** 徽章会同时出现在主窗口状态栏与结果页，两处摆两份
   文案，改了一处就会出现「状态栏说研究版、结果页说计量模式」这种事——
   而它不报错、不告警，只是在客户面前自相矛盾。
2. **`claims_metrology` 只许从 `status.may_report_metrology` 抄。**
   本模块一个 `if mode is ...` 都不许有：模式判定在 `calibration.py`，
   这里再判一次就是第二个判定器（DP-102 那四个缺陷的形状）。
3. **红徽章必须同时说明「已降级为研究用途，软件仍可用」。**
   `calibration.py` 的设计是「降级 + 说明原因，不是报错退出」；如果徽章只写
   「标定文件不可信」，用户合理的理解是「这软件坏了/不能用了」，于是他会去重装、
   去删文件、或者干脆放弃——**把一次降级说成一次故障，代价比故障本身大。**

本模块**只用标准库**，不许 import PySide6（守卫钉住）——徽章文案要能在没有
显示器的环境里被测试，这是本仓所有「文案即契约」的东西的共同要求。
"""

from __future__ import annotations

from dataclasses import dataclass

from desktop.app.services.calibration import Badge, CalibrationStatus

#: 非计量模式下必须出现的那句话。**导出层（B6）的声明与这里说的是同一件事，
#: 但两处受众不同（纸面报告 vs 屏幕角落），所以文本各自维护、不强行合并。**
#: 唯一不许分叉的是这个判断本身，而它只有一个来源：`may_report_metrology`。
NO_METROLOGY_LINE = "本软件当前没有计量资质，秒数不得作为计量结果或合规证据使用。"


@dataclass(frozen=True)
class BadgeView:
    """徽章的全部可见内容。渲染层只许读这几个字段，不许自己拼。"""

    text: str                    # 徽章上的字
    fg: str                      # 前景色（#RRGGBB）
    bg: str                      # 背景色
    border: str                  # 边框色
    tooltip: str                 # 悬停全文：结论 + 每一条原因 + 免责句
    claims_metrology: bool       # 这个徽章是否声称「可作计量结果」


@dataclass(frozen=True)
class _Style:
    label: str
    fg: str
    bg: str
    border: str
    lead: str          # tooltip 第一行（结论）


#: `Badge` 每个成员一条，**不许有默认分支**：将来加一种徽章而忘了写文案，
#: 守卫会红；若这里用 `.get(badge, 某个默认)`，新徽章会静默长成旧样子。
_STYLES: dict[Badge, _Style] = {
    Badge.YELLOW: _Style(
        label="研究用途 · 未计量标定",
        fg="#5c4400", bg="#fff3cd", border="#e0a800",
        lead="当前为研究版。",
    ),
    Badge.GREEN: _Style(
        label="计量模式",
        fg="#0f4a1e", bg="#d4edda", border="#28a745",
        lead="当前为计量模式：验收门全部通过，标定文件校验一致。",
    ),
    Badge.RED: _Style(
        label="标定不可信 · 已按研究用途运行",
        fg="#6b1620", bg="#f8d7da", border="#dc3545",
        # 「软件仍可继续使用」这半句是本行的重点，见模块头规则 3。
        lead="标定文件不可信，已忽略其内容并降级为研究用途——软件仍可继续使用，"
             "只是秒数不具备计量效力。",
    ),
}


def build_badge_view(status: CalibrationStatus) -> BadgeView:
    """把启动自检的结论变成徽章内容。

    `status.reasons` **逐条原样进 tooltip**：那几句话是用户唯一能看到的
    「为什么是这个徽章」，少一条就等于把一个已知问题藏起来。
    """
    claims = status.may_report_metrology   # 唯一来源，本模块不自己判模式

    # 两个字段必须自洽：绿徽章 ⇔ 有计量资质。
    # **不自洽时不许画**——这是本模块写这几行的原因：`badge` 与 `mode` 在
    # `CalibrationStatus` 里是两个独立字段，文案按 `badge` 取、声称按 `mode` 算，
    # 于是一个手搓的「绿徽章 + research」会在屏幕上写出「计量模式」，
    # 而 `claims_metrology` 是 False，导出那边照研究版声明。
    # 我自己的守卫第一次跑就抓到了这个（GREEN + RESEARCH ⇒ 文字「计量模式」）。
    # 这种状态 `evaluate_calibration` 今天产不出来，但「今天产不出来」不是保证；
    # 它只可能来自代码里手搓 status，所以当场抛比画一句假话对。
    if (status.badge is Badge.GREEN) != claims:
        raise ValueError(
            f"标定结论自相矛盾：badge={status.badge.value}、"
            f"may_report_metrology={claims}。徽章与资质必须同进同退，"
            "不许画出一个说着计量模式却没有资质的徽章")

    style = _STYLES[status.badge]          # KeyError 就该炸：徽章没文案是发布事故
    lines = [style.lead]
    lines.extend(f"· {reason}" for reason in status.reasons)
    if not claims:
        lines.append(NO_METROLOGY_LINE)
    return BadgeView(
        text=style.label,
        fg=style.fg,
        bg=style.bg,
        border=style.border,
        tooltip="\n".join(lines),
        claims_metrology=claims,
    )
