"""几何语义图（Geometric Semantic Map）。

命名与结构对齐 EthoPlex Shared Assay Core 的 GeometryPrimitive / GeometryEnvelope，
以便后续并入共享库。本模块只新增 FST/TST 特有的语义角色。

铁律：
- primitive_id 固定，改名不改号。
- 颜色只用于显示，绝不参与计算（故本模块不存颜色）。
- 证据不足时标 confirmed=False，由上层决定是否拒绝分析。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Sequence

# ---- 语义角色 ----------------------------------------------------------------

# 通用
ROLE_BACKGROUND = "background"
ROLE_SCALE_BAR = "scale_bar"
ROLE_REFERENCE_POINT = "reference_point"

# FST 特有
ROLE_TANK = "tank"
ROLE_TANK_WALL = "tank_wall"
ROLE_WATER_SURFACE = "water_surface"

# TST 特有
ROLE_CHAMBER = "chamber"
ROLE_SUSPENSION_BAR = "suspension_bar"
ROLE_SUSPENSION_POINT = "suspension_point"
# 胶带走廊：悬挂胶带所在的竖直列区间（加最大竖直延伸）。相机固定 ⇒ 胶带位置是
# 每段录像的常量，应**标定一次**（人工确认后 confirmed=True），而不是每帧推断。
# 缺失时分割退化为无走廊的启发式路径，不报错——它是可选优化项，不是必需角色。
ROLE_TAPE_CORRIDOR = "tape_corridor"

KIND_POINT = "point"
KIND_LINE = "line"
KIND_POLYLINE = "polyline"
KIND_POLYGON = "polygon"
KIND_RECT = "rect"

# 每个角色允许的几何类型。用于校验，防止把水面线画成多边形之类的错配。
_ROLE_KINDS: dict[str, tuple[str, ...]] = {
    ROLE_BACKGROUND: (KIND_POLYGON, KIND_RECT),
    ROLE_SCALE_BAR: (KIND_LINE,),
    ROLE_REFERENCE_POINT: (KIND_POINT,),
    ROLE_TANK: (KIND_POLYGON, KIND_RECT),
    ROLE_TANK_WALL: (KIND_LINE, KIND_POLYLINE),
    ROLE_WATER_SURFACE: (KIND_LINE,),
    ROLE_CHAMBER: (KIND_POLYGON, KIND_RECT),
    ROLE_SUSPENSION_BAR: (KIND_LINE,),
    ROLE_SUSPENSION_POINT: (KIND_POINT,),
    ROLE_TAPE_CORRIDOR: (KIND_RECT,),
}

# 每个范式必须具备的角色，缺一个就不允许出正式结果。
# 注意：tape_corridor **不在其中**——走廊是可选优化项，缺失时分割退化为
# 无走廊的启发式路径而非报错（见 segment.segment_animal 的 corridor 参数）。
REQUIRED_ROLES: dict[str, tuple[str, ...]] = {
    "FST": (ROLE_TANK, ROLE_WATER_SURFACE),
    "TST": (ROLE_CHAMBER, ROLE_SUSPENSION_BAR),
}

_MIN_COORDS = {
    KIND_POINT: 1,
    KIND_LINE: 2,
    KIND_POLYLINE: 2,
    KIND_POLYGON: 3,
    KIND_RECT: 2,
}


class GeometryError(ValueError):
    """几何语义图不合法。"""


@dataclass(frozen=True)
class GeometryPrimitive:
    """一个几何对象。coords 为 [(x, y), ...]，像素坐标，原点左上。"""

    primitive_id: int
    semantic_role: str
    kind: str
    coords: tuple[tuple[float, float], ...]
    instance: int = 1  # 同角色多实例时的序号，如 tank 1..4
    confirmed: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        if self.semantic_role not in _ROLE_KINDS:
            raise GeometryError(f"未知语义角色: {self.semantic_role}")
        allowed = _ROLE_KINDS[self.semantic_role]
        if self.kind not in allowed:
            raise GeometryError(
                f"角色 {self.semantic_role} 不允许几何类型 {self.kind}，允许: {allowed}"
            )
        need = _MIN_COORDS[self.kind]
        if len(self.coords) < need:
            raise GeometryError(
                f"{self.kind} 至少需要 {need} 个坐标，得到 {len(self.coords)}"
            )

    @property
    def key(self) -> str:
        """稳定的人可读标识，如 tank_2。"""
        return f"{self.semantic_role}_{self.instance}"


@dataclass
class GeometryEnvelope:
    """一个视频的完整几何语义图。"""

    assay: str  # "FST" | "TST"
    video_size: tuple[int, int]  # (width, height) 像素
    primitives: list[GeometryPrimitive] = field(default_factory=list)
    display_size: tuple[int, int] | None = None
    sar: float = 1.0  # sample aspect ratio
    dar: float | None = None
    mm_per_px: float | None = None  # 由 scale_bar 标定得出
    confirmed: bool = False

    # ---- 构建 ----

    def add(self, primitive: GeometryPrimitive) -> None:
        if any(p.primitive_id == primitive.primitive_id for p in self.primitives):
            raise GeometryError(f"primitive_id 重复: {primitive.primitive_id}")
        self.primitives.append(primitive)

    # ---- 查询 ----

    def by_role(self, role: str) -> list[GeometryPrimitive]:
        return sorted(
            (p for p in self.primitives if p.semantic_role == role),
            key=lambda p: p.instance,
        )

    def one(self, role: str) -> GeometryPrimitive:
        """取唯一实例。不存在或多于一个都报错——避免静默取错。"""
        found = self.by_role(role)
        if len(found) != 1:
            raise GeometryError(f"角色 {role} 期望恰好 1 个实例，实得 {len(found)}")
        return found[0]

    def water_surface_y(self) -> float:
        """水面线的 y（取两端点均值）。FST 专用。"""
        line = self.one(ROLE_WATER_SURFACE)
        return sum(y for _, y in line.coords) / len(line.coords)

    def suspension_point(self, instance: int = 1) -> tuple[float, float]:
        """悬挂点。优先用显式标注的 suspension_point，否则取悬挂杆在该室水平中心处的点。"""
        for p in self.by_role(ROLE_SUSPENSION_POINT):
            if p.instance == instance:
                return p.coords[0]
        bar = self.one(ROLE_SUSPENSION_BAR)
        bar_y = sum(y for _, y in bar.coords) / len(bar.coords)
        chambers = self.by_role(ROLE_CHAMBER)
        target = next((c for c in chambers if c.instance == instance), None)
        if target is None:
            raise GeometryError(f"找不到 chamber 实例 {instance}，无法推断悬挂点")
        xs = [x for x, _ in target.coords]
        return ((min(xs) + max(xs)) / 2.0, bar_y)

    # ---- 校验 ----

    def validate(self) -> list[str]:
        """返回问题列表；空列表表示可用于正式分析。"""
        problems: list[str] = []
        if self.assay not in REQUIRED_ROLES:
            return [f"未知范式: {self.assay}"]

        for role in REQUIRED_ROLES[self.assay]:
            if not self.by_role(role):
                problems.append(f"缺少必需角色: {role}")

        w, h = self.video_size
        for p in self.primitives:
            for x, y in p.coords:
                if not (0 <= x <= w and 0 <= y <= h):
                    problems.append(f"{p.key} 坐标 ({x}, {y}) 超出画面 {w}x{h}")
                    break

        if self.assay == "FST" and self.by_role(ROLE_WATER_SURFACE):
            # FST 真实素材是一帧多杯（DP-136）：tank 与 water_surface 按同号
            # 配对检查。单实例旧素材两边都是 instance=1，配对退化为旧行为。
            # R2-115 G2：不等号**保持旧版严格开区间**——早先版本在这里悄悄
            # 放宽成闭区间，改变了旧单实例语义（"水线压杯顶/杯底"从不通过
            # 变通过）。共享契约不接受静默语义变更；研究层若要表达"水线取自
            # ROI 顶边"这类新语义，须走显式版本/研究模式并过变更评审——
            # 研究提案的做法是让分析 ROI 在水线上方留白（cup_geometry），
            # 正确提案天然满足严格不等式，压边提案照实报。
            lines = self.by_role(ROLE_WATER_SURFACE)
            tank_instances = {t.instance for t in self.by_role(ROLE_TANK)}
            line_count: dict[int, int] = {}
            for line in lines:
                line_count[line.instance] = line_count.get(line.instance, 0) + 1
            for inst in sorted(line_count):
                if line_count[inst] > 1:
                    problems.append(
                        f"water_surface_{inst} 出现 {line_count[inst]} 次"
                        "（同号重复，配对有歧义）")
                if inst not in tank_instances:
                    problems.append(
                        f"water_surface_{inst} 没有同号 tank（孤儿水线）")
            for tank in self.by_role(ROLE_TANK):
                matched = [p for p in lines if p.instance == tank.instance]
                if not matched:
                    problems.append(
                        f"{tank.key} 缺同号 water_surface（多杯时水线不共用）")
                    continue
                ys = [y for _, y in tank.coords]
                for line in matched:
                    wy = sum(y for _, y in line.coords) / len(line.coords)
                    # 严格开区间（旧版语义）：水线必须严格在 tank 垂直范围内，
                    # 恰好压在顶边/底边也算不通过。
                    if not (min(ys) < wy < max(ys)):
                        problems.append(
                            f"水面线 y={wy:.1f} 不在 {tank.key} 垂直范围内")

        unconfirmed = [p.key for p in self.primitives if not p.confirmed]
        if unconfirmed:
            problems.append(f"未确认的几何对象: {', '.join(unconfirmed)}")
        return problems

    @property
    def is_valid(self) -> bool:
        return not self.validate()

    # ---- 持久化 ----

    def to_json(self, *, indent: int = 2) -> str:
        payload = asdict(self)
        payload["primitives"] = [asdict(p) for p in self.primitives]
        return json.dumps(payload, ensure_ascii=False, indent=indent)

    @classmethod
    def from_json(cls, text: str) -> "GeometryEnvelope":
        raw = json.loads(text)
        prims = [GeometryPrimitive(**_coerce_prim(p)) for p in raw.pop("primitives", [])]
        env = cls(**_coerce_env(raw))
        env.primitives = prims
        return env


def _coerce_prim(d: dict) -> dict:
    d = dict(d)
    d["coords"] = tuple((float(x), float(y)) for x, y in d["coords"])
    return d


def _coerce_env(d: dict) -> dict:
    d = dict(d)
    d["video_size"] = tuple(int(v) for v in d["video_size"])
    if d.get("display_size") is not None:
        d["display_size"] = tuple(int(v) for v in d["display_size"])
    return d


def next_id(env: GeometryEnvelope) -> int:
    return 1 + max((p.primitive_id for p in env.primitives), default=0)


def make_rect(
    env: GeometryEnvelope,
    role: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    instance: int = 1,
    confirmed: bool = False,
) -> GeometryPrimitive:
    """便捷构造轴对齐矩形（存左上、右下两点）。"""
    prim = GeometryPrimitive(
        primitive_id=next_id(env),
        semantic_role=role,
        kind=KIND_RECT,
        coords=((min(x0, x1), min(y0, y1)), (max(x0, x1), max(y0, y1))),
        instance=instance,
        confirmed=confirmed,
    )
    env.add(prim)
    return prim


def make_line(
    env: GeometryEnvelope,
    role: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    instance: int = 1,
    confirmed: bool = False,
) -> GeometryPrimitive:
    prim = GeometryPrimitive(
        primitive_id=next_id(env),
        semantic_role=role,
        kind=KIND_LINE,
        coords=((x0, y0), (x1, y1)),
        instance=instance,
        confirmed=confirmed,
    )
    env.add(prim)
    return prim


def rect_bounds(prim: GeometryPrimitive) -> tuple[float, float, float, float]:
    """返回 (x0, y0, x1, y1)。对 polygon 也适用（取外接框）。"""
    xs: Sequence[float] = [x for x, _ in prim.coords]
    ys: Sequence[float] = [y for _, y in prim.coords]
    return min(xs), min(ys), max(xs), max(ys)
