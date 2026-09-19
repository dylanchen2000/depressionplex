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


def test_water_surface_strict_inequality_single_instance() -> None:
    """R2-115 G2：单实例旧素材的水线区间是**严格开区间** min(ys) < wy < max(ys)。

    早先版本悄悄放宽成闭区间（<=），把"水线压杯顶/杯底"从不通过变通过——
    这改变了旧单实例语义。共享契约不接受静默语义变更，这里钉死严格开区间：
    恰好压在顶边/底边都算不通过。
    """
    def _env(wy: float) -> G.GeometryEnvelope:
        e = G.GeometryEnvelope(assay="FST", video_size=(640, 480))
        G.make_rect(e, G.ROLE_TANK, 100, 100, 300, 300, confirmed=True)  # ys 100..300
        G.make_line(e, G.ROLE_WATER_SURFACE, 100, wy, 300, wy, confirmed=True)
        return e

    assert _env(200).validate() == []                       # 严格内部：通过
    assert any("不在" in p for p in _env(100).validate())    # 压顶边：不通过
    assert any("不在" in p for p in _env(300).validate())    # 压底边：不通过
    assert any("不在" in p for p in _env(99).validate())     # 顶边之外：不通过
    assert any("不在" in p for p in _env(301).validate())    # 底边之外：不通过


def test_fst_multi_cup_pairing_by_instance() -> None:
    """R2-115 G2：多杯时 tank 与 water_surface 按同号配对，逐条检查。"""
    def _two_cup() -> G.GeometryEnvelope:
        e = G.GeometryEnvelope(assay="FST", video_size=(640, 480), confirmed=True)
        for inst, (x0, x1) in {1: (20, 90), 2: (120, 190)}.items():
            G.make_rect(e, G.ROLE_TANK, x0, 100, x1, 300, instance=inst, confirmed=True)
            G.make_line(e, G.ROLE_WATER_SURFACE, x0, 200, x1, 200,
                        instance=inst, confirmed=True)
        return e

    assert _two_cup().validate() == []                      # 两杯各自配对：通过

    # 缺同号水线：tank_2 没有 water_surface_2
    e = _two_cup()
    e.primitives = [p for p in e.primitives
                    if not (p.semantic_role == G.ROLE_WATER_SURFACE and p.instance == 2)]
    assert any("tank_2" in p and "缺同号" in p for p in e.validate())

    # 同号重复：water_surface_1 出现两次 ⇒ 配对有歧义
    e = _two_cup()
    G.make_line(e, G.ROLE_WATER_SURFACE, 20, 210, 90, 210, instance=1, confirmed=True)
    assert any("water_surface_1" in p and "重复" in p for p in e.validate())

    # 孤儿水线：water_surface_3 没有同号 tank
    e = _two_cup()
    G.make_line(e, G.ROLE_WATER_SURFACE, 300, 200, 380, 200, instance=3, confirmed=True)
    assert any("water_surface_3" in p and "孤儿" in p for p in e.validate())


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
