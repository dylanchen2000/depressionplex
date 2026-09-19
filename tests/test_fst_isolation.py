"""路径隔离测试：Spec A §4 A1 的验收原话是"路径测试证明未调用 TST 专属判据"。

三道证据，缺一不够：
1. 静态闸干净（AST 扫包 + CLI 入口，零违规）；
2. 动态闸响（真去碰一个 TST 符号，哨兵必须炸）；
3. **整条研究路径在动态闸武装下跑通**——证明"没碰"不是"没写名字"，
   而是执行路径上真的没有它们。
"""

from __future__ import annotations

import numpy as np

from depressionplex.assay_core import rules as tst_rules
from depressionplex.assay_core import segment as tst_segment
from depressionplex.assay_core import silhouette as sil
from depressionplex.fst_research import cup_features as feat
from depressionplex.fst_research import cup_geometry as cg
from depressionplex.fst_research import cup_perception as perc
from depressionplex.fst_research import isolation
from depressionplex.fst_research import record as rec
from depressionplex.fst_research import timeline as tl

import fst_synth


def test_static_audit_clean() -> None:
    violations = isolation.audit_package()
    assert violations == [], f"研究入口引用了 TST 专属符号/被禁模块: {violations}"


def test_static_audit_scans_cli_entry_too() -> None:
    files = isolation._scanned_files()
    names = [p.name for p in files]
    assert "fst_research.py" in names, f"CLI 入口没进扫描范围: {names}"
    assert "__init__.py" in names and "isolation.py" in names


def test_dynamic_guard_booms_on_call() -> None:
    with isolation.tst_forbidden_raising():
        try:
            tst_segment.panel_band(np.zeros((10, 10), dtype=np.uint8))
        except isolation.IsolationViolation as e:
            assert "panel_band" in str(e)
        else:
            raise AssertionError("panel_band 在动态闸下居然能调用")
        try:
            tst_rules.TstRulesParams()
        except isolation.IsolationViolation:
            pass
        else:
            raise AssertionError("TstRulesParams 在动态闸下居然能构造")


def test_dynamic_guard_restores_afterwards() -> None:
    before = tst_segment.panel_band
    with isolation.tst_forbidden_raising():
        assert tst_segment.panel_band is not before
    assert tst_segment.panel_band is before
    # 还原后真函数还能用（合成背光场景）
    g = np.full((20, 30), 40.0)
    g[5:15, :] = 230.0
    assert tst_segment.panel_band(g) == (5, 14)


def test_guard_refuses_to_silently_lose_targets() -> None:
    """武装名单里的落点若消失，动态闸必须响——不许悄悄少武装一个。"""
    saved = isolation.TST_ONLY_SYMBOLS
    isolation.TST_ONLY_SYMBOLS = saved + (("depressionplex.assay_core.rules",
                                           "no_such_symbol_anymore"),)
    try:
        try:
            with isolation.tst_forbidden_raising():
                pass
        except isolation.IsolationViolation as e:
            assert "no_such_symbol_anymore" in str(e)
        else:
            raise AssertionError("落点消失没被发现：动态闸会悄悄少武装")
    finally:
        isolation.TST_ONLY_SYMBOLS = saved


def _run_research_path() -> dict:
    """在调用方武装的动态闸里跑完整研究路径（提案→诊断→特征→记录）。"""
    scene = fst_synth.Scene(cups=2)
    frames = scene.swim_series(30, cup=0)
    props, problems = cg.propose_cups(frames, n_cups=2)
    assert not problems
    median = np.median(np.stack([f.astype(np.float64) for f in frames]), axis=0)
    props = [cg.propose_waterline(median, p) for p in props]
    env = cg.to_envelope(props, (scene.w, scene.h))
    pairs: list = []
    last: dict = {}
    diagnosers = []

    def make_cb(cup_index: int, width_px: int):
        def on_observed(idx, mask, diag, gap_records):
            mm = sil.metrics(mask, with_holes=False)
            theta = mm.theta if mm is not None else 0.0
            prev = last.get(cup_index)
            if prev is not None:
                pairs.append(feat.pair_features(
                    frame_prev=prev[0], frame_cur=idx, fps=25.0,
                    mask_prev=prev[1], mask_cur=mask,
                    cent_prev=prev[2], cent_cur=diag.centroid,
                    theta_prev=prev[3], theta_cur=theta,
                    spatial_scale_px=float(width_px),
                    above_water_frac_cur=diag.above_water_frac,
                    wall_dist_px_cur=diag.wall_dist_px,
                    gap_records_between=gap_records))
            last[cup_index] = (idx, mask, diag.centroid, theta)
        return on_observed

    for p in props:
        diagnosers.append((p, perc.CupDiagnoser(
            p, on_observed=make_cb(p.index, p.width_px))))
    for f in frames:
        for _, d in diagnosers:
            d.see_background(f)
    for i, f in enumerate(frames):
        for _, d in diagnosers:
            d.diagnose(i, f)
    cups = []
    for p, d in diagnosers:
        diags = d.finish()
        counts = rec.counts_of(diags)
        assert rec.check_partition(counts, len(diags)) == []
        cups.append(rec.cup_record(cup_index=p.index, diags=diags, fps=25.0,
                                   declared_absent=False,
                                   geometry={"cup_id": p.cup_id,
                                             "roi": list(p.roi),
                                             "water_body": list(p.water_body) if p.water_body else None,
                                             "confirmed": False},
                                   features_summary=feat.summarize(pairs),
                                   spatial_scale_px=float(p.width_px)))
    tb = tl.TimeBase(fps=25.0, n_frames=len(frames), clock=tl.CLOCK_SOURCE_MEDIA,
                     protocol_alignment=tl.PROTOCOL_ALIGNMENT_UNKNOWN,
                     t0_source_s=None, analysis_offset_s=None)
    return {"env": env, "cups": cups, "plan": tl.plan_window(tb)}


def test_research_path_runs_under_armed_guard() -> None:
    """核心验收：动态闸武装下整条路径跑通 ⇒ 执行路径上没有 TST 判据。"""
    with isolation.tst_forbidden_raising():
        out = _run_research_path()
    assert out["cups"][0]["sampled_state_counts"]["observed"] > 0
    assert out["plan"].applies_standard_window is False
    # 几何提案必然未确认：validate 报"未确认"是设计，不是失败
    assert any("未确认" in p for p in out["env"].validate())


def test_no_csi_import_anywhere_in_package() -> None:
    """研究入口不依赖 CSI 程序/DT/结果才能运行（Spec A §4 A1 末句）。"""
    import ast
    for path in isolation._scanned_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            else:
                continue
            for m in mods:
                assert ".csi" not in m and not m.endswith("csi"), \
                    f"{path.name} import 了 CSI: {m}"
