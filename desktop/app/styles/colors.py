"""Gene&I VI v2.0 深色主题色板 —— 全 UI 颜色值的唯一来源。

来源：姊妹仓 `dylanchen2000/drugeffectscan` 的 `desktop/app/styles/colors.py`
（commit `52e392d`），**原样搬入**：同公司 VI，色值不许在这里改。
原文件的说明照录于此（其中提到的 workspace.py / candidate_item.py 等是那个仓的
文件，本仓没有；留着是为了说明色板本身的演进史）：

    DrugEffect-Plex brand color tokens (v2.0 — launch flow upgrade).

    Single source of truth for all UI color values. New code should reference
    these constants instead of hard-coding hex literals.

    Notes:
    - Legacy code in workspace.py / candidate_item.py / etc. still uses the old
      cyan #22D3EE and other hardcoded values. Those are intentionally NOT
      refactored here — see PR-A self-audit. Migration happens incrementally
      in later PRs as each page is touched.
    - ARM_COLORS palette is used by the new-experiment wizard to assign
      distinct colors to each drug arm; cage bindings inherit arm.color.

新代码一律引用这里的常量，**不许在 QSS 之外硬编码十六进制色值**。
"""

from __future__ import annotations


class C:
    """Gene&I VI v2.0 色板（来源见文件顶部；原仓引用的 docs/launch/spec.md 本仓没有）。"""

    # Backgrounds
    BG          = "#0F1A2E"   # main canvas
    BG_ALT      = "#142238"   # sidebar / second-tier surfaces
    BG_CARD     = "#1A2A45"   # raised cards
    BG_HOVER    = "#1F2F4D"   # hover state on cards/rows

    # Borders
    BORDER      = "#2A3B5C"   # primary border
    BORDER_SOFT = "#1F2D48"   # subdued separators

    # Text tiers
    TEXT_1      = "#E5EAF2"   # primary text on dark bg
    TEXT_2      = "#98A6BF"   # secondary
    TEXT_3      = "#5F7090"   # tertiary / meta

    # Brand
    BRAND_NAVY  = "#1B2A4A"   # legacy navy from logo wordmark
    BRAND_TEAL  = "#0E7490"   # gradient start
    BRAND_MINT  = "#14B8A6"   # gradient end / primary accent

    # Semantic states
    DANGER      = "#F87171"
    WARN        = "#FBBF24"
    SUCCESS     = "#14B8A6"   # alias for mint when used as success indicator


# Drug-arm palette. Wizard assigns these in order; user can edit later.
# Picked to be visually distinct on the dark BG without clashing with mint.
ARM_COLORS: list[str] = [
    "#14B8A6",  # mint   (default for arm 1 / control)
    "#F472B6",  # pink
    "#FCD34D",  # amber
    "#A78BFA",  # violet
    "#60A5FA",  # sky
    "#FB923C",  # orange
]


# Convenience: 135deg accent gradient stops (used by Qt QLinearGradient).
ACCENT_GRADIENT = (C.BRAND_TEAL, C.BRAND_MINT)


__all__ = ["C", "ARM_COLORS", "ACCENT_GRADIENT"]
