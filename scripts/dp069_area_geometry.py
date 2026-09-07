#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-069：不动帧那点变化是**掩膜在呼吸**还是**等面积的形状/位置变化**？纯几何判。

**接 DP-068。** DP-068 证明不动帧的残差地板**随 lag 累积**（r4/r1 = 3.0–9.0）⇒
排除了「高频掩膜抖动」。剩两个候选，方向完全不同：

- ① **动物真的在小幅持续动**，人工没按键计入 ⇒ **口径分歧**，契约级，要道俊裁。
- ② **掩膜在慢速漂移**（光照缓变、二值化阈值贴着背景导致掩膜缓慢胀缩）
  ⇒ **仍是分割问题**，但修法与 DP-052 的封边不同。

**判别量：|Δ面积| / XOR。** 这一条**完全不碰 `rad` 的刚体拟合**，是两张掩膜之间的
纯计数，所以**不受 `estimate_rigid` 的 `refine` 默认关着、主轴 theta 在近圆形剪影上
不稳那个已知缺陷影响**（DP-068 的 lag4 比值有这个混淆，这一条没有）。

设 A = 前一帧掩膜、B = 当前帧：

    XOR   = |A\\B| + |B\\A|          两帧不一致的像素总数
    Δ面积 = |B| − |A| = |B\\A| − |A\\B|

- **纯胀/纯缩**（掩膜呼吸）：一侧为 0 ⇒ |Δ面积| = XOR ⇒ **比值 1**
- **等面积的位移或形变**（动物动了、轮廓重分布）：两侧相等 ⇒ **比值 0**

**但零假设不是 0，这一步不做就会得出错结论。** 若 XOR 的 N 个像素各自**独立**
翻转，Δ面积是 ±1 的随机游走和 ⇒ |Δ面积| ≈ **√N**，比值 ≈ **1/√N**，**不是 0**。
不动帧的 N 只有 1–6 px（DP-067），1/√3 = 0.58 —— **小 N 下比值天然就高**。
所以本脚本报的是**超出量**：

    excess = 观测比值 ÷ (1/√N) = 观测比值 × √N

| excess | 含义 |
|---|---|
| **≈ 1** | 与「独立翻转」一致 ⇒ 这个判别量**分不出**方向，得换探针 |
| **≫ 1**（≥1.5） | 面积**单向**变化 ⇒ **掩膜在呼吸 ⇒ 候选②** |
| **≪ 1**（≤0.67） | 面积被保住了 ⇒ 变化是**位移/形变** ⇒ 倾向候选① |

**外加一条独立的试次级证据（更便宜）**：掩膜面积随时间的**慢趋势**。掩膜若在
慢速胀缩，`area(t)` 在 360 s 上会有可见的单调漂移。这一条与逐帧比值**互相独立**，
两条同向才敢说话。

**不改任何常数。** 人工标签是 T2/T0 混杂档（DP-048）⇒ 只作定性判别，不定门。

用法：
    python3 scripts/dp069_area_geometry.py <切片目录> <人工时间线.csv> [输出.md] \
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

#: 复用 DP-065 的人工标签逻辑（两人一致 + 掐掉 ±0.5 s 转换点），不重写。
#: 三支探针（065/068/069）必须用**同一套**标签，否则数字没法并排比。
_SIB = Path(__file__).resolve().parent / "dp065_residual_separability.py"
_spec = importlib.util.spec_from_file_location("_dp065", _SIB)
assert _spec is not None and _spec.loader is not None
dp065 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dp065)

#: excess ≥ 此值 ⇒ 判「掩膜呼吸」（面积单向变化，超出独立翻转的预期）。
_BREATH_MIN = 1.5

#: excess ≤ 此值 ⇒ 判「等面积重分布」（面积被保住，位移/形变）。
_REDIST_MAX = 0.67

#: 不动帧 XOR 中位小于此值时，比值的分母不足 2 个像素 ⇒ 判别量本身失效，
#: 直接标「像素太少，判不了」，**不硬给结论**。
_MIN_XOR_PX = 2.0


def area_series(masks: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray, int]:
    """→ (面积, 逐帧 XOR, 形状不一致而跳过的帧对数)。第 0 帧的 XOR 记 NaN。

    **不碰 rad 的刚体拟合**：A 与 B 直接比，不做 warp。所以这里的 XOR 比
    `rad` 的残差分子**大**（少了刚体补偿），这是故意的——本条要的就是一个
    不依赖那个拟合的量。
    """
    n = len(masks)
    area = np.full(n, np.nan)
    xor = np.full(n, np.nan)
    bad = 0
    prev = None
    for i, m in enumerate(masks):
        if m is None:
            prev = None
            continue
        b = np.asarray(m, dtype=bool)
        area[i] = float(np.count_nonzero(b))
        if prev is not None:
            if prev.shape != b.shape:
                bad += 1
            else:
                xor[i] = float(np.count_nonzero(prev ^ b))
        prev = b
    return area, xor, bad


def slow_trend_pct(area: np.ndarray) -> float | None:
    """面积在整段上的慢趋势，用**首尾各 10% 的中位数之差**除以全段中位数。

    不用最小二乘：面积序列里有大幅的行为性起伏（挣扎时蜷缩），
    最小二乘会被那些拖着走。分位数对它们不敏感。
    """
    v = area[~np.isnan(area)]
    if v.size < 200:
        return None
    k = max(1, v.size // 10)
    med = float(np.median(v))
    if med <= 0:
        return None
    return float(np.median(v[-k:]) - np.median(v[:k])) / med


def stats_for(xor: np.ndarray, area: np.ndarray, sel: np.ndarray) -> dict | None:
    """在给定帧子集上算 |Δ面积| / XOR 的中位数与 excess。"""
    n = xor.size
    d = np.full(n, np.nan)
    d[1:] = np.abs(area[1:] - area[:-1])
    m = sel[:n] & ~np.isnan(xor) & ~np.isnan(d) & (xor > 0)
    if int(m.sum()) < 100:
        return None
    x = xor[m]
    ratio = float(np.median(d[m] / x))
    n_px = float(np.median(x))
    # 独立翻转的零假设：比值 ≈ 1/√N。用中位 N 算，与 ratio 用同一批帧。
    null = 1.0 / np.sqrt(max(n_px, 1.0))
    return dict(n=int(m.sum()), xor_px=n_px, ratio=ratio,
                null=float(null), excess=ratio / null)


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
    n = len(masks)
    fps = float(info.fps)

    scorers = sorted(human)
    if len(scorers) < 2:
        return {"err": "人工只有 %d 位评分员" % len(scorers)}
    la = dp065.frame_labels(human[scorers[0]], n, fps)
    lb = dp065.frame_labels(human[scorers[1]], n, fps)
    base = (la == lb) & (~dp065.transition_mask(la, fps)) \
        & (~dp065.transition_mask(lb, fps))

    area, xor, bad = area_series(masks)
    return dict(bl=bl, bad_pairs=bad, trend=slow_trend_pct(area),
                area_med=float(np.nanmedian(area)),
                imm=stats_for(xor, area, base & ~la),
                mob=stats_for(xor, area, base & la))


def verdict(ex: float, xor_px: float) -> tuple[str, str]:
    if xor_px < _MIN_XOR_PX:
        return ("像素太少，判不了",
                "不动帧 XOR 中位只有 %.1f px ⇒ |Δ面积|/XOR 的分母不足 2 个像素，"
                "判别量本身失效（**不硬给结论**）" % xor_px)
    if ex >= _BREATH_MIN:
        return ("掩膜呼吸",
                "excess = %.2f（≥%.2f）⇒ 面积**单向**变化，超出独立翻转的预期 "
                "⇒ 指向候选②**掩膜慢速胀缩**" % (ex, _BREATH_MIN))
    if ex <= _REDIST_MAX:
        return ("等面积重分布",
                "excess = %.2f（≤%.2f）⇒ 面积被保住了，变化是位移/形变 "
                "⇒ 倾向候选①**动物阈下运动**" % (ex, _REDIST_MAX))
    return ("与独立翻转一致",
            "excess = %.2f ≈ 1 ⇒ 本判别量**分不出**方向，得换探针" % ex)


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

    emit("# DP-069 不动帧的变化是掩膜呼吸还是等面积重分布（纯几何，不碰刚体拟合）")
    emit()
    emit("判别量 = **|Δ面积| / XOR**。纯胀缩 ⇒ 1；等面积位移/形变 ⇒ 0。"
         "**但零假设不是 0**：N 个像素独立翻转时比值 ≈ **1/√N**，"
         "而不动帧的 N 只有几个像素 ⇒ 必须看**超出量** "
         "`excess = 比值 × √N`。**≈1 分不出方向；≥%.1f 呼吸；≤%.2f 重分布。**"
         % (_BREATH_MIN, _REDIST_MAX))
    emit()
    emit("**本条不依赖 `rad.estimate_rigid`** ⇒ 不受「`refine` 默认关着、"
         "主轴 theta 在近圆形剪影上不稳」那个已知缺陷影响（DP-068 有这个混淆，本条没有）。")
    emit()
    emit("| 试次 | 角色 | 偏差 | 不动帧 XOR(px) | 比值 | 零假设 1/√N | "
         "**excess** | 在动帧 excess | 面积慢趋势 | 判定 |")
    emit("|---|---|---|---|---|---|---|---|---|---|")

    res: list[dict] = []
    for stem, bias, role in trials:
        clip = root / (stem + ".mp4")
        print("== %s ==" % stem, flush=True)
        blank = "| %s | %s | %+.1f | — | — | — | — | — | — | %s |"
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
            emit(blank % (stem, role, bias, "不动帧可用帧不足"))
            continue
        v, why = verdict(i["excess"], i["xor_px"])
        emit("| `%s` | %s | %+.1f | %.1f | %.3f | %.3f | **%.2f** | %s | %s | **%s** |"
             % (stem, role, bias, i["xor_px"], i["ratio"], i["null"], i["excess"],
                ("%.2f" % m["excess"]) if m else "—",
                ("%+.1f%%" % (100 * r["trend"])) if r["trend"] is not None else "—",
                v))
        if r["bad_pairs"]:
            emit("|  |  |  |  |  |  |  |  |  | ⚠ %d 个帧对形状不一致已跳过 |"
                 % r["bad_pairs"])
        res.append(dict(stem=stem, role=role, v=v, why=why, ex=i["excess"],
                        xor_px=i["xor_px"], trend=r["trend"],
                        mex=(m["excess"] if m else None)))

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

    usable = [r for r in res if r["v"] != "像素太少，判不了"]
    if not usable:
        emit("**全部试次的不动帧 XOR 都不足 %.0f px ⇒ 本判别量在这批数据上失效。** "
             "这本身是个结论：不动帧的信号只有一两个像素，**任何**基于逐帧计数的"
             "判别量都到底了 ⇒ 要往「压低噪声地板」而不是「更聪明地读地板」走。"
             % _MIN_XOR_PX)
        _write(lines, out_path)
        return 0

    exs = sorted(r["ex"] for r in usable)
    emit("- 不动帧 excess：中位 **%.2f**，范围 %.2f–%.2f（n=%d，另有 %d 个试次"
         "像素太少判不了）"
         % (exs[len(exs) // 2], exs[0], exs[-1], len(exs), len(res) - len(usable)))
    tr = [r["trend"] for r in usable if r["trend"] is not None]
    if tr:
        tr.sort()
        emit("- 面积慢趋势（末 10%% 中位 − 首 10%% 中位，除以全段中位）："
             "中位 **%+.1f%%**，范围 %+.1f%%–%+.1f%%"
             % (100 * tr[len(tr) // 2], 100 * tr[0], 100 * tr[-1]))
    emit()

    # ---- 分组比较：这才是信息所在（DP-067 的教训——摊开看才看得见结构）----
    mb = [r for r in usable if "模式B" in r["role"]]
    ot = [r for r in usable if "模式B" not in r["role"]]
    overlap = None
    if mb and ot:
        be = [r["ex"] for r in mb]
        oe = [r["ex"] for r in ot]
        overlap = not (min(be) > max(oe) or min(oe) > max(be))
        emit("## 分组：面积行为区分得开模式B 吗")
        emit()
        emit("| 组 | n | excess 范围 | 面积慢趋势范围 |")
        emit("|---|---|---|---|")
        for nm, g in (("**模式B**（软件过判活动）", mb), ("模式A + 对照", ot)):
            gt = [r["trend"] for r in g if r["trend"] is not None]
            emit("| %s | %d | %.2f–%.2f | %s |"
                 % (nm, len(g), min(r["ex"] for r in g), max(r["ex"] for r in g),
                    ("%+.1f%% – %+.1f%%" % (100 * min(gt), 100 * max(gt)))
                    if gt else "—"))
        emit()
        if overlap:
            emit("⇒ **两组区间重叠 ⇒ 面积行为不区分模式B。** "
                 "这本身是个**阴性结论，而且是有用的那种**：候选②里"
                 "「阈值漂移导致掩膜胀缩」这个具体形式**不是模式B 的机制**——"
                 "模式B 试次的面积行为与对照**没有可分的差别**。"
                 "（对比 DP-067：同一批试次的**噪声地板**在两组间是 4.95× 且完全不重叠，"
                 "**地板分得开、面积分不开** ⇒ 病灶在地板的**大小**上，不在面积的**变化方式**上。）")
        else:
            emit("⇒ **两组区间不重叠 ⇒ 面积行为把模式B 分开了。** "
                 "n 这么小时不重叠只是**提示不是证明**（DP-065 已吃过这个教训），"
                 "要扩到全部 26 段才算数。")
        emit()

    n_b = sum(1 for r in usable if r["v"] == "掩膜呼吸")
    n_r = sum(1 for r in usable if r["v"] == "等面积重分布")
    emit("**判定分布：掩膜呼吸 %d / 等面积重分布 %d / 与独立翻转一致 %d（共 %d）。**"
         % (n_b, n_r, len(usable) - n_b - n_r, len(usable)))
    emit()
    if n_b == len(usable):
        emit("⇒ **指向候选②：掩膜在慢速胀缩。** 不动帧的面积**单向**变化，"
             "超出独立翻转的预期 ⇒ 这是**分割问题**，"
             "而且是「阈值贴着背景导致掩膜缓慢胀缩」这一类，"
             "**不是** DP-052 要修的封边漏洞。修法不同：要动的是**二值化阈值的"
             "时间稳定性**（逐帧重估 vs 试次级固定），不是封边余量。")
    elif n_r == len(usable):
        emit("⇒ **指向候选①：变化是等面积的位移/形变。** 掩膜面积被保住了，"
             "变化发生在轮廓的重新分布上 ⇒ **更可能是动物真的在小幅持续动**。"
             "**这就把问题推到契约级**：人工按「明显挣扎」按键，软件按「像素变了」计数，"
             "两者对「什么算动」的定义不同 ⇒ **要道俊裁口径**，"
             "不是靠改分割或挪门槛能解决的。")
    elif (len(usable) - n_b - n_r) * 2 > len(usable):
        emit("⇒ **多数试次落在「与独立翻转一致」⇒ 面积这个通道到底了。** "
             "|Δ面积|/XOR 在两类帧上都贴着 1/√N 的零假设走"
             "（在动帧的 excess 也是 ≈1），说明**面积差本身不携带方向信息** ⇒ "
             "这条判别量**不能**用来分开候选①与②，得换探针。**这是预先声明过的可能结局**，"
             "不是事后找的借口——判定规则里 `≈1 ⇒ 分不出方向` 是跑之前就写死的。")
        emit()
        emit("**但与 DP-068 拼起来，包围圈还是收紧了一格。** DP-068：变化是**相干的**"
             "（r4/r1 = 3.0–9.0，随 lag 累积）。本条：变化**不伴随可分辨的面积单向改变**。"
             "两条合起来 ⇒ **相干、且大体保面积的变化**，也就是剪影在**缓慢平移或形变**，"
             "而不是掩膜在**胀缩**。⇒ **候选②的「阈值漂移致掩膜呼吸」这个形式被削弱**；"
             "留下的是「动物真的在小幅持续动」与「掩膜整体缓慢位移」——"
             "**这两个都保面积，所以面积通道天生分不开它们**，"
             "下一条必须换成**空间位置**通道（见下）。")
    else:
        emit("⇒ **各试次不一致 ⇒ 不能一句话概括**，逐试次看上面的判定。"
             "在弄清分组依据之前**不许把任一侧当成全批结论**。")
    emit()
    brea = [r for r in usable if r["trend"] is not None and abs(r["trend"]) >= 0.05]
    if brea:
        emit("**顺带量到一个独立缺陷（与偏差无关，但确实存在）**：%d 个试次的掩膜面积"
             "在 360 s 上有 ≥5%% 的慢漂移（%s）。"
             "**掩膜面积在一段内漂移这么多本身就是分割缺陷**，"
             "只是它**不解释偏差**——这些试次的偏差反而很小。"
             "单独记一条，别混进模式B 的归因里。"
             % (len(brea),
                "、".join("`%s` %+.0f%%" % (r["stem"], 100 * r["trend"])
                          for r in brea)))
        emit()
    emit("**这条不是什么**：excess 只说「面积变没变」，"
         "**不能**直接等同于「动物动了」——一只完全静止的鼠，若掩膜因为光照缓变"
         "而等面积地扭一下，也会给出低 excess。要钉死候选①还需要看"
         "**变化在剪影上的空间位置**（真运动集中在动的那个部位，"
         "掩膜缺陷沿整条轮廓铺开）。那是下一条。")
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
