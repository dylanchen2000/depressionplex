"""DP-012：人工评分校验器 + holds 并集重算。

四条硬规则（SPEC_人工比对与验收_v2 §1/§2、派工单 v2 §2；均源自真实事故）：

1. **一律丢弃 `mobile_seconds`**，改用 `union(sorted(holds))` 重算。工具每按键
   多算约 0.121 s 墙钟时间（7 个独立估计 0.110–0.131），进入视频时间时被倍速
   缩放——`mobile_seconds` 不可采信，只作为记账列保留（`mobile_seconds_DISCARDED`）。
2. **读 holds 必须先排序再取并集。** 43 个试次里 12 个数组乱序自嵌套（朴素求和
   最多虚高 +54.8 s / +30%）。乱序出现位置随机，不存在"只看最后一个"的捷径。
3. **拒绝入库**：`mobile_seconds == 0` 且 `len(holds) == 0` 且 `unscoreable == false`
   ⇒ 报错。这是"没评"不是"评出 0"；按 0 入库会变成 immobility = 360 s（物理上限），
   单点拉歪 r 与 Bland-Altman。
4. **零长段记账**：并集口径下自动为 0，无害，但必须报出来（不静默）。

**本模块不做任何一致性指标**——r / ICC / Bland-Altman 属于 DP-013（框架）与
DP-014（正式报告）。在这里出现任何"谁和谁相关了多少"都是越界。

纪律（全项目硬规矩）：
- 禁止静默兜底：拿不到合理结果就报警（warnings 列 / 拒绝状态 / 非零退出码），
  不 clamp、不补 0、不猜。
- 时间一律秒。窗长 360 s 是 TST 全程计分口径（DP-004 已关闭：工具在 360 s
  硬收口，39 个有 holds 的试次无一超过 360.00 s）。
- 只用标准库；不碰 assay_core。
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Sequence

AUDIT_FORMAT = "depressionplex.stopwatch-audit.v1"

#: TST 全程计分窗口（秒）。FST 才是 6 min 里只计后 4 min，两范式不得抹平。
TST_WINDOW_S = 360.0
#: 时间戳的录制分辨率（工具以 0.01 s 落盘）。这是数据格式属性，不是判定阈值。
TIMESTAMP_RESOLUTION_S = 0.01
#: 乱序/自嵌套下 naive 求和与并集的偏差达到该量级（秒）时在报告里点名。
#: 单位是秒（时间），不是像素/帧。
NAIVE_INFLATION_FLAG_S = 5.0

#: 配套 `human_scores_*.csv` 的 mobile_seconds 只落 1 位小数，JSON 落 2 位。
#: 对账容差 = 量化半步长 + 浮点余量。这是落盘格式的分辨率属性（噪底台账
#: 一类），**不是判定阈值**。
SUMMARY_CSV_MOBILE_TOL_S = 0.05 + 1e-9

#: 抢救 CSV 无 scorer_id 列，从文件名解析：human_scores_<评分员>_<日期>_SALVAGED_*
_SCORER_IN_NAME_RE = re.compile(r"^human_scores_(.+?)_\d{4}-\d{2}-\d{2}")

TRIAL_ID_RE = re.compile(r"^(?P<video>.+)-ch(?P<chamber>[1-9][0-9]*)$")


def _is_fst(rec: dict[str, Any], trial_id: str) -> bool:
    """FST 判据用工具自己的两个约定：记录里的 `assay`，或 trial_id 的 `FST-` 前缀
    （工具 v1.5 对 FST 设 requirePrefix=true，前缀是规范而非习惯）。"""
    return (str(rec.get("assay", "")).upper() == "FST"
            or trial_id.startswith("FST-"))


def resolve_assay(rec: dict[str, Any], trial_id: str,
                  doc_assay: Any = None) -> tuple[str, str]:
    """本试次的范式 → (范式, 来源)。来源三档，**如实标注，不许伪装成声明值**。

    - `record`：记录自带 `assay`（工具 v1.5 起逐条落）
    - `document`：只有文件头有 `assay`（同一份导出只可能是一个范式）
    - `trial_id_prefix`：v1.x 旧件两处都没有 ⇒ 按 `FST-` 前缀判，无前缀判 TST。
      这是**推断**不是声明：工具对 FST 设 requirePrefix=true，前缀是规范；
      而 v1.x 只有悬尾一个范式，所以无前缀等价于 TST。下游要区分"声明的"和
      "推断的"，看 `assay_source` 列。
    """
    a = str(rec.get("assay", "") or "").strip().upper()
    if a:
        return a, "record"
    a = str(doc_assay or "").strip().upper()
    if a:
        return a, "document"
    return ("FST" if trial_id.startswith("FST-") else "TST"), "trial_id_prefix"


def window_bound_s(rec: dict[str, Any]) -> tuple[float, bool]:
    """本试次的时基上界，返回 (上界秒, 是否取自记录本身)。

    秒表工具 v1.5 起逐场落 `window_s` ＝ **该场录像实长**；v1.x 旧件没有这一列。
    FST 录像实测 362.20–467.56 s，若沿用 TST 的 360 s 当上界，会把每段录像尾部
    2–108 s 全判成越窗，并让 `immobility_s` 算成负数——这是分母错，不是数据错。

    **本函数不发明窗口。** 要不要把 FST 截成 2–6 min 计分窗是 DP-057/DP-074
    未决的口径问题（开录时鼠已在水里、入水时刻不可知），这里只如实用录像实长。
    `window_s` 缺失时退回 TST_WINDOW_S，且调用方对 FST 记录必须打标。
    """
    w = _f(rec.get("window_s"))
    if w is not None and w > 0:
        return w, True
    return TST_WINDOW_S, False

_TRUES = {"true", "1", "yes", "是"}
_FALSES = {"false", "0", "no", "否"}

STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_UNSCOREABLE = "unscoreable"

REJECT_UNSCORED_TRIPLE = (
    "mobile_seconds==0 且 holds==[] 且 unscoreable==false —— "
    "这是『没评』不是『评出 0』，按 0 入库会得到 immobility=360 s（物理上限）"
)


class TrialRejected(Exception):
    """试次违反拒绝入库规则（rule 3）。strict 消费方（DP-013/014）应捕此异常。"""

    def __init__(self, scorer_id: str, trial_id: str, reason: str) -> None:
        super().__init__(f"[{scorer_id}] {trial_id}: {reason}")
        self.scorer_id = scorer_id
        self.trial_id = trial_id
        self.reason = reason


# ---------------------------------------------------------------- 布尔/数值解析


def parse_bool(value: Any) -> bool | None:
    """把 JSON/CSV 里的布尔杂形态解析为 True/False；解析不了返回 None（不猜）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _TRUES:
            return True
        if v in _FALSES:
            return False
        if v == "":
            return None
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    return None


def _f(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v


# ---------------------------------------------------------------- 规则 1+2+4：并集


@dataclass(frozen=True)
class UnionStats:
    total_s: float          # union(sorted(holds)) 时长（唯一可采信的 mobile）
    n_segments: int         # 按键段数
    unsorted: bool          # holds 数组是否乱序（后段起点 < 前段起点）
    zero_length: int        # 零长/负长段个数（按键抖动），并集口径下自动为 0
    naive_sum_s: float      # Σ max(b-a,0)：乱序自嵌套时虚高，仅记账用
    #: 排序取并集之后的**互不重叠**段，录像起点时基的半开区间秒 `[start, end)`。
    #: `total_s` 就是这些段长之和（四舍五入到两位）——**不是**另算一遍，
    #: 由 tests/test_timeline.py 钉住。人工秒表只记"在动"，所以这就是人工侧的
    #: Mobility 段；零长/负长段（按键抖动）不产生段。
    segments: tuple[tuple[float, float], ...] = ()


def union_holds(holds: Sequence[Sequence[float]]) -> UnionStats:
    """排序取并集。**必须先排序**：见模块头规则 2。

    与 `depressionplex/cli/salvage_truncated_audit.union_seconds` 语义一致（后者已在 DP-010
    抢救里实证），两者等价由 tests/test_human_agreement.py 钉住，防止分叉。
    """
    pairs = [(float(a), float(b)) for a, b in holds]
    unsorted = any(pairs[i][0] > pairs[i + 1][0] for i in range(len(pairs) - 1))
    zero_length = sum(1 for a, b in pairs if b <= a)
    naive = sum(max(b - a, 0.0) for a, b in pairs)
    total = 0.0
    merged: list[tuple[float, float]] = []
    cur_a = cur_b = None
    for a, b in sorted(pairs):
        if b <= a:
            continue
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += cur_b - cur_a
                merged.append((cur_a, cur_b))
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += cur_b - cur_a
        merged.append((cur_a, cur_b))
    return UnionStats(
        total_s=round(total, 2),
        n_segments=len(pairs),
        unsorted=unsorted,
        zero_length=zero_length,
        naive_sum_s=round(naive, 2),
        segments=tuple(merged),
    )


# ---------------------------------------------------------------- 行模型


@dataclass
class TrialRow:
    scorer_id: str
    trial_id: str
    source: str                    # 'audit_json' | 'salvaged_csv'
    video: str
    chamber: int | None
    seed: int | None
    presentation_order: int | None
    playback_rate: float | None
    tail_climbing: bool | None
    unscoreable: bool | None
    status: str
    n_hold_segments: int
    holds_unsorted: bool
    zero_length_segments: int
    mobile_union_s: float | None   # rule 1 的唯一 mobile 口径；rejected 为 None
    immobility_s: float | None     # 录像实长（window_s；缺则 TST_WINDOW_S）− mobile_union_s
    mobile_seconds_DISCARDED: float | None
    naive_inflation_s: float | None    # rule 2 记账：naive − union（乱序才有意义）
    per_key_excess_wallclock_s: float | None  # (discarded−union)/段数/倍速（§2 证据链）
    reject_reason: str = ""
    note: str = ""
    scored_at: str = ""
    warnings: list[str] = field(default_factory=list)
    # —— DP-080：以下五列是**如实转载**评分工具落的字段，不参与任何重算 ——
    assay: str = ""                 # 'FST' / 'TST'
    assay_source: str = ""          # record | document | trial_id_prefix
    window_s: float | None = None   # 该场录像实长；v1.x 旧件没有这一列 ⇒ None
    window_source: str = ""         # record | tst_default（见 window_bound_s）
    wall_support_still: bool | None = None  # FST 收尾第一问；TST 不问 ⇒ None
    declared_empty: bool | None = None      # 清单声明的空杯（G10 对照用）
    tool_version: str = ""          # 导出工具版本（文件头），用于分层

    def csv_fields(self) -> dict[str, str]:
        def fmt(v: Any) -> str:
            if v is None:
                return ""
            if isinstance(v, bool):
                return "True" if v else "False"
            return str(v)
        d = asdict(self)
        d["warnings"] = ";".join(self.warnings)
        return {k: fmt(v) for k, v in d.items()}


CSV_COLUMNS = (
    "scorer_id", "trial_id", "assay", "assay_source", "source", "video",
    "chamber", "seed", "presentation_order", "playback_rate",
    "tail_climbing", "wall_support_still", "declared_empty", "unscoreable",
    "status", "n_hold_segments", "holds_unsorted", "zero_length_segments",
    "mobile_union_s", "window_s", "window_source", "immobility_s",
    "mobile_seconds_DISCARDED",
    "naive_inflation_s", "per_key_excess_wallclock_s",
    "reject_reason", "note", "scored_at", "tool_version", "warnings",
)


def split_trial_id(trial_id: str) -> tuple[str, int | None]:
    m = TRIAL_ID_RE.match(trial_id)
    if not m:
        return trial_id, None
    return m.group("video"), int(m.group("chamber"))


def _rule3_violation(mobile_seconds: float | None, n_holds: int,
                     unscoreable: bool | None) -> bool:
    return (mobile_seconds == 0.0) and (n_holds == 0) and (unscoreable is False)


def _excess_wallclock(discarded: float | None, union: float | None,
                      n_segments: int, rate: float | None) -> float | None:
    """每按键墙钟超额（§2 的 0.121 s 估计的逐试次原料）。数据不齐就返回 None，不猜。"""
    if discarded is None or union is None or rate is None:
        return None
    if n_segments <= 0 or rate <= 0:
        return None
    return round((discarded - union) / n_segments / rate, 4)


# ---------------------------------------------------------------- 加载：审计 JSON


def load_audit_json(path: Path | str, *, on_reject: str = "mark") -> tuple[dict, list[TrialRow]]:
    """读一份秒表工具审计导出（王娟/陈璇/徐乐彤格式）。

    on_reject='raise' ⇒ 命中 rule 3 抛 TrialRejected（下游 DP-013/014 用，
    真值入库必须显式处理"没评"）；'mark' ⇒ 行标 rejected 但保留在表里报出。
    """
    path = Path(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    fmt = doc.get("format")
    if fmt != AUDIT_FORMAT:
        raise ValueError(f"{path.name}: format={fmt!r}，不是本校验器认识的 {AUDIT_FORMAT!r}")
    scorer = str(doc.get("scorer_id", ""))
    seed = doc.get("seed")
    delivered: list[str] = list(doc.get("delivered_order", []))
    doc_assay = doc.get("assay")
    tool_version = str(doc.get("tool_version", "") or "")
    rows: list[TrialRow] = []
    for rec in doc.get("records", []):
        row = _row_from_record(rec, scorer=scorer, seed=seed, delivered=delivered,
                               doc_assay=doc_assay, tool_version=tool_version)
        if row.status == STATUS_REJECTED and on_reject == "raise":
            raise TrialRejected(scorer, row.trial_id, row.reject_reason)
        rows.append(row)
    header = {
        "scorer_id": scorer, "seed": seed,
        "done_count": doc.get("done_count"), "total_trials": doc.get("total_trials"),
        "tool_version": doc.get("tool_version"), "partial": doc.get("partial"),
        "delivered_order": delivered, "file": path.name,
    }
    return header, rows


def _row_from_record(rec: dict, *, scorer: str, seed: Any,
                     delivered: Sequence[str], doc_assay: Any = None,
                     tool_version: str = "") -> TrialRow:
    trial_id = str(rec.get("trial_id", ""))
    video, chamber = split_trial_id(trial_id)
    assay, assay_src = resolve_assay(rec, trial_id, doc_assay)
    passthrough = dict(
        assay=assay, assay_source=assay_src,
        window_s=_f(rec.get("window_s")),
        wall_support_still=parse_bool(rec.get("wall_support_still"))
        if rec.get("wall_support_still") is not None else None,
        declared_empty=parse_bool(rec.get("declared_empty"))
        if rec.get("declared_empty") is not None else None,
        tool_version=tool_version,
    )
    warns: list[str] = []
    holds = rec.get("holds") or []
    discarded = _f(rec.get("mobile_seconds"))
    unscoreable = parse_bool(rec.get("unscoreable", False))
    if unscoreable is None:
        warns.append("unscoreable_field_unparseable")

    # rule 3：先于一切——"没评"不许伪装成"评出 0"
    if _rule3_violation(discarded, len(holds), unscoreable):
        return TrialRow(
            scorer_id=scorer, trial_id=trial_id, source="audit_json",
            video=video, chamber=chamber, seed=seed,
            presentation_order=rec.get("presentation_order"),
            playback_rate=_f(rec.get("playback_rate")),
            tail_climbing=parse_bool(rec.get("tail_climbing", False)),
            unscoreable=unscoreable, status=STATUS_REJECTED,
            n_hold_segments=0, holds_unsorted=False, zero_length_segments=0,
            mobile_union_s=None, immobility_s=None,
            mobile_seconds_DISCARDED=discarded,
            naive_inflation_s=None, per_key_excess_wallclock_s=None,
            reject_reason=REJECT_UNSCORED_TRIPLE,
            note=str(rec.get("note", "")), scored_at=str(rec.get("scored_at", "")),
            warnings=warns, window_source="", **passthrough,
        )

    # rule 1+2+4
    u = union_holds(holds)
    rate = _f(rec.get("playback_rate"))
    porder = rec.get("presentation_order")
    if isinstance(porder, int) and delivered:
        if not (1 <= porder <= len(delivered)) or delivered[porder - 1] != trial_id:
            warns.append("presentation_order_mismatch")
    else:
        warns.append("presentation_order_missing")
    for a, b in ((float(x), float(y)) for x, y in holds):
        if a < -TIMESTAMP_RESOLUTION_S or b < -TIMESTAMP_RESOLUTION_S:
            warns.append("hold_negative_time")
            break
    wbound, w_from_rec = window_bound_s(rec)
    if not w_from_rec and _is_fst(rec, trial_id):
        # TST 的 360 s 是有据的名义窗口；FST 没有，缺 window_s 就是分母不明
        warns.append("fst_window_s_missing:fallback_360s")
    for b in (float(y) for _, y in holds):
        if b > wbound + TIMESTAMP_RESOLUTION_S:
            warns.append("hold_beyond_window")
            break

    status = STATUS_UNSCOREABLE if unscoreable else STATUS_ACCEPTED
    union = u.total_s if status == STATUS_ACCEPTED else None
    immob = round(wbound - u.total_s, 2) if status == STATUS_ACCEPTED else None
    inflation = round(u.naive_sum_s - u.total_s, 2) if (u.unsorted and status == STATUS_ACCEPTED) else None
    if inflation is not None and inflation >= NAIVE_INFLATION_FLAG_S:
        warns.append("naive_sum_inflation>=5s")

    return TrialRow(
        scorer_id=scorer, trial_id=trial_id, source="audit_json",
        video=video, chamber=chamber, seed=seed,
        presentation_order=porder, playback_rate=rate,
        tail_climbing=parse_bool(rec.get("tail_climbing", False)),
        unscoreable=unscoreable, status=status,
        n_hold_segments=u.n_segments, holds_unsorted=u.unsorted,
        zero_length_segments=u.zero_length,
        mobile_union_s=union, immobility_s=immob,
        mobile_seconds_DISCARDED=discarded,
        naive_inflation_s=inflation,
        per_key_excess_wallclock_s=_excess_wallclock(discarded, union, u.n_segments, rate),
        note=str(rec.get("note", "")), scored_at=str(rec.get("scored_at", "")),
        warnings=warns,
        window_source=("record" if w_from_rec else "tst_default"), **passthrough,
    )


# ---------------------------------------------------------- 加载：张的抢救 CSV
#
# 抢救文件（depressionplex/cli/salvage_truncated_audit.py 产出）里 holds 已聚合成
# mobile_union_s / holds_unsorted / zero_length_segments 三列——并集口径与
# 乱序判据是同一实现（见 union_holds 的等价钉测）。本 loader 只做透传 +
# 一致性复算（immobility），并回填 seed（从同目录 TRUNCATED txt 头部，
# 那是原始导出的一部分，不是猜测）。


def _seed_from_truncated_txt(path: Path) -> int | None:
    try:
        head = path.read_text(encoding="utf-8")[:200]
    except FileNotFoundError:
        return None
    m = re.search(r'"seed"\s*:\s*(\d+)', head)
    return int(m.group(1)) if m else None


def load_salvaged_csv(path: Path | str, *, on_reject: str = "mark") -> list[TrialRow]:
    path = Path(path)
    sibling_txt = sorted(path.parent.glob("*TRUNCATED*json.txt"))
    seed = _seed_from_truncated_txt(sibling_txt[0]) if sibling_txt else None
    m = _SCORER_IN_NAME_RE.match(path.name)
    scorer_from_name = m.group(1) if m else path.name  # 解析不出如实回退文件名
    rows: list[TrialRow] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for rec in csv.DictReader(fh):
            trial_id = rec.get("trial_id", "")
            video, chamber = split_trial_id(trial_id)
            warns: list[str] = ["union_precomputed_by_salvage_tool"]
            # 抢救件来自 v1.x 悬尾导出：没有 assay/window_s/杯壁问，如实留空
            assay, assay_src = resolve_assay(rec, trial_id, None)
            n_seg = int(_f(rec.get("n_hold_segments")) or 0)
            discarded = _f(rec.get("mobile_seconds_DISCARDED"))
            union = _f(rec.get("mobile_union_s"))
            unscoreable = parse_bool(rec.get("unscoreable"))
            unsorted = parse_bool(rec.get("holds_unsorted")) is True
            zero = int(_f(rec.get("zero_length_segments")) or 0)
            rate = _f(rec.get("playback_rate"))
            row = TrialRow(
                scorer_id=str(rec.get("scorer_id") or scorer_from_name),
                trial_id=trial_id, source="salvaged_csv",
                video=video, chamber=chamber, seed=seed,
                presentation_order=(int(v) if (v := _f(rec.get("presentation_order"))) is not None else None),
                playback_rate=rate,
                tail_climbing=parse_bool(rec.get("tail_climbing")),
                unscoreable=unscoreable, status=STATUS_ACCEPTED,
                n_hold_segments=n_seg, holds_unsorted=unsorted,
                zero_length_segments=zero,
                mobile_union_s=union,
                immobility_s=(round(TST_WINDOW_S - union, 2) if union is not None else None),
                mobile_seconds_DISCARDED=discarded,
                naive_inflation_s=None,  # 抢救件无 holds 明细，朴素虚高不可复算（如实留空）
                per_key_excess_wallclock_s=_excess_wallclock(discarded, union, n_seg, rate),
                note=str(rec.get("note", "")), scored_at=str(rec.get("scored_at", "")),
                warnings=warns,
                assay=assay, assay_source=assay_src,
                window_s=_f(rec.get("window_s")),
                window_source=("record" if _f(rec.get("window_s")) else "tst_default"),
                wall_support_still=None, declared_empty=None,
                tool_version=str(rec.get("tool_version", "") or ""),
            )
            if _rule3_violation(discarded, n_seg, unscoreable):
                row.status = STATUS_REJECTED
                row.reject_reason = REJECT_UNSCORED_TRIPLE
                row.mobile_union_s = None
                row.immobility_s = None
                if on_reject == "raise":
                    raise TrialRejected(row.scorer_id, trial_id, REJECT_UNSCORED_TRIPLE)
            rows.append(row)
    return rows


# ---------------------------------------------------------------- 交叉核对：CSV 摘要


def _crosscheck_one_csv(by_trial: dict[str, "TrialRow"], path: Path,
                        ) -> tuple[list[str], set[str]]:
    """核对**单份** CSV 的逐条数值 → (不一致清单, 这份 CSV 覆盖到的试次集合)。

    覆盖集合是返回值而不是就地判缺失：缺失只能对同一评分员**全部**分次导出的
    并集判，见 `crosscheck_summary_csv`（DP-076）。
    """
    mismatches: list[str] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for rec in csv.DictReader(fh):
            tid = rec.get("trial_id", "")
            seen.add(tid)
            row = by_trial.get(tid)
            if row is None:
                mismatches.append(f"{path.name}: CSV 有 JSON 无 → {tid}")
                continue
            c_mob, j_mob = _f(rec.get("mobile_seconds")), row.mobile_seconds_DISCARDED
            # 容差是 CSV 落盘分辨率（1 位小数）的半步长，不是判定阈值——见
            # SUMMARY_CSV_MOBILE_TOL_S 注释。超出它才是真对不上账。
            if c_mob is None or j_mob is None or abs(c_mob - j_mob) > SUMMARY_CSV_MOBILE_TOL_S:
                mismatches.append(f"{path.name}/{tid}: mobile_seconds CSV={c_mob} ≠ JSON={j_mob}")
            c_p, j_p = _f(rec.get("presentation_order")), row.presentation_order
            if c_p is None or j_p is None or int(c_p) != j_p:
                mismatches.append(f"{path.name}/{tid}: presentation_order CSV={c_p} ≠ JSON={j_p}")
            if parse_bool(rec.get("unscoreable")) != row.unscoreable:
                mismatches.append(f"{path.name}/{tid}: unscoreable 不一致")
            if parse_bool(rec.get("tail_climbing")) != row.tail_climbing:
                mismatches.append(f"{path.name}/{tid}: tail_climbing 不一致")
    return mismatches, seen


def crosscheck_summary_csv(
    rows: Sequence[TrialRow],
    paths: Path | str | Sequence[Path | str],
) -> list[str]:
    """配套 `human_scores_*.csv` 与审计 JSON 的一致性核对（只核对，不采信其数值）。

    返回不一致清单（空 = 一致）。CSV 无 holds，mobile_seconds 列不可采信——
    它的存在价值就是被拿来和 JSON 对账。

    `paths` 收的是**同一个评分员的全部**配套 CSV（也接受单个路径）。逐条数值核对
    是**逐文件**的，那是它该有的粒度；但「JSON 有 CSV 无」这一条必须对**并集**判
    ——计时工具是分次导出的，每份各覆盖一部分试次（文件名都写 `partialNofM`），
    逐份判会把别份文件里的试次全报成缺失（DP-076）。
    """
    ps = ([Path(paths)] if isinstance(paths, (str, Path))
          else [Path(p) for p in paths])
    if not ps:
        raise ValueError("没给任何配套 CSV ⇒ 拒绝返回空清单假装「全部一致」")
    by_trial = {r.trial_id: r for r in rows if r.source == "audit_json"}
    mismatches: list[str] = []
    in_csv: set[str] = set()
    for p in ps:
        ms, seen = _crosscheck_one_csv(by_trial, p)
        mismatches.extend(ms)
        in_csv |= seen
    where = ps[0].name if len(ps) == 1 else "%s 等 %d 份" % (ps[0].name, len(ps))
    mismatches.extend(
        "%s: JSON 有 CSV 无 → %s/%s" % (where, by_trial[t].scorer_id, t)
        for t in sorted(t for t in by_trial if t not in in_csv))
    return mismatches


# ---------------------------------------------------------------- 装配 + 报告


@dataclass
class TableResult:
    rows: list[TrialRow]
    n_trials: int = 0
    n_accepted: int = 0
    n_rejected: int = 0
    n_unscoreable: int = 0
    unsorted_total: int = 0
    zero_length_total: int = 0
    max_naive_inflation_s: float = 0.0
    per_key_estimates: list[float] = field(default_factory=list)
    window_violations: list[str] = field(default_factory=list)
    order_mismatches: list[str] = field(default_factory=list)
    crosscheck_mismatches: list[str] = field(default_factory=list)
    # DP-081：按（评分员, 范式）记种子。一个人的悬尾和 FST 是两把独立的随机顺序，
    # 用同一个 key 会静默互相覆盖，导致共享种子漏报（已真实发生 3 次）。
    seeds: dict[tuple[str, str], int | None] = field(default_factory=dict)
    seed_groups: dict[int, list[str]] = field(default_factory=dict)
    # DP-081：同一（评分员, 范式）在不同批次导出里出现了不同 seed。
    # 这是**正常的**（重评批次本来就该换种子），只记账不报错。
    seed_rebatches: list[str] = field(default_factory=list)

    def by_scorer(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for r in self.rows:
            b = out.setdefault(r.scorer_id, {"n": 0, "unsorted": 0, "zero_length": 0,
                                             "rejected": 0, "union_sum_s": 0.0,
                                             "rates": {}})
            b["n"] += 1
            b["unsorted"] += int(r.holds_unsorted)
            b["zero_length"] += r.zero_length_segments
            b["rejected"] += int(r.status == STATUS_REJECTED)
            b["union_sum_s"] += r.mobile_union_s or 0.0
            # DP-046：倍速台账。key 用 float 或 None（未记录），**不许把 None 当默认值填掉**
            b["rates"][r.playback_rate] = b["rates"].get(r.playback_rate, 0) + 1
        return out

    def rate_changed_scorers(self) -> dict[str, dict[Any, int]]:
        """批内换过倍速的评分员 → 各倍速的试次数（DP-046）。

        倍速不是备注，是**受控实验参数**：倍速减半 ⇒ 按键粒度减半（徐 1.55@0.25x
        / 3.22@0.5x、陈璇 2.33@0.5x / 4.85@1.0x，两人独立复现），而**一处倍速错配
        就把 ICC 由 0.864 打到 0.344**，与"一个人粗一个人细"同量级。批内换速的批
        次不能整批合算一致性，必须分层——所以这里必须报出来，不许静默。
        `None`（未记录倍速）与已知倍速混在一起同样算换速：分不了层就是分不了层。
        """
        return {s: dict(sorted(b["rates"].items(), key=lambda kv: (kv[0] is None, kv[0])))
                for s, b in self.by_scorer().items() if len(b["rates"]) > 1}


def _record_seed(result: "TableResult", scorer: str, assay: str,
                 seed: int, fname: str) -> None:
    """把一份导出的 seed 记进台账。同 key 换了 seed ⇒ 记 rebatch，**保留最新的**。"""
    key = (scorer, assay)
    old = result.seeds.get(key)
    if old is not None and old != seed:
        result.seed_rebatches.append(f"{scorer}/{assay}: {old} → {seed} ({fname})")
    result.seeds[key] = seed


def build_table(data_dir: Path | str) -> TableResult:
    data_dir = Path(data_dir)
    result = TableResult(rows=[])
    audit_jsons = sorted(p for p in data_dir.glob("timer_audit_*.json"))
    salvaged = sorted(p for p in data_dir.glob("human_scores_*SALVAGED*.csv"))
    if not audit_jsons:
        raise FileNotFoundError(f"{data_dir} 下没有 timer_audit_*.json —— 拒绝产出空表")

    for p in audit_jsons:
        header, rows = load_audit_json(p, on_reject="mark")
        result.rows.extend(rows)
        assays = {r.assay for r in rows if r.assay}
        if len(assays) > 1:
            # 一份导出只可能是一个范式（工具按范式分别导出）。混了就是数据问题，
            # 报出来但不猜——不许挑一个当代表。
            result.seed_rebatches.append(f"{header['scorer_id']}: 同一份导出混了范式 {sorted(assays)} ({p.name})")
        if header["seed"] is not None and len(assays) == 1:
            _record_seed(result, header["scorer_id"], assays.pop(), int(header["seed"]), p.name)
    for p in salvaged:
        rows = load_salvaged_csv(p, on_reject="mark")
        result.rows.extend(rows)
        for r in rows:
            if r.seed is not None:
                _record_seed(result, r.scorer_id, r.assay, int(r.seed), p.name)

    result.rows.sort(key=lambda r: (r.scorer_id, r.presentation_order or 0, r.trial_id))

    for r in result.rows:
        result.n_trials += 1
        if r.status == STATUS_REJECTED:
            result.n_rejected += 1
        elif r.status == STATUS_UNSCOREABLE:
            result.n_unscoreable += 1
        else:
            result.n_accepted += 1
        result.unsorted_total += int(r.holds_unsorted)
        result.zero_length_total += r.zero_length_segments
        if r.naive_inflation_s:
            result.max_naive_inflation_s = max(result.max_naive_inflation_s, r.naive_inflation_s)
        if r.per_key_excess_wallclock_s is not None and r.status == STATUS_ACCEPTED:
            result.per_key_estimates.append(r.per_key_excess_wallclock_s)
        if "hold_beyond_window" in r.warnings:
            result.window_violations.append(f"{r.scorer_id}/{r.trial_id}")
        if "presentation_order_mismatch" in r.warnings:
            result.order_mismatches.append(f"{r.scorer_id}/{r.trial_id}")

    for seed, keys in _invert(result.seeds).items():
        result.seed_groups[seed] = sorted(f"{s}/{a}" for s, a in keys)

    # 配套 CSV 交叉核对（有 JSON 的评分员才核）
    json_scorers = {r.scorer_id for r in result.rows if r.source == "audit_json"}
    csvs = [p for p in sorted(data_dir.glob("human_scores_*.csv"))
            if "SALVAGED" not in p.name]
    for s in sorted(json_scorers):
        # 用文件名里的评分员名过滤（文件名形如 human_scores_<评分员>_<日期>_partialNofM.csv）。
        # 一个评分员可能有多份分次导出 ⇒ 一次全给，缺失对并集判（DP-076）。
        mine = [p for p in csvs if f"_{s}_" in p.name]
        if mine:
            result.crosscheck_mismatches.extend(
                crosscheck_summary_csv([r for r in result.rows if r.scorer_id == s], mine))
    return result


def _invert(d: dict[Any, int]) -> dict[int, list[Any]]:
    out: dict[int, list[Any]] = {}
    for k, v in d.items():
        out.setdefault(v, []).append(k)
    return out


def write_table_csv(rows: Sequence[TrialRow], path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r.scorer_id, r.presentation_order or 0, r.trial_id)):
            w.writerow(r.csv_fields())


def table_csv_text(rows: Sequence[TrialRow]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in sorted(rows, key=lambda r: (r.scorer_id, r.presentation_order or 0, r.trial_id)):
        w.writerow(r.csv_fields())
    return buf.getvalue()


def format_report(res: TableResult) -> str:
    lines = [
        f"试次总数: {res.n_trials}（accepted {res.n_accepted} / rejected {res.n_rejected} "
        f"/ unscoreable {res.n_unscoreable}）",
        f"holds 乱序自嵌套试次: {res.unsorted_total}",
        f"零长段总数: {res.zero_length_total}（并集口径下自动为 0，此处仅记账）",
        f"朴素求和最大虚高: {res.max_naive_inflation_s} s",
        f"holds 越过 {TST_WINDOW_S} s 硬收口: {res.window_violations or '无'}",
        f"presentation_order 与 delivered_order 不符: {res.order_mismatches or '无'}",
        f"配套 CSV 交叉核对: {res.crosscheck_mismatches or '全部一致'}",
        f"seed 分组: { {s: g for s, g in res.seed_groups.items()} }",
        f"seed 换批次记账: {res.seed_rebatches or '无'}",
    ]
    if res.per_key_estimates:
        import statistics
        m = statistics.mean(res.per_key_estimates)
        sd = statistics.stdev(res.per_key_estimates) if len(res.per_key_estimates) > 1 else 0.0
        lines.append(
            f"每按键墙钟超额（逐试次估计 n={len(res.per_key_estimates)}）: "
            f"均值 {m:.4f} s（审计 §5 的 0.121 s 为试次×倍速合并口径，逐试次更抖）"
        )
    if res.n_rejected:
        lines.append("⚠ 以下试次被拒绝入库（rule 3，重新排评）:")
        for r in res.rows:
            if r.status == STATUS_REJECTED:
                lines.append(f"  - {r.scorer_id} / {r.trial_id}: {r.reject_reason}")
    changed = res.rate_changed_scorers()
    if changed:
        lines.append("⚠ 批内换过倍速（DP-046，**这些人的数据不许整批合算一致性，必须按倍速分层**）:")
        for s, rates in sorted(changed.items()):
            lines.append(f"  - {s}: " + " ".join(
                f"{'未记录' if k is None else k}x×{v}" for k, v in rates.items()))
        lines.append("  倍速是受控实验参数：一处错配就把 ICC 由 0.864 打到 0.344（与'粗×细'同量级）。"
                     "SOP v1.3 起全项目锁 0.5x，且同一对两人必须同速")
    lines.append("按评分员: ")
    for s, b in sorted(res.by_scorer().items()):
        rates = " ".join(f"{'未记录' if k is None else k}x×{v}" for k, v in
                         sorted(b["rates"].items(), key=lambda kv: (kv[0] is None, kv[0])))
        lines.append(
            f"  {s}: n={b['n']} 乱序={b['unsorted']} 零长段={b['zero_length']} "
            f"拒绝={b['rejected']} union合计={b['union_sum_s']:.2f}s 倍速[{rates}]"
        )
    lines.append("本表不含任何一致性指标（r/ICC/BA 属 DP-013/DP-014）。")
    return "\n".join(lines)
