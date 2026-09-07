# -*- coding: utf-8 -*-
"""DP-069 汇总段的冒烟测试：用已经跑出来的 6 组数字喂进去，**不重跑分割**。

为什么要这一步：上一次跑挂在汇总段（`10%` 后面跟中文 ⇒ `%` 格式化炸了），
6 个试次的 189 s × 6 全白跑。汇总段只在**所有试次跑完之后**才执行一次 ⇒
**它是整个脚本里最贵的一段代码路径，却最少被执行**。用桩数据在 2 秒内把它走一遍。

这一招通用：凡是「循环跑完才执行一次」的汇总/判定段，都该有个桩测试。
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("dp069", HERE / "dp069_area_geometry.py")
dp069 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dp069)

#: 2026-09-07 实测值，从 /tmp/dp069.log 的表里抄回来（xor_px, ratio, excess, mob_ex, trend）
OBS = {
    "30mg_2周-ch3":              (6.0, 0.500, 1.22, 1.13, +0.011),
    "30mg_2周_2+20_2周2-ch3":    (7.0, 0.429, 1.13, 1.00, +0.000),
    "20mg_3周-ch3":              (4.0, 0.333, 0.67, 1.06, +0.011),
    "20mg_3周-ch1":              (2.0, 1.000, 1.41, 0.89, -0.005),
    "10mg_2周-ch2":              (3.0, 0.962, 1.67, 0.97, +0.172),
    "20mg_1周_1-3+20_2周1-ch2":  (3.0, 0.615, 1.07, 1.00, +0.006),
}


def fake_probe(clip, human):
    xor_px, ratio, excess, mob_ex, trend = OBS[clip.stem]
    return dict(bl=30.0, bad_pairs=0, trend=trend, area_med=900.0,
                imm=dict(n=5000, xor_px=xor_px, ratio=ratio,
                         null=ratio / excess, excess=excess),
                mob=dict(n=3000, xor_px=25.0, ratio=0.2,
                         null=0.2 / mob_ex, excess=mob_ex))


dp069.probe_one = fake_probe
dp069.dp065.load_human = lambda p: {k: {"a": [], "b": []} for k in OBS}
# 桩掉切片存在性检查：本测试不碰素材
dp069.Path = Path
_real_exists = Path.exists
Path.exists = lambda self: True if self.suffix == ".mp4" else _real_exists(self)

sys.argv = ["dp069", "/nonexistent_clips", "/nonexistent_human.csv",
            "/tmp/dp069_smoke.md"]
rc = dp069.main()
Path.exists = _real_exists
print("\n=== 冒烟测试 rc=%d ===" % rc)
if rc != 0:
    raise SystemExit("汇总段没走通")
print("汇总段全路径执行通过（含分组比较与慢漂移那一段）")
