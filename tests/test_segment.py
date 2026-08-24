"""S1 阈值分割测试。

多数用例是**回归测试**，锁住 2026-08-24 在真实 TST 素材上踩到的静默失败：
1. `panel_band` 无行达标时曾静默返回整幅高度 → 下游挑到底部收集盒而非动物
2. 缺"不取触边组件"规则时会挑到箱体/收集盒 → 静态物不抖，让噪声门**假通过**
3. `find_chambers` 曾按"亮列段 + 贪心合并"实现，随参数变化把两隔间并成一个
"""

from __future__ import annotations

import numpy as np

from depressionplex.assay_core import segment as S


def _tst_scene(
    *,
    animal_row: int = 150,
    animal_h: int = 30,
    animal_w: int = 12,
    with_box: bool = True,
) -> np.ndarray:
    """合成一个 TST 隔间：亮背光面板 + 顶/底黑框 + 竖直细胶带 + 动物 + 收集盒。"""
    h, w = 268, 95
    g = np.full((h, w), 250.0)          # 背光面板
    g[0:70, :] = 10.0                   # 顶框
    g[259:, :] = 10.0                   # 底框
    g[70:135, 44:50] = 15.0             # 胶带（细、触顶）
    r0 = animal_row
    c0 = (w - animal_w) // 2
    g[r0 : r0 + animal_h, c0 : c0 + animal_w] = 20.0   # 动物
    if with_box:
        g[240:258, 5:90] = 90.0         # 半透明收集盒（中等灰度、软边）
        g[240:258, 5:8] = 30.0
        g[240:258, 87:90] = 30.0
    return g


def test_otsu_lands_between_modes() -> None:
    g = _tst_scene()
    thr = S.otsu_threshold(g)
    assert 40.0 < thr < 230.0, thr


def test_panel_band_returns_none_instead_of_full_height() -> None:
    """回归：无行达标时必须返回 None，不能静默返回整幅高度。

    静默兜底比报错危险——它让下游拿着错误的行带继续算，最终挑到收集盒。
    """
    # 竖条纹：每行恰好一半亮一半暗，故任何行的亮占比都是 0.5，
    # 达不到 0.9。（不能用"全暗"图——Otsu 在均匀图上会把全部像素判为亮。）
    g = np.full((50, 50), 10.0)
    g[:, 25:] = 250.0
    assert S.panel_band(g, bright_frac=0.9, fallbacks=()) is None


def test_panel_band_excludes_collection_box() -> None:
    """默认 0.85 应把底部收集盒行带切掉。

    回归要点：必须取**最长连续区段**。收集盒下方还有一行纯面板会达标，
    若取"达标行 min..max"就会把盒区夹在带内，下游随即挑到盒子。
    """
    g = _tst_scene(with_box=True)
    band = S.panel_band(g)
    assert band is not None
    r0, r1 = band
    assert r0 >= 65, f"上边界 {r0} 未排除顶框"
    assert r1 < 240, f"下边界 {r1} 未排除收集盒（240 起）"


def test_segment_picks_animal_not_box() -> None:
    """回归：必须挑动物，不能挑收集盒或箱框。"""
    g = _tst_scene(animal_row=150, animal_h=30, animal_w=12)
    r = S.segment_animal(g)
    assert r.ok, r.reason
    rows = np.flatnonzero(r.mask.any(axis=1))
    assert 145 <= rows[0] <= 155, f"选中对象起始行 {rows[0]}，不是动物"
    assert int(r.mask.sum()) == 30 * 12, int(r.mask.sum())


def test_segment_prefers_animal_below_tape() -> None:
    """胶带在上、动物在下，应取最低的候选，不能取到胶带。"""
    g = _tst_scene()
    r = S.segment_animal(g)
    assert r.ok
    rows = np.flatnonzero(r.mask.any(axis=1))
    assert rows[0] > 135, "选中了胶带所在行段"
    assert "tape_attached" not in r.flags


def test_segment_fails_explicitly_when_only_border_objects() -> None:
    """只有触边物体时必须显式失败，不能返回空掩膜。

    空掩膜会被下游解读为"动物没动" = immobility，把分割失败伪装成信号。
    """
    g = np.full((268, 95), 250.0)
    g[0:70, :] = 10.0
    g[259:, :] = 10.0
    g[240:258, 0:95] = 20.0  # 横贯左右的收集盒，触边
    r = S.segment_animal(g)
    assert not r.ok
    assert r.mask is None
    assert r.reason


def test_segment_flags_tape_attached() -> None:
    """动物与胶带在掩膜里连通时必须打标记，而不是静默给出被拉长的 BL。"""
    g = _tst_scene(animal_row=150)
    g[135:150, 44:50] = 15.0  # 把胶带一直连到动物
    r = S.segment_animal(g)
    assert r.ok, r.reason
    assert "tape_attached" in r.flags


def test_find_chambers_uses_divider_not_greedy_merge() -> None:
    """回归：按结构立柱定位，且结果不随 panel_band 参数漂移。

    立柱与胶带宽度相近（都约 6–7 px），只能靠"暗行占比"区分：
    立柱占满整个面板高度（≈1.0），胶带只到动物处（≈0.3–0.7）。
    """
    h, w = 268, 400
    g = np.full((h, w), 250.0)
    g[0:70, :] = 10.0
    g[259:, :] = 10.0
    for c in (0, 99, 199, 299, 396):      # 结构立柱：整个高度都暗
        g[:, c : c + 4] = 10.0
    for c in (48, 148, 248, 348):         # 胶带：只从顶部到中段
        g[70:150, c : c + 5] = 15.0
    chambers = S.find_chambers(g)
    assert len(chambers) == 4, chambers
    widths = [b - a + 1 for a, b in chambers]
    assert all(wd > 80 for wd in widths), widths


def test_contrast_report_uses_animal_mask() -> None:
    """暗侧应取动物掩膜内像素，不是面板带内所有暗像素。

    后者会把箱框与抗锯齿过渡带算进去，显著低估对比度（实测 166 vs 真值 238）。
    """
    g = _tst_scene()
    rep = S.contrast_report(g)
    assert rep["dark_from_mask"] == 1.0
    assert rep["abs_diff"] > 200.0, rep["abs_diff"]
    assert rep["passes_gate"] == 1.0


def test_structural_noise_floor_is_near_zero_on_clean_scene() -> None:
    """静态高对比结构的面积抖动应接近零——这才是分割噪声的正确代理。

    实测真实素材：顶框（高对比）0.43 px；半透明收集盒（低对比软边）6.14 px。
    噪声底强依赖边缘对比度，用低对比物体做代理会高估近一个数量级。
    """
    frames = [_tst_scene(animal_row=150 + i) for i in range(6)]
    nf = S.structural_noise_floor(frames)
    assert nf["delta_max"] <= 1.0, nf


def test_label_components_sorted_and_bboxes() -> None:
    m = np.zeros((20, 20), dtype=bool)
    m[2:5, 2:5] = True      # 9 px
    m[10:16, 10:18] = True  # 48 px
    comps = S.label_components(m)
    assert [c.area for c in comps] == [48, 9]
    assert comps[0].bbox == (10, 10, 15, 17)
    assert comps[0].height == 6 and comps[0].width == 8
