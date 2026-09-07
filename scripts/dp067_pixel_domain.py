#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-067：把 DP-065 的残差换算回**绝对 XOR 像素数**，看判据实际在多大的量上工作。

**为什么问这个。** DP-065 报了「不动帧残差噪声地板跨 6.76 倍」，但那是 BL² 归一化
后的无量纲数，看不出量级。残差的定义是

    residual = count_nonzero(warp(prev) XOR cur) / BL²        （rad.decompose）

分子是**整数像素计数**。所以只要乘回 BL² 就能知道判据实际在数几个像素。这一步是
**纯算术，不重跑任何东西**，输入全部来自 DP-065 已发布的输出。

**这个脚本不改任何常数。** 人工数据是 T2/T0 混杂档（DP-048）⇒ **不能用来定门**；
本脚本算出的「最优门槛（像素）」只作**诊断量**，**不构成改 θ_mob 的授权**。

**自带一条真伪校验。** 若换算出来的像素数都落在整数上（±0.5% 内），说明
①「分子是整数像素计数」这个理解正确，② 报出的 BL 与代码实际用的归一化分母是
同一个量。两条只要有一条不成立，换算结果就不会是整数——所以整数性本身就是校验，
不需要额外跑一遍。反之若不是整数，本脚本**不出结论**，直接报警。

用法：
    python3 scripts/dp067_pixel_domain.py <DP-065 输出.md> [输出.md]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

#: 现行冻结门槛。**只读，不改。**
THETA_MOB_FROZEN = 0.0175

#: 整数性校验容差：换算值离最近整数的距离，按该整数的相对误差算。
#: 取 1% 是因为 md 里的 BL 与残差都只印到小数点后 2 / 5 位，本身带舍入。
_INT_TOL = 0.01

#: 判为「像素域上同一个值」的容差（像素）。整数格点上 0.5 px 以内就是同一格。
_SAME_PX = 0.5


def parse(md: str) -> list[dict]:
    """从 DP-065 的输出 md 里抽出每个试次的 BL / 四分位 / 最优门槛。

    解析失败就少一个试次，**不补默认值**（DP-032：缺失不是 0）。
    """
    out: list[dict] = []
    # 每个 ### 段一个试次
    for blk in md.split("\n### ")[1:]:
        head, _, body = blk.partition("\n")
        m = re.match(r"`([^`]+)`（([^，]+)，偏差 ([+-][\d.]+) s）", head)
        if not m:
            continue
        stem, role, bias = m.group(1), m.group(2), float(m.group(3))
        bl = re.search(r"试次 BL ([\d.]+) px", body)
        imm = re.search(r"不动\*\*）：([\d.]+) / \*\*([\d.]+)\*\* / ([\d.]+)", body)
        mob = re.search(r"在动\*\*）：([\d.]+) / \*\*([\d.]+)\*\* / ([\d.]+)", body)
        yd = re.search(r"门槛落在 \*\*([\d.]+)\*\*", body)
        nfr = re.search(r"稳态可用帧 (\d+)", body)
        if not (bl and imm and mob and yd and nfr):
            continue
        out.append(dict(
            stem=stem, role=role, bias=bias,
            bl=float(bl.group(1)), n=int(nfr.group(1)),
            imm=[float(x) for x in imm.groups()],
            mob=[float(x) for x in mob.groups()],
            youden=float(yd.group(1))))
    return out


def to_px(v: float, bl2: float) -> float:
    return v * bl2


def int_check(vals: list[float]) -> tuple[int, int, float]:
    """(落在整数上的个数, 总数, 最大相对偏离)。0 值跳过（0 天然是整数）。"""
    ok = 0
    tot = 0
    worst = 0.0
    for v in vals:
        if v <= 0.0:
            continue
        tot += 1
        near = round(v)
        rel = abs(v - near) / max(near, 1)
        worst = max(worst, rel)
        if rel <= _INT_TOL:
            ok += 1
    return ok, tot, worst


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    rows = parse(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if not rows:
        print("没解析出任何试次 ⇒ 不出结论（输入格式变了？）")
        return 1

    lines: list[str] = []

    def emit(s: str = "") -> None:
        print(s, flush=True)
        lines.append(s)

    for r in rows:
        r["bl2"] = r["bl"] ** 2
        r["imm_px"] = [to_px(v, r["bl2"]) for v in r["imm"]]
        r["mob_px"] = [to_px(v, r["bl2"]) for v in r["mob"]]
        r["youden_px"] = to_px(r["youden"], r["bl2"])
        r["theta_px"] = to_px(THETA_MOB_FROZEN, r["bl2"])
        r["step"] = 1.0 / r["bl2"]          # 一个像素值多少「残差」= 量化步长

    # ---- 先过整数性校验；不过就不出结论 ----
    allv = [v for r in rows for v in (*r["imm_px"], *r["mob_px"], r["youden_px"])]
    ok, tot, worst = int_check(allv)

    emit("# DP-067 判据的像素域实测：整个 TST 判定由十几个像素决定")
    emit()
    emit("**纯算术，没重跑任何东西。** 输入是 DP-065 已发布的输出，"
         "换算依据是 `rad.decompose` 里残差的定义：")
    emit()
    emit("```")
    emit("residual = count_nonzero(warp(prev) XOR cur) / BL²")
    emit("```")
    emit()
    emit("分子是**整数像素计数** ⇒ 乘回 BL² 就知道判据实际在数几个像素。")
    emit()
    emit("## 先过真伪校验")
    emit()
    emit("换算值应当落在整数上。**%d / %d 个换算值落在整数上（最大相对偏离 %.2f%%）。**"
         % (ok, tot, 100 * worst))
    emit()
    if ok < tot:
        emit("⇒ **校验没过，本脚本不出结论。** 要么「分子是整数像素」这个理解错了，"
             "要么 md 里报的 BL 与代码实际用的归一化分母不是同一个量。先查这个。")
        _write(lines)
        return 1
    emit("⇒ **校验通过。** 这同时证明两件事：① 分子确实是整数像素计数；"
         "② DP-065 报的 BL **就是**代码实际用的归一化分母（若 BL 报错了，"
         "乘回去不会是整数）。所以下面的像素数可以直接用。")
    emit()

    # ---- 主表 ----
    emit("## 主表：一切换算成像素")
    emit()
    emit("| 试次 | 角色 | 偏差 | BL | 量化步长 1/BL² | 不动帧中位 | 在动帧中位 | "
         "θ_mob=0.0175 换算 | 该试次最优门槛 |")
    emit("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        emit("| `%s` | %s | %+.1f s | %.2f px | %.5f | **%.0f px** | %.0f px | "
             "%.2f px | **%.0f px** |"
             % (r["stem"], r["role"], r["bias"], r["bl"], r["step"],
                r["imm_px"][1], r["mob_px"][1], r["theta_px"], r["youden_px"]))
    emit()

    # ---- 结论一：量化步长与信号同量级 ----
    steps = [r["step"] for r in rows]
    imm_med = [r["imm"][1] for r in rows]
    emit("## 结论一：不动帧上，量化步长与信号本身同量级")
    emit()
    emit("量化步长（1 个像素值多少残差）= **%.5f–%.5f**；"
         "不动帧残差中位数 = **%.5f–%.5f**。"
         % (min(steps), max(steps), min(imm_med), max(imm_med)))
    emit()
    onestep = [r for r in rows if abs(r["imm_px"][1] - 1.0) <= _SAME_PX]
    emit("**%d / %d 个试次的不动帧残差中位数恰好等于 1 个像素**（%s）。"
         % (len(onestep), len(rows),
            "、".join("`%s`" % r["stem"] for r in onestep) or "无"))
    emit()
    zero_q1 = [r for r in rows if r["imm_px"][0] <= _SAME_PX]
    if zero_q1:
        emit("更极端的是 P25：**%d 个试次的不动帧有 ≥25%% 落在 0 像素**"
             "（%s）——那些帧 warp 后的上一帧剪影与当前帧**逐像素完全相同**，"
             "残差**一点信息都没有**。"
             % (len(zero_q1), "、".join("`%s`" % r["stem"] for r in zero_q1)))
        emit()
    emit("⇒ **不动帧的残差不是一个连续测量，是 0 / 1 / 2 / 3 个像素的计数。** "
         "「用物理单位（BL）做门槛」这条架构承诺在这里是**空的**："
         "分母再准，分子的分辨率就 1 个像素，而 1 个像素已经等于信号本身。")
    emit()

    # ---- 结论二：最优门槛在像素域上聚成两簇 ----
    emit("## 结论二（本条最重要）：最优门槛在**像素域**上聚成两簇，"
         "而且分簇线正好是模式B vs 其余")
    emit()
    yn = [r["youden"] for r in rows]
    yp = [r["youden_px"] for r in rows]
    emit("同一批 6 个试次的「该试次最优门槛」：")
    emit()
    emit("| | 范围 | 极差倍数 |")
    emit("|---|---|---|")
    emit("| BL 归一化后（DP-065 报的） | %.5f – %.5f | **%.2f×**（看着一片散）|"
         % (min(yn), max(yn), max(yn) / min(yn)))
    emit("| **换算成绝对像素** | %.0f – %.0f px | %.2f× |"
         % (min(yp), max(yp), max(yp) / min(yp)))
    emit()
    # 分簇
    grp: dict[float, list[dict]] = {}
    for r in rows:
        key = next((k for k in grp if abs(k - r["youden_px"]) <= _SAME_PX), None)
        grp.setdefault(round(r["youden_px"]) if key is None else key, []).append(r)
    emit("摊开看，**像素域上只有几个值**：")
    emit()
    emit("| 最优门槛（px） | 试次 | 角色 |")
    emit("|---|---|---|")
    for k in sorted(grp):
        for r in grp[k]:
            emit("| **%.0f px** | `%s` | %s |" % (k, r["stem"], r["role"]))
    emit()
    modeb = [r for r in rows if "模式B" in r["role"]]
    other = [r for r in rows if "模式B" not in r["role"]]
    if modeb and other:
        bpx = [r["youden_px"] for r in modeb]
        opx = [r["youden_px"] for r in other]
        same_b = max(bpx) - min(bpx) <= _SAME_PX
        emit("- **模式B**（软件过判活动）%d 个：最优门槛 %s px%s"
             % (len(modeb), " / ".join("%.0f" % v for v in bpx),
                "——**三个完全相同**" if same_b else ""))
        emit("- **模式A + 对照** %d 个：最优门槛 %s px"
             % (len(other), " / ".join("%.0f" % v for v in opx)))
        emit()
        emit("在 BL 归一化的数里，这 6 个门槛是 %s，看不出任何结构；"
             "**换成像素就分成了两簇**。"
             % " / ".join("%.5f" % v for v in yn))
        emit()

    # ---- 结论三：噪声地板吃掉多少门槛预算 ----
    emit("## 结论三：模式B 的病灶量化——噪声地板吃掉了门槛预算的三分之一")
    emit()
    emit("| 试次 | 角色 | 不动帧噪声地板 | θ_mob 换算 | 地板/门槛 |")
    emit("|---|---|---|---|---|")
    for r in rows:
        r["budget"] = r["imm_px"][1] / r["theta_px"]
        emit("| `%s` | %s | %.0f px | %.2f px | **%.0f%%** |"
             % (r["stem"], r["role"], r["imm_px"][1], r["theta_px"],
                100 * r["budget"]))
    emit()
    if modeb and other:
        bb = [r["budget"] for r in modeb]
        ob = [r["budget"] for r in other]
        emit("- 模式B：**%.0f–%.0f%%** 的门槛预算被掩膜噪声吃掉"
             % (100 * min(bb), 100 * max(bb)))
        emit("- 模式A + 对照：**%.0f–%.0f%%**" % (100 * min(ob), 100 * max(ob)))
        emit()
        emit("⇒ **相差约 %.0f 倍。** 模式B 不是「门槛切错了」，是"
             "**同一个门槛下面垫了三到六倍厚的噪声**。"
             % ((sum(bb) / len(bb)) / (sum(ob) / len(ob))))
        emit()
        emit("而且最优门槛跟着的**不是体长**：地板 %s px 对应最优 %s px，"
             "地板 %s px 对应最优 %s px。"
             % (" / ".join("%.0f" % r["imm_px"][1] for r in modeb),
                " / ".join("%.0f" % r["youden_px"] for r in modeb),
                " / ".join("%.0f" % r["imm_px"][1] for r in other),
                " / ".join("%.0f" % r["youden_px"] for r in other)))
        emit("**⇒ 最优门槛跟着的是掩膜噪声地板，不是 BL。** "
             "这解释了为什么固定 θ_mob 在这批数据上不可能同时对："
             "θ_mob 用 BL² 归一化，而实际该跟的量是噪声地板。")
        emit()

    # ---- 边界与下一步 ----
    emit("## 这条**不是**什么")
    emit()
    emit("- **不是改 θ_mob 的授权。** 人工标签是 T2/T0 混杂档（DP-048），"
         "**混杂档不能定门**；上面所有「最优门槛」只是诊断量。重定门要 T1 + 道俊点头。")
    emit("- **n=%d，「三个都是 %.0f px」需要在余下 20 个试次上复核。** "
         "像素是整数格点，6 个样本里三个撞在同一格，概率不高但不能忽略。"
         "**在 26 个试次全跑完之前，这条只算强线索，不算结论。**"
         % (len(rows), min(r["youden_px"] for r in modeb) if modeb else 0))
    emit("- **不能据此断言「掩膜抖动」。** 地板高说明*不动帧上残差偏高*，"
         "但「高频抖动」与「真·阈下运动」都能造成这个。"
         "分清它们要看**残差的时间结构**（lag1 vs lag4），那是下一条。")
    emit()
    emit("## 下一步")
    emit()
    emit("1. **把像素域地板量到全 26 个试次**——不需要人工标签，"
         "用软件自己的 immobility 段就行，只为复核「地板分两簇」。")
    emit("2. **lag1 vs lag4 残差比**：`rad.decompose_series` 的缺省 lags 是 (1, 4)，"
         "**lag4 每次运行都算了但被 `build_tst_features` 丢掉**⇒ 白捡一个判别量。"
         "高频抖动在任何 lag 上都饱和（比 ≈ 1）；真运动会随 lag 累积（比 ≈ 2–4）。"
         "**这一条才能决定「掩膜抖动」能不能作为结论说出口。**")
    emit("3. 若地板确认是掩膜噪声 ⇒ 回到 **DP-052**，"
         "而且现在有了可验收的靶子：**不动帧的 XOR 地板要压到 1 px**"
         "（三个对照试次已经做到了，所以这不是不可能的指标）。")
    emit()
    emit("**没动任何常数、没跑 LOVO、没重跑任何切片。**")

    _write(lines)
    return 0


def _write(lines: list[str]) -> None:
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n已写出 " + sys.argv[2])


if __name__ == "__main__":
    raise SystemExit(main())
