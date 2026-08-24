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


# ---- 胶带走廊标定与走廊路径 ---------------------------------------------------
#
# 场景约定（与 _tst_scene 一致）：95 宽隔间 ROI，行 0–70 顶框（黑），
# 70–258 亮面板，259+ 底框。胶带 = 列 44–50 的竖直暗条，动物 = 暗矩形。


def _corridor_scene(
    *,
    tape_rows: tuple[int, int] = (70, 135),
    tape_cols: tuple[int, int] = (44, 50),
    connect_rows: tuple[int, int] | None = None,  # 胶带延伸到动物（连通）
    animal_row: int = 150,
    animal_h: int = 30,
    animal_w: int = 12,
    animal_col: int | None = None,  # None = 居中
    height: int = 268,
) -> np.ndarray:
    h, w = height, 95
    g = np.full((h, w), 250.0)
    g[0:70, :] = 10.0
    g[h - 9 :, :] = 10.0
    tc0, tc1 = tape_cols
    tr0, tr1 = tape_rows
    g[tr0:tr1, tc0:tc1] = 15.0
    if connect_rows is not None:
        g[connect_rows[0]:connect_rows[1], tc0:tc1] = 15.0
    c0 = (w - animal_w) // 2 if animal_col is None else animal_col
    g[animal_row : animal_row + animal_h, c0 : c0 + animal_w] = 20.0
    return g


def test_corridor_none_is_bit_identical_to_default() -> None:
    """C3 精神：corridor=None 必须走原路径，行为逐位一致。"""
    for scene in (
        _tst_scene(),
        _tst_scene(animal_row=170),
        _corridor_scene(connect_rows=(135, 150)),
    ):
        base = S.segment_animal(scene)
        res = S.segment_animal(scene, corridor=None)
        assert res.ok == base.ok
        assert res.flags == base.flags
        assert res.reason == base.reason
        if base.ok:
            assert np.array_equal(res.mask, base.mask)


def test_calibrate_finds_static_tape_with_moving_animal() -> None:
    """暗频率法：动物在动，胶带静止 ⇒ 走廊 = 胶带列。"""
    frames = [
        _corridor_scene(animal_row=150 + 6 * i, animal_col=36 + 3 * i)
        for i in range(6)
    ]
    corr = S.calibrate_tape_corridor([f[:, :] for f in frames])
    assert corr is not None, "标定不应失败"
    assert corr.col_range == (44, 49), corr
    # 行下界 = 胶带底端（暗频率首次跌破 0.9 的上一行），允许 ±1 行
    assert abs(corr.row_range[1] - 134) <= 1, corr
    assert corr.confidence >= 0.95, corr


def test_calibrate_needs_at_least_two_frames() -> None:
    assert S.calibrate_tape_corridor([_corridor_scene()]) is None
    assert S.calibrate_tape_corridor([]) is None


def test_calibrate_none_without_static_vertical_structure() -> None:
    """没有静态胶带（动物每帧换位、无固定暗列）⇒ 返回 None，不猜。"""
    frames = [
        _corridor_scene(tape_rows=(0, 0), animal_row=140 + 10 * i,
                        animal_col=30 + 8 * i)
        for i in range(5)
    ]
    assert S.calibrate_tape_corridor(frames) is None


def test_calibrate_disambiguates_edge_pillar() -> None:
    """多个候选段时：贴 ROI 边缘的是结构立柱渗漏，应丢弃，取内部胶带段。"""
    frames = []
    for i in range(4):
        g = _corridor_scene(animal_row=150 + 5 * i)
        g[:, 0:4] = 10.0  # 左缘立柱：整条高度全暗
        frames.append(g)
    corr = S.calibrate_tape_corridor(frames)
    assert corr is not None
    assert corr.col_range == (44, 49), corr


def test_calibrate_none_on_two_interior_runs() -> None:
    """两段内部静态暗条、消歧后仍唯一性不足 ⇒ 返回 None，不猜。"""
    frames = []
    for i in range(3):
        g = _corridor_scene(animal_row=160 + 4 * i)
        g[70:135, 20:26] = 15.0  # 第二条"胶带"
        frames.append(g)
    assert S.calibrate_tape_corridor(frames) is None


def test_corridor_separates_connected_tape() -> None:
    """核心修复：动物与胶带连通时，走廊路径按胶带底端截断，不再挑到胶带。"""
    g = _corridor_scene(connect_rows=(135, 150))
    # 无走廊：连通体被整体挑中（胶带把面积拉长），打 tape_attached
    base = S.segment_animal(g)
    assert base.ok
    assert "tape_attached" in base.flags
    assert int(base.mask.sum()) > 30 * 12 + 100, "基线应包含胶带像素"

    corr = S.TapeCorridor(
        col_range=(44, 49),
        row_range=(70, 149),   # 胶带最大延伸（含连接段）
        confidence=1.0,
        band_range=(70, 238),
    )
    res = S.segment_animal(g, corridor=corr)
    assert res.ok, res.reason
    assert "tape_attached" not in res.flags
    assert int(res.mask.sum()) == 30 * 12, int(res.mask.sum())
    rows = np.flatnonzero(res.mask.any(axis=1))
    assert rows.min() >= 150, "截断面以上（胶带区）不应有动物像素"


def test_corridor_nothing_below_tape_fails_explicitly() -> None:
    """边界情况 1：胶带底端以下没有任何动物 ⇒ 显式失败 + 标记，绝不返回胶带。

    截断面在胶带底端，结构上已不可能把胶带当动物；此处验证"动物缺失/完全
    缩在走廊里与胶带无法区分"时的显式失败纪律（空掩膜会被下游当成
    immobility，把失败伪装成信号）。
    """
    g = _corridor_scene()
    g[150:180, :] = 250.0           # 抹掉默认动物：胶带下方空无一物
    corr = S.TapeCorridor((44, 49), (70, 134), 1.0, (70, 238))
    res = S.segment_animal(g, corridor=corr)
    assert not res.ok
    assert res.mask is None
    assert res.reason
    assert "animal_in_corridor" in res.flags


def test_corridor_thin_animal_below_tape_is_returned() -> None:
    """细瘦动物顺胶带轴悬挂、完全在走廊列内、但在胶带底端**以下** ⇒ 正常返回。

    这是真实姿态（挣扎期动物沿胶带轴拉长，实测隔间 2 A 批），不是胶带：
    截断面在胶带底端，其下的细条只可能是动物。与"缩在胶带区无法区分"不同，
    不得误杀。
    """
    g = _corridor_scene()
    g[150:180, :] = 250.0           # 抹掉默认动物
    g[150:180, 46:49] = 20.0        # 3 px 宽、在走廊列内、位于胶带底端之下
    corr = S.TapeCorridor((44, 49), (70, 134), 1.0, (70, 238))
    res = S.segment_animal(g, corridor=corr)
    assert res.ok, res.reason
    assert int(res.mask.sum()) == 30 * 3, int(res.mask.sum())
    rows = np.flatnonzero(res.mask.any(axis=1))
    assert rows.min() >= 150, "胶带区（截断面以上）不应有像素"


def test_corridor_climbing_animal_returned_and_flagged() -> None:
    """边界情况 2+4：动物爬上走廊区是真实行为——必须返回、打标记、不裁身体。"""
    g = _corridor_scene(tape_rows=(70, 131))   # 胶带底端 = 行 130，与走廊一致
    g[150:180, :] = 250.0           # 抹掉默认动物
    g[90:131, 40:61] = 20.0         # 身体整体在胶带底端之上，且突出走廊两侧
    corr = S.TapeCorridor((44, 49), (70, 130), 1.0, (70, 238))
    res = S.segment_animal(g, corridor=corr)
    assert res.ok, res.reason
    assert "animal_in_corridor" in res.flags
    # 不静默裁掉：身体在走廊外的像素必须保留
    assert int(res.mask.sum()) == 41 * 21, int(res.mask.sum())
    cols = np.flatnonzero(res.mask.any(axis=0))
    assert cols.min() <= 40 and cols.max() >= 60


def test_corridor_invalid_falls_back_with_flag() -> None:
    """边界情况 3：走廊结构非法 ⇒ 退回无走廊路径 + corridor_unused 标记。"""
    g = _corridor_scene(connect_rows=(135, 150))
    base = S.segment_animal(g)
    bad = S.TapeCorridor((500, 510), (70, 134), 1.0, (70, 238))
    res = S.segment_animal(g, corridor=bad)
    assert res.ok == base.ok
    assert np.array_equal(res.mask, base.mask)
    assert "corridor_unused" in res.flags
    assert "tape_attached" in res.flags  # 退化路径的原标记保留


def test_corridor_band_range_prevents_truncation() -> None:
    """回归：逐帧 panel_band 底缘在阴影带前截断时，走廊路径用标定带找回动物。

    实测踩坑：面板底部阴影行亮占比 0.84–0.93，落在 0.85 门槛附近，
    逐帧 band 底缘在 163/239 之间跳变，动物被随机截断。标定期逐帧扩展
    取并（>0.70）后带是稳定的。
    """
    def frame(animal_row: int) -> np.ndarray:
        g = _corridor_scene(animal_row=animal_row, animal_h=20)
        # 面板下部的阴影带：亮占比 0.842（<0.85 ⇒ panel_band 截断；>0.70 ⇒ 可扩回）。
        # 与动物重叠时亮占比 0.716，仍 >0.70——对齐实测（阴影 7–16% 暗、
        # 动物 ~13% 宽），扩展不会被动物挡住。
        g[200:240, 0:15] = 10.0
        # 阴影带以下彻底暗（收集盒），扩展到此为止
        g[240:259, :] = 30.0
        return g

    frames = [frame(210), frame(214), frame(218)]
    g0 = frames[0]
    # 单帧 panel_band 确实截断在阴影带之前，动物（行 210–230）被丢在带外
    assert S.panel_band(g0)[1] < 210
    r_no = S.segment_animal(g0)
    assert (not r_no.ok) or int(r_no.mask[210:230, :].sum()) == 0, \
        "无走廊路径不应找到被截断的动物"

    corr = S.calibrate_tape_corridor(frames)
    assert corr is not None
    assert corr.col_range == (44, 49), corr
    assert corr.band_range[1] >= 239, corr   # 扩展带覆盖阴影区
    res = S.segment_animal(g0, corridor=corr)
    assert res.ok, res.reason
    assert int(res.mask.sum()) == 20 * 12, int(res.mask.sum())


def test_band_cap_stops_above_collection_box() -> None:
    """回归：扩展带不得进入底部收集盒区。

    玻璃盒的部分行仍是亮的（亮占比 >0.70），纯亮度扩展会一路伸进盒区，
    盒内不触边的碎屑会成为假候选（实测 ch3 行 246–251 有 44 px 碎屑）。
    收口依据：动物悬挂在胶带上，其运动痕迹（中频暗像素块）必须起始于胶带
    底端附近；盒区的痕迹（粪粒累积）远离胶带。带底封到动物运动块底 + 余量。
    """
    def frame(i: int) -> np.ndarray:
        g = _corridor_scene(animal_row=160 + 5 * i, animal_h=20)
        # 盒区碎屑：行 240–255，只在半数帧出现（粪粒累积 → 中频暗）
        if i % 2 == 0:
            g[240:256, 30:51] = 15.0
        return g

    frames = [frame(i) for i in range(6)]
    corr = S.calibrate_tape_corridor(frames)
    assert corr is not None
    assert corr.sealed, corr
    assert corr.band_range[1] < 240, corr     # 收口在盒区之上
    assert corr.band_range[1] >= 184, corr    # 但仍覆盖动物最深行 + 余量

    # 碎屑帧上分割：只出动物，不出碎屑
    res = S.segment_animal(frames[0], corridor=corr)
    assert res.ok, res.reason
    assert int(res.mask.sum()) == 20 * 12, int(res.mask.sum())
    assert res.mask[240:, :].sum() == 0, "盒区像素不得进入掩膜"
    assert "band_unsealed" not in res.flags


def test_band_cap_absent_without_motion() -> None:
    """静止采样（动物每帧都在，频率=1.0）同样构成出现行块 ⇒ 正常收口；
    只有胶带底端以下**毫无痕迹**（空隔间/动物脱落）才不收口，且必须显式标记。

    不收口 = 回到"带底可能进盒区"的已知危险态；静默退化违反 §6.2 纪律。
    """
    frames = [_corridor_scene(animal_row=160) for _ in range(4)]
    corr = S.calibrate_tape_corridor(frames)
    assert corr is not None
    assert corr.sealed is True, corr
    assert corr.band_range[1] >= 189, corr   # 覆盖静止动物（160–189）+ 余量

    # 真空走廊：胶带下没有任何痕迹 ⇒ 不收口 + 显式危险态标记
    empty = [_corridor_scene(animal_row=0, animal_h=0) for _ in range(4)]
    corr2 = S.calibrate_tape_corridor(empty)
    assert corr2 is not None
    assert corr2.sealed is False, corr2
    assert corr2.band_range[1] >= 250, corr2   # 保持扩展带
    res = S.segment_animal(empty[0], corridor=corr2)
    assert "band_unsealed" in res.flags


def test_seal_margin_scales_with_body_length() -> None:
    """收口余量 = k×BL，不是硬编码像素（D2 体长归一化主张）。

    同一布局、仅动物体长不同（高场景，两者都碰不到帧底）：
    大动物的收口应比小动物显著更深。硬编码余量下两者之差只来自块底，
    k×BL 下还叠加余量之差。
    """
    def video(animal_h: int) -> list[np.ndarray]:
        return [
            _corridor_scene(animal_row=150 + 5 * i, animal_h=animal_h, height=400)
            for i in range(6)
        ]

    small = S.calibrate_tape_corridor(video(20))
    large = S.calibrate_tape_corridor(video(80))
    assert small is not None and large is not None
    assert small.sealed and large.sealed
    assert large.band_range[1] - small.band_range[1] >= 40, (small, large)


def test_seal_respects_box_trace_hard_cap() -> None:
    """硬上界：块底 + k×BL 伸进盒区时，取 盒区痕迹顶 − margin（两侧都有约束）。

    高场景：动物（60 px 体长）挂得深，盒区痕迹离动物块底 66 行
    （> BL ⇒ 不会被生长误并），但 块底 + k·BL 会越过 痕迹顶 − 10 ⇒
    硬上界兜底生效，同时动物最深行仍被覆盖。
    """
    def frame(i: int) -> np.ndarray:
        g = _corridor_scene(animal_row=150 + 3 * i, animal_h=60, height=340)
        if i % 2 == 0:                      # 盒区痕迹（中频暗）：行 290–310
            g[290:310, 30:51] = 15.0
        return g

    corr = S.calibrate_tape_corridor([frame(i) for i in range(6)])
    assert corr is not None
    assert corr.sealed, corr
    # 上界 = 290 − 10 = 280；动物最深行 ≈224 必须仍被覆盖
    assert 250 <= corr.band_range[1] <= 280, corr
    assert corr.band_range[1] < 290, corr
