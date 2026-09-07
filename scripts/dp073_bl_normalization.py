#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-073：残差的 BL² 归一化在本批数据上比**完全不归一化**更差。

## 上游给的局面

- **DP-058 写下了规格**：归一化用的 BL 须准到 **±5.0%**（实测 ±15% 的 BL 误差
  值 51.8 s immobility 中位摆动，而全部偏差是 35.15 s）。
- **DP-058 同时把两个 BL 分开了**：`corridor.bl_est`（标定期 24 帧）在 27 试次上
  跨 2.27 倍、同录像组内差 94% ⇒ 判它不可信、**不许顶到分母上**；而
  `rad.trial_body_length`（全帧主轴长中位数）被当成「另一个量」留在分母里。
- **本条测的就是留在分母里的那一份。** 结论：它有同样的病。

## 三件事，每件都配一个对照

**① 分母本身超规格约 7 倍，而且是在解剖学被控住的比较里超的。**
同一段录像的 ch1–ch4 是**同一台相机、同一时刻、并排的同批同种鼠** ⇒ 真实体长
差异应在 ±10% 量级。实测「同录像**且**同批」组内 BL 极差比 **1.12–1.91、中位
1.34（即 ±34%）**，是 DP-058 立的 ±5.0% 规格的 7 倍。
**对照（这一步是必须的，否则数字虚高）**：拼接切片（名字含 `+`）里 ch4 来自另一
批 ⇒ 不能拿跨批体型差异充当测量误差 ⇒ ch4 单独成组。不做这个拆分时中位是 1.46，
拆分后降到 1.34 ⇒ **结论没有靠拼接片撑着，只是幅度小一点。**

**② 不是腔位（透视）效应。** **腔位之间**的 BL 中位只差 26.8–30.3（极差比
**1.13x**），而**每个腔位内部**的极差比就有 **1.42–1.77x**；腔位内去均值后
rho(BL, 偏差) 从 +0.356 只降到 +0.325 ⇒ 离散在腔位内部，不在腔位之间。

**③ BL² 归一化让「一个固定门槛通用」这件事变得更难，不是更容易。**
判别量：**每试次自己的最优残差门槛**（在该试次自己的帧上最大化 TPR−FPR，
锚在**人工**标签上）。归一化如果做对了，这个门槛在各试次间应当**一致**。
实测它跨 **19.3 倍**。把同一批原始像素门槛换算到 BL^0 / BL^1 / BL^2 三种归一化下
比离散度：**5.3x / 10.1x / 19.3x** ⇒ **单调地越归一化越差，除以 BL² 比完全不归
一化差 3.6 倍。** log-log 斜率 −1.08 / −2.08 / −3.08 ⇒ 原始像素门槛本身就与 BL
**负**相关（若 BL 是干净长度，XOR 面积 ∝ BL² ⇒ 应当是 **+2**）。

## 判别量先标定，读不对就不出结论

合成三种**已知真相**（原始像素门槛分别 ∝ BL^0 / BL^1 / BL^2，叠 15% 对数正态
噪声，BL 用真实的那 26 个值），看这个判别量能不能各自指对。**三次都必须指对**，
否则说明「低指数看起来总是更好」是判别量自己的偏性 ⇒ `return 1`、不出结论。

## 这条不是什么

- **不是改 θ_mob 或任何常数的授权。** 最优门槛锚在 T2/T0 混杂档人工标签上，
  DP-048 已裁明混杂档不能定门；本条只做归因。
- **不报秒数。** 门槛离散度是「一个固定门槛能不能通用」的代理，**降低它不自动
  意味着 G7/G8 变好**——那要真跑一遍。要秒数请引 DP-058 已测的敏感度。
- **不是说 BL² 在物理上错了。** 若 BL 是干净的长度，XOR 面积 ∝ BL² ⇒ BL² 正确。
  本条说的是**实测的这个 BL 主要是误差**（同录像内就差 1.46 倍）⇒ 除以一个噪声量
  的平方，把误差按平方放大了进来。
- **不做组间归因**（DP-072）。每试次最优门槛是**在该试次自己的帧上**算的，
  是试次内量，不受 DP-065 六试次面板那个混杂影响。

用法：
    python3 scripts/dp073_bl_normalization.py <DP-065全量输出.md> <冻结对比CSV> [输出.md]
"""
from __future__ import annotations

import csv
import math
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

#: 判别量标定时叠的对数正态噪声（标准差，对数尺度）。不是可调参数，是标定设定。
_CAL_SIGMA = 0.15

#: 合成标定的随机种子，保证可复现。
_CAL_SEED = 7

#: DP-058 立下的 BL 精度规格（相对误差）。本条拿它当「超了多少」的尺子。
_BL_SPEC = 0.05

#: 现行冻结的 mobility 门槛（无量纲，残差 / BL²）。只用于换算，不改。
_THETA_MOB = 0.0175


# ------------------------------------------------------------------ 统计工具

def _rank(v: list[float]) -> list[float]:
    """平均秩（处理平局）。"""
    s = sorted(range(len(v)), key=lambda i: v[i])
    rk = [0.0] * len(v)
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and v[s[j + 1]] == v[s[i]]:
            j += 1
        m = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            rk[s[k]] = m
        i = j + 1
    return rk


def pearson(a: list[float], b: list[float]) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((x - mb) ** 2 for x in b))
    if sa <= 0 or sb <= 0:
        return float("nan")
    return sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (sa * sb)


def spearman(a: list[float], b: list[float]) -> float:
    return pearson(_rank(a), _rank(b))


def partial(a: list[float], b: list[float], c: list[float]) -> float:
    """控制 c 之后 a 与 b 的偏 Spearman 相关。"""
    rab, rac, rbc = spearman(a, b), spearman(a, c), spearman(b, c)
    den = math.sqrt(max((1 - rac ** 2) * (1 - rbc ** 2), 1e-12))
    return (rab - rac * rbc) / den


def loglog_slope(x: list[float], y: list[float]) -> float:
    lx = [math.log(v) for v in x]
    ly = [math.log(v) for v in y]
    n = len(lx)
    mx, my = sum(lx) / n, sum(ly) / n
    den = sum((v - mx) ** 2 for v in lx)
    return sum((lx[i] - mx) * (ly[i] - my) for i in range(n)) / den


def dispersion(v: list[float]) -> tuple[float, float]:
    """→ (极差比 max/min, CV%)。"""
    s = sorted(v)
    m = sum(v) / len(v)
    cv = 100 * math.sqrt(sum((x - m) ** 2 for x in v) / len(v)) / m
    return (s[-1] / s[0] if s[0] > 0 else float("inf"), cv)


def exponent_table(bl: list[float], raw_px: list[float]) -> list[tuple]:
    """对 BL^0 / BL^1 / BL^2 三种归一化，报 (指数, 极差比, CV%, rho, 斜率)。"""
    out = []
    for p in (0, 1, 2):
        v = [raw_px[i] / bl[i] ** p for i in range(len(bl))]
        d, cv = dispersion(v)
        out.append((p, d, cv, spearman(bl, v), loglog_slope(bl, v)))
    return out


# ------------------------------------------------------------------ 标定硬门

def self_test(bl: list[float]) -> tuple[bool, list[str]]:
    """合成三种已知真相，检查判别量能不能各自指对。三次全对才放行。

    **为什么必须有这一步**：本条的结论是「低指数更好」。如果判别量本身对低指数
    有偏性（比如除以一个离散的量必然抬高 CV），那结论就是判别量的假象。
    造真相 = BL^2 的数据，判别量必须选中 BL^2。
    """
    rng = random.Random(_CAL_SEED)
    lines, ok = [], True
    for truth in (0, 1, 2):
        raw = [(0.012 * b ** truth) * math.exp(rng.gauss(0.0, _CAL_SIGMA)) for b in bl]
        tab = exponent_table(bl, raw)
        pick = min(tab, key=lambda z: z[2])[0]
        good = pick == truth
        ok = ok and good
        lines.append("| 真相 = BL^%d | %s | **BL^%d** | %s |"
                     % (truth,
                        " / ".join("%.1f%%" % cv for _, _, cv, _, _ in tab),
                        pick, "指对" if good else "**指错**"))
    return ok, lines


# ------------------------------------------------------------------ 读数据

def parse_dp065(path: Path) -> list[dict]:
    """从 DP-065 全量输出里解析逐试次读数。字段缺一个就报错，不静默跳过。"""
    blocks = re.split(r"^### ", path.read_text(encoding="utf-8"), flags=re.M)[1:]
    out = []
    for b in blocks:
        def g(pat: str, cast=float):
            m = re.search(pat, b)
            if m is None:
                raise SystemExit("解析失败，缺 %r，块首=%r" % (pat, b[:40]))
            return cast(m.group(1))
        stem = g(r"`([^`]+)`", str)
        out.append(dict(
            stem=stem,
            role=g(r"（([^，]+)，偏差", str),
            bias=g(r"偏差 ([-+][\d.]+) s）"),
            bl=g(r"BL ([\d.]+) px"),
            mob_pct=g(r"「在动」(\d+)%"),
            n_frames=g(r"稳态可用帧 (\d+)", int),
            imm_med=g(r"不动\*\*）：[\d.]+ / \*\*([\d.]+)\*\*"),
            mob_med=g(r"在动\*\*）：[\d.]+ / \*\*([\d.]+)\*\*"),
            tpr=g(r"不动的帧有 \*\*(\d+)%\*\* 落在门下"),
            fpr=g(r"在动的帧有 \*\*(\d+)%\*\* 也落在门下"),
            jth=g(r"门槛落在 \*\*([\d.]+)\*\*"),
            jval=g(r"（J = ([\d.]+)）"),
        ))
    return out


def parse_frozen(path: Path) -> dict[str, dict]:
    return {r["trial_id"]: r for r in csv.DictReader(path.open(encoding="utf-8"))}


def cohort_of(stem: str) -> str:
    """同录像**且**同批的分组键。

    拼接切片（名字含 `+`，如 `30mg_2周_1-3+20_1周1`）里 ch1–ch3 与 ch4 来自不同批 ⇒
    ch4 单独成组，免得拿跨批体型差异当成测量误差。
    """
    m = re.match(r"(.+)-ch(\d)$", stem)
    if m is None:
        raise SystemExit("试次名不含腔位：" + stem)
    vid, ch = m.group(1), int(m.group(2))
    if "+" in vid:
        return "%s#%s" % (vid, "1-3" if ch <= 3 else "4")
    return vid


# ------------------------------------------------------------------ 汇总

def main() -> int:
    argv = sys.argv[1:]
    if len(argv) < 2:
        print(__doc__)
        return 2
    lines: list[str] = []

    def emit(s: str = "") -> None:
        print(s, flush=True)
        lines.append(s)

    rows = parse_dp065(Path(argv[0]))
    frozen = parse_frozen(Path(argv[1]))
    out_path = argv[2] if len(argv) > 2 else None

    bl = [r["bl"] for r in rows]
    bias = [r["bias"] for r in rows]
    jth = [r["jth"] for r in rows]
    tpr = [r["tpr"] for r in rows]
    fpr = [r["fpr"] for r in rows]
    jval = [r["jval"] for r in rows]
    imm = [r["imm_med"] for r in rows]
    mob = [r["mob_med"] for r in rows]
    mobpct = [r["mob_pct"] for r in rows]
    missing = [r["stem"] for r in rows if r["stem"] not in frozen]
    if missing:
        emit("**冻结表里缺这些试次，不出结论**：" + "、".join(missing))
        _write(lines, out_path)
        return 1
    hum = [float(frozen[r["stem"]]["human_immobility_mean_s"]) for r in rows]
    unk = [float(frozen[r["stem"]]["unknown_fraction_window"]) for r in rows]
    raw_px = [jth[i] * bl[i] ** 2 for i in range(len(rows))]

    emit("# DP-073 残差的 BL² 归一化在本批数据上比完全不归一化更差")
    emit()
    emit("**DP-058 立的规格是 BL 须准到 ±%.0f%%**（它实测 ±15%% 的 BL 误差值 51.8 s "
         "immobility 摆动）。DP-058 把 `corridor.bl_est` 判为不可信、不许顶到分母上，"
         "但**留在分母里的 `rad.trial_body_length` 没被同样审过**。本条审它。" % (100 * _BL_SPEC))
    emit()

    # ---- 标定硬门 ----
    emit("## 0 判别量标定（读不对就不出结论）")
    emit()
    emit("造三种**已知真相**的合成数据（原始像素门槛 ∝ BL^0/BL^1/BL^2，叠 %.0f%% "
         "对数正态噪声，BL 用真实的这 %d 个值），看判别量能不能各自指对。"
         % (100 * _CAL_SIGMA, len(rows)))
    emit()
    emit("| 合成真相 | BL^0 / BL^1 / BL^2 的 CV | 判别量选中 | 结果 |")
    emit("|---|---|---|---|")
    ok, cal = self_test(bl)
    for ln in cal:
        emit(ln)
    emit()
    if not ok:
        emit("⇒ **标定没过 ⇒ 不出结论。** 判别量对某个指数有偏性，先修判别量。")
        _write(lines, out_path)
        return 1
    emit("⇒ **三次全指对** ⇒ 判别量**有能力**读出「BL² 才对」这个相反答案 ⇒ "
         "下面真数据上的结论不是判别量自己的偏性。")
    emit()

    # ---- ① 分母的离散度 ----
    grp: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        grp[cohort_of(r["stem"])].append(r)
    _rt = sorted(max(x["bl"] for x in v) / min(x["bl"] for x in v)
                 for v in grp.values() if len(v) > 1)
    med_rt = _rt[len(_rt) // 2]
    emit("## 1 分母本身：同一段录像内就超规格约 %.0f 倍"
         % ((med_rt - 1) / _BL_SPEC))
    emit()
    emit("同录像的 ch1–ch4 是**同一台相机、同一时刻、并排的同批同种鼠** ⇒ "
         "真实体长差异应在 ±10% 量级。拼接切片（名字含 `+`）的 ch4 来自另一批 ⇒ "
         "单独成组，**不拿跨批体型差异充当测量误差**。")
    emit()
    emit("| 同录像同批组 | 各腔 BL (px) | 极差比 |")
    emit("|---|---|---|")
    ratios = []
    for k in sorted(grp):
        v = sorted(grp[k], key=lambda r: r["stem"])
        bls = [r["bl"] for r in v]
        if len(bls) < 2:
            emit("| `%s` | %s | 单腔，不参与 |" % (k, "%.2f" % bls[0]))
            continue
        rt = max(bls) / min(bls)
        ratios.append(rt)
        emit("| `%s` | %s | **%.2fx** |" % (k, " / ".join("%.2f" % x for x in bls), rt))
    emit()
    rs = sorted(ratios)
    emit("⇒ **组内极差比中位 %.2fx（范围 %.2f–%.2f），即 ±%.0f%%，是 DP-058 规格 ±%.0f%% 的 %.0f 倍。**"
         % (rs[len(rs) // 2], rs[0], rs[-1], 100 * (rs[len(rs) // 2] - 1),
            100 * _BL_SPEC, (rs[len(rs) // 2] - 1) / _BL_SPEC))
    bs = sorted(bl)
    emit("全 %d 试次 BL 跨 **%.2f–%.2f px（%.2fx）** —— 与 DP-058 报的 `bl_est` 的 2.27x "
         "是同一量级 ⇒ **「全帧中位数」并没有比「标定期估计」干净。**"
         % (len(rows), bs[0], bs[-1], bs[-1] / bs[0]))
    emit()

    # ---- ② 腔位对照 ----
    emit("## 2 对照：不是腔位（透视）效应")
    emit()
    ch_grp: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        ch_grp[re.search(r"-ch(\d)$", r["stem"]).group(1)].append(i)
    emit("| 腔位 | n | BL 中位 | BL 范围 | 偏差中位 |")
    emit("|---|---|---|---|---|")
    for c in sorted(ch_grp):
        ids = ch_grp[c]
        b2 = sorted(bl[i] for i in ids)
        d2 = sorted(bias[i] for i in ids)
        emit("| ch%s | %d | %.2f | %.1f–%.1f | %+.1f |"
             % (c, len(ids), b2[len(b2) // 2], b2[0], b2[-1], d2[len(d2) // 2]))
    rb, rbi = [], []
    for ids in ch_grp.values():
        mb = sum(bl[i] for i in ids) / len(ids)
        mi = sum(bias[i] for i in ids) / len(ids)
        for i in ids:
            rb.append(bl[i] - mb)
            rbi.append(bias[i] - mi)
    emit()
    ch_med = [sorted(bl[i] for i in ids)[len(ids) // 2] for ids in ch_grp.values()]
    ch_rt = [max(bl[i] for i in ids) / min(bl[i] for i in ids)
             for ids in ch_grp.values()]
    emit("⇒ **腔位之间**的 BL 中位只差 %.1f–%.1f（极差比 %.2fx），"
         "而**每个腔位内部**的 BL 极差比就有 %.2f–%.2fx；"
         "腔位内去均值后 rho(BL, 偏差) = **%+.3f**（未去均值 %+.3f，几乎没掉）⇒ "
         "**BL 的离散主要在腔位内部，不是腔位（透视）效应。**"
         % (min(ch_med), max(ch_med), max(ch_med) / min(ch_med),
            min(ch_rt), max(ch_rt), spearman(rb, rbi), spearman(bl, bias)))
    emit()
    # ---- ③ 指数比较（本条的正题） ----
    emit("## 3 正题：BL² 归一化让「一个固定门槛通用」变得更难")
    emit()
    emit("**判别量**：每试次**自己**的最优残差门槛——在该试次自己的稳态可用帧上"
         "最大化 TPR−FPR（Youden J），锚在**人工**标签上。"
         "归一化如果实现了尺度不变，这个门槛在各试次间应当**一致**（离散度→1x）。"
         "它是**试次内**量 ⇒ 不受 DP-072 那个六试次面板混杂的影响。")
    emit()
    emit("把同一批门槛换算回**原始像素**（门槛 × BL²），再分别除以 BL^0 / BL^1 / BL^2：")
    emit()
    emit("| 归一化 | 极差比 max/min | CV | rho(BL, 门槛) | log-log 斜率 |")
    emit("|---|---|---|---|---|")
    tab = exponent_table(bl, raw_px)
    for p, d, cv, rho, sl in tab:
        tag = "**BL^%d（现行）**" % p if p == 2 else "BL^%d%s" % (p, "（原始像素）" if p == 0 else "")
        emit("| %s | %s | %s | %+.3f | %+.2f |"
             % (tag,
                ("**%.1fx**" % d) if p in (0, 2) else "%.1fx" % d,
                ("**%.1f%%**" % cv) if p in (0, 2) else "%.1f%%" % cv,
                rho, sl))
    emit()
    d0, d2 = tab[0][1], tab[2][1]
    emit("⇒ **单调地越归一化越差**：除以 BL² 的离散度是**完全不归一化的 %.1f 倍**"
         "（%.1fx vs %.1fx）。log-log 斜率也是单调的 %+.2f → %+.2f → %+.2f："
         "原始像素门槛本身就已经与 BL **负相关**（斜率 %+.2f，理想是 0），"
         "每除一次 BL 就再减 1 ⇒ **BL² 把这个负相关放大到 %+.2f。**"
         % (d2 / d0, d2, d0, tab[0][4], tab[1][4], tab[2][4], tab[0][4], tab[2][4]))
    emit()
    emit("**这个方向本身就是诊断**：若 BL 是干净的长度，则 XOR 面积 ∝ BL² ⇒ 原始像素门槛"
         "应当**正**比于 BL²（斜率 +2）⇒ 除以 BL² 后斜率归零。实测原始像素门槛与 BL "
         "**反**着走（斜率 %+.2f）⇒ **BL 大的试次原始像素门槛反而低** ⇒ "
         "**这个 BL 承载的主要不是「鼠有多长」，而是「分割糊到什么程度」**"
         "（下一节的 rho(BL, unknown) 支持这个读法）。"
         % tab[0][4])
    emit()

    # ---- ④ 相关性 + DP-064 人工基线 ----
    emit("## 4 相关性（每一条都配 DP-064 要求的人工基线）")
    emit()
    emit("Spearman，n=%d ⇒ |rho| > 0.39 约当 p<0.05。" % len(rows))
    emit()
    emit("| 与 | BL | **人工不动时长（DP-064 基线）** |")
    emit("|---|---|---|")
    for name, v in (("J 最优门槛", jth), ("偏差", bias), ("unknown 占窗口比", unk),
                    ("FPR@冻结门", fpr), ("J 值（区分力）", jval),
                    ("不动帧残差中位", imm), ("在动帧残差中位", mob)):
        rb_ = spearman(bl, v)
        rh_ = spearman(hum, v)
        emit("| %s | %s | %+.3f |"
             % (name, ("**%+.3f**" % rb_) if abs(rb_) > 0.5 else "%+.3f" % rb_, rh_))
    emit("| 人工不动时长 | %+.3f | — |" % spearman(bl, hum))
    emit()
    emit("**偏相关把因果方向拆开了**：控制人工不动时长之后 rho(BL, J门槛) = **%+.3f**"
         "（几乎没掉，原值 %+.3f）；反过来控制 BL 之后 rho(人工, J门槛) 从 %+.3f "
         "**塌到 %+.3f** ⇒ **是 BL 在驱动门槛位置，不是「这只鼠真的不动得多」。**"
         % (partial(bl, jth, hum), spearman(bl, jth),
            spearman(hum, jth), partial(hum, jth, bl)))
    emit()
    emit("**DP-058 的坑一在这里被量到了、也被越过了**：DP-058 指出 BL 本身会随行为变"
         "（不动的鼠悬得长、挣扎的鼠蜷起来）⇒ BL 与不动天然相关，**不能凭 "
         "rho(BL, 偏差) 就说 BL 有误差**。本条不靠那条相关：靠的是"
         "**同录像同批组内**的 BL 极差（第 1 节，行为差异控不住 %.2f 倍）与" % med_rt +
         "**偏相关**（BL 的作用在控住人工不动之后仍然存在）。")
    emit()
    emit("**rho(BL, unknown) = %+.3f** ⇒ BL 大的试次**分割失败也更多** ⇒ "
         "支持「BL 被糊掉的掩膜抬高」而非「鼠真的更长」。"
         "**最极端的一条因果链是单试次的、可以逐环节看的**：" % spearman(bl, unk))
    worst = max(rows, key=lambda r: r["bl"])
    sib = [r for r in rows if cohort_of(r["stem"]) == cohort_of(worst["stem"])
           and r["stem"] != worst["stem"]]
    emit("`%s` 的 BL = **%.2f px**，而同录像同批的邻居只有 %s ⇒ BL² 被抬高约 **%.1f 倍** ⇒ "
         "残差被压低同样倍数 ⇒ 几乎所有帧都掉到 θ_mob 之下 ⇒ "
         "该试次 unknown 占比 **%.2f（全批最高）**、人工只判 %.0f%% 在动、"
         "偏差 **%+.1f s（模式A 里最差）**。"
         % (worst["stem"], worst["bl"],
            " / ".join("%.2f" % r["bl"] for r in sorted(sib, key=lambda r: r["bl"])),
            worst["bl"] ** 2 / (sum(r["bl"] for r in sib) / max(len(sib), 1)) ** 2,
            float(frozen[worst["stem"]]["unknown_fraction_window"]),
            worst["mob_pct"], worst["bias"]))
    emit()

    # ---- ⑤ 冻结门槛的位置 ----
    emit("## 5 顺带量到的：冻结门槛系统性偏高，且偏差来自门槛位置而非区分力")
    emit()
    below = sum(1 for v in jth if v < _THETA_MOB)
    js = sorted(jth)
    emit("- **%d/%d 个试次的最优门槛低于冻结的 θ_mob = %.4f**，中位 **%.5f = 冻结值的 %.0f%%**"
         "，范围 %.5f–%.5f（跨 %.0f 倍）。"
         % (below, len(rows), _THETA_MOB, js[len(js) // 2],
            100 * js[len(js) // 2] / _THETA_MOB, js[0], js[-1], js[-1] / js[0]))
    emit("- 偏差与 **FPR@冻结门 rho = %+.3f**、与 **TPR rho = %+.3f**，"
         "而与 **J 值（区分力）只有 %+.3f** ⇒ "
         "**偏差是门槛位置生成的，不是区分力丢失生成的。**"
         % (spearman(bias, fpr), spearman(bias, tpr), spearman(bias, jval)))
    emit()
    emit("| 模式 | n | TPR@冻结门 中位 | **FPR@冻结门 中位** | J 最优门槛 中位 |")
    emit("|---|---|---|---|---|")
    role_grp: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        role_grp[r["role"]].append(i)
    for k in sorted(role_grp):
        ids = role_grp[k]
        f2 = sorted(fpr[i] for i in ids)
        t2 = sorted(tpr[i] for i in ids)
        j2 = sorted(jth[i] for i in ids)
        emit("| %s | %d | %.0f%% | **%.0f%%** | %.5f |"
             % (k, len(ids), t2[len(t2) // 2], f2[len(f2) // 2], j2[len(j2) // 2]))
    emit()
    emit("⇒ 这是**顺带读数，不是本条的结论**，也**不构成改 θ_mob 的授权**："
         "最优门槛锚在 T2/T0 混杂档人工标签上，DP-048 已裁明混杂档不能定门。"
         "它在这里的作用只有一个——说明「门槛该定在哪」这个问题**在分母修好之前问不出答案**，"
         "因为每个试次的答案差 %.0f 倍。" % (js[-1] / js[0]))
    emit()

    # ---- ⑥ 候选修法的预测 ----
    emit("## 6 候选修法：换分母里的 BL，不动任何常数")
    emit()
    emit("**把每试次的 `rad.trial_body_length` 换成「同录像同批组的中位 BL」**——"
         "θ_mob 不动、残差定义不动、只换一个数从哪来。理由：如果 BL 的离散主要是"
         "**测量误差**，组内取中位就把它平均掉；如果主要是**真实体型差异**，"
         "取中位反而会**变差**。这是个**有可能失败的预测**，所以值得报。")
    emit()
    med_bl = {}
    for k in grp:
        v = sorted(r["bl"] for r in grp[k])
        med_bl[k] = v[len(v) // 2]
    gbl = sorted(bl)[len(bl) // 2]
    emit("| 分母用什么 BL | 极差比 | CV | rho(BL_真, 门槛) |")
    emit("|---|---|---|---|")
    for name, denom in (
        ("每试次自己的 BL（**现行**）", [b ** 2 for b in bl]),
        ("**同录像同批组的中位 BL**", [med_bl[cohort_of(rows[i]["stem"])] ** 2
                                       for i in range(len(rows))]),
        ("全批一个常数 BL = %.2f" % gbl, [gbl ** 2] * len(rows)),
    ):
        v = [raw_px[i] / denom[i] for i in range(len(rows))]
        d, cv = dispersion(v)
        emit("| %s | %.1fx | %.1f%% | %+.3f |" % (name, d, cv, spearman(bl, v)))
    emit()
    emit("⇒ **组内中位 BL 把离散度从 %.1fx 压到与「全批一个常数」同一档** ⇒ "
         "**这批 BL 里有害的那部分方差几乎全在录像内部，也就是几乎全是误差。**"
         % d2)
    emit()
    emit("**注意「全批一个常数」不是修法建议**：它在本批数据上最好，只是因为本批的鼠"
         "体型差异**真的**比 BL 的测量误差小。跨批次、跨品系、跨相机高度就未必 ⇒ "
         "**方向是「让 BL 变准」（组内取中位是最省的一步），不是「取消归一化」。**")
    emit()

    emit("## 这条不是什么")
    emit()
    emit("- **不是改 θ_mob 或任何常数的授权。** 最优门槛锚在混杂档人工标签上"
         "（DP-048），本条只做归因。")
    emit("- **不报秒数。** 门槛离散度是「一个固定门槛能不能通用」的**代理**，"
         "**降低它不自动意味着 G7/G8 变好**——那要真跑一遍 LOVO。"
         "要秒数请引 DP-058 已测的敏感度（±15% BL ⇒ 51.8 s 中位摆动）。")
    emit("- **不是说 BL² 在物理上错了。** 若 BL 是干净长度，XOR 面积 ∝ BL² ⇒ BL² 正确。"
         "本条说的是**实测的这个 BL 主要是误差** ⇒ 除以噪声量的平方，"
         "把误差按平方放进来了。")
    emit("- **不做组间归因**（DP-072）。每试次最优门槛是在该试次自己的帧上算的，"
         "是试次内量。")
    emit("- **不构成对 DP-058 的否定。** DP-058 判 `bl_est` 不可信是对的；"
         "本条补的是它当时**没审**的那一份——`trial_body_length` 有同样的病。")
    emit()
    emit("**没动任何常数、没跑 LOVO、没改一行产品 `.py`。**")
    _write(lines, out_path)
    return 0


def _write(lines: list[str], out_path: str | None) -> None:
    if out_path:
        Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n已写出 " + out_path)


if __name__ == "__main__":
    raise SystemExit(main())
