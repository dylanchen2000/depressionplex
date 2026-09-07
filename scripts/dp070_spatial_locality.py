#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-070：不动帧那几个变化的像素**挤在一处**还是**沿整条轮廓铺开**？

**这是 DP-068 → DP-069 逼出来的最后一条判别。** 已经知道：变化是**相干的**
（DP-068，r4/r1 = 3–9）、且**大体保面积**（DP-069，excess ≈ 1）。剩两个候选都保面积，
面积通道分不开它们，只剩**空间位置**这一条：

| 候选 | XOR 像素的空间分布 | 含义 |
|---|---|---|
| **动物某个部位在小幅动**（爪、头、尾根） | **挤在一处** | 口径分歧，人工没把它算作活动 |
| **整个剪影缓慢位移**（掩膜漂移 或 被动摆动） | **沿轮廓铺开**（前后缘各一条） | 刚体补偿没做干净 |

**判别量：XOR 像素之间的最大两点距离 ÷ BL。** 挤在一处 ⇒ 几个像素、距离 1–3 px ⇒
比值 0.05 量级；沿轮廓铺开 ⇒ 距离接近剪影直径 ⇒ 比值 0.5–1 量级。**两个候选相差一个
数量级，所以这条在 N 只有 2–7 px 时依然管用**（DP-069 那条判别量恰恰是被小 N 拖垮的）。

**零假设是逐帧算出来的，不是拍的。** 「沿轮廓铺开」到底该给多大的距离，取决于该帧
剪影的形状 ⇒ 所以对每一帧，**从它自己的轮廓上均匀抽同样多的像素**，算同一个统计量，
重复若干次取中位数作为该帧的零假设。报出的是**超出量**：

    locality = 实测最大两点距离 ÷ 该帧「沿轮廓均匀」的零假设

**≤0.4 ⇒ 挤在一处（候选①）；≥0.8 ⇒ 与沿轮廓铺开一致（候选②）；中间 ⇒ 分不出。**

**同样不碰 `rad` 的刚体拟合**（用未 warp 的相邻两帧直接 XOR）⇒ 不受
「`estimate_rigid` 的 `refine` 默认关着、主轴 theta 在近圆形剪影上不稳」影响。
**代价要写明**：不 warp ⇒ 全局位移会原样留在 XOR 里。但这正是本条要测的东西
（候选②就是全局位移），所以这个"代价"在这里是**特性不是缺陷**。

**若结论是「沿轮廓铺开」，落点很具体**：`rad` 的刚体补偿本来就该把全局位移吸收掉，
吸收不干净说明 `estimate_rigid` 不够准 ⇒ 靶子是**它**（`refine`、主轴估计），
**不是** DP-052 的封边。而且这一支下面还分两种（掩膜漂移 / 动物被动摆动），
**两种都不该算作活动**（被动摆动按契约明确不计入 mobility）⇒ **无论哪种，
残差都在量错东西**，修法同一个方向。

**不改任何常数。** 人工标签是 T2/T0 混杂档（DP-048）⇒ 只作定性判别，不定门。

用法：
    python3 scripts/dp070_spatial_locality.py <切片目录> <人工时间线.csv> [输出.md] \
        [--all=<冻结对比.csv>]
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from depressionplex import runner, video                      # noqa: E402
from depressionplex.assay_core import rad                     # noqa: E402

_SIB = Path(__file__).resolve().parent / "dp065_residual_separability.py"
_spec = importlib.util.spec_from_file_location("_dp065", _SIB)
assert _spec is not None and _spec.loader is not None
dp065 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dp065)

#: locality ≤ 此值 ⇒ 判「挤在一处」（候选①：某个部位在动）。
_LOCAL_MAX = 0.40

#: locality ≥ 此值 ⇒ 判「沿轮廓铺开」（候选②：整体位移 / 刚体补偿没做干净）。
_SPREAD_MIN = 0.80

#: 每帧零假设重抽次数。取中位数，所以不需要很多次。
_NULL_DRAWS = 7

#: 参与统计的帧上限（够了就停，别为了好看多烧 CPU）。
_MAX_FRAMES = 1500

#: XOR 像素少于这么多就跳过该帧：1 个像素谈不上"分布"，2 个才有距离。
_MIN_PIX = 2

#: 算最大两点距离时参与的点数上限。**在动帧的 XOR 可以有几百个像素**，
#: 全对距离是 O(k²) ⇒ k=500 时单帧 25 万对、再乘 7 次零假设、再乘 1500 帧就跑不动了。
#: 超过就随机抽到这个数。**最大两点距离是极值统计，抽样会把它压低**——
#: 所以零假设必须用**同样的点数**抽（代码里 `min(k_eff, ...)` 保证了这点），
#: 观测与零假设同样被压低，比值仍然可比。
_MAX_PTS = 200


def boundary_pixels(m: np.ndarray) -> np.ndarray:
    """剪影的边界像素坐标 (k, 2)。边界 = 掩膜内、但四邻域里有掩膜外的像素。"""
    p = np.zeros((m.shape[0] + 2, m.shape[1] + 2), dtype=bool)
    p[1:-1, 1:-1] = m
    inner = (p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:])
    return np.argwhere(m & ~inner)


def max_pair_dist(pts: np.ndarray) -> float:
    """点集的最大两点距离。点数少（个位到几十），直接算全对距离最省事。"""
    if pts.shape[0] < 2:
        return 0.0
    d = pts[:, None, :].astype(np.float64) - pts[None, :, :].astype(np.float64)
    return float(np.sqrt((d * d).sum(-1)).max())


def frame_locality(prev: np.ndarray, cur: np.ndarray,
                   rng: np.random.Generator) -> float | None:
    """一帧的 locality：实测最大两点距离 ÷ 「沿轮廓均匀抽同样多点」的零假设。"""
    xor = prev ^ cur
    pts = np.argwhere(xor)
    k = pts.shape[0]
    if k < _MIN_PIX:
        return None
    # 点太多就抽样封顶（见 _MAX_PTS）。观测与零假设用**同样的 k_eff**，比值仍可比。
    if k > _MAX_PTS:
        pts = pts[rng.choice(k, size=_MAX_PTS, replace=False)]
    k_eff = pts.shape[0]
    obs = max_pair_dist(pts)
    # 零假设：从当前帧自己的轮廓上均匀抽 k_eff 个点。轮廓不够就用全部。
    bnd = boundary_pixels(cur)
    if bnd.shape[0] < _MIN_PIX:
        return None
    draws = []
    for _ in range(_NULL_DRAWS):
        idx = rng.choice(bnd.shape[0], size=min(k_eff, bnd.shape[0]),
                         replace=False)
        draws.append(max_pair_dist(bnd[idx]))
    null = float(np.median(draws))
    if null <= 0:
        return None
    return obs / null


def self_test() -> dict:
    """**先标定仪器，再读数。** 造两种已知答案的变化，看判别量读不读得出来。

    为什么非做不可：真数据里在动帧的 locality 也是 ≈1.05（与不动帧一样）⇒
    **「人工说在动」那一列当不了低端对照**，它证明不了这个统计量*有能力*读出低值。
    没有一个已知的「挤在一处」样本读出 ≪1，就不能把 6/6 的「铺开」当结论——
    统计量可能根本就只会输出 ≈1。所以在合成图上标定：

    - **局部变化**：只在轮廓一处贴一小块 ⇒ locality **应当 ≪1**
    - **整体位移**：整个剪影平移 1 px ⇒ XOR 是对侧两条月牙 ⇒ locality **应当 ≈1**

    两个都读对，判别量才算可用。
    """
    rng = np.random.default_rng(20260907)
    h = w = 64
    yy, xx = np.mgrid[0:h, 0:w]
    # 椭圆剪影，长轴 ~30 px，量级贴近真实 BL（26–33 px）
    base = (((yy - 32) / 15.0) ** 2 + ((xx - 32) / 8.0) ** 2) <= 1.0

    # ① 局部变化：在轮廓某一处贴一小块（3×3）
    local = base.copy()
    bnd = boundary_pixels(base)
    y0, x0 = bnd[0]
    local[max(0, y0 - 1):y0 + 2, max(0, x0 - 1):x0 + 2] = True
    v_local = frame_locality(base, local, rng)

    # ② 整体位移：平移 1 px
    shift = np.zeros_like(base)
    shift[1:, :] = base[:-1, :]
    v_shift = frame_locality(base, shift, rng)

    return dict(local=v_local, shift=v_shift)


def stats_for(masks: list[np.ndarray], sel: np.ndarray) -> dict | None:
    rng = np.random.default_rng(20260907)
    vals: list[float] = []
    skipped = 0
    idx = np.flatnonzero(sel[:len(masks)])
    # 均匀取样到 _MAX_FRAMES，别只取开头（开头与结尾的行为不同）
    if idx.size > _MAX_FRAMES:
        idx = idx[np.linspace(0, idx.size - 1, _MAX_FRAMES).astype(int)]
    for i in idx:
        if i == 0:
            continue
        a, b = masks[i - 1], masks[i]
        if a is None or b is None or a.shape != b.shape:
            skipped += 1
            continue
        v = frame_locality(np.asarray(a, bool), np.asarray(b, bool), rng)
        if v is None:
            skipped += 1
            continue
        vals.append(v)
    if len(vals) < 50:
        return None
    v = np.asarray(vals)
    return dict(n=int(v.size), skipped=skipped,
                med=float(np.median(v)),
                q=[float(x) for x in np.quantile(v, [0.25, 0.75])])


def probe_one(clip: Path, human: dict) -> dict | None:
    info = video.probe(clip)
    idx = runner.calibration_indices(info.n_frames)
    plan = runner.build_plan(video.frames_at(info, idx), n_chambers=1,
                             calib_indices=tuple(idx))
    seqs = runner.segment_series(video.iter_gray(info), plan)
    ch = plan.chambers[0]
    masks = seqs.get(ch.index)
    if masks is None or len(masks) == 0 or ch.suspension is None:
        return None
    n = len(masks)
    fps = float(info.fps)
    scorers = sorted(human)
    if len(scorers) < 2:
        return {"err": "人工只有 %d 位评分员" % len(scorers)}
    la = dp065.frame_labels(human[scorers[0]], n, fps)
    lb = dp065.frame_labels(human[scorers[1]], n, fps)
    base = (la == lb) & (~dp065.transition_mask(la, fps)) \
        & (~dp065.transition_mask(lb, fps))
    return dict(bl=rad.trial_body_length(masks),
                imm=stats_for(masks, base & ~la),
                mob=stats_for(masks, base & la))


def verdict(loc: float) -> tuple[str, str]:
    if loc <= _LOCAL_MAX:
        return ("挤在一处",
                "locality = %.2f（≤%.2f）⇒ 变化的像素**集中在剪影的一小块**，"
                "远比「沿轮廓均匀」紧 ⇒ 指向候选①**某个部位在小幅动**"
                % (loc, _LOCAL_MAX))
    if loc >= _SPREAD_MIN:
        return ("沿轮廓铺开",
                "locality = %.2f（≥%.2f）⇒ 变化的像素**摊在整条轮廓上** ⇒ "
                "指向候选②**整个剪影在位移**（掩膜漂移 或 被动摆动），"
                "刚体补偿没做干净" % (loc, _SPREAD_MIN))
    return ("分不出",
            "locality = %.2f 落在 %.2f 与 %.2f 之间 ⇒ 本判别量在该试次上分不出"
            % (loc, _LOCAL_MAX, _SPREAD_MIN))


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith("--all=")]
    allspec = next((a[len("--all="):] for a in sys.argv[1:]
                    if a.startswith("--all=")), None)
    if len(argv) < 2:
        print(__doc__)
        return 2
    root = Path(argv[0])
    human_all = dp065.load_human(Path(argv[1]))
    out_path = argv[2] if len(argv) > 2 else None
    trials = (dp065.trials_from_frozen(Path(allspec), root, human_all)
              if allspec else dp065.TRIALS)

    lines: list[str] = []

    def emit(s: str = "") -> None:
        print(s, flush=True)
        lines.append(s)

    emit("# DP-070 不动帧的变化像素：挤在一处还是沿轮廓铺开")
    emit()
    emit("判别量 = **XOR 像素的最大两点距离 ÷ 「从该帧自己轮廓上均匀抽同样多点」的"
         "零假设**（每帧重抽 %d 次取中位）。**≤%.2f 挤在一处（候选①某部位在动）；"
         "≥%.2f 沿轮廓铺开（候选②整体位移）。**"
         % (_NULL_DRAWS, _LOCAL_MAX, _SPREAD_MIN))
    emit()
    emit("零假设**逐帧算**，不是拍的——「铺开」该给多大距离取决于该帧剪影的形状。"
         "**不碰 `rad` 的刚体拟合**（未 warp 直接 XOR）⇒ 不受 `estimate_rigid` "
         "那个已知不稳影响；全局位移会原样留在 XOR 里，而**这正是本条要测的东西**。")
    emit()

    # ---- 先标定仪器，再读数 ----
    st = self_test()
    emit("## 仪器标定（先标定再读数）")
    emit()
    emit("**为什么必须有这一步**：真数据里**在动帧的 locality 也是 ≈1.05**，"
         "与不动帧一样 ⇒ **「人工说在动」那一列当不了低端对照**，"
         "它证明不了这个统计量*有能力*读出低值。"
         "没有一个已知的「挤在一处」样本读出 ≪1，"
         "「铺开」就可能只是统计量本身只会输出 ≈1。所以在合成图上标定：")
    emit()
    emit("| 已知答案的合成变化 | 应当读出 | **实测** |")
    emit("|---|---|---|")
    emit("| 轮廓一处贴 3×3 小块（**局部**） | ≪1 | **%s** |"
         % ("%.2f" % st["local"] if st["local"] is not None else "算不出"))
    emit("| 整个剪影平移 1 px（**整体位移**） | ≈1 | **%s** |"
         % ("%.2f" % st["shift"] if st["shift"] is not None else "算不出"))
    emit()
    ok = (st["local"] is not None and st["shift"] is not None
          and st["local"] <= _LOCAL_MAX and st["shift"] >= _SPREAD_MIN)
    if not ok:
        emit("⇒ **标定没过 ⇒ 本脚本不出结论。** 判别量读不出已知答案，"
             "真数据上的读数无从解释。先修判别量。")
        _write(lines, out_path)
        return 1
    emit("⇒ **标定通过，两端都读对了。** 判别量**确实有能力**读出 ≪1"
         "（局部变化读 %.2f）⇒ 下面真数据若读出 ≈1，那是**实测结果**，"
         "不是统计量的天花板。而且合成「整体位移」读 %.2f 给了一个**具体的比对值**。"
         % (st["local"], st["shift"]))
    emit()
    emit("| 试次 | 角色 | 偏差 | 可用帧 | 不动帧 locality（P25 / **中位** / P75） | "
         "在动帧中位 | 判定 |")
    emit("|---|---|---|---|---|---|---|")

    res: list[dict] = []
    for stem, bias, role in trials:
        clip = root / (stem + ".mp4")
        print("== %s ==" % stem, flush=True)
        blank = "| %s | %s | %+.1f | — | — | — | %s |"
        if not clip.exists():
            emit(blank % (stem, role, bias, "素材不存在"))
            continue
        if stem not in human_all:
            emit(blank % (stem, role, bias, "人工时间线里没有此试次"))
            continue
        t0 = time.time()
        try:
            r = probe_one(clip, human_all[stem])
        except Exception as e:                                  # noqa: BLE001
            emit(blank % (stem, role, bias, "失败：" + str(e)[:50]))
            continue
        print("   %.0f s" % (time.time() - t0), flush=True)
        if r is None or "err" in r:
            emit(blank % (stem, role, bias, (r or {}).get("err", "不产数字")))
            continue
        i, m = r["imm"], r["mob"]
        if not i:
            emit(blank % (stem, role, bias, "不动帧可用帧不足（XOR 像素太少）"))
            continue
        v, why = verdict(i["med"])
        emit("| `%s` | %s | %+.1f | %d | %.2f / **%.2f** / %.2f | %s | **%s** |"
             % (stem, role, bias, i["n"], i["q"][0], i["med"], i["q"][1],
                ("%.2f" % m["med"]) if m else "—", v))
        res.append(dict(stem=stem, role=role, v=v, why=why, med=i["med"],
                        mmed=(m["med"] if m else None)))

    if not res:
        emit()
        emit("**没有任何试次产出数字 ⇒ 不出结论。**")
        _write(lines, out_path)
        return 1

    emit()
    emit("## 判定")
    emit()
    for r in res:
        emit("- `%s`（%s）：%s" % (r["stem"], r["role"], r["why"]))
    emit()
    ms = sorted(r["med"] for r in res)
    emit("- 不动帧 locality：中位 **%.2f**，范围 %.2f–%.2f（n=%d）"
         % (ms[len(ms) // 2], ms[0], ms[-1], len(ms)))
    mm = sorted(r["mmed"] for r in res if r["mmed"] is not None)
    if mm:
        emit("- 在动帧 locality：中位 **%.2f**，范围 %.2f–%.2f "
             "——**这一列原本想当量尺，但没当上**：它与不动帧几乎一样，"
             "所以它证明不了本判别量*有能力*读出低值 ⇒ "
             "**低端参照改由上面的合成标定提供（局部变化读 %.2f），见下面边界 2**"
             % (mm[len(mm) // 2], mm[0], mm[-1], st["local"]))
    emit()

    n_l = sum(1 for r in res if r["v"] == "挤在一处")
    n_s = sum(1 for r in res if r["v"] == "沿轮廓铺开")
    emit("**判定分布：挤在一处 %d / 沿轮廓铺开 %d / 分不出 %d（共 %d）。**"
         % (n_l, n_s, len(res) - n_l - n_s, len(res)))
    emit()
    if n_s == len(res):
        emit("⇒ **实测中位 %.2f 对上合成「整体位移」的 %.2f，"
             "而合成「局部变化」是 %.2f ⇒ 差了一个数量级。**"
             % (ms[len(ms) // 2], st["shift"], st["local"]))
        emit()
        emit("⇒ **候选②：不动帧的残差来自整个剪影的缓慢位移，不是某个部位在动。** "
             "落点很具体：`rad` 的刚体补偿**本来就该把全局位移吸收掉**，"
             "吸收不干净说明 `estimate_rigid` 不够准 ⇒ "
             "**靶子是它（`refine` 默认关着、主轴 theta 在近圆形剪影上不稳），"
             "不是 DP-052 的封边。** "
             "这一支下面还分两种——掩膜漂移 / 动物被动摆动——但**两种都不该算作活动**"
             "（被动摆动按契约明确不计入 mobility）⇒ **无论哪种，残差都在量错东西，"
             "修法同一个方向。**")
    elif n_l == len(res):
        emit("⇒ **候选①：不动帧的残差来自剪影某一小块的持续变化。** "
             "这最可能是**动物某个部位在小幅动**（爪、头、尾根），"
             "人工按「明显挣扎」按键时没把它算作活动。⇒ **这是口径分歧，契约级**："
             "软件按「像素变了」计数、人工按「看得出在挣扎」计数，"
             "两者对「什么算动」的定义不同。**要道俊裁，不是靠改分割或挪门槛能解决的。**")
    else:
        emit("⇒ **各试次不一致 ⇒ 不能一句话概括**，逐试次看上面的判定。"
             "**在弄清分组依据之前不许把任一侧当成全批结论。**")
    emit()
    emit("**这条的边界（两条，都要写明）**：")
    emit()
    emit("1. locality 只说「变化挤不挤」，**不能**区分「掩膜整体漂移」与"
         "「动物被动摆动」——两者都是全局位移。但那两个的**修法方向相同**"
         "（都该被刚体补偿吸收、都不该计入 mobility），所以本条不需要再往下分。")
    emit("2. **在动帧那一列没能当上对照**：它读 %s，与不动帧几乎一样 ⇒ "
         "**这个判别量分不开「因挣扎而铺开」与「因漂移而铺开」**。"
         "本条能站住，靠的是**合成标定**（局部变化读 %.2f）而不是那一列。"
         "对不动帧来说这不影响结论——那些帧是「两位评分员都说不动」筛出来的，"
         "挣扎在构造上就被排除了；但**必须写明结论是靠标定支撑的，不是靠那一列**。"
         % (("%.2f–%.2f" % (mm[0], mm[-1])) if mm else "—", st["local"]))
    emit()
    emit("**没动任何常数、没跑 LOVO。** 人工标签是 T2/T0 混杂档（DP-048）⇒ "
         "本条只作定性判别，**不构成改 θ_mob 的授权**。")
    _write(lines, out_path)
    return 0


def _write(lines: list[str], out_path: str | None) -> None:
    if out_path:
        Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n已写出 " + out_path)


if __name__ == "__main__":
    raise SystemExit(main())
