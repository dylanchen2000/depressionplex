"""试次级有效性：从未有动物 / 脱落 / 截断 / 有效（TST 产品特性，CSI 两份手册均无）。

TST 金标准（Can et al. 2012）要求排除脱落/攀爬等动物——自动检测脱落因此是
卖点而非副产品。本模块把"这隔间里到底有没有过动物"提升为**试次级判据**，
不埋在分割失败原因里。

核心判据（评审定稿，2026-08-24；DP-031 拆分 2026-09-04）：全片标定帧里该
隔间是否**存在过身体级面积**（≥ body_frac × 同批其他隔间面积中位数），以及
是否**存在过任何动物级掩膜**（≥ presence_frac × 身体门槛，相对量⇒标度不变）。

- 从未有动物级掩膜 ⇒ `never_occupied`：布置/录制问题（动物从头到尾没进过
  这个隔间，如 DP-028 的 20mg_3周-ch4）。处置与 detached
  相同（建议排除），**但科学含义与判据都不同，不得混报**：空场不是实验失败，
  拿它冒充"脱落检测已验证"是虚假验收（G10 拆分见 DP-034，门槛写死不许合并）。
- **标定帧全部分割失败 ⇒ `unknown`，永不 `never_occupied`（DP-032，2026-09-06）。**
  `None` 是"这一帧没看成"，不是"这一帧没有动物"。旧代码里两者走同一条路
  （占比分母是全部标定帧、`None` 只算不命中 ⇒ 占比 0 ⇒ never_occupied），
  于是软件在**从未成功观测该隔间**的情况下断言了"这里从来没有动物"——
  `30mg 2周` 隔间 4 就这样被静默丢掉，那只鼠至今没被任何人评过分。
  实测更硬的一条：真空的 `20mg_3周-ch4` 与它的失败签名**逐字相同**
  （同样走廊未收口、同样 `animal_in_corridor`、同样全 None）⇒ 两者无法区分
  ⇒ 不许下判。配套：`occupied_fraction` 的分母改为**分割成功的帧数**；
  每个隔间记 `unsegmentable_fraction`，"多少帧根本没看见"不再静默。
  代价要如实报：G10a 的唯一正样本走的是同一条错误路径，所以**自动识别空隔间
  在本数据集变为不可测**（与 G10b 同状态），不得声称已验证。
- 有尾级掩膜但从未身体级 ⇒ `detached`（未悬挂/悬挂失效）；或前半有身体、
  后半整段无动物级掩膜 ⇒ `detached`（**中途脱落**——实验失败，须上报）。
- 存在过、但当前分析只给出尾级面积 ⇒ `truncated_suspect`：**这是 bug**
  （分割把活鼠截断成尾块），必须上报修复，绝不允许自动贴上"脱落"标签
  静默丢弃——"说得通的叙事盖住 bug"正是本项目反复撞到的失败模式。
- 其余 ⇒ `valid`。

**空场判据（DP-028 N1–N3）**：被排除的隔间**不得输出任何 immobility 数字**——
空隔间被判 `immobility = 全窗口`（如 360 s）等于伪造最强抑郁表型、凭空造药效，
是 GLP 下最不能有的失败模式。所有评分必须过 `score_gate()` 唯一入口：
排除态 ⇒ 不放行且**幻影候选值出现即报警，不许静默丢弃**；"有动物"帧占比
（`occupied_fraction`，对 never_occupied）必须**恰等于 0**，不是"很低"。

判据只依赖面积量级，逐帧分割细节变了也稳定；参考量取**其他**隔间的中位数，
脱落隔间自身不参与定标。

**相对判据的已知边界与常开下界**（评审加固，2026-08-25）：全试次所有隔间
都只有尾级面积时（整批脱落），相对判据自洽地"认为"尾级就是身体级——不可决；
3/4 脱落时 median_others 也会退化。故 `body_area_prior`（硬件规格级绝对
先验，如按规格分辨率与预期 BL 推出的最小身体面积）是**常开下界**而非兜底：
threshold = max(0.5×median_others, prior)，任意脱落数量下成立。不给先验时
整批尾级边界保持不可决（调用方自行承担），probe_frames 以
--body-area-prior 显式传入。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

STATUS_VALID = "valid"
STATUS_DETACHED = "detached"
STATUS_NEVER_OCCUPIED = "never_occupied"   # DP-031：从未有动物（处置同 detached，含义不同）
STATUS_TRUNCATED = "truncated_suspect"
STATUS_UNKNOWN = "unknown"  # 参考量不可估（全试次都无身体级面积等）

#: 排除态：金标准口径下都不产出统计量。拆分是为了**报告不许合并**（G10a/b）。
EXCLUDED_STATUSES = (STATUS_DETACHED, STATUS_NEVER_OCCUPIED)

# 与分割 min_area 同量级的下限：小于它的"面积"视为噪声，不进中位数。
_AREA_FLOOR = 20.0

#: "动物在场"的最低证据 = presence_frac × 身体门槛（**相对量**——不是像素常数，
#: 全片面积 ×4 判定不变；见 test_verdicts_invariant_to_area_scale）。
#: 1–5 px 的尾链 ÷ 门槛 ~90 px ≈ 0.05 < 0.15 ⇒ 不算在场 ⇒ never_occupied；
#: ~40 px 的尾级悬挂 ≈ 0.4 ≥ 0.15 ⇒ 在场但从未身体级 ⇒ detached（悬挂失效）。
_PRESENCE_FRAC = 0.15


@dataclass(frozen=True)
class ChamberValidity:
    chamber: int
    status: str
    max_area: float          # 标定帧里观测到的最大动物级掩膜面积
    ref_body_area: float     # 其他隔间面积中位数（参考量）
    body_threshold: float    # body_frac × ref_body_area
    ever_had_body: bool
    note: str
    #: 判为"有动物在场"的标定帧占比（≥ presence_frac×门槛 计为在场）。
    #: never_occupied 必须**恰为 0.0**（N2：不是"很低"，是 0）。
    #: 参考量不可估（thr≤0）时为 None——不许拿伪占比冒充可判。
    occupied_fraction: float | None = None
    #: 分割失败（无掩膜）帧占比（DP-032）。**1.0 = 整段隔间一帧都没看成**，
    #: 此时不得判 never_occupied——那是"没有证据"，不是"没有动物"。
    #: None = 剖面为空，不可估（不许记 0）。
    unsegmentable_fraction: float | None = None


@dataclass(frozen=True)
class TrialValidity:
    chambers: tuple[ChamberValidity, ...]

    @property
    def exclude(self) -> tuple[int, ...]:
        """金标准口径建议排除的隔间：真实脱落 + 从未有动物（处置相同）。"""
        return tuple(
            c.chamber for c in self.chambers if c.status in EXCLUDED_STATUSES
        )

    @property
    def never_occupied(self) -> tuple[int, ...]:
        """布置/录制问题（空隔间）——G10a 的正样本口径，不与脱落混报。"""
        return tuple(
            c.chamber for c in self.chambers
            if c.status == STATUS_NEVER_OCCUPIED
        )

    @property
    def detached(self) -> tuple[int, ...]:
        """脱落/悬挂失效——G10b 的正样本口径，不与空场混报。"""
        return tuple(
            c.chamber for c in self.chambers if c.status == STATUS_DETACHED
        )

    @property
    def needs_repair(self) -> tuple[int, ...]:
        """疑似截断 bug 的隔间：不得入统计，先修分割。"""
        return tuple(
            c.chamber for c in self.chambers if c.status == STATUS_TRUNCATED
        )


def _areas(profile: list[float | None]) -> np.ndarray:
    vals = [float(a) for a in profile if a is not None and not np.isnan(a)]
    vals = [v for v in vals if v >= _AREA_FLOOR]
    return np.asarray(vals, dtype=float)


def _usable(profile: list[float | None]) -> int:
    """分割成功、有面积可读的帧数。

    `None`/`NaN` 是**"这一帧没看成"**，不是"这一帧面积为 0"——DP-032 的整个
    缺陷就在于这两件事被混为一谈。凡是要拿占比下结论的地方，分母都得用这个。
    """
    return sum(1 for v in profile if v is not None and not np.isnan(float(v)))


def _unseg_frac(profile: list[float | None]) -> float | None:
    """分割失败帧占比。剖面为空 ⇒ None（不可估，不许记 0）。"""
    if not profile:
        return None
    return 1.0 - _usable(profile) / len(profile)


def assess_trial_validity(
    calib_areas: dict[int, list[float | None]],
    current_areas: dict[int, list[float | None]] | None = None,
    *,
    body_frac: float = 0.5,
    body_area_prior: float | None = None,
) -> TrialValidity:
    """逐隔间判定试次有效性。

    `calib_areas`：全片均匀标定帧上、走廊路径分割出的动物掩膜面积（无掩膜
    记 None）。`current_areas`：当前分析批次的面积；给了才能识别截断（标定
    有过身体、当前却只有尾级面积）。
    """
    meds: dict[int, float] = {}
    maxes: dict[int, float] = {}
    for k, v in calib_areas.items():
        a = _areas(v)
        meds[k] = float(np.median(a)) if a.size else 0.0
        maxes[k] = float(a.max()) if a.size else 0.0

    # 两遍法：尾级面积会污染参考量。先用全局中位数粗判"有身体"的隔间，
    # 参考量再只取其他**有身体**隔间的中位数。
    pos = [m for m in meds.values() if m > 0]
    body_ids = (
        [k for k, m in meds.items() if m >= body_frac * float(np.median(pos))]
        if pos
        else []
    )

    # 先验是**常开下界**（评审加固，2026-08-25）：threshold = max(0.5×median_others,
    # prior)。相对基准在任意脱落数量下都可能退化（3/4 脱落时 median_others 可为
    # 尾级/0），常开下界使判据对脱落数量鲁棒，不必枚举"几个脱落"的分支。
    prior = body_area_prior or 0.0

    def _present_frac(profile: list[float | None], t: float) -> float:
        """"有动物在场"帧占比：raw ≥ _PRESENCE_FRAC×t（纯相对量 ⇒ 标度不变）。

        **分母是分割成功的帧，不是全部标定帧（DP-032）。**分割失败的帧是
        "没看到"，把它算进分母等于把"看不见"记成"看见了、没有动物"——
        DP-032 里 27/50 帧分割失败的隔间就因此被稀释成占比 0。
        """
        n = _usable(profile)
        if t <= 0 or n == 0:
            raise ValueError("present_frac 只在门槛>0 且有分割成功的标定帧时可算")
        hit = sum(1 for v in profile
                  if v is not None and not np.isnan(v) and v >= _PRESENCE_FRAC * t)
        return hit / n

    out: list[ChamberValidity] = []
    for k in sorted(calib_areas):
        others = [meds[j] for j in body_ids if j != k and meds[j] > 0]
        ref = float(np.median(others)) if others else 0.0
        thr = max(body_frac * ref, prior)
        if thr <= 0:
            if k in body_ids and meds[k] > 0:
                # 唯一有身体的隔间且无先验：以自身为参考（相对项）。
                ref = meds[k]
                thr = body_frac * ref
                out.append(ChamberValidity(
                    k, STATUS_VALID, maxes[k], ref, thr, True,
                    "仅自身有身体级面积，参考取自身",
                    _present_frac(calib_areas[k], thr),
                ))
                continue
            out.append(ChamberValidity(
                k, STATUS_UNKNOWN, maxes[k], 0.0, 0.0, False,
                "无有身体的隔间可作参考且无先验，参考量不可估",
                None,
            ))
            continue
        prof = calib_areas[k]
        if _usable(prof) == 0:
            # DP-032：整段隔间一帧都没分割成功 ⇒ **没有证据**，不是"没有动物"。
            # 实测路径：走廊未收口(band_unsealed) + animal_in_corridor ⇒ 整段
            # 不可分割 ⇒ 面积全 None。把它记成 never_occupied 等于静默丢掉一只
            # 真动物（`30mg 2周` 隔间 4 已真实发生，那只鼠至今没被任何人评过），
            # 而且与真空场（`20mg_3周-ch4`）的签名逐字相同 ⇒ 无法区分 ⇒ 不许下判。
            out.append(ChamberValidity(
                k, STATUS_UNKNOWN, maxes[k], ref, thr, False,
                "标定帧全部分割失败（无掩膜可用）：在场与否不可判——"
                "不得判为空隔间（DP-032）",
                None,
            ))
            continue
        frac = _present_frac(prof, thr)
        ever = maxes[k] >= thr
        if not ever:
            if frac == 0.0:
                # N2：占比是**恰好的 0**，不是"很低"。尾链 1–5 px ÷ 门槛 ~90 px
                # ≈ 0.05 < presence_frac ⇒ 判为从未有动物（布置/录制问题）。
                out.append(ChamberValidity(
                    k, STATUS_NEVER_OCCUPIED, maxes[k], ref, thr, False,
                    "标定帧里从未出现任何动物级掩膜：该隔间从头到尾没有动物"
                    "（布置/录制问题，非实验失败）——处置同脱落建议排除，"
                    "但不得并入 G10b 脱落口径",
                    0.0,
                ))
            else:
                out.append(ChamberValidity(
                    k, STATUS_DETACHED, maxes[k], ref, thr, False,
                    "有尾级动物掩膜但全片从未身体级：未悬挂/悬挂失效，建议排除",
                    frac,
                ))
            continue
        # 中途脱落检测：标定帧按时间顺序抽——前半有过身体、后半整段无动物级
        # 掩膜 ⇒ 脱落（实验失败，须上报）。后半不足 2 帧不下此判（证据不够）。
        n = len(prof)
        if n >= 4:
            half = n // 2
            late_absent = all(
                v is None or np.isnan(v) or v < _PRESENCE_FRAC * thr
                for v in prof[half:])
            first_body = any(
                v is not None and not np.isnan(v) and v >= thr for v in prof[:half])
            if late_absent and first_body:
                out.append(ChamberValidity(
                    k, STATUS_DETACHED, maxes[k], ref, thr, True,
                    f"标定帧前半（{half} 帧）有过身体级面积、后半整段无动物级掩膜"
                    "⇒ 疑似中途脱落：实验失败须上报（G10b 口径）",
                    frac,
                ))
                continue
        if current_areas is not None:
            cur = _areas(current_areas.get(k, []))
            if cur.size and float(np.median(cur)) < thr:
                out.append(ChamberValidity(
                    k, STATUS_TRUNCATED, maxes[k], ref, thr, True,
                    "标定帧存在过身体，当前分析仅尾级面积：疑似截断 bug，"
                    "不得按脱落处理",
                    frac,
                ))
                continue
        out.append(ChamberValidity(
            k, STATUS_VALID, maxes[k], ref, thr, True, "", frac
        ))
    # DP-032：把"这个隔间有多少帧我们根本没看见"补进每条记录，不再静默丢失。
    return TrialValidity(tuple(
        replace(cv, unsegmentable_fraction=_unseg_frac(calib_areas[cv.chamber]))
        for cv in out
    ))


def score_gate(cv: ChamberValidity,
               candidate_immobility_s: float | None = None,
               ) -> tuple[bool, tuple[str, ...]]:
    """**任何隔间要产出 immobility 数字，必须过这道闸**（DP-028 N1/N3）。

    返回 (放行, 消息)。规则：
    - `never_occupied` / `detached`（排除态）、`unknown`、`truncated_suspect`
      ⇒ **不放行，immobility 数字不过滤不出来**——空场被判 `immobility=360 s`
      等于伪造最强抑郁表型，GLP 下最不能有。
    - 对不放行的隔间仍递来候选数字 ⇒ **报警**（`[PHANTOM-IMMOBILITY]`），
      幻影必须响，不许静默丢弃——静默= bug 可以在阴影里活着。
    """
    if cv.status == STATUS_VALID:
        return True, ()
    msgs = [f"隔间{cv.chamber} 状态 {cv.status!r} 被排除，不产出 immobility"
            + (f"（在场帧占比 {cv.occupied_fraction}）"
               if cv.occupied_fraction is not None else "")
            + f"：{cv.note}"]
    if candidate_immobility_s is not None:
        msgs.insert(0,
                    f"[PHANTOM-IMMOBILITY 报警] 隔间{cv.chamber}（{cv.status}）"
                    f"被流水线判出 immobility={candidate_immobility_s:.1f} s——"
                    "排除态隔间的一切数值都是幻影，已拦截但必须上报，不得静默")
    return False, tuple(msgs)
