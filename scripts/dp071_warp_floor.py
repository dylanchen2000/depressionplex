#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-071：`warp_mask` 有个精确的亚像素死区 —— 刚体补偿在不动帧上根本没发生。

**先纠正 DP-070 的一个越界推论。** DP-069/070 明确「不碰刚体拟合、未 warp 直接
XOR」⇒ 它们量的是**补偿前**的帧间差，而 `rad.residual` 是**补偿后**的量。
「补偿前的差沿轮廓铺开」不等于「补偿后剩下的是未被吸收的位移」。
**本条测的结果是：这两者在不动帧上恰好相等——因为补偿是空转。**

**「把 refine 打开」这条路已经被否过了**：`estimate_rigid` 的 docstring 写着
「实测它会主动转动去匹配移位的肢体，把关节运动当成刚体旋转吸收掉，区分度从 5.2
掉到 4.3」。所以本条先不改拟合，而是问：**补偿之后到底剩下了什么。**

## 结构性的发现（与数据无关，纯代码性质）

`warp_mask` 对二值掩膜做双线性采样后**按 0.5 硬二值化**。对二值输入，这等价于
**把位移四舍五入到最近的整数像素**：

    边界像素若在内(1)、邻居在外(0) ⇒ 采样值 = 1-u ⇒ u < 0.5 时仍 ≥ 0.5 ⇒ 不变
    边界像素若在外(0)、邻居在内(1) ⇒ 采样值 = u   ⇒ u < 0.5 时仍 < 0.5 ⇒ 不变

⇒ **任何小于半像素的平移，warp 输出与输入逐位相同 ⇒ 补偿完全没发生。**
旋转同理：30 px 长的剪影转 1° 端点才移 0.26 px，同样落在死区里。

**而不动帧上的真实运动几乎全是亚像素**（DP-067：残差中位 1–6 px，且 ≥25% 的帧
恰好 0 px）⇒ **在最需要补偿的那批帧上，刚体分解退化成了未补偿的原始帧差。**
这一条同时解释了三件已观测到的事：

- **DP-068 的 r4/r1 = 3–9 随 lag 累积**：没有补偿，漂移就线性累积，残差自然 ∝ lag
- **DP-070 的「沿轮廓铺开」**：补偿是空转 ⇒ 补偿前后是同一张图 ⇒ 那个读数**确实**
  适用于残差（结论对，但机制比我原先写的具体得多）
- **`refine=True` 反而更差**：搜索用的是同一个有死区的 warp，能压的本来就不多，
  代价却是把关节运动当刚体吸收掉

## 本脚本做四件事，全部先在合成图上标定，再上真数据

**A1 真伪校验**：整数平移必须精确复现（解析光栅化 XOR warp 结果 = 0）。
不过就不出结论。
**A2 死区扫描**：纯平移 u 从 0.05 到 1.0，报 warp 输出改变了几个像素。
**A3 软掩膜够不够**：最显然的修法是「别硬二值化，用软掩膜比」。**先在这里否掉它**。
**A4 1 px 容差 XOR 的标定**：只数「离对方边界超过 1 px」的分歧像素。
在合成图上看它能不能杀掉抖动又保住真运动。
**B 真数据**：不动帧的死区命中率，以及硬 XOR 与容差 XOR 的**不动/在动可分性
（AUC）**孰高。

## 跑之前就写死的判定

| B 的读数 | 结论 |
|---|---|
| 死区命中率 ≥80% | **补偿在不动帧上确实是空转**（结构性发现在真数据上成立） |
| ΔAUC ≥ +0.03 | 容差方向**有证据**，可作为候选修法提给道俊 |
| ΔAUC ≤ 0 | 容差是**错方向**，本条只留下死区这个发现 |
| 其间 | **分不出，不硬给结论** |

**不改任何常数、不改一行产品 `.py`。** 人工标签是 T2/T0 混杂档（DP-048）⇒
只作定性判别，**不构成改 `residual` 定义或 θ_mob 的授权**；AUC 是帧级量，
而验收门是试次级，**两者不可混用**。

用法：
    python3 scripts/dp071_warp_floor.py                  # 只跑 A（秒级，不碰视频）
    python3 scripts/dp071_warp_floor.py <切片目录> <人工时间线.csv> [输出.md]
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

#: 容差 XOR 的容差半径（像素）。取 1：正好覆盖「亚像素抖动 + 1 px 整数漂移」，
#: 而真肢体运动通常有 2–3 px 以上的深度。**不是可调参数，是本条的判别设定。**
_TOL_PX = 1

#: 判「补偿是空转」的死区命中率门槛。
_DEADZONE_MIN = 0.80

#: 判「容差方向有证据」的 AUC 增量门槛。
_DAUC_GOOD = 0.03

#: 合成标定用的椭圆半轴（像素）：长轴 30 px、宽 12 px，贴近实测 BL 26–33 px。
_ELL_A, _ELL_B = 15.0, 6.0

#: 每类参与统计的帧数上限（够出中位数与 AUC 就行）。
_MAX_FRAMES = 1500

_SIB = Path(__file__).resolve().parent / "dp065_residual_separability.py"
_spec = importlib.util.spec_from_file_location("_dp065", _SIB)
assert _spec is not None and _spec.loader is not None
dp065 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dp065)


# ---------------------------------------------------------------- 基本算子

def _dilate(m: np.ndarray) -> np.ndarray:
    """4-连通二值膨胀 1 px（纯 numpy）。"""
    p = np.zeros((m.shape[0] + 2, m.shape[1] + 2), dtype=bool)
    p[1:-1, 1:-1] = m
    return (p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1]
            | p[1:-1, :-2] | p[1:-1, 2:])


def tol_xor(a: np.ndarray, b: np.ndarray, r: int = _TOL_PX) -> int:
    """容差 XOR：只数**离对方边界超过 r 像素**的分歧像素。

    亚像素抖动造成的分歧全都贴着对方边界 ⇒ 被容差吃掉；
    真运动会把轮廓推出 2–3 px 以上 ⇒ 留得下来。
    """
    da, db = a, b
    for _ in range(r):
        da, db = _dilate(da), _dilate(db)
    return int(np.count_nonzero((a & ~db) | (b & ~da)))


def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """P(随机在动帧的值 > 随机不动帧的值)，平局算 0.5。秩和法，O(n log n)。"""
    if pos.size == 0 or neg.size == 0:
        return float("nan")
    allv = np.concatenate([pos, neg])
    order = allv.argsort(kind="mergesort")
    ranks = np.empty(allv.size, dtype=np.float64)
    ranks[order] = np.arange(1, allv.size + 1, dtype=np.float64)
    # 平局取平均秩
    uniq, inv, cnt = np.unique(allv, return_inverse=True, return_counts=True)
    sums = np.zeros(uniq.size)
    np.add.at(sums, inv, ranks)
    ranks = (sums / cnt)[inv]
    r_pos = ranks[:pos.size].sum()
    return float((r_pos - pos.size * (pos.size + 1) / 2.0)
                 / (pos.size * neg.size))


# ---------------------------------------------------------------- A 合成标定

def raster_ellipse(u: float, v: float, h: int = 96, w: int = 96) -> np.ndarray:
    """解析椭圆按「像素中心落在内部」精确光栅化，中心偏移 (u, v)。

    A 与 B 用**同一条规则**光栅化，只差偏移 ⇒ 两者的差异只能来自 warp。
    """
    yy, xx = np.mgrid[0:h, 0:w]
    cy, cx = h / 2.0, w / 2.0
    return ((((yy - cy - v) / _ELL_A) ** 2
             + ((xx - cx - u) / _ELL_B) ** 2) <= 1.0)


def _shift_t(u: float, v: float, h: int = 96, w: int = 96) -> rad.RigidTransform:
    cy, cx = h / 2.0, w / 2.0
    return rad.RigidTransform(dx=u, dy=v, dtheta=0.0, scale=1.0,
                              c_prev=(cx, cy), c_cur=(cx + u, cy + v))


def _rot_t(deg: float, h: int = 96, w: int = 96) -> rad.RigidTransform:
    cy, cx = h / 2.0, w / 2.0
    return rad.RigidTransform(dx=0.0, dy=0.0, dtheta=float(np.deg2rad(deg)),
                              scale=1.0, c_prev=(cx, cy), c_cur=(cx, cy))


def _soft_warp(m: np.ndarray, u: float) -> np.ndarray:
    """与 warp_mask 同样的采样，但**不二值化**——用来评估「软掩膜」这个修法。"""
    h, w = m.shape
    cy, cx = h / 2.0, w / 2.0
    yy, xx = np.mgrid[0:h, 0:w]
    return rad._sample_bilinear(np.asarray(m, float),
                                (xx - (cx + u)) + cx, (yy - cy) + cy)


def part_a() -> dict:
    m0 = raster_ellipse(0.0, 0.0)

    # A1 真伪校验：整数平移必须精确
    ints = [int(np.count_nonzero(
        np.asarray(rad.warp_mask(m0, _shift_t(du, dv), m0.shape), bool)
        ^ raster_ellipse(du, dv)))
        for du, dv in ((1.0, 0.0), (0.0, 1.0), (2.0, -3.0), (-1.0, 1.0))]

    # A2 死区扫描
    dz = []
    for u in (0.05, 0.1, 0.2, 0.3, 0.4, 0.45, 0.49, 0.5, 0.51, 0.6, 1.0):
        w = np.asarray(rad.warp_mask(m0, _shift_t(u, 0.0), m0.shape), bool)
        dz.append((u, int(np.count_nonzero(w ^ m0))))
    rz = []
    for d in (0.5, 1.0, 1.5, 2.0, 3.0, 5.0):
        w = np.asarray(rad.warp_mask(m0, _rot_t(d), m0.shape), bool)
        rz.append((d, int(np.count_nonzero(w ^ m0)), _ELL_A * np.deg2rad(d)))

    # A3 软掩膜够不够
    soft = []
    for u in (0.02, 0.05, 0.1, 0.2, 0.3):
        b = raster_ellipse(u, 0.0)
        sw = _soft_warp(m0, u)
        hard = int(np.count_nonzero(np.asarray(sw >= 0.5, bool) ^ b))
        soft.append((u, hard, float(np.abs(sw - b.astype(float)).sum())))

    # A4 容差 XOR 标定
    ys, xs = np.nonzero(m0)
    y0 = int(ys.max())
    limb = m0.copy()
    limb[y0 - 1:y0 + 2, 46:49] = True          # 伸出一条"腿"
    limb2 = m0.copy()
    limb2[y0 + 2:y0 + 5, 46:49] = True         # 同一条腿再往外 3 px
    combo = raster_ellipse(0.3, 0.0)
    combo[y0 + 2:y0 + 5, 46:49] = True         # 亚像素漂移 + 肢体位移
    cases = []
    for name, a, b in (
        ("整体亚像素漂移 0.10 px", m0, raster_ellipse(0.1, 0.0)),
        ("整体亚像素漂移 0.49 px", m0, raster_ellipse(0.49, 0.0)),
        ("整体整数漂移 1 px（未补偿）", m0, raster_ellipse(1.0, 0.0)),
        ("整体整数漂移 2 px（未补偿）", m0, raster_ellipse(2.0, 0.0)),
        ("**真肢体位移 3 px**", limb, limb2),
        ("**亚像素漂移 0.3 + 肢体位移 3 px**", limb, combo),
    ):
        cases.append((name, int(np.count_nonzero(a ^ b)), tol_xor(a, b)))

    return dict(int_max=max(ints), dz=dz, rz=rz, soft=soft, cases=cases)


# ---------------------------------------------------------------- B 真数据

def pair_stats(prev: np.ndarray, cur: np.ndarray) -> tuple[float, float, bool, float] | None:
    """→ (硬 XOR px, 容差 XOR px, 补偿是否空转, |平移| px)。

    「空转」的判据不用 dx/dy 代理，而是**直接比 warp 输出与输入是否逐位相同**——
    那才是「补偿没发生」的定义本身。
    """
    t = rad.estimate_rigid(prev, cur)
    if t is None:
        return None
    w = np.asarray(rad.warp_mask(prev, t, cur.shape), bool)
    noop = bool(w.shape == prev.shape and np.array_equal(w, prev))
    return (float(np.count_nonzero(w ^ cur)), float(tol_xor(w, cur)),
            noop, float(np.hypot(t.dx, t.dy)))


def _collect(masks: list[np.ndarray], sel: np.ndarray) -> dict | None:
    idx = np.flatnonzero(sel[:len(masks)])
    idx = idx[idx > 0]
    if idx.size > _MAX_FRAMES:
        idx = idx[np.linspace(0, idx.size - 1, _MAX_FRAMES).astype(int)]
    hard, tol, noop, tr = [], [], [], []
    for i in idx:
        a, b = masks[i - 1], masks[i]
        if a is None or b is None or a.shape != b.shape:
            continue
        r = pair_stats(np.asarray(a, bool), np.asarray(b, bool))
        if r is None:
            continue
        hard.append(r[0]); tol.append(r[1]); noop.append(r[2]); tr.append(r[3])
    if len(hard) < 50:
        return None
    return dict(n=len(hard), hard=np.asarray(hard), tol=np.asarray(tol),
                noop=float(np.mean(noop)), trans=float(np.median(tr)))


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
    imm = _collect(masks, base & ~la)
    mob = _collect(masks, base & la)
    if imm is None or mob is None:
        return {"err": "不动帧或在动帧可用帧不足"}
    return dict(bl=rad.trial_body_length(masks), imm=imm, mob=mob,
                auc_hard=auc(mob["hard"], imm["hard"]),
                auc_tol=auc(mob["tol"], imm["tol"]))


def verdict(dauc: float) -> tuple[str, str]:
    if dauc >= _DAUC_GOOD:
        return ("容差方向有改善",
                "AUC 提高 **%+.3f**（≥%+.2f）⇒ 容差 XOR 把不动/在动分得**更开**"
                % (dauc, _DAUC_GOOD))
    if dauc <= 0:
        return ("容差方向没用",
                "AUC 变化 **%+.3f**（≤0）⇒ 容差没帮上忙，本试次上是错方向" % dauc)
    return ("分不出",
            "AUC 变化 %+.3f，落在 0 与 %+.2f 之间 ⇒ 不硬给结论"
            % (dauc, _DAUC_GOOD))


# ---------------------------------------------------------------- 汇总

def main() -> int:
    argv = sys.argv[1:]
    lines: list[str] = []

    def emit(s: str = "") -> None:
        print(s, flush=True)
        lines.append(s)

    out_path = argv[2] if len(argv) > 2 else None

    emit("# DP-071 `warp_mask` 的亚像素死区：刚体补偿在不动帧上没有发生")
    emit()
    emit("**先纠正 DP-070 的越界推论**：DP-069/070 量的是**补偿前**的帧间差，"
         "而 `rad.residual` 是**补偿后**的量 ⇒ 「补偿前的差沿轮廓铺开」不等于"
         "「补偿后剩下的是未被吸收的位移」。**本条的结果是这两者在不动帧上恰好相等"
         "—— 因为补偿是空转。** 另外「打开 `refine`」已被否过"
         "（docstring：「实测……区分度从 5.2 掉到 4.3」）。")
    emit()
    emit("**结构性的发现，与数据无关**：`warp_mask` 对二值掩膜双线性采样后按 0.5 "
         "硬二值化 ⇒ 对二值输入这等价于**把位移四舍五入到最近整数像素**"
         "（边界像素在内时采样值 = 1−u，u<0.5 仍 ≥0.5 ⇒ 不变；在外时 = u，"
         "u<0.5 仍 <0.5 ⇒ 不变）⇒ **任何小于半像素的平移，warp 输出与输入逐位相同。**")
    emit()

    a = part_a()

    emit("## A1 真伪校验（不过就不出结论）")
    emit()
    emit("整数平移必须精确复现：解析光栅化的平移结果 XOR `warp_mask` 的结果，"
         "四组整数平移的最大值 = **%d px**。" % a["int_max"])
    emit()
    if a["int_max"] != 0:
        emit("⇒ **校验没过 ⇒ 本脚本不出结论。** 光栅化规则与 `warp_mask` 不可比。")
        _write(lines, out_path)
        return 1
    emit("⇒ **校验通过**（整数位移下 warp 是精确的）⇒ 下面测出的死区不是我这边的"
         "光栅化误差。")
    emit()

    emit("## A2 死区扫描：纯平移 u 下，warp 输出改变了几个像素")
    emit()
    emit("| 平移 u (px) | warp 输出改变 |")
    emit("|---|---|")
    for u, n in a["dz"]:
        emit("| %.2f | **%d px**%s" % (u, n, " ← 补偿完全没发生 |"
                                      if n == 0 else " |"))
    emit()
    emit("| 纯旋转 (°) | 端点位移 (px) | warp 输出改变 |")
    emit("|---|---|---|")
    for d, n, tip in a["rz"]:
        emit("| %.1f | %.2f | **%d px**%s" % (d, tip, n,
             " ← 没发生 |" if n == 0 else " |"))
    emit()
    dead_edge = max((u for u, n in a["dz"] if n == 0), default=0.0)
    emit("⇒ **死区在 u < 0.5 px 处是精确的**（实测 u=%.2f 仍然 0 px 改变，"
         "u=0.50 才开始动）。旋转同理：30 px 长的剪影转 1° 端点只移 0.26 px，"
         "同样落在死区里 ⇒ **亚像素刚体补偿在这个 warp 下结构上不可能。**"
         % dead_edge)
    emit()
    emit("**为什么这正好打在最要紧的地方**：DP-067 量到不动帧残差中位只有 1–6 px、"
         "且 ≥25% 的帧恰好 0 px ⇒ 不动帧上的真实运动几乎全是亚像素 ⇒ "
         "**在最需要补偿的那批帧上，刚体分解退化成未补偿的原始帧差。**")
    emit()
    emit("**这一条同时解释了三件已观测到的事**："
         "① **DP-068 的 r4/r1 = 3–9 随 lag 累积** —— 没有补偿，漂移就线性累积；"
         "② **DP-070 的「沿轮廓铺开」** —— 补偿是空转 ⇒ 补偿前后是同一张图 ⇒ "
         "那个读数**确实**适用于残差（结论对，机制比原先写的具体得多）；"
         "③ **`refine=True` 反而更差** —— 搜索用的是同一个有死区的 warp，"
         "能压的本来就不多，代价却是把关节运动当刚体吸收掉。")
    emit()

    emit("## A3 先否掉最显然的修法：改用软掩膜")
    emit()
    emit("| 平移 u (px) | 硬二值 XOR | **软掩膜 L1** |")
    emit("|---|---|---|")
    for u, hd, sf in a["soft"]:
        emit("| %.2f | %d | **%.1f** |" % (u, hd, sf))
    emit()
    worse = all(sf >= hd for _, hd, sf in a["soft"])
    emit("⇒ **软掩膜%s。** 原因是 `cur` 本身是分割出来的**硬二值**掩膜 ⇒ "
         "软对硬的比较沿**整条轮廓**都在累账，比硬对硬还多。"
         "**「别硬二值化」不是修法，这条到此为止。**"
         % ("更差，不是改小了" if worse else "在部分位移下更小，需再看"))
    emit()

    emit("## A4 候选修法的合成标定：1 px 容差 XOR")
    emit()
    emit("只数**离对方边界超过 %d px** 的分歧像素。亚像素抖动造成的分歧全都贴着"
         "对方边界 ⇒ 被吃掉；真运动会把轮廓推出 2–3 px 以上 ⇒ 留得下来。" % _TOL_PX)
    emit()
    emit("| 合成场景（已知答案） | 硬 XOR | **%d px 容差 XOR** |" % _TOL_PX)
    emit("|---|---|---|")
    for name, hd, tl in a["cases"]:
        emit("| %s | %d | **%d** |" % (name, hd, tl))
    emit()
    emit("⇒ **最要紧的是最后一行**：亚像素漂移 0.3 px 叠加真肢体位移 3 px 时，"
         "硬 XOR 读 %d（被漂移主导），容差 XOR 读 %d —— **几乎等于纯肢体运动的 %d** "
         "⇒ 容差把抖动扣掉了，把信号留下了。"
         % (a["cases"][5][1], a["cases"][5][2], a["cases"][4][2]))
    emit()
    emit("**容差不是免费的**：真肢体位移本身从 %d 掉到 %d（约 %.0f%%）⇒ "
         "**灵敏度有损失**，所以必须在真数据上看**净效果**（下面的 AUC），"
         "不能只看抖动被杀掉了就高兴。"
         % (a["cases"][4][1], a["cases"][4][2],
            100.0 * a["cases"][4][2] / max(a["cases"][4][1], 1)))
    emit()

    if len(argv) < 2:
        emit("**只跑了 A（没给切片目录）⇒ B 未做 ⇒ 不出最终结论。**")
        _write(lines, out_path)
        return 0

    root = Path(argv[0])
    human_all = dp065.load_human(Path(argv[1]))

    emit("## B 真数据：死区命中率，以及容差 XOR 的净效果")
    emit()
    emit("「空转」的判据**不用 dx/dy 代理**，而是直接比 warp 输出与输入是否**逐位"
         "相同**——那才是「补偿没发生」的定义本身。AUC = P(随机在动帧的值 > "
         "随机不动帧的值)，**帧级**可分性。")
    emit()
    emit("**≥%.0f%% 死区命中率 ⇒ 补偿确实是空转；ΔAUC ≥ %+.2f ⇒ 容差方向有证据。**"
         % (100 * _DEADZONE_MIN, _DAUC_GOOD))
    emit()
    emit("| 试次 | 角色 | 偏差 | BL | 不动帧 **空转率** | 平移 px | "
         "不动 硬/容差 | 在动 硬/容差 | AUC 硬 | AUC 容差 | **ΔAUC** | 判定 |")
    emit("|---|---|---|---|---|---|---|---|---|---|---|---|")

    res: list[dict] = []
    for stem, bias, role in dp065.TRIALS:
        clip = root / (stem + ".mp4")
        print("== %s ==" % stem, flush=True)
        blank = "| %s | %s | %+.1f | — | — | — | — | — | — | — | — | %s |"
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
        dauc = r["auc_tol"] - r["auc_hard"]
        v, why = verdict(dauc)
        emit("| `%s` | %s | %+.1f | %.1f | **%.0f%%** | %.3f | %.0f/**%.0f** | "
             "%.0f/**%.0f** | %.3f | %.3f | **%+.3f** | **%s** |"
             % (stem, role, bias, r["bl"], 100 * i["noop"], i["trans"],
                np.median(i["hard"]), np.median(i["tol"]),
                np.median(m["hard"]), np.median(m["tol"]),
                r["auc_hard"], r["auc_tol"], dauc, v))
        res.append(dict(stem=stem, role=role, v=v, why=why, dauc=dauc,
                        noop=i["noop"], trans=i["trans"],
                        ih=float(np.median(i["hard"])), it=float(np.median(i["tol"])),
                        mh=float(np.median(m["hard"])), mt=float(np.median(m["tol"])),
                        ah=r["auc_hard"], at=r["auc_tol"]))

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
    nz = sorted(r["noop"] for r in res)
    emit("- 不动帧的**空转率**：中位 **%.0f%%**，范围 %.0f%%–%.0f%%"
         % (100 * nz[len(nz) // 2], 100 * nz[0], 100 * nz[-1]))
    ts = sorted(r["trans"] for r in res)
    emit("- 不动帧的估计平移量：中位 **%.3f px**，范围 %.3f–%.3f px"
         % (ts[len(ts) // 2], ts[0], ts[-1]))
    ds = sorted(r["dauc"] for r in res)
    emit("- ΔAUC：中位 **%+.3f**，范围 %+.3f–%+.3f" % (ds[len(ds) // 2], ds[0], ds[-1]))
    emit()

    dead_ok = nz[len(nz) // 2] >= _DEADZONE_MIN
    emit("**（一）死区在真数据上成立吗**：%s"
         % ("**成立** —— 不动帧里有 %.0f%% 的帧，刚体 warp 的输出与输入**逐位相同**，"
            "即补偿一个像素都没动。结构性发现不只是纸上推导。"
            % (100 * nz[len(nz) // 2]) if dead_ok else
            "**没达到预设的 %.0f%%**（实测中位 %.0f%%）⇒ 补偿并非总是空转，"
            "死区的影响面比推导得小，**按预先声明这条不算成立**。"
            % (100 * _DEADZONE_MIN, 100 * nz[len(nz) // 2])))
    emit()
    n_g = sum(1 for r in res if r["v"] == "容差方向有改善")
    n_b = sum(1 for r in res if r["v"] == "容差方向没用")
    emit("**（二）容差方向的净效果**：有改善 %d / 没用 %d / 分不出 %d（共 %d）。"
         % (n_g, n_b, len(res) - n_g - n_b, len(res)))
    emit()
    if n_g == len(res):
        emit("⇒ **6/6 都改善 ⇒ 容差 XOR 是有证据的候选修法。** "
             "机制上讲得通：死区让亚像素漂移**整份**进了残差，"
             "容差把贴着边界那一圈扣掉，扣掉的正是漂移贡献的部分。")
    elif n_b == len(res):
        emit("⇒ **6/6 都没用 ⇒ 容差是错方向，本条只留下死区这个发现。** "
             "灵敏度的损失盖过了抖动的收益 ⇒ 该往「让 warp 真的能做亚像素」"
             "（升采样、或距离场表示）而不是「让残差变钝」走。")
    else:
        emit("⇒ **各试次不一致 ⇒ 不能一句话概括**，逐试次看上面的判定。"
             "**在弄清分组依据之前不许把任一侧当成全批结论。**")
    emit()
    emit("**这条不是什么**：① **不构成改 `residual` 定义或 θ_mob 的授权**——"
         "换残差定义是**契约级**改动，θ_mob 的含义随之变，必须在 T1 上重新标定"
         "（DP-048/DP-055）；② **AUC 是帧级量，验收门是试次级**，两者不可混用，"
         "帧级 AUC 提高**不自动**意味着 G7/G8 会变好；"
         "③ 容差半径 %d px **不是可调参数**，是本条的判别设定——"
         "把它当旋钮去调就成了「重标门槛」那条被禁的路；"
         "④ n=6，与 DP-067/068/069/070 同一批试次、同一套人工标签。" % _TOL_PX)
    emit()
    emit("**下一步的两条候选（都要道俊点头，不在本条授权内）**："
         "① **让 warp 真的能做亚像素**——升采样 2–4 倍后再 warp，或改用有符号距离场"
         "表示（治本，但改的是 `warp_mask`，会动所有下游数字）；"
         "② **容差 XOR**（治标，改的是 `residual` 的定义，同样动所有下游数字）。"
         "**两条都必须先在 T1 上重新标定 θ_mob 才能谈验收。**")
    _write(lines, out_path)
    return 0


def _write(lines: list[str], out_path: str | None) -> None:
    if out_path:
        Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n已写出 " + out_path)


if __name__ == "__main__":
    raise SystemExit(main())
