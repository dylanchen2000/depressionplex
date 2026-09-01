"""人工秒表评分 vs 软件 immobility 的比对与验收（SPEC_人工比对与验收_v1）。

这是 TST v1 出货的门：trial 级 immobility 总时长 vs 人工，Pearson r ≥ 0.95。
本模块只做**消费侧**——读人工 CSV（§2.1）与软件 per-trial JSON（§2.2），
执行 §3 三个坑的防护、§4 排除规则、§5 全部报告项、§6 判据 V1–V7、§7 铁律
（参数快照只记录不回写、排除前后双数字、预填痕迹拒收、不达标就报不达标）。

软件侧输入（生成器是另一 PR 的事）：目录内每试次一个 `<trial_id>.json`，最少字段：

    trial_id, duration_sec, immobility_seconds, scoreable_frames, total_frames,
    unknown_fraction, validity("valid"|"detached"|"truncated_suspect"|"unknown"),
    tail_climbing(bool), source("software_pipeline"|其他),
    parameters{...含 theta_mob...}, versions{code_git_sha, tool_version, model_version}

时间单位一律秒（单位不变量）；帧↔秒只在换算处经显式 fps。
人工记 mobile，软件出 immobility：immobility_human = duration − mobile（§3.1）。
TST 全程 360 s 计分，两侧窗口必须一致，不一致硬报错、不缩放（§3.2）。

诚实锁：任一试次 source != "software_pipeline"，V1–V7 一律 "pending_data"，
不产生任何 "pass"——派生/占位数据永远伪装不成验收结果。
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# ---------------------------------------------------------------- 人工 CSV §2.1

HUMAN_CSV_COLUMNS = (
    "scorer_id", "trial_id", "mobile_seconds", "tail_climbing",
    "unscoreable", "note", "scored_at", "presentation_order",
)
TRIAL_ID_RE = re.compile(r"^(?P<video>.+)-ch(?P<chamber>[1-9][0-9]*)$")

_TRUES = {"true", "1", "yes", "是"}
_FALSES = {"false", "0", "no", "否"}

# §7 铁律「人工文件带有任何软件预填痕迹即拒绝」的启发式：预填=把软件数字**逐位
# 抄进**人工栏，抄出来的数与软件 mobility 差 ≤0.01 s（两边都是 0.01 s 分辨率）；
# 独立秒表在 360 s 尺度上偶合到 0.01 s 以内，单/双试次尚可是巧合，≥3 个试次不是。
# 阈值只许经契约评审修改，不许在看到报告后为放行某文件而改（§7 诚实纪律）。
PREFILL_TIE_TOL_SEC = 0.01
PREFILL_MAX_TIES = 2

TST_WINDOW_SEC = 360.0          # TST 全程计分（FST 才是后 4 分钟）
WINDOW_TOL_SEC = 0.5
GATE_R = 0.95
GATE_ICC = 0.90
GATE_TAIL_RECALL = 0.95
GATE_DETACHED_SENS = 0.90
GATE_BIAS_FRAC = 0.05           # BA 偏差 < 试次时长的 5%
GATE_MIN_N = 25

REPORT_FORMAT = "depressionplex.human-agreement.v1"
RESULT_FORMAT = "depressionplex.result.v1"


class AgreementError(ValueError):
    """聚合全部校验错误后一次性抛出（仿 annotations.contract 风格）。"""

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) or "validation failed")


class WindowMismatchError(AgreementError):
    pass


@dataclass(frozen=True)
class HumanRow:
    scorer_id: str
    trial_id: str
    mobile_seconds: float | None   # None ⇔ unscoreable
    tail_climbing: bool
    unscoreable: bool
    note: str
    scored_at: str
    presentation_order: int


def _parse_bool(raw: str, ctx: str, errors: list[str]) -> bool | None:
    v = raw.strip().lower()
    if v in _TRUES:
        return True
    if v in _FALSES:
        return False
    errors.append(f"{ctx}: not a boolean: {raw!r}")
    return None


def parse_human_csv(text: str) -> tuple[list[HumanRow], list[str]]:
    """解析人工评分 CSV。结构性错误进 errors；能解析的行照常返回。"""
    errors: list[str] = []
    text = text.lstrip("﻿")  # BOM：utf-8-sig 之外的入口也保证幂等
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return [], ["empty CSV"]
    header = [h.strip().lstrip("﻿") for h in reader.fieldnames]
    if set(header) != set(HUMAN_CSV_COLUMNS):
        missing = [c for c in HUMAN_CSV_COLUMNS if c not in header]
        extra = [c for c in header if c not in HUMAN_CSV_COLUMNS]
        return [], [f"CSV header mismatch: missing={missing} extra={extra}"]
    rows: list[HumanRow] = []
    for i, rec in enumerate(reader, start=2):
        ctx = f"row {i}"
        scorer = (rec["scorer_id"] or "").strip()
        trial = (rec["trial_id"] or "").strip()
        if not scorer:
            errors.append(f"{ctx}: empty scorer_id")
        if not TRIAL_ID_RE.match(trial):
            errors.append(f"{ctx}: bad trial_id {trial!r} (want <video>-ch<N>)")
        tail = _parse_bool(rec["tail_climbing"] or "", f"{ctx}: tail_climbing", errors)
        uns = _parse_bool(rec["unscoreable"] or "", f"{ctx}: unscoreable", errors)
        if tail is None or uns is None:
            continue
        raw_mobile = (rec["mobile_seconds"] or "").strip()
        mobile: float | None = None
        if raw_mobile:
            try:
                mobile = float(raw_mobile)
            except ValueError:
                errors.append(f"{ctx}: mobile_seconds not a number: {raw_mobile!r}")
                continue
        if uns and raw_mobile:
            errors.append(f"{ctx}: unscoreable=True but mobile_seconds={raw_mobile!r}")
            continue
        if not uns and not raw_mobile:
            errors.append(f"{ctx}: unscoreable=False but mobile_seconds empty")
            continue
        sa = (rec["scored_at"] or "").strip()
        try:
            date.fromisoformat(sa)
        except ValueError:
            errors.append(f"{ctx}: scored_at not ISO date: {sa!r}")
        try:
            order = int((rec["presentation_order"] or "").strip())
            if order < 1:
                raise ValueError
        except ValueError:
            errors.append(f"{ctx}: presentation_order must be int >= 1")
            continue
        rows.append(HumanRow(scorer, trial, mobile, tail, uns,
                            (rec["note"] or "").strip(), sa, order))
    return rows, errors


def group_by_scorer(rows: Sequence[HumanRow]) -> dict[str, list[HumanRow]]:
    out: dict[str, list[HumanRow]] = {}
    for r in rows:
        out.setdefault(r.scorer_id, []).append(r)
    return out


def human_validation_errors(
    rows: Sequence[HumanRow],
    durations: Mapping[str, float],
) -> list[str]:
    """§2.1 全部跨行校验 + 越界拒收（不裁剪）。"""
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for r in rows:
        key = (r.scorer_id, r.trial_id)
        if key in seen:
            errors.append(f"duplicate (scorer_id,trial_id): {key}")
        seen.add(key)
        if r.mobile_seconds is not None and r.trial_id in durations:
            dur = durations[r.trial_id]
            # 错误消息保留原始越界值——证明是拒收，不是被悄悄裁回界内
            if not (0.0 <= r.mobile_seconds <= dur):
                errors.append(
                    f"{r.scorer_id}/{r.trial_id}: mobile_seconds={r.mobile_seconds} "
                    f"outside [0, {dur}] (rejected, not clipped)"
                )
    by = group_by_scorer(rows)
    if len(by) != 2:
        errors.append(f"expected exactly 2 scorer_ids, got {len(by)}: {sorted(by)}")
    else:
        a, b = sorted(by)
        sa, sb = {r.trial_id for r in by[a]}, {r.trial_id for r in by[b]}
        if sa != sb:
            errors.append(
                f"trial_id sets differ: only-{a}={sorted(sa - sb)} only-{b}={sorted(sb - sa)}"
            )
    for scorer, rws in by.items():
        orders = [r.presentation_order for r in rws]
        if sorted(orders) != list(range(1, len(rws) + 1)):
            errors.append(f"{scorer}: presentation_order must be 1..{len(rws)}, got {sorted(orders)}")
    return errors


def prefill_trace_errors(
    rows: Sequence[HumanRow],
    software_mobility: Mapping[str, float],
) -> list[str]:
    """§7：某评分员多个试次的 mobile 与软件 mobility 几乎逐位相同 ⇒ 疑似预填。"""
    errors: list[str] = []
    for scorer, rws in group_by_scorer(rows).items():
        ties = [
            r.trial_id for r in rws
            if r.mobile_seconds is not None and r.trial_id in software_mobility
            and abs(r.mobile_seconds - software_mobility[r.trial_id]) <= PREFILL_TIE_TOL_SEC
        ]
        if len(ties) > PREFILL_MAX_TIES:
            errors.append(
                f"{scorer}: {len(ties)} trials within ±{PREFILL_TIE_TOL_SEC}s of software "
                f"mobility {ties} — possible software-prefill anchoring; file rejected"
            )
    return errors


def load_human_csv(paths: Sequence[Path], durations: Mapping[str, float]) -> dict[str, list[HumanRow]]:
    """读多位评分员的 CSV（可一人一文件或多行混排），全量校验后一次抛错。"""
    all_rows: list[HumanRow] = []
    errors: list[str] = []
    for p in paths:
        rows, errs = parse_human_csv(Path(p).read_text(encoding="utf-8-sig"))
        all_rows.extend(rows)
        errors.extend(f"{Path(p).name}: {e}" for e in errs)
    errors.extend(human_validation_errors(all_rows, durations))
    if errors:
        raise AgreementError(errors)
    return group_by_scorer(all_rows)


# ---------------------------------------------------------------- 符号与窗口 §3

def human_immobility_seconds(mobile_seconds: float, trial_duration_sec: float) -> float:
    """§3.1：人工记 mobile，此处换算成 immobility。越界 raise，绝不裁剪。"""
    if not (0.0 <= mobile_seconds <= trial_duration_sec):
        raise AgreementError([
            f"mobile_seconds={mobile_seconds} outside [0, {trial_duration_sec}] "
            "(rejected, not clipped)"
        ])
    return trial_duration_sec - mobile_seconds


def assert_window_match(result: "TrialSoftware") -> None:
    """§3.2：TST 全程计分。窗口≠试次时长、或 ≠360s 全程窗 ⇒ 硬错误，绝不静默缩放。"""
    window = result.window_sec
    problems: list[str] = []
    if abs(window - result.duration_sec) > WINDOW_TOL_SEC:
        problems.append(
            f"{result.trial_id}: software window {window}s != trial duration "
            f"{result.duration_sec}s (TST scores the whole trial)"
        )
    if abs(window - TST_WINDOW_SEC) > WINDOW_TOL_SEC:
        problems.append(
            f"{result.trial_id}: scoring window {window}s != TST formal 360s; "
            "do NOT rescale — fix the window or use the FST convention"
        )
    if problems:
        raise WindowMismatchError(problems)


# ---------------------------------------------------------------- 软件侧 §2.2

_RESULT_REQUIRED = (
    "trial_id", "duration_sec", "immobility_seconds", "scoreable_frames",
    "total_frames", "unknown_fraction", "validity", "tail_climbing", "source",
    "parameters", "versions",
)
_VALIDITY_STATUSES = {"valid", "detached", "truncated_suspect", "unknown"}


@dataclass(frozen=True)
class TrialSoftware:
    trial_id: str
    duration_sec: float
    immobility_seconds: float
    scoreable_frames: int
    total_frames: int
    unknown_fraction: float
    validity: str
    tail_climbing: bool
    source: str
    parameters: Mapping[str, Any]
    versions: Mapping[str, Any]
    window_start_sec: float = 0.0
    window_end_sec: float | None = None

    @property
    def window_sec(self) -> float:
        end = self.window_end_sec if self.window_end_sec is not None else self.duration_sec
        return end - self.window_start_sec

    @property
    def mobility_seconds(self) -> float:
        return self.duration_sec - self.immobility_seconds

    @property
    def exclude(self) -> bool:
        return self.validity == "detached"

    @property
    def needs_repair(self) -> bool:
        return self.validity == "truncated_suspect"


def _trial_errors(trial: str, doc: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for k in _RESULT_REQUIRED:
        if k not in doc:
            errors.append(f"{trial}: missing field {k!r}")
    if errors:
        return errors
    if doc["duration_sec"] <= 0:
        errors.append(f"{trial}: duration_sec must be > 0")
    for sec_key in ("immobility_seconds",):
        v = doc[sec_key]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
            errors.append(f"{trial}: {sec_key} must be a non-negative number (seconds)")
        elif v > doc["duration_sec"] + 0.5:
            errors.append(f"{trial}: {sec_key}={v} exceeds duration {doc['duration_sec']}")
    if any(re.search(r"seconds?_frames|duration_frames", k) for k in doc):
        errors.append(f"{trial}: frame-valued time field violates the seconds unit invariant")
    if not (0 <= doc["scoreable_frames"] <= doc["total_frames"]):
        errors.append(f"{trial}: scoreable_frames outside [0, total_frames]")
    if not (0.0 <= doc["unknown_fraction"] <= 1.0):
        errors.append(f"{trial}: unknown_fraction outside [0,1]")
    if doc["validity"] not in _VALIDITY_STATUSES:
        errors.append(f"{trial}: validity {doc['validity']!r} not in {sorted(_VALIDITY_STATUSES)}")
    if not isinstance(doc["tail_climbing"], bool):
        errors.append(f"{trial}: tail_climbing must be true/false")
    if not isinstance(doc["parameters"], Mapping) or "theta_mob" not in doc["parameters"]:
        errors.append(f"{trial}: parameters snapshot with theta_mob is mandatory (§7)")
    if not isinstance(doc["versions"], Mapping):
        errors.append(f"{trial}: versions fingerprint is mandatory (§7)")
    return errors


def load_results(package_dir: Path) -> dict[str, TrialSoftware]:
    """读软件结果目录（每试次一个 <trial_id>.json），校验聚合一次抛错。"""
    pkg = Path(package_dir)
    files = sorted(pkg.glob("*.json"))
    if not files:
        raise AgreementError([f"no per-trial JSON files in {pkg}"])
    out: dict[str, TrialSoftware] = {}
    errors: list[str] = []
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            errors.append(f"{f.name}: invalid JSON: {e}")
            continue
        if not isinstance(doc, dict):
            errors.append(f"{f.name}: top level must be an object")
            continue
        trial = str(doc.get("trial_id") or f.stem)
        errs = _trial_errors(trial, doc)
        errors.extend(errs)
        if errs:
            continue
        out[trial] = TrialSoftware(
            trial_id=trial,
            duration_sec=float(doc["duration_sec"]),
            immobility_seconds=float(doc["immobility_seconds"]),
            scoreable_frames=int(doc["scoreable_frames"]),
            total_frames=int(doc["total_frames"]),
            unknown_fraction=float(doc["unknown_fraction"]),
            validity=str(doc["validity"]),
            tail_climbing=bool(doc["tail_climbing"]),
            source=str(doc["source"]),
            parameters=dict(doc["parameters"]),
            versions=dict(doc["versions"]),
            window_start_sec=float(doc.get("window_start_sec", 0.0)),
            window_end_sec=(float(doc["window_end_sec"]) if doc.get("window_end_sec") is not None else None),
        )
    if errors:
        raise AgreementError(errors)
    return out


# ---------------------------------------------------------------- 统计（纯 numpy 风格，math 手算）

def pearson_r(x: Sequence[float], y: Sequence[float]) -> float:
    xs, ys = list(map(float, x)), list(map(float, y))
    n = len(xs)
    if n < 3 or n != len(ys):
        raise AgreementError([f"pearson_r needs n>=3 equal-length pairs, got {n}"])
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    if sxx <= 1e-15 or syy <= 1e-15:
        raise AgreementError(["pearson_r degenerate: zero variance on a side"])
    return sxy / math.sqrt(sxx * syy)


def pearson_ci_fisher_z(r: float, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Fisher z 变换求 95% CI。z_{0.975}=1.959964 为常量（正态分位，非 t）。"""
    if n < 4:
        raise AgreementError([f"fisher CI needs n>=4, got {n}"])
    r = max(min(r, 0.999999), -0.999999)
    z = 0.5 * math.log((1 + r) / (1 - r))
    se = 1.0 / math.sqrt(n - 3)
    crit = 1.959963985 if abs(alpha - 0.05) < 1e-12 else _norm_ppf(1 - alpha / 2)
    lo, hi = z - crit * se, z + crit * se
    return _tanh_z(lo), _tanh_z(hi)


def _tanh_z(z: float) -> float:
    return math.tanh(z)


def _norm_ppf(p: float) -> float:
    # Acklam 逆正态近似，|误差|<1.15e-9，够用且零依赖
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447137054701157e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r_ = q * q
    return (((((a[0]*r_+a[1])*r_+a[2])*r_+a[3])*r_+a[4])*r_+a[5])*q / (((((b[0]*r_+b[1])*r_+b[2])*r_+b[3])*r_+b[4])*r_+1)


def _betacf(a: float, b: float, x: float) -> float:
    """Lentz 连分式（Numerical Recipes）。不收敛则 raise，绝不返回错值。"""
    tiny, eps, itmax = 1e-30, 3e-14, 300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            return h
    raise AgreementError([f"betacf failed to converge (a={a},b={b},x={x})"])


def _betai(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log(1 - x))
    bt = math.exp(lbeta)
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1 - x) / b


def student_t_two_sided_p(t: float, df: int) -> float:
    if df <= 0:
        raise AgreementError(["t p-value needs df>0"])
    return _betai(df / 2.0, 0.5, df / (df + t * t))


@dataclass(frozen=True)
class LinearFit:
    slope: float
    intercept: float
    se_slope: float | None
    t_stat: float | None
    p_value: float | None
    n: int


def linear_regression(x: Sequence[float], y: Sequence[float]) -> LinearFit:
    xs, ys = list(map(float, x)), list(map(float, y))
    n = len(xs)
    if n != len(ys) or n < 3:
        raise AgreementError([f"linear_regression needs n>=3 pairs, got {n}"])
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((a - mx) ** 2 for a in xs)
    if sxx <= 1e-15:
        return LinearFit(float("nan"), my, None, None, None, n)
    slope = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / sxx
    intercept = my - slope * mx
    resid = [b - (intercept + slope * a) for a, b in zip(xs, ys)]
    dof = n - 2
    s2 = sum(r * r for r in resid) / dof
    if s2 <= 1e-24:
        se = 0.0
        t_stat = math.inf if abs(slope) > 1e-24 else 0.0
        p = 0.0 if math.isinf(t_stat) else 1.0
    else:
        se = math.sqrt(s2 / sxx)
        t_stat = slope / se
        p = student_t_two_sided_p(t_stat, dof)
    return LinearFit(slope, intercept, se, t_stat, p, n)


@dataclass(frozen=True)
class BlandAltman:
    bias: float
    sd: float
    loa_low: float
    loa_high: float
    slope: float
    slope_p: float | None
    proportional_bias: bool


def bland_altman(diffs: Sequence[float], means: Sequence[float], alpha: float = 0.05) -> BlandAltman:
    ds, ms = list(map(float, diffs)), list(map(float, means))
    n = len(ds)
    if n != len(ms) or n < 3:
        raise AgreementError([f"bland_altman needs n>=3 pairs, got {n}"])
    bias = sum(ds) / n
    sd = math.sqrt(sum((d - bias) ** 2 for d in ds) / (n - 1))
    fit = linear_regression(ms, ds)
    p = fit.p_value
    return BlandAltman(
        bias=bias, sd=sd, loa_low=bias - 1.96 * sd, loa_high=bias + 1.96 * sd,
        slope=fit.slope, slope_p=p,
        proportional_bias=(p is not None and p < alpha),
    )


def icc_2_1(a: Sequence[float], b: Sequence[float]) -> float | None:
    """ICC(2,1)：双评分员、随机效应、单向匹配、绝对一致（ANOVA 平方和）。
    退化（分母≈0 / n<2）返回 None——宁可 N/A，不装 0。"""
    xs, ys = list(map(float, a)), list(map(float, b))
    n = len(xs)
    if n != len(ys) or n < 2:
        return None
    k = 2
    grand = (sum(xs) + sum(ys)) / (n * k)
    row_means = [(x + y) / 2 for x, y in zip(xs, ys)]
    ssr = k * sum((m - grand) ** 2 for m in row_means)          # rows (trials)
    col_means = [sum(xs) / n, sum(ys) / n]
    ssc = n * sum((c - grand) ** 2 for c in col_means)          # columns (raters)
    sst = sum((v - grand) ** 2 for v in xs + ys)
    sse = sst - ssr - ssc
    msr, msc, mse = ssr / (n - 1), ssc / (k - 1), sse / ((n - 1) * (k - 1))
    den = msr + (k - 1) * mse + k * (msc - mse) / n
    if den <= 1e-12:
        return None
    return (msr - mse) / den


@dataclass(frozen=True)
class Confusion2x2:
    tp: int
    fp: int
    tn: int
    fn: int

    @property
    def sensitivity(self) -> float | None:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) > 0 else None

    @property
    def specificity(self) -> float | None:
        return self.tn / (self.tn + self.fp) if (self.tn + self.fp) > 0 else None

    def as_dict(self) -> dict[str, Any]:
        return {"tp": self.tp, "fp": self.fp, "tn": self.tn, "fn": self.fn,
                "sensitivity": self.sensitivity, "specificity": self.specificity}


def confusion_from_bools(truth: Sequence[bool], predicted: Sequence[bool]) -> Confusion2x2:
    tp = fp = tn = fn = 0
    for t, p in zip(truth, predicted):
        if t and p:
            tp += 1
        elif t and not p:
            fn += 1
        elif not t and p:
            fp += 1
        else:
            tn += 1
    return Confusion2x2(tp, fp, tn, fn)


# ---------------------------------------------------------------- 引擎与报告 §4/§5

@dataclass(frozen=True)
class TrialRecord:
    trial_id: str
    duration_sec: float
    human_a: HumanRow
    human_b: HumanRow
    software: TrialSoftware
    immob_a: float | None
    immob_b: float | None
    ground_truth: float | None      # 两人均值（§4.1），任一不可评 ⇒ None
    diff: float | None              # software − ground_truth（符号约定！）
    single_tail_climbing: bool


def join_records(
    rows_by_scorer: Mapping[str, Sequence[HumanRow]],
    results: Mapping[str, TrialSoftware],
) -> list[TrialRecord]:
    (a, b), = _exactly_two(rows_by_scorer)
    by_a = {r.trial_id: r for r in rows_by_scorer[a]}
    by_b = {r.trial_id: r for r in rows_by_scorer[b]}
    problems: list[str] = []
    all_ids = sorted(set(by_a) | set(by_b) | set(results))
    records: list[TrialRecord] = []
    for tid in all_ids:
        ra, rb, so = by_a.get(tid), by_b.get(tid), results.get(tid)
        if not (ra and rb and so):
            problems.append(f"{tid}: missing side(s) "
                            f"{[n for n, v in ((a, ra), (b, rb), ('software', so)) if not v]}")
            continue
        dur = so.duration_sec
        assert_window_match(so)
        ia = None if ra.unscoreable else human_immobility_seconds(ra.mobile_seconds or 0.0, dur)
        ib = None if rb.unscoreable else human_immobility_seconds(rb.mobile_seconds or 0.0, dur)
        gt = (ia + ib) / 2 if (ia is not None and ib is not None) else None
        diff = so.immobility_seconds - gt if gt is not None else None
        records.append(TrialRecord(tid, dur, ra, rb, so, ia, ib, gt, diff,
                                   single_tail_climbing=(ra.tail_climbing != rb.tail_climbing)))
    if problems:
        raise AgreementError(problems)
    return records


def _exactly_two(rows_by_scorer: Mapping[str, Sequence[HumanRow]]) -> tuple[tuple[str, str],]:
    if len(rows_by_scorer) != 2:
        raise AgreementError([f"exactly two scorers required, got {sorted(rows_by_scorer)}"])
    return (tuple(sorted(rows_by_scorer)),)  # type: ignore[return-value]


@dataclass(frozen=True)
class ExclusionOutcome:
    kept: Sequence[TrialRecord]
    all_records: Sequence[TrialRecord]
    dropped: Sequence[tuple[TrialRecord, str]]
    review_list: Sequence[str]            # 单侧爬尾：保留但需复核
    needs_repair: Sequence[str]           # 截断 bug：保留并显著标出
    counts_by_reason: Mapping[str, int]


def apply_exclusions(records: Sequence[TrialRecord]) -> ExclusionOutcome:
    kept: list[TrialRecord] = []
    dropped: list[tuple[TrialRecord, str]] = []
    review: list[str] = []
    repair: list[str] = []
    for r in records:
        if r.software.needs_repair:
            repair.append(r.trial_id)      # §4.2：bug 不豁免、也不许"排除"绕过
        if r.human_a.unscoreable or r.human_b.unscoreable:
            dropped.append((r, "human_unscoreable"))
            continue
        if r.human_a.tail_climbing and r.human_b.tail_climbing:
            dropped.append((r, "human_tail_climbing_both"))
            continue
        if r.software.exclude:
            dropped.append((r, "software_validity_exclude"))
            continue
        if r.single_tail_climbing:
            review.append(r.trial_id)
        kept.append(r)
    counts: dict[str, int] = {}
    for _, reason in dropped:
        counts[reason] = counts.get(reason, 0) + 1
    return ExclusionOutcome(kept, tuple(records), dropped, review, repair, counts)


def _agreement_block(kept: Sequence[TrialRecord]) -> dict[str, Any]:
    pairs = [(r.diff, r.ground_truth) for r in kept if r.diff is not None and r.ground_truth is not None]
    if len(pairs) < 4:
        return {"n": len(pairs), "r": None, "ci95": None,
                "bland_altman": None, "note": "insufficient pairs (n<4)"}
    sw = [r.software.immobility_seconds for r in kept if r.diff is not None]
    gt = [r.ground_truth for r in kept if r.ground_truth is not None]
    diffs = [d for d, _ in pairs]
    means = [(s + g) / 2 for s, (d, g) in zip(sw, pairs)]
    r = pearson_r(gt, sw)
    lo, hi = pearson_ci_fisher_z(r, len(pairs))
    ba = bland_altman(diffs, means)
    return {"n": len(pairs), "r": r, "ci95": [lo, hi],
            "bland_altman": {"bias": ba.bias, "sd": ba.sd, "loa": [ba.loa_low, ba.loa_high],
                             "slope": ba.slope, "slope_p": ba.slope_p,
                             "proportional_bias": ba.proportional_bias}}


def evaluate_gates(report: Mapping[str, Any], pending_reason: str | None) -> dict[str, dict[str, Any]]:
    gates: dict[str, dict[str, Any]] = {}

    def g(key: str, criterion: str, observed: Any, ok: bool | None) -> None:
        if pending_reason or ok is None:
            status = "pending_data"
        else:
            status = "pass" if ok else "fail"
        gates[key] = {"criterion": criterion, "observed": observed, "status": status,
                      **({"pending_reason": pending_reason} if pending_reason else {})}

    post = report["software_vs_truth"]
    if pending_reason:
        g("V1", "Pearson r >= 0.95", None, None)
        g("V2", "BA |bias| < 5% window & no proportional bias", None, None)
        g("V3", "inter-rater ICC >= 0.90", None, None)
        g("V4", "tail-climbing recall >= 0.95", None, None)
        g("V5", "detached sensitivity >= 0.90 and FP == 0", None, None)
        g("V6", "needs_repair trials == 0", None, None)
        g("V7", "post-exclusion n >= 25", None, None)
        return gates

    r = post.get("r")
    g("V1", "Pearson r >= 0.95", r, r is not None and r >= GATE_R)
    ba = post.get("bland_altman")
    dur = report.get("trial_duration_sec", TST_WINDOW_SEC)
    bias_ok = None if ba is None else (abs(ba["bias"]) < GATE_BIAS_FRAC * dur and not ba["proportional_bias"])
    g("V2", f"BA |bias| < {GATE_BIAS_FRAC:.0%} of window ({GATE_BIAS_FRAC*dur:g}s) & no proportional bias",
      None if ba is None else {"bias": ba["bias"], "proportional_bias": ba["proportional_bias"]}, bias_ok)
    icc = report["inter_rater"].get("icc_2_1")
    g("V3", "inter-rater ICC >= 0.90", icc, None if icc is None else icc >= GATE_ICC)
    tail = report["events_2x2"]["tail_climbing"]
    rec = tail.get("sensitivity")
    # 零人工阳性时分母为空：pending_data（无从评估），不装 pass 也不装 fail
    g("V4", "tail-climbing recall >= 0.95", rec, None if rec is None else rec >= GATE_TAIL_RECALL)
    det = report["events_2x2"]["detached"]
    sens = det.get("sensitivity")
    g("V5", "detached sensitivity >= 0.90 and FP == 0",
      {"sensitivity": sens, "false_positives": det["fp"]},
      None if sens is None else (sens >= GATE_DETACHED_SENS and det["fp"] == 0))
    n_repair = len(report["exclusions"]["needs_repair"])
    g("V6", "needs_repair trials == 0", n_repair, n_repair == 0)
    n_post = post.get("n", 0)
    g("V7", "post-exclusion n >= 25", n_post, n_post >= GATE_MIN_N)
    return gates


def build_report(
    package_dir: Path,
    human_paths: Sequence[Path],
    *,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """主入口：加载→校验→换算→排除→统计→判据→报告 dict。"""
    results = load_results(package_dir)
    durations = {t: r.duration_sec for t, r in results.items()}
    rows = load_human_csv(human_paths, durations)
    flat = [r for rws in rows.values() for r in rws]
    software_mobility = {t: r.duration_sec - r.immobility_seconds for t, r in results.items()}
    prefill = prefill_trace_errors(flat, software_mobility)
    if prefill:
        raise AgreementError(prefill)

    records = join_records(rows, results)
    excl = apply_exclusions(records)
    (a, b) = _exactly_two(rows)[0]

    # §5.3 评分员间一致性（用全部双侧可评的试次）
    both = [(r.immob_a, r.immob_b) for r in records
            if r.immob_a is not None and r.immob_b is not None]
    inter: dict[str, Any] = {"n": len(both)}
    if len(both) >= 2:
        va = [x for x, _ in both]
        vb = [y for _, y in both]
        inter["icc_2_1"] = icc_2_1(va, vb)
        inter["pearson_r"] = pearson_r(va, vb) if len(both) >= 3 else None
        d = [x - y for x, y in both]
        inter["diff_mean"] = sum(d) / len(d)
        inter["diff_sd"] = math.sqrt(sum((x - inter["diff_mean"]) ** 2 for x in d) / (len(d) - 1)) if len(d) > 1 else None
    else:
        inter["icc_2_1"] = None

    top_warning: str | None = None
    post = _agreement_block(excl.kept)
    pre = _agreement_block([r for r in records if r.diff is not None])
    if (post.get("r") is not None and inter.get("pearson_r") is not None
            and abs(inter["pearson_r"]) < abs(post["r"])):
        top_warning = ("TOP WARNING: 评分员间一致性低于软件vs人工均值一致性——"
                       "真值本身不稳，主指标 V1 不可采信；先解决人工评分质量。")

    # §5.4 混淆矩阵（人工真值 = 任一评分员阳性，召回优先口径）
    tail_h = [(r.human_a.tail_climbing or r.human_b.tail_climbing) for r in records]
    tail_s = [r.software.tail_climbing for r in records]
    det_h = [(r.human_a.unscoreable or r.human_b.unscoreable) for r in records]
    det_s = [r.software.exclude for r in records]

    # §5.5 分母分布
    fracs_scoreable = sorted(r.software.scoreable_frames / r.software.total_frames for r in records)
    fracs_unknown = sorted(r.software.unknown_fraction for r in records)

    def dist(vals: Sequence[float]) -> dict[str, Any]:
        if not vals:
            return {"n": 0}
        return {"n": len(vals), "min": vals[0], "median": vals[len(vals) // 2], "max": vals[-1]}

    # §5.6 顺序效应
    order_effects: dict[str, Any] = {}
    for scorer, rws in rows.items():
        resid, order = [], []
        for r in rws:
            rec = next((x for x in records if x.trial_id == r.trial_id), None)
            if rec is None or rec.diff is None or r.mobile_seconds is None:
                continue
            human_im = human_immobility_seconds(r.mobile_seconds, rec.duration_sec)
            resid.append(human_im - rec.ground_truth if rec.ground_truth is not None else 0.0)
            order.append(r.presentation_order)
        if len(resid) >= 5:
            fit = linear_regression(order, resid)
            order_effects[scorer] = {"n": len(resid), "slope_sec_per_order": fit.slope,
                                     "slope_p": fit.p_value,
                                     "significant_drift": (fit.p_value is not None and fit.p_value < alpha)}
        else:
            order_effects[scorer] = {"n": len(resid), "note": "insufficient trials for drift test"}

    sources = sorted({r.software.source for r in records})
    pending_reason = None
    if sources != ["software_pipeline"]:
        pending_reason = (f"software result source(s) {sources} != ['software_pipeline'] — "
                          "derived/placeholder data cannot pass acceptance gates")

    per_trial = []
    for r in records:
        flags = []
        if r.trial_id in excl.needs_repair:
            flags.append("NEEDS_REPAIR(software bug, kept)")
        if r.single_tail_climbing:
            flags.append("single-rater tail_climbing (kept, review)")
        for tid, reason in excl.dropped:
            if tid == r.trial_id:
                flags.append(f"EXCLUDED:{reason}")
        per_trial.append({
            "trial_id": r.trial_id,
            "human_a": r.immob_a, "human_b": r.immob_b,
            "human_mean": r.ground_truth,
            "software": r.software.immobility_seconds,
            "diff": r.diff,
            "scoreable_fraction": r.software.scoreable_frames / r.software.total_frames,
            "unknown_fraction": r.software.unknown_fraction,
            "validity": r.software.validity,
            "flags": flags,
        })

    report: dict[str, Any] = {
        "format": REPORT_FORMAT,
        "assay": "TST",
        "top_warning": top_warning,
        "scorers": sorted(rows),
        "trial_duration_sec": records[0].duration_sec if records else None,
        "per_trial": per_trial,
        "software_vs_truth": post,
        "software_vs_truth_pre_exclusion": pre,
        "inter_rater": inter,
        "events_2x2": {
            "tail_climbing": confusion_from_bools(tail_h, tail_s).as_dict(),
            "detached": confusion_from_bools(det_h, det_s).as_dict(),
        },
        "denominators": {
            "scoreable_fraction": dist(fracs_scoreable),
            "unknown_fraction": dist(fracs_unknown),
        },
        "order_effects": order_effects,
        "exclusions": {
            "n_before": len(records), "n_after": len(excl.kept),
            "counts_by_reason": dict(excl.counts_by_reason),
            "dropped": [[r.trial_id, why] for r, why in excl.dropped],
            "review_list": list(excl.review_list),
            "needs_repair": list(excl.needs_repair),
        },
        "gates": {},  # filled below
        "run_manifest": capture_manifest(package_dir, human_paths),
        "limitations": "本报告的 V1–V7 判据仅在 source=software_pipeline 且人工双标数据"
                       "真实到位后才有判定力；pending_data 不等于通过。",
    }
    report["gates"] = evaluate_gates(report, pending_reason)
    return report


# ---------------------------------------------------------------- §7 运行指纹

def capture_manifest(package_dir: Path, human_paths: Sequence[Path]) -> dict[str, Any]:
    import hashlib
    import platform
    import subprocess
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent.parent,
                             capture_output=True, text=True, timeout=10).stdout.strip() or "unknown"
    except Exception:
        sha = "unknown"

    def fsha(p: Path) -> str:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()

    results_dir = Path(package_dir)
    params: dict[str, Any] = {}
    for f in sorted(results_dir.glob("*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and isinstance(doc.get("parameters"), Mapping):
            params = dict(doc["parameters"])
            break
    inputs = []
    for p in list(human_paths) + sorted(results_dir.glob("*.json")):
        pp = Path(p)
        if pp.is_file():
            inputs.append({"path_basename": pp.name, "sha256": fsha(pp)})
    try:
        import numpy
        numpy_version = numpy.__version__
    except Exception:
        numpy_version = "unknown"
    return {
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "numpy": numpy_version,
        "code_git_sha": sha,
        "parameters_snapshot": params,
        "parameters_note": "θ_mob 等全部 bout 参数为 FROZEN provisional；此快照仅记录，"
                           "本工具任何输出一律不得回调参数（spec §7 铁律）。",
        "inputs": inputs,
    }


# ---------------------------------------------------------------- Markdown 渲染

def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def render_markdown(report: Mapping[str, Any]) -> str:
    lines: list[str] = ["# 人工评分比对与验收报告（TST v1）", ""]
    if report.get("top_warning"):
        lines += [f"> **{report['top_warning']}**", ""]
    lines += [f"- 评分员：{_fmt(report.get('scorers'))}",
              f"- 试次时长：{_fmt(report.get('trial_duration_sec'))} s",
              f"- 排除前 n={report['exclusions']['n_before']}，排除后 n={report['exclusions']['n_after']}", ""]

    nr = report["exclusions"]["needs_repair"]
    if nr:
        lines += [f"## ⚠ needs_repair（软件 bug，未排除、必须修复后重跑）：{nr}", ""]

    lines += ["## §5.1 逐试次对照表", "",
              "| trial | 人工A | 人工B | 均值 | 软件 | 差值 | 可评帧占比 | unknown | validity | 标记 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for row in report["per_trial"]:
        lines.append("| " + " | ".join([
            row["trial_id"], _fmt(row["human_a"]), _fmt(row["human_b"]),
            _fmt(row["human_mean"]), _fmt(row["software"]), _fmt(row["diff"]),
            _fmt(row["scoreable_fraction"]), _fmt(row["unknown_fraction"]),
            row["validity"], "; ".join(row["flags"]) or "",
        ]) + " |")

    for label, key in (("排除后（主指标）", "software_vs_truth"),
                       ("排除前（对照）", "software_vs_truth_pre_exclusion")):
        blk = report[key]
        lines += [f"## §5.2 {label}", ""]
        lines.append(f"- n = {blk.get('n')}")
        if blk.get("r") is None:
            lines.append(f"- 不可计算：{blk.get('note', 'n/a')}")
        else:
            lo, hi = blk["ci95"]
            ba = blk["bland_altman"]
            lines += [f"- Pearson r = {_fmt(blk['r'])}（95% CI [{_fmt(lo)}, {_fmt(hi)}]）",
                      f"- Bland-Altman：bias = {_fmt(ba['bias'])} s，LoA = [{_fmt(ba['loa'][0])}, {_fmt(ba['loa'][1])}] s",
                      f"- 比例性偏差：slope = {_fmt(ba['slope'])}，p = {_fmt(ba['slope_p'])} → "
                      + ("**显著，存在系统性比例偏差**" if ba["proportional_bias"] else "不显著")]

    it = report["inter_rater"]
    lines += ["## §5.3 评分员间一致性", "",
              f"- n = {it.get('n')}，ICC(2,1) = {_fmt(it.get('icc_2_1'))}，Pearson r = {_fmt(it.get('pearson_r'))}",
              f"- 两人差值：mean = {_fmt(it.get('diff_mean'))} s，SD = {_fmt(it.get('diff_sd'))} s", ""]

    lines += ["## §5.4 事件 2×2（人工真值=任一评分员阳性；爬尾召回优先）", ""]
    for name, cn in (("tail_climbing", "尾巴攀爬"), ("detached", "脱落/无法评分")):
        c = report["events_2x2"][name]
        lines += [f"- **{cn}**：TP={c['tp']} FP={c['fp']} TN={c['tn']} FN={c['fn']}，"
                  f"敏感性={_fmt(c['sensitivity'])}，特异性={_fmt(c['specificity'])}"]
    lines.append("")

    dn = report["denominators"]
    lines += ["## §5.5 分母（不可省略）", "",
              f"- 可评分帧占比：{_fmt(dn['scoreable_fraction'])}",
              f"- unknown 占比：{_fmt(dn['unknown_fraction'])}", "",
              "## §5.6 顺序效应", ""]
    for scorer, blk in report["order_effects"].items():
        if "slope_p" in blk:
            lines.append(f"- {scorer}：slope={_fmt(blk['slope_sec_per_order'])} s/序号，"
                         f"p={_fmt(blk['slope_p'])}"
                         + ("（**显著漂移**）" if blk["significant_drift"] else ""))
        else:
            lines.append(f"- {scorer}：{blk.get('note', 'n/a')}（n={blk.get('n')}）")
    lines.append("")

    lines += ["## §6 验收判据", "", "| 判据 | 门槛 | 实测 | 状态 |", "|---|---|---|---|"]
    for k in ("V1", "V2", "V3", "V4", "V5", "V6", "V7"):
        v = report["gates"].get(k, {})
        lines.append(f"| {k} | {v.get('criterion','')} | {_fmt(v.get('observed'))} | **{v.get('status','?')}** |")
    excl = report["exclusions"]
    lines += ["", f"排除明细：{dict(excl['counts_by_reason'])}；"
                  f"需复核（单侧爬尾）：{excl['review_list'] or '无'}", ""]
    rm = report["run_manifest"]
    lines += [f"运行指纹：git={rm['code_git_sha']}，参数快照 θ_mob="
              f"{_fmt(rm['parameters_snapshot'].get('theta_mob'))}（FROZEN provisional，只记录不回写）", ""]
    lines += [f"> {report['limitations']}"]
    return "\n".join(lines)


def dump_report_json(report: Mapping[str, Any]) -> str:
    """json 落盘前清 NaN/inf —— 任何退化统计量必须是 null，不许是裸 nan。"""
    def clean(x: Any) -> Any:
        if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
            return None
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [clean(v) for v in x]
        return x
    return json.dumps(clean(report), ensure_ascii=False, indent=2, allow_nan=False)
