"""DP-094：CSI DepressionScan（FST）结果导入子包。

- `xlsx`：只用标准库的最小 .xlsx 读取（R7：不许引入 openpyxl/pandas/numpy）。
- `fst_import`：事件时间线 / Statistics / Bin / .SET / .CLB 的如实解析 + 自检。

口径与铁律见 `fst_import` 模块 docstring 和 `docs/spec_DP-094_CSI_FST结果导入.md`。
"""
