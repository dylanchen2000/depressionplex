"""范式窗口表（与引擎同源，防漂移由 tests/test_experiment_contract.py 守）。

本表在外壳里的唯一副本。任何显示窗口的地方必须 import 这个表，不许抄第二份。
"""

# 与引擎 depressionplex/assay_core/trial_report.py::ASSAY_WINDOWS 同源。
# 对不上时 tests/test_experiment_contract.py::test_assay_windows_sync 会红。
ASSAY_WINDOWS_UI: dict[str, tuple[float, float]] = {
    "TST": (0.0, 360.0),
    "FST": (120.0, 360.0),
}
