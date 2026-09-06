"""位打包掩膜序列测试。

**核心用例只有一条**（其余都是护栏）：`areas()` 必须把"这一帧没看见"记 `None`、
把"看见了、面积是 0"记 `0.0`。DP-032 的整条事故链就建在这两者被混成一件事上：
分母把 `None` 当"不命中"，占比算成 0，软件于是在**从未成功观测**的情况下
断言"这里从来没有动物"，一只真老鼠被静默丢掉。
"""

from __future__ import annotations

import numpy as np

from depressionplex.maskseq import PackedMasks


def _blob(h: int = 12, w: int = 10) -> np.ndarray:
    m = np.zeros((h, w), dtype=bool)
    m[3:8, 2:6] = True
    return m


def test_roundtrip_is_bit_exact() -> None:
    """打包/解包不许改一个像素——掩膜是后面所有几何量的唯一来源。"""
    seq = PackedMasks((12, 10))
    a, b = _blob(), np.zeros((12, 10), dtype=bool)
    b[0, 0] = b[11, 9] = True          # 边角，最容易被 reshape 错位吃掉
    seq.append(a)
    seq.append(b)
    assert len(seq) == 2
    assert np.array_equal(seq[0], a)
    assert np.array_equal(seq[1], b)
    assert seq[0].dtype == np.dtype(bool)


def test_missing_frame_is_not_zero_area() -> None:
    """DP-032 的那条线：没看见 ⇒ None；看见了但空 ⇒ 0.0。两者不许混。"""
    seq = PackedMasks((12, 10))
    seq.append(_blob())                        # 看见了，有面积
    seq.append(None)                           # 没看见
    seq.append(np.zeros((12, 10), dtype=bool))  # 看见了，面积 0
    areas = seq.areas()
    assert areas[0] == 20.0, areas
    assert areas[1] is None, "分割失败必须是 None，0 会被下游读成'没有动物'"
    assert areas[2] == 0.0, "看见了但空是 0.0，不是 None"
    assert seq.n_missing == 1
    assert (seq.missing(0), seq.missing(1), seq.missing(2)) == (False, True, False)


def test_missing_frame_stores_empty_silhouette() -> None:
    """缺帧存全 False（→ RAD 返回 None → 特征 NaN → 该帧记 unknown）。
    **不许用上一帧补齐**：补齐出来的"没动"就是把分割失败渲染成 immobility。"""
    seq = PackedMasks((12, 10))
    seq.append(_blob())
    seq.append(None)
    assert not seq[1].any()
    assert not hasattr(seq, "fill_forward"), "本类不得提供任何缺帧填充入口"


def test_shape_mismatch_raises() -> None:
    """混形状 = 两套账。宁可炸也不许串成一个试次。"""
    seq = PackedMasks((12, 10))
    seq.append(_blob())
    try:
        seq.append(np.zeros((12, 11), dtype=bool))
    except ValueError as e:
        assert "形状" in str(e)
    else:
        raise AssertionError("形状不符必须 raise")


def test_slice_and_sequence_protocol() -> None:
    """`rad.decompose_series` 只用 len() 与 [i]（含 i-lag 随机访问），
    所以 Sequence 协议必须真的能用。"""
    seq = PackedMasks((12, 10))
    for _ in range(5):
        seq.append(_blob())
    assert len(seq[1:4]) == 3
    assert len(list(iter(seq))) == 5
    assert np.array_equal(seq[-1], _blob())


def test_rejects_degenerate_shape() -> None:
    for bad in ((0, 10), (10, 0)):
        try:
            PackedMasks(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad} 应被拒绝")
