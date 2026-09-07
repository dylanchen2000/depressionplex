"""逐段时间线导出（DP-061）：软件侧与人工侧共用一种行形状，好让两边直接比。

**为什么导"段"而不是导"逐秒"**：逐秒必须先决定"这一秒算动还是算不动"——
半秒在动的那一秒归谁？这是一个**新的口径决定**，一旦落在导出层，口径就分叉了。
段是原始形状：软件侧 `bouts` 流水线出来的本来就是段，人工侧秒表按键记下的
本来也是段，而两边段长之和分别**就是各自已发布的总时长**。所以导段
**不引入任何新口径**。要逐秒的人在段上自己分箱，口径写在他那一层。

**时基统一到录像起点**（video seconds from 0）：

- 软件侧 `CategoryStat.segments_s` 是**相对计分窗口起点**的（与 `first_onset_s`
  一致），本层加 `window_start_s` 平移过去；
- 人工侧秒表记的本来就是录像时基，**不平移**。

TST 两者差 0，FST 差 120 s——**抹平就是口径事故**，所以每行都把 `window_start_s`
带出来供审计，人工行也带（只是记录该范式的窗口起点，不参与平移）。

**没产出 ≠ 零段活动**（DP-032）：未放行的试次不写 0 长段，写一行
`start_s` 为空的**说明行**，把原因放在 `note` 里。读这张表的人必须跳过
`start_s` 为空的行——那不是段，是"这里本来该有段但没有"的痕迹。

本模块**不算任何一致性指标**。Jaccard 一律走 `lovo_cv.jaccard_segs`，
它吃的就是 `segments_s` 这个形状。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Sequence

from . import trial_report

TIMELINE_FIELDS = ("trial_id", "assay", "source", "scorer_id", "category",
                   "seg_index", "start_s", "end_s", "duration_s",
                   "window_start_s", "note")

SOURCE_SOFTWARE = "software"
SOURCE_HUMAN = "human"


def note_row(*, trial_id: str, assay: str, source: str, note: str,
             scorer_id: str = "", category: str = "Mobility") -> dict[str, object]:
    """说明行：本试次**没有段**，以及为什么没有。

    `start_s` / `end_s` / `duration_s` / `seg_index` 全留空——**不许填 0**。
    """
    if not note:
        raise ValueError("说明行必须给出原因：没有原因的空行等于把'没有'伪装成'零'")
    return {
        "trial_id": trial_id, "assay": assay, "source": source,
        "scorer_id": scorer_id, "category": category,
        "seg_index": "", "start_s": "", "end_s": "", "duration_s": "",
        "window_start_s": trial_report.ASSAY_WINDOWS.get(assay, (0.0, 0.0))[0],
        "note": note,
    }


def _seg_rows(segments: Sequence[tuple[float, float]], *, trial_id: str,
              assay: str, source: str, scorer_id: str, category: str,
              shift_s: float, window_start_s: float,
              note: str = "") -> list[dict[str, object]]:
    rows = []
    for i, (a, b) in enumerate(segments):
        rows.append({
            "trial_id": trial_id, "assay": assay, "source": source,
            "scorer_id": scorer_id, "category": category,
            "seg_index": i,
            "start_s": round(a + shift_s, 3),
            "end_s": round(b + shift_s, 3),
            "duration_s": round(b - a, 3),
            "window_start_s": window_start_s,
            "note": note,
        })
    return rows


def rows_from_report(r: trial_report.TrialReport, *,
                     category: str = "Mobility") -> list[dict[str, object]]:
    """软件侧：把 `TrialReport` 的段平移到录像起点时基。

    未放行（`scored=False`）时 `categories` 是空的 ⇒ 出一行说明行，
    把闸门报警文本带上，**不写 0 长段**。
    """
    w0 = r.window_start_s
    stat = r.categories.get(category)
    if stat is None:
        why = " | ".join(r.gate_messages) or (
            f"未放行或无 {category} 类别 ⇒ 无段（不是零段）")
        return [note_row(trial_id=r.trial_id, assay=r.assay,
                         source=SOURCE_SOFTWARE, note=why, category=category)]
    if not stat.segments_s:
        return [note_row(trial_id=r.trial_id, assay=r.assay,
                         source=SOURCE_SOFTWARE, category=category,
                         note=f"窗口内 {category} 段数为 0（这是真的 0 段，"
                              f"不是没产出：本试次 scored={r.scored}）")]
    return _seg_rows(stat.segments_s, trial_id=r.trial_id, assay=r.assay,
                     source=SOURCE_SOFTWARE, scorer_id="", category=category,
                     shift_s=w0, window_start_s=w0)


def rows_from_human_holds(segments: Sequence[tuple[float, float]], *,
                          trial_id: str, scorer_id: str, assay: str = "TST",
                          category: str = "Mobility",
                          note: str = "") -> list[dict[str, object]]:
    """人工侧：`human_agreement.union_holds(...).segments` 直接进表。

    **不平移**：秒表记的就是录像时基。`window_start_s` 只作记录。
    """
    w0 = trial_report.ASSAY_WINDOWS[assay][0]
    if not segments:
        return [note_row(trial_id=trial_id, assay=assay, source=SOURCE_HUMAN,
                         scorer_id=scorer_id, category=category,
                         note=note or "并集后段数为 0（整段判为不动）")]
    return _seg_rows(segments, trial_id=trial_id, assay=assay,
                     source=SOURCE_HUMAN, scorer_id=scorer_id,
                     category=category, shift_s=0.0, window_start_s=w0,
                     note=note)


def write_csv(rows: Iterable[dict[str, object]], path: Path) -> int:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=TIMELINE_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in TIMELINE_FIELDS})
    return len(rows)
