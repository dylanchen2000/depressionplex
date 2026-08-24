"""几何语义图测试。"""

from __future__ import annotations

from depressionplex.assay_core import geometry as G


def _fst_env(confirmed: bool = True) -> G.GeometryEnvelope:
    env = G.GeometryEnvelope(assay="FST", video_size=(1280, 720))
    G.make_rect(env, G.ROLE_TANK, 300, 100, 500, 640, confirmed=confirmed)
    G.make_line(env, G.ROLE_WATER_SURFACE, 300, 260, 500, 262, confirmed=confirmed)
    return env


def _tst_env(confirmed: bool = True) -> G.GeometryEnvelope:
    env = G.GeometryEnvelope(assay="TST", video_size=(1280, 720))
    G.make_line(env, G.ROLE_SUSPENSION_BAR, 100, 80, 1180, 80, confirmed=confirmed)
    for i in range(1, 5):
        x0 = 100 + (i - 1) * 270
        G.make_rect(env, G.ROLE_CHAMBER, x0, 80, x0 + 250, 700,
                    instance=i, confirmed=confirmed)
    return env


def test_fst_env_valid() -> None:
    env = _fst_env()
    assert env.validate() == [], env.validate()
    assert env.is_valid


def test_missing_required_role_flagged() -> None:
    env = G.GeometryEnvelope(assay="FST", video_size=(640, 480))
    G.make_rect(env, G.ROLE_TANK, 10, 10, 100, 400, confirmed=True)
    problems = env.validate()
    assert any(G.ROLE_WATER_SURFACE in p for p in problems), problems


def test_unconfirmed_is_flagged() -> None:
    """未确认的几何对象必须拦住正式分析——不允许静默使用未确认标定。"""
    env = _fst_env(confirmed=False)
    problems = env.validate()
    assert any("未确认" in p for p in problems), problems
    assert not env.is_valid


def test_water_surface_must_be_inside_tank() -> None:
    env = G.GeometryEnvelope(assay="FST", video_size=(1280, 720))
    G.make_rect(env, G.ROLE_TANK, 300, 300, 500, 640, confirmed=True)
    # 水面线画在缸的上方之外
    G.make_line(env, G.ROLE_WATER_SURFACE, 300, 100, 500, 100, confirmed=True)
    problems = env.validate()
    assert any("不在" in p for p in problems), problems


def test_out_of_frame_coords_flagged() -> None:
    env = G.GeometryEnvelope(assay="FST", video_size=(640, 480))
    G.make_rect(env, G.ROLE_TANK, 10, 10, 700, 400, confirmed=True)
    G.make_line(env, G.ROLE_WATER_SURFACE, 10, 100, 600, 100, confirmed=True)
    assert any("超出画面" in p for p in env.validate())


def test_role_kind_mismatch_rejected() -> None:
    """水面线只能是 line，画成矩形应当直接报错而不是静默接受。"""
    env = G.GeometryEnvelope(assay="FST", video_size=(640, 480))
    try:
        G.make_rect(env, G.ROLE_WATER_SURFACE, 0, 0, 10, 10)
    except G.GeometryError:
        return
    raise AssertionError("角色/几何类型错配未被拒绝")


def test_duplicate_id_rejected() -> None:
    env = _fst_env()
    dup = G.GeometryPrimitive(
        primitive_id=env.primitives[0].primitive_id,
        semantic_role=G.ROLE_REFERENCE_POINT,
        kind=G.KIND_POINT,
        coords=((5.0, 5.0),),
    )
    try:
        env.add(dup)
    except G.GeometryError:
        return
    raise AssertionError("重复 primitive_id 未被拒绝")


def test_water_surface_y() -> None:
    env = _fst_env()
    assert abs(env.water_surface_y() - 261.0) < 1e-9


def test_one_raises_on_multiple() -> None:
    """one() 在多实例时必须报错，避免静默取错实例。"""
    env = _tst_env()
    try:
        env.one(G.ROLE_CHAMBER)
    except G.GeometryError:
        return
    raise AssertionError("多实例时 one() 应报错")


def test_suspension_point_inferred_from_bar_and_chamber() -> None:
    env = _tst_env()
    x, y = env.suspension_point(instance=2)
    assert abs(y - 80.0) < 1e-9
    # 第 2 室 x 范围 370..620 → 中心 495
    assert abs(x - 495.0) < 1e-9


def test_explicit_suspension_point_wins() -> None:
    env = _tst_env()
    env.add(
        G.GeometryPrimitive(
            primitive_id=G.next_id(env),
            semantic_role=G.ROLE_SUSPENSION_POINT,
            kind=G.KIND_POINT,
            coords=((400.0, 90.0),),
            instance=2,
            confirmed=True,
        )
    )
    assert env.suspension_point(instance=2) == (400.0, 90.0)


def test_json_roundtrip() -> None:
    env = _tst_env()
    back = G.GeometryEnvelope.from_json(env.to_json())
    assert back.assay == env.assay
    assert back.video_size == env.video_size
    assert len(back.primitives) == len(env.primitives)
    assert back.validate() == env.validate()
    assert back.suspension_point(3) == env.suspension_point(3)


def test_primitive_key_is_stable() -> None:
    env = _tst_env()
    keys = [p.key for p in env.by_role(G.ROLE_CHAMBER)]
    assert keys == ["chamber_1", "chamber_2", "chamber_3", "chamber_4"], keys


def test_no_colour_field_exists() -> None:
    """铁律：颜色不进数据结构，避免有人拿颜色当计算依据。"""
    fields = G.GeometryPrimitive.__dataclass_fields__
    for banned in ("color", "colour", "rgb", "hue"):
        assert banned not in fields, f"GeometryPrimitive 不应有 {banned} 字段"


def test_tape_corridor_role_is_optional_rect() -> None:
    """tape_corridor：只能画成矩形；是可选角色，缺失时 TST 标定仍然合法。

    走廊是"标定一次的优化项"，不是出正式结果的前提——缺失时分割退化为
    无走廊的启发式路径（见 segment.segment_animal），而不是拒绝分析。
    """
    env = _tst_env()
    assert env.validate() == [], "缺少 tape_corridor 不应影响 TST 合法性"

    G.make_rect(env, G.ROLE_TAPE_CORRIDOR, 40, 68, 46, 124,
                instance=1, confirmed=True)
    assert env.validate() == []
    p = env.by_role(G.ROLE_TAPE_CORRIDOR)[0]
    assert p.key == "tape_corridor_1"

    try:
        G.make_line(env, G.ROLE_TAPE_CORRIDOR, 0, 0, 10, 10)
    except G.GeometryError:
        return
    raise AssertionError("tape_corridor 画成 line 应被拒绝")
