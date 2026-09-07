#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-058：BL 敏感性——一个 BL 估计误差值多少秒 immobility？

**为什么问这个。** DP-055 实测逐试次偏差平均 35.15 s、达标 5/26，但平均偏差只有
+1.74 s、回归斜率 0.933 ⇒ 不是整体偏移，是每个试次各自往两边偏。找归因时从
DP-053 的 27 份日志里量到一件事：标定期 BL 估计（`corridor.bl_est`）在 27 个
试次上跨 21.86–49.69 px（**2.27 倍**），同一段录像的四个隔间之间就能差 **94%**
（`30mg_2周`：ch1=25.7 / ch2=49.7）。同批同种鼠、同机位、同距离。

**相关不能当归因，两个坑必须先绕开：**

坑一：BL 是**从剪影估的**，剪影形状本身依赖行为——不动的鼠垂直悬吊、主轴长；
挣扎的鼠蜷缩、主轴短。所以 BL 与 immobility 天然相关，**不是误差也会相关**。
实测 r(BL, 人工)=+0.528 就是这个：人工分数不可能受软件内部估的 BL 影响，
这 0.528 全是「BL 反映了真实行为」。r(BL, 软件)=+0.726 只比它高一点，
**高出来的那点才是可疑的**，撑不起归因结论。

坑二：BL 进流水线有**两条**路径，不是一条：
  ① `segment_series` 把 `corridor.bl_est` 传给 `segment_animal`（收口余量
     `_SEAL_BL_K × BL`）⇒ **BL 影响掩膜本身**
  ② `build_tst_features` → `rad.decompose_series(bl=)` → `bl2 = BL²` 做残差
     归一化分母 ⇒ **BL 影响判据**
日志里打印的是①，真正做归一化的是②，两者不是一个量。

**所以这里只回答一个能干净回答的问题**：把掩膜**固定**住（只隔离路径②），
只把归一化 BL 上下扰动，immobility 动多少秒？

判定标准（先写下来，免得看到数字再挑标准）：
- ±15% 的 BL 误差只值几秒 ⇒ BL 不是 DP-055 的一阶误差源，**这条线到此为止，
  回去查 DP-052 的裁剪窗口**；
- 值 17.7 s（G8 门槛）以上 ⇒ BL 是一阶误差源，下一问是它为什么在同批组内漂。

**判定标准事后要补一刀（DP-058-R，2026-09-07 实测后补）。** 上面这两条把「敏感」
直接读成「病因」，不成立。实测 ±15% 的中位摆动 **51.8 s** 远超 17.7 s，但**两个
偏差本来就很小的对照试次（+2.8 / +0.1 s）摆动 42.0 / 57.7 s，和大偏差试次一样狠**
⇒ 敏感性**区分不了准与不准的试次**。所以本探针证明的是「归一化分母是**脆性**
环节」，**不是**「BL 是那 35 s 偏差的病因」。能用的产出是一条**规格**：BL 须准到
**±5.0%**（±9.9% 的 BL 误差就等于当前全部 35.15 s 偏差），这条规格落到 DP-052。

**这个版本扰的是真旋钮。** 上一版扰的是 `label_tst_events(bl=)`，那个 bl 只走到
`climb_rise_frac`（爬尾巴），本批 26 个试次 TailClimbing 一个 bout 都没有 ⇒
实测 immobility 全幅摆动 **0.0 s**，是个不存在的旋钮。DP-058 已修好透传，
`tests/test_rules.py::test_features_bl_scales_residual_quadratically` 钉住了
「分母是活的」这件事。**代价**：真旋钮在 `decompose_series` 里面，每个倍数都要
重跑一遍全帧 RAD（约 1–2 min/切片/倍数），不能像上一版那样只重标一次。

**本脚本不回答路径①**（那要每个 BL 重跑一次分割，再贵一个数量级），
也**不改任何常数**。DP-055 记的纪律照旧：没归因完不许拿「重标门槛」当解法。

用法：
    python3 scripts/dp058_bl_sensitivity.py <切片目录> [输出.md]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from depressionplex import runner, video                      # noqa: E402
from depressionplex.assay_core import rad, rules, trial_report  # noqa: E402

#: 探针试次：4 段大偏差 + 2 段对照。与 T-1 精标那 8 段有重叠是故意的——
#: 人工 T1 数据落地后能直接和这里的敏感性曲线对上。
TRIALS = [
    ("10mg_2周-ch1", -72.5, "大偏差"),
    ("30mg_2周-ch2", +68.5, "大偏差"),
    ("30mg_2周-ch3", -69.5, "大偏差"),
    ("20mg_3周-ch1", +60.7, "大偏差"),
    ("10mg_2周-ch2", +2.8, "对照"),
    ("20mg_1周_1-3+20_2周1-ch2", +0.1, "对照"),
]

#: BL 扰动倍数。±15% 这一档对标的是组内实测离散（`30mg_2周` 组内差 94%）。
#: **但不许把那 94% 整个当成误差**（DP-064）：BL 是从剪影估的，随姿态合法变化，
#: 那 94% 里有多少是误差、多少是姿态，本脚本分不开。所以这组倍数只是**扰动幅度**，
#: 不是「实测误差幅度」。
MULTS = [0.70, 0.85, 1.00, 1.15, 1.30]

#: 主口径字段名。**开跑前先断言它存在**：这一类坑已经踩过两次——
#: `ChamberValidity.reason`（真名 `note`）和 `TrialReport.immobility_s`
#: （真名 `immobility_mirror_pipeline_s`）都是算完 9000 帧才死在打印那一行，
#: 失败时机最坏：算力全花完才炸。断言放在解码前，一秒之内就红。
IMM_FIELD = "immobility_mirror_pipeline_s"


def selfcheck() -> None:
    ann = getattr(trial_report.TrialReport, "__annotations__", {})
    if IMM_FIELD not in ann:
        raise SystemExit(
            "字段名不对：TrialReport 上没有 %s。现有主口径候选：%s"
            % (IMM_FIELD, [k for k in ann if "immobil" in k]))
    # 真旋钮存在性：透传没落地的话整个脚本又白跑一遍（见 docstring 末段）。
    import inspect
    if "bl" not in inspect.signature(rules.build_tst_features).parameters:
        raise SystemExit(
            "build_tst_features 不接受 bl ⇒ 扰的还是死旋钮，先合 DP-058 的透传修复")


def probe_one(clip: Path) -> dict | None:
    """一段单隔间切片：解码 + 分割一次，然后每个倍数重跑一遍 RAD 与判定。"""
    info = video.probe(clip)
    idx = runner.calibration_indices(info.n_frames)
    plan = runner.build_plan(video.frames_at(info, idx), n_chambers=1,
                             calib_indices=tuple(idx))
    seqs = runner.segment_series(video.iter_gray(info), plan)
    ch = plan.chambers[0]
    masks = seqs.get(ch.index)
    if masks is None or len(masks) == 0 or ch.suspension is None:
        return None

    bl_trial = rad.trial_body_length(masks)
    bl_calib = float(ch.corridor.bl_est) if (ch.corridor and ch.corridor.bl_est) else 0.0
    if bl_trial <= 0:
        return None
    cv = {c.chamber: c for c in plan.trial_validity.chambers}.get(ch.index)

    out = {}
    for m in MULTS:
        t0 = time.time()
        # 特征**必须**每个倍数重算：分母在 decompose_series 里面。
        feats = rules.build_tst_features(masks, suspension=ch.suspension,
                                        fps=info.fps, bl=bl_trial * m)
        # 爬尾巴那条路径**固定不扰**：这里要隔离的是归一化分母，不是爬尾巴阈值。
        labels = rules.label_tst_events(feats, bl=bl_trial)
        rep = trial_report.build_trial_report(
            labels, fps=info.fps, assay="TST", trial_id=clip.stem,
            chamber_validity=cv)
        out[m] = getattr(rep, IMM_FIELD)
        print("    ×%.2f ⇒ %s（%.0f s）" % (m, out[m], time.time() - t0),
              flush=True)
    return dict(bl_trial=bl_trial, bl_calib=bl_calib, imm=out,
                fps=info.fps, n=info.n_frames)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    selfcheck()
    root = Path(sys.argv[1])
    lines = []

    def emit(s: str = "") -> None:
        print(s, flush=True)
        lines.append(s)

    emit("# DP-058 BL 敏感性实测")
    emit()
    emit("掩膜固定（只隔离归一化路径②），只扰 `build_tst_features(bl=)`，"
         "爬尾巴那条路径固定不扰。")
    emit()
    emit("| 试次 | 角色 | 目前偏差 | 标定BL① | 试次BL② | "
         + " | ".join("×%.2f" % m for m in MULTS) + " | 全幅摆动 |")
    emit("|---|---|---|---|---|" + "---|" * (len(MULTS) + 1))

    rows = []
    for stem, bias, role in TRIALS:
        clip = root / (stem + ".mp4")
        print("== %s ==" % stem, flush=True)
        if not clip.exists():
            emit("| %s | %s | %+.1f | — | — | %s | 素材不存在 |"
                 % (stem, role, bias, " | ".join("—" for _ in MULTS)))
            continue
        try:
            r = probe_one(clip)
        except Exception as e:                                  # noqa: BLE001
            emit("| %s | %s | %+.1f | — | — | %s | 失败：%s |"
                 % (stem, role, bias, " | ".join("—" for _ in MULTS), str(e)[:60]))
            continue
        if r is None:
            emit("| %s | %s | %+.1f | — | — | %s | 不产数字 |"
                 % (stem, role, bias, " | ".join("—" for _ in MULTS)))
            continue
        vals = [r["imm"][m] for m in MULTS]
        if any(v is None for v in vals):
            # scored=False ⇒ 主口径是 None。**不许拿 0 顶上**（DP-032 的整条
            # 事故链就建在「没看见」被当成「看见了没动」）。
            emit("| %s | %s | %+.1f | %.2f | %.2f | %s | 未计分（主口径 None） |"
                 % (stem, role, bias, r["bl_calib"], r["bl_trial"],
                    " | ".join("—" if v is None else "%.1f" % v for v in vals)))
            continue
        span = max(vals) - min(vals)
        rows.append((stem, role, bias, r, span))
        emit("| %s | %s | %+.1f | %.2f | %.2f | %s | **%.1f s** |"
             % (stem, role, bias, r["bl_calib"], r["bl_trial"],
                " | ".join("%.1f" % v for v in vals), span))

    if rows:
        emit()
        emit("## 判定")
        emit()
        spans = [s for *_, s in rows]
        emit("BL ×0.70…×1.30 之间 immobility 的全幅摆动："
             "中位 **%.1f s**，最大 **%.1f s**，最小 %.1f s。"
             % (sorted(spans)[len(spans) // 2], max(spans), min(spans)))
        emit()
        narrow = []
        for stem, role, bias, r, _ in rows:
            d = abs(r["imm"][1.15] - r["imm"][0.85])
            narrow.append(d)
            emit("- `%s`（%s，偏差 %+.1f s）：BL ±15%% ⇒ immobility 差 **%.1f s**"
                 % (stem, role, bias, d))
        emit()
        med = sorted(narrow)[len(narrow) // 2]
        emit("**±15%% 这一档的中位摆动 = %.1f s。** 对照 G8 的门槛 17.7 s 与 "
             "DP-055 实测的平均绝对偏差 35.15 s：" % med)
        if med >= 17.7:
            emit()
            emit("⇒ **归一化分母是脆性环节**：仅 ±15% 的 BL 扰动就能单独吃掉整个 "
                 "G8 门槛。**但敏感 ≠ 病因**（DP-058-R）：看上面逐试次那一行——"
                 "偏差本来就很小的**对照**试次摆动得和大偏差试次一样狠 ⇒ 敏感性"
                 "**区分不了准与不准**，所以这不能证明 BL 是 35 s 偏差的病因。"
                 "**能用的产出是一条规格**：BL 须准到 **±5.0%**（±9.9% 的 BL 误差"
                 "就等于当前全部 35.15 s 偏差）。**下一步：DP-052 把 BL 做到 ±5%**，"
                 "而不是去调 θ_mob。")
        else:
            emit()
            emit("⇒ **BL 不是一阶误差源**（±15%% 只值 %.1f s < 17.7 s）。"
                 "这条线到此为止，**回去查 DP-052 的裁剪窗口**。"
                 "组内 BL 离散仍是缺陷，但那 94%% 里有多少是误差、多少是合法姿态，"
                 "本脚本分不开（DP-064），而且它解释不了 35 s 的偏差。" % med)
        emit()
        emit("**本条不构成改常数的授权。** DP-055 的纪律照旧：没归因完不许拿"
             "「重标门槛」当解法；本脚本一个常数都没动，也没跑 LOVO。")
        emit()
        emit("**未回答的**：路径①（`corridor.bl_est` → 收口余量 → 掩膜本身）。"
             "那要每个 BL 重跑一次分割，贵一个数量级，等本条的判定出来再决定值不值。")

    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n已写出 " + sys.argv[2])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
