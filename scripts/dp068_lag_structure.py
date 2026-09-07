#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-068：不动帧的残差地板是**高频掩膜抖动**还是**相干变化**？看时间结构定。

**为什么必须先分清这个。** DP-067 量出不动帧的 XOR 地板在模式B 试次是 3–6 px、
在对照试次是 1 px。DP-063/065 一路把「掩膜逐帧抖动」当头号候选，但那一直是猜测
（DP-065 的下一步①明写：在做完这条之前「掩膜抖动」不许当结论说出口）。

**判别原理：残差随 lag 怎么长。** 残差的定义是

    residual(lag) = count_nonzero(warp(frame[i-lag]) XOR frame[i]) / BL²

注意 `decompose_series` 里 **residual 没有除以 lag**（omega 与 trans 除了），
所以 residual_lag4 就是「隔 4 帧」的原始 XOR 面积。于是：

| 地板的来源 | XOR 面积随 lag | **r4/r1 预期** |
|---|---|---|
| **逐帧独立**的边界抖动 | 不累积 | **≈ 1** |
| 扩散式（轮廓随机游走） | ∝ √lag | ≈ 2 |
| **相干**变化（持续同向） | ∝ lag | **≈ 4** |

**这个判别量是白捡的。** `rad.DEFAULT_LAGS = (1, 4)`，`build_tst_features` 每次
运行**都算了 lag4 然后丢掉**（它只取 `residual_lag1`）。所以本脚本的成本与 DP-065
一模一样，没有额外计算。

**这条能证伪什么、不能证伪什么（边界写在前面）。**

- **能**：r4/r1 ≈ 1 与 r4/r1 ≈ 4 是**互斥**的，所以这条能干净地**排除**其中一个。
- **不能**：r4/r1 ≈ 4 只说明「变化是相干的」，**不能进一步说是动物在动**。
  慢速的掩膜漂移（光照缓变、阈值贴着背景导致掩膜缓慢胀缩、悬挂胶带缓慢移动）
  同样是相干的。**「相干」⇒ 排除高频抖动，不等于「动物真在动」**，那要另一条
  探针（把残差与 `scale_lag1` / `trans_lag1` / `omega_lag1` 对起来看）。

**饱和这条必须自己盯住。** XOR 在位移超过重叠尺度后会**饱和**，饱和把比值压向 1
⇒ 会把相干变化**假装**成抖动。所以脚本把在动帧的比值一起报出来当**饱和参照**：
在动帧振幅大、先饱和，它的比值就是「本试次的饱和有多厉害」。
**关键性质：饱和只会把比值往下压。** 所以在饱和存在的前提下测到的比值是
**相干性的下界**——测到 4 就至少是 4，不会是虚高。

**一条方法论记录（这支探针第一版的判定逻辑是错的）。** 第一版假定「在动帧是真运动
的正对照，比值该更高」，于是要求「在动比值 − 不动比值 ≥ 0.5」才出结论。实测反了：
**在动帧 1.25–2.05（已饱和），不动帧 3.00–9.00**。第一版那道守卫因此对全部 6 个
试次都印了「不出结论」——**守卫本身救了一次**（没让错的方向假设变成结论），
但守卫的方向设定是错的。本版把在动帧从「正对照」改成「饱和参照」，判定只看不动帧
本身的比值。**原始数字一个没改**，改的只是判定逻辑。

**不改任何常数。** 人工标签是 T2/T0 混杂档（DP-048）⇒ 只作定性判别，不定门。

用法：
    python3 scripts/dp068_lag_structure.py <切片目录> <人工时间线.csv> [输出.md] \
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

#: **复用 DP-065 的标签逻辑，不重写。** 两支探针必须用**一模一样**的人工标签
#: （同样的两人一致、同样的 ±0.5 s 转换点掐除），否则两边的数没法并排比。
#: 复制粘贴一份迟早会漂，所以这里直接 import 兄弟脚本。
_SIB = Path(__file__).resolve().parent / "dp065_residual_separability.py"
_spec = importlib.util.spec_from_file_location("_dp065", _SIB)
assert _spec is not None and _spec.loader is not None
dp065 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dp065)

#: r4/r1 ≤ 此值 ⇒ 判「高频抖动」（不累积）。1.5 是 1 与 2 之间的中点。
_JITTER_MAX = 1.5

#: r4/r1 ≥ 此值 ⇒ 判「相干」（随 lag 累积）。2.5 是 2 与 4 之间偏保守的位置。
_COHERENT_MIN = 2.5

#: 不动帧 r1 小于此像素数时，比值的分母不足 2 个像素 ⇒ 比值**定得很虚**，
#: 单独标出来。1 px 的分母意味着 ±0.5 px 的量化误差就让比值浮动 ±50%。
_WEAK_DENOM_PX = 2.0


def ratio_stats(masks: list[np.ndarray], bl: float,
                imm: np.ndarray, mob: np.ndarray) -> dict | None:
    """按 lag 1 / 4 分解，分别在不动帧与在动帧上取中位数。

    `decompose_series` 的缺省 lags 就是 (1, 4)，所以这里一次拿到两个 lag，
    没有额外成本。不可算的帧是 None ⇒ 剔掉，**不补 0**（DP-032）。
    """
    rows = rad.decompose_series(masks, bl=bl)
    n = len(rows)
    r1 = np.full(n, np.nan)
    r4 = np.full(n, np.nan)
    for i, row in enumerate(rows):
        v1 = row.get("residual_lag1")
        v4 = row.get("residual_lag4")
        if v1 is not None:
            r1[i] = float(v1)
        if v4 is not None:
            r4[i] = float(v4)
    ok = ~np.isnan(r1) & ~np.isnan(r4)
    bl2 = bl * bl
    out: dict = {"bl": bl, "n_ok": int(ok.sum())}
    for tag, sel in (("imm", imm), ("mob", mob)):
        m = ok & sel[:n]
        if int(m.sum()) < 100:
            out[tag] = None
            continue
        m1 = float(np.median(r1[m]))
        m4 = float(np.median(r4[m]))
        out[tag] = dict(
            n=int(m.sum()), r1=m1, r4=m4,
            px1=m1 * bl2, px4=m4 * bl2,
            ratio=(m4 / m1) if m1 > 0 else None)
    return out


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
    bl = rad.trial_body_length(masks)
    if bl <= 0:
        return None
    n = len(masks)
    fps = float(info.fps)

    scorers = sorted(human)
    if len(scorers) < 2:
        return {"err": "人工只有 %d 位评分员" % len(scorers)}
    la = dp065.frame_labels(human[scorers[0]], n, fps)
    lb = dp065.frame_labels(human[scorers[1]], n, fps)
    agree = la == lb
    steady = (~dp065.transition_mask(la, fps)) & (~dp065.transition_mask(lb, fps))
    base = agree & steady
    return ratio_stats(masks, bl, base & ~la, base & la)


def verdict(imm_ratio: float, imm_px1: float) -> tuple[str, str]:
    """(判定, 说明)。**只看不动帧本身的比值**——在动帧已饱和，当不了正对照。"""
    weak = ("（注意：分母只有 %.0f px，比值定得虚，±0.5 px 的量化误差就让它浮动 "
            "±%.0f%%）" % (imm_px1, 100 * 0.5 / max(imm_px1, 0.5))
            ) if imm_px1 < _WEAK_DENOM_PX else ""
    if imm_ratio <= _JITTER_MAX:
        return ("高频抖动",
                "不动帧 r4/r1 = %.2f ≈ 1 ⇒ 残差不随 lag 累积 ⇒ 逐帧独立的边界抖动%s"
                % (imm_ratio, weak))
    if imm_ratio >= _COHERENT_MIN:
        return ("相干变化",
                "不动帧 r4/r1 = %.2f ⇒ 残差随 lag 累积 ⇒ **不是**高频抖动；"
                "是相干的持续变化（动物阈下运动 或 慢速掩膜漂移，本条分不开）%s"
                % (imm_ratio, weak))
    return ("居中",
            "不动帧 r4/r1 = %.2f 落在 1 与 2.5 之间 ⇒ 介于抖动与相干之间%s"
            % (imm_ratio, weak))


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

    emit("# DP-068 不动帧残差地板的时间结构：高频抖动还是相干变化")
    emit()
    emit("判别量 = **r4/r1**（隔 4 帧的 XOR 面积 ÷ 隔 1 帧的）。"
         "`residual` **没有**除以 lag，所以：**≈1 ⇒ 不累积 ⇒ 高频抖动**；"
         "**≈4 ⇒ 随 lag 线性累积 ⇒ 相干变化**；≈2 ⇒ 扩散式。")
    emit()
    emit("在动帧那一列是**饱和参照，不是正对照**：在动帧振幅大、XOR 先饱和，"
         "所以它的比值低是**预期**的。**饱和只会把比值往下压** ⇒ "
         "不动帧测到的比值是**相干性的下界**。")
    emit()
    emit("| 试次 | 角色 | 偏差 | 不动 r1(px) | 不动 r4(px) | **不动 r4/r1** | "
         "在动 r1(px) | 在动 r4/r1（饱和参照） | 判定 |")
    emit("|---|---|---|---|---|---|---|---|---|")

    res: list[dict] = []
    for stem, bias, role in trials:
        clip = root / (stem + ".mp4")
        print("== %s ==" % stem, flush=True)
        if not clip.exists():
            emit("| %s | %s | %+.1f | — | — | — | — | — | 素材不存在 |"
                 % (stem, role, bias))
            continue
        if stem not in human_all:
            emit("| %s | %s | %+.1f | — | — | — | — | — | 人工时间线里没有此试次 |"
                 % (stem, role, bias))
            continue
        t0 = time.time()
        try:
            r = probe_one(clip, human_all[stem])
        except Exception as e:                                  # noqa: BLE001
            emit("| %s | %s | %+.1f | — | — | — | — | — | 失败：%s |"
                 % (stem, role, bias, str(e)[:50]))
            continue
        print("   %.0f s" % (time.time() - t0), flush=True)
        if r is None or "err" in r:
            emit("| %s | %s | %+.1f | — | — | — | — | — | %s |"
                 % (stem, role, bias, (r or {}).get("err", "不产数字")))
            continue
        i, m = r.get("imm"), r.get("mob")
        if not i or i["ratio"] is None:
            emit("| %s | %s | %+.1f | — | — | — | — | — | 不动帧可用帧不足 |"
                 % (stem, role, bias))
            continue
        v, why = verdict(i["ratio"], i["px1"])
        emit("| `%s` | %s | %+.1f | %.1f | %.1f | **%.2f** | %s | %s | **%s** |"
             % (stem, role, bias, i["px1"], i["px4"], i["ratio"],
                ("%.1f" % m["px1"]) if m else "—",
                ("%.2f" % m["ratio"]) if (m and m["ratio"]) else "—", v))
        res.append(dict(stem=stem, role=role, bias=bias, v=v, why=why,
                        ir=i["ratio"], px1=i["px1"], px4=i["px4"],
                        mr=(m["ratio"] if m else None)))

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

    irs = sorted(r["ir"] for r in res)
    med_i = irs[len(irs) // 2]
    mrs = sorted(r["mr"] for r in res if r["mr"])
    strong = [r for r in res if r["px1"] >= _WEAK_DENOM_PX]
    emit("- 不动帧 r4/r1：中位 **%.2f**，范围 %.2f–%.2f（n=%d）"
         % (med_i, irs[0], irs[-1], len(irs)))
    if mrs:
        emit("- 在动帧 r4/r1（饱和参照）：中位 **%.2f**，范围 %.2f–%.2f"
             % (mrs[len(mrs) // 2], mrs[0], mrs[-1]))
    emit("- 其中**分母 ≥ %.0f px、比值定得实**的试次 %d 个：%s"
         % (_WEAK_DENOM_PX, len(strong),
            "、".join("`%s` %.2f" % (r["stem"], r["ir"]) for r in strong) or "无"))
    emit()

    n_jit = sum(1 for r in res if r["v"] == "高频抖动")
    n_coh = sum(1 for r in res if r["v"] == "相干变化")
    emit("**判定分布：高频抖动 %d / 相干变化 %d / 居中 %d（共 %d）。**"
         % (n_jit, n_coh, len(res) - n_jit - n_coh, len(res)))
    emit()
    if n_coh == len(res):
        emit("⇒ **「高频掩膜抖动」这个候选被排除，全部试次一致。** "
             "不动帧的残差地板**随 lag 累积**，不是逐帧独立的边界闪烁。"
             "而且饱和只会把比值往下压 ⇒ 这些比值是**下界**，相干性只会更强。")
        emit()
        emit("**这条改了路线图。** DP-063/065 一路把「掩膜逐帧抖动」当模式B 的头号"
             "候选，据此把 **DP-052** 当作模式B 的解法。现在这个候选被排除 ⇒ "
             "**DP-052（封边/分割质量）不再是模式B 的自动答案**。")
        emit()
        emit("**但这条也不能反过来说「动物真在动」。** 相干变化有两类成因，"
             "本探针分不开：① 动物真的在小幅持续动，人工没按键计入（**口径分歧**，"
             "契约级问题，要道俊裁）；② 掩膜在慢速漂移——光照缓变、二值化阈值贴着"
             "背景导致掩膜缓慢胀缩、悬挂胶带缓慢移动（**仍是分割问题，但不是抖动**，"
             "修法与 DP-052 的封边不同）。")
        emit()
        emit("**下一步（分开①与②，而且同样是白捡的）**：`decompose_series` 每帧还"
             "顺手算了 `scale_lag1`（掩膜胀缩）、`trans_lag1`（质心平移）、"
             "`omega_lag1`（主轴转动），全都被 `build_tst_features` 丢掉了。"
             "把不动帧的残差与这三个量对起来看：残差跟着 `scale` 走 ⇒ 掩膜胀缩（②）；"
             "跟着 `omega`/`trans` 走 ⇒ 刚体运动没被 warp 吸收干净（②的另一种，"
             "而且 `rad.estimate_rigid` 的 `refine` 默认关着、docstring 自己写了"
             "「主轴 theta 在近圆形剪影上不稳」⇒ 这条有代码级依据）；"
             "三个都不跟 ⇒ 才轮到①。")
    elif n_jit == len(res):
        emit("⇒ **不动帧的残差地板是高频掩膜抖动，全部试次一致。** "
             "回到 **DP-052**，靶子：把不动帧的 XOR 地板压到 1 px。")
    else:
        emit("⇒ **各试次不一致 ⇒ 不能一句话概括。** 逐试次看上面的判定，"
             "分组归因；在弄清分组依据之前，**不许把任一侧当成全批结论**。")
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
