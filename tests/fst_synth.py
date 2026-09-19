"""FST 研究入口的合成夹具：**标签合成**，不是真值。

造的是"背光水缸"场景：亮背板 + 恒暗的杯壁/挂钩/边框 + 一条静态水线 +
一只会动的暗动物。所有测试用的几何量都从这里的构造参数来，
**不**从任何真实素材反推——合成输入在报告里必须标合成（Spec A §4 A1）。

用法：
    scene = fst_synth.Scene(cups=2)
    frames = scene.series(motion=("swim", "still", ...))
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

BG_BRIGHT = 200.0
WATER_GRAY = 172.0        # 水面以下略暗 ⇒ 中值帧上是一条静态水平边
STRUCT_DARK = 30.0        # 杯壁/挂钩/边框
ANIMAL_GRAY = 28.0


@dataclass
class Cup:
    c0: int
    c1: int
    r0: int
    r1: int
    water_y: int

    @property
    def interior(self) -> tuple[int, int, int, int]:
        return (self.r0, self.c0, self.r1, self.c1)

    @property
    def cx(self) -> float:
        return (self.c0 + self.c1) / 2.0


@dataclass
class Scene:
    cups: int = 2
    h: int = 120
    w: int = 220
    cup_defs: list[Cup] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.cup_defs:
            span = self.w // self.cups
            self.cup_defs = [
                Cup(c0=k * span + 18, c1=k * span + span - 18,
                    r0=40, r1=self.h - 14, water_y=self.h - 46)
                for k in range(self.cups)
            ]

    def base(self) -> np.ndarray:
        g = np.full((self.h, self.w), BG_BRIGHT)
        g[:8, :] = STRUCT_DARK            # 顶框
        g[-6:, :] = STRUCT_DARK           # 底框
        for cup in self.cup_defs:
            # 杯壁：恒暗两列（在 interior 之外，模拟"壁不算动物可达区"）
            g[cup.r0 - 4:cup.r1 + 5, cup.c0 - 3:cup.c0 - 1] = STRUCT_DARK
            g[cup.r0 - 4:cup.r1 + 5, cup.c1 + 1:cup.c1 + 3] = STRUCT_DARK
            # 挂钩：杯子上方的恒暗竖条（TST 场景没有这东西，这里是干扰项）
            g[8:cup.r0 - 8, int(cup.cx) - 1:int(cup.cx) + 2] = STRUCT_DARK
            # 水线以下略暗
            g[cup.water_y:cup.r1 + 1, cup.c0 - 1:cup.c1 + 2] = WATER_GRAY
        return g

    def add_animal(self, g: np.ndarray, cup: Cup, x: float, y: float,
                   rx: int = 6, ry: int = 4, value: float = ANIMAL_GRAY) -> None:
        yy, xx = np.mgrid[0:self.h, 0:self.w]
        blob = ((xx - x) / rx) ** 2 + ((yy - y) / ry) ** 2 <= 1.0
        g[blob] = value

    def frame(self, positions) -> np.ndarray:
        """positions: 每杯一个 (x, y) 或 None（本帧该杯无动物）。"""
        g = self.base()
        for cup, pos in zip(self.cup_defs, positions):
            if pos is not None:
                self.add_animal(g, cup, pos[0], pos[1])
        return g

    def swim_series(self, n: int, cup: int = 0, step: float = 2.0,
                    absent: tuple[int, ...] = (), y_jitter: float = 0.0,
                    rng: np.random.Generator | None = None) -> list[np.ndarray]:
        """第 cup 杯的动物左右游；`absent` 里的帧号该杯消失（模拟分割丢失）。"""
        rng = rng or np.random.default_rng(7)
        c = self.cup_defs[cup]
        out = []
        for i in range(n):
            pos = None
            if i not in absent:
                x = c.c0 + 8 + (i * step) % max(1.0, (c.c1 - c.c0 - 16))
                y = c.water_y + 6 + (rng.uniform(-y_jitter, y_jitter) if y_jitter else 0.0)
                pos = (x, y)
            others = [None] * len(self.cup_defs)
            others[cup] = pos
            out.append(self.frame(others))
        return out
