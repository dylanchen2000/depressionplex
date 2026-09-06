"""位打包的掩膜序列：内存 1/8，接口仍是 `masks[i] -> bool 数组`。

为什么需要：一个 6 min 试次 = 9000 帧，单隔间 ROI 约 118×360 ⇒ 逐帧 bool
存法约 380 MB，四个隔间同时装就 1.5 GB。而 RAD 要随机访问 `masks[i-lag]`
（多时标分解），**不能只留上一帧**，所以纯流式行不通。位打包后约 48 MB/隔间。

`rad.decompose_series` / `rad.trial_body_length` 只用到 `len()` 与 `[i]`，
所以这里做成 `Sequence` 就能直接喂进去，assay_core 一行不用改。

**分割失败的帧存全 False。** 那不是"动物没动"，是"这一帧没看见"：空剪影让
`rad.decompose` 返回 None ⇒ 特征 NaN ⇒ 该帧记 `unknown` ⇒ 不进可评分分母。
DP-032 的教训就在这条链上——把"没看见"和"看见了没动"混成一件事，
就会把分割失败渲染成 immobility（空场判 360 s = 伪造最强抑郁表型）。
所以本类**不提供**"用上一帧补齐"之类的填充选项，缺帧就是缺帧。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


class PackedMasks(Sequence):
    """定形掩膜序列，内部用 `np.packbits` 存。

    形状在构造时钉死：混形状的掩膜串成一个试次 = 不同源数据混账，直接 raise。
    """

    __slots__ = ("_shape", "_rows", "_missing")

    def __init__(self, shape: tuple[int, int]) -> None:
        h, w = int(shape[0]), int(shape[1])
        if h <= 0 or w <= 0:
            raise ValueError(f"掩膜形状必须为正：{shape}")
        self._shape = (h, w)
        self._rows: list[np.ndarray] = []
        self._missing: list[bool] = []

    @property
    def shape(self) -> tuple[int, int]:
        return self._shape

    @property
    def n_missing(self) -> int:
        """分割失败的帧数。**报告必须带这个数**，不许静默。"""
        return sum(self._missing)

    def missing(self, i: int) -> bool:
        """第 i 帧是不是"没看见"。**与"看见了但面积 0"分开记**——两者在
        DP-032 里被混成一件事，代价是一只真老鼠被静默丢掉。"""
        return self._missing[i]

    def append(self, mask: np.ndarray | None) -> None:
        """`None` = 这一帧没分割出来 ⇒ 存全 False，并记进 `missing`。"""
        if mask is None:
            self._rows.append(np.zeros((self._shape[0] * self._shape[1] + 7) // 8,
                                       dtype=np.uint8))
            self._missing.append(True)
            return
        m = np.asarray(mask, dtype=bool)
        if m.shape != self._shape:
            raise ValueError(
                f"掩膜形状 {m.shape} ≠ 序列形状 {self._shape}"
                "——不同形状不得串成一个试次（两套账护栏）")
        self._rows.append(np.packbits(m.reshape(-1)))
        self._missing.append(False)

    def __len__(self) -> int:
        return len(self._rows)

    def __getitem__(self, i):                       # type: ignore[override]
        if isinstance(i, slice):
            return [self[k] for k in range(*i.indices(len(self)))]
        h, w = self._shape
        bits = np.unpackbits(self._rows[i], count=h * w)
        return bits.reshape(h, w).astype(bool)

    def areas(self) -> list[float | None]:
        """逐帧面积剖面；**分割失败的帧是 `None` 不是 0**。

        这个区分就是 `validity._present_frac` 的分母口径（DP-032）：`None`
        进不了分母，0 会被当成"看到了、没有动物"。喂给 `assess_trial_validity`
        的剖面必须由本方法产生，不许自己 `mask.sum()` 了事。
        """
        return [None if self._missing[i] else float(self[i].sum())
                for i in range(len(self))]
