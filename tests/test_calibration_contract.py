"""标定契约守卫（DP-100）。架构文件 §4 的解锁条件，逐条被踩一遍。

**每个 `test_` 都对应一条判定分支**，因为本仓的教训是「只有失败时才执行的分支
必须被测试直接踩」（DP-069 / DP-071 / DP-076）。

这套测试**故意不 import PySide6**——被测模块也不许 import 它（架构 §3.4），
这正是它能在没有显示器的沙箱里被验的原因。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# `desktop` 不是本仓的可导入包（外壳与引擎是进程边界，不共享 sys.path），
# 所以按文件路径直接加载被测模块。
_SPEC = importlib.util.spec_from_file_location(
    "_calibration_under_test", ROOT / "desktop/app/services/calibration.py")
assert _SPEC and _SPEC.loader, "找不到 desktop/app/services/calibration.py"
cal = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = cal
_SPEC.loader.exec_module(cal)


def _good_payload(**over: object) -> dict:
    """一份「全都合格」的标定文件内容。各用例只改自己关心的那一处。"""
    d = {
        "schema_version": "1",
        "mode": "validated",
        "theta_mob": 0.0175,
        "batch": "T1-2026Q4-01",
        "dataset_sha256": "0" * 64,
        "signed_at": "2026-12-01T10:00:00+08:00",
        "tool_version": "1.0.0",
        "gates": {
            "G7": {"value": 0.851, "threshold": 0.818},
            "G8": {"value": -4.2, "threshold": 17.7},
            "G11": {"value": 0.79, "threshold": 0.75},
        },
        "g10a_false_positives": 0,
        "g10a_prime_false_positives": 0,
        "mae_s": 11.4,
        "rmse_s": 15.9,
        "per_trial_error_range_s": [-21.3, 18.7],
    }
    d.update(over)
    return d


def _write(payload: object) -> tuple[Path, str, tempfile.TemporaryDirectory]:
    """写一份标定文件，返回 (路径, 它的 sha256, 需要保活的临时目录)。"""
    tmp = tempfile.TemporaryDirectory()
    p = Path(tmp.name) / "calibration.json"
    if isinstance(payload, str):
        p.write_text(payload, encoding="utf-8")
    else:
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p, cal.file_sha256(p), tmp


def test_no_expectation_no_file_is_research():
    """研究版：不期望标定文件、也没有文件 ⇒ 黄徽章，不是错误。"""
    st = cal.evaluate_calibration(None, expected_sha256=None)
    assert st.mode is cal.Mode.RESEARCH
    assert st.badge is cal.Badge.YELLOW
    assert not st.may_report_metrology


def test_unexpected_file_is_red():
    """这一版不带标定文件，安装目录里却出现一个 ⇒ 红，且忽略其内容。"""
    p, _, tmp = _write(_good_payload())
    with tmp:
        st = cal.evaluate_calibration(p, expected_sha256=None)
    assert st.badge is cal.Badge.RED
    assert st.mode is cal.Mode.RESEARCH
    assert st.calibration is None, "不可信的文件不许把内容带进 status"


def test_expected_but_missing_is_red():
    """该带标定文件却找不到 ⇒ 红（安装不完整或被删）。"""
    st = cal.evaluate_calibration(Path("/nonexistent/calibration.json"),
                                 expected_sha256="a" * 64)
    assert st.badge is cal.Badge.RED
    assert st.mode is cal.Mode.RESEARCH


def test_hash_mismatch_is_red_and_content_ignored():
    """文件被改过 ⇒ 红，且**不读它的内容**（哈希校验必须先于解析）。"""
    p, _, tmp = _write(_good_payload())
    with tmp:
        st = cal.evaluate_calibration(p, expected_sha256="b" * 64)
    assert st.badge is cal.Badge.RED
    assert st.mode is cal.Mode.RESEARCH
    assert st.calibration is None


def test_all_gates_pass_is_validated():
    """哈希对、声明 validated、三个门都过、无假阳性 ⇒ 绿徽章。"""
    p, h, tmp = _write(_good_payload())
    with tmp:
        st = cal.evaluate_calibration(p, expected_sha256=h)
    assert st.mode is cal.Mode.VALIDATED
    assert st.badge is cal.Badge.GREEN
    assert st.may_report_metrology
    assert st.calibration is not None
    assert st.calibration.batch == "T1-2026Q4-01"


def test_g11_threshold_null_forces_research():
    """**本模块最重要的一条（DP-097）**：G11 门槛为 null ⇒ 强制 research，
    哪怕文件声明 validated、G7/G8 都过、无假阳性。

    反例来自实测：CSI 在我们这 27 个试次上 r = 0.842（过 G7）、偏差 +0.15 s
    （过 G8），同时把一个只有水没有鼠的杯位报成 466.88 s 不动。
    **总量门全过不等于有资格。不报 ≠ 过。**
    """
    payload = _good_payload()
    payload["gates"]["G11"] = {"value": None, "threshold": None}
    p, h, tmp = _write(payload)
    with tmp:
        st = cal.evaluate_calibration(p, expected_sha256=h)
    assert st.mode is cal.Mode.RESEARCH, "G11 门槛未定却解锁了计量模式"
    assert st.badge is cal.Badge.YELLOW
    assert not st.may_report_metrology
    assert any("G11" in r for r in st.reasons)
    # 这不是「文件坏了」，所以内容仍然带回来（界面要显示 θ_mob 与批次）
    assert st.calibration is not None


def test_declared_research_is_never_promoted():
    """文件声明 research 但读数全过 ⇒ 仍然 research。**只降级不升级。**"""
    p, h, tmp = _write(_good_payload(mode="research"))
    with tmp:
        st = cal.evaluate_calibration(p, expected_sha256=h)
    assert st.mode is cal.Mode.RESEARCH
    assert st.badge is cal.Badge.YELLOW


def test_gate_reading_below_threshold_is_red():
    """声称 validated 但 G7 读数不达标 ⇒ 红（声明与读数矛盾）。"""
    payload = _good_payload()
    payload["gates"]["G7"] = {"value": 0.7641, "threshold": 0.818}
    p, h, tmp = _write(payload)
    with tmp:
        st = cal.evaluate_calibration(p, expected_sha256=h)
    assert st.badge is cal.Badge.RED
    assert st.mode is cal.Mode.RESEARCH
    assert any("G7" in r for r in st.reasons)


def test_tampered_threshold_is_red():
    """把 G7/G8 的门槛值改松以求解锁 ⇒ 红。门槛只认代码里的常量。"""
    payload = _good_payload()
    payload["gates"]["G7"] = {"value": 0.60, "threshold": 0.50}
    p, h, tmp = _write(payload)
    with tmp:
        st = cal.evaluate_calibration(p, expected_sha256=h)
    assert st.badge is cal.Badge.RED
    assert any("门槛被改" in r for r in st.reasons)


def test_g10a_false_positive_is_red():
    """G10a / G10a' 是硬安全门：空隔间报出 immobility ⇒ 一个都不许有。"""
    for key in ("g10a_false_positives", "g10a_prime_false_positives"):
        p, h, tmp = _write(_good_payload(**{key: 1}))
        with tmp:
            st = cal.evaluate_calibration(p, expected_sha256=h)
        assert st.badge is cal.Badge.RED, f"{key}=1 竟然过了"
        assert st.mode is cal.Mode.RESEARCH


def test_malformed_json_is_red():
    """读不通 ⇒ 红，但**不抛异常退出**（客户机上「装了打不开」比黄徽章更糟）。"""
    p, h, tmp = _write("{ 这不是 json")
    with tmp:
        st = cal.evaluate_calibration(p, expected_sha256=h)
    assert st.badge is cal.Badge.RED
    assert st.mode is cal.Mode.RESEARCH


def test_missing_field_is_red_not_defaulted():
    """缺字段一律红，**不许填默认值**——默认值会把「这版没量」伪装成「量了且合格」。"""
    for key in ("theta_mob", "batch", "dataset_sha256", "signed_at",
                "tool_version", "mae_s", "rmse_s", "per_trial_error_range_s",
                "g10a_false_positives", "gates", "mode", "schema_version"):
        payload = _good_payload()
        payload.pop(key)
        p, h, tmp = _write(payload)
        with tmp:
            st = cal.evaluate_calibration(p, expected_sha256=h)
        assert st.badge is cal.Badge.RED, f"缺 {key} 竟然没红"


def test_gate_missing_key_differs_from_null():
    """`threshold` 缺字段 ≠ `threshold: null`：前者是文件坏了（红），
    后者是门槛未定（黄）。混成一种会让「坏文件」被当成「正常研究版」。"""
    payload = _good_payload()
    payload["gates"]["G11"] = {"value": 0.79}      # 缺 threshold
    p, h, tmp = _write(payload)
    with tmp:
        st = cal.evaluate_calibration(p, expected_sha256=h)
    assert st.badge is cal.Badge.RED


def test_unknown_key_is_red():
    """未知键拒收：多出来的字段说明这不是我们发布链签出来的文件。"""
    p, h, tmp = _write(_good_payload(force_validated=True))
    with tmp:
        st = cal.evaluate_calibration(p, expected_sha256=h)
    assert st.badge is cal.Badge.RED
    assert st.mode is cal.Mode.RESEARCH


def test_doc_example_matches_code():
    """`docs/标定文件示例_研究版.json` 必须能被本模块原样解析，且判为研究版。

    这是**防文档漂移**的守卫：签发标定的人照那份示例填，示例一旦与代码要求的
    字段表不一致，他就会拿到一个红徽章而不知道为什么。示例声明 research 且
    `gates.G11.threshold` 为 null——正是 2026-09-13 的真实状态。
    """
    example = ROOT / "docs/标定文件示例_研究版.json"
    assert example.exists(), "示例标定文件不在了"
    st = cal.evaluate_calibration(example, expected_sha256=cal.file_sha256(example))
    assert st.badge is cal.Badge.YELLOW, f"示例文件解析不过：{st.reasons}"
    assert st.mode is cal.Mode.RESEARCH
    assert st.calibration is not None
    assert st.calibration.theta_mob == 0.0175, "示例里的 θ_mob 不是冻结值"


def test_no_api_path_to_force_validated():
    """**不许存在任何入参能把模式抬到 validated。**

    守卫的是「以后有人为了方便加一个 `force_mode=` 开关」这件事——
    §4 写死了「外壳不许有任何路径能把模式手动切到 Validated」。
    """
    import inspect
    sig = inspect.signature(cal.evaluate_calibration)
    assert set(sig.parameters) == {"path", "expected_sha256"}, (
        f"evaluate_calibration 的入参变了：{list(sig.parameters)}"
        "——多出来的每一个都可能成为手动解锁的后门")
    src = inspect.getsource(cal)
    assert "Mode.VALIDATED" in src
    # 只允许一处构造 validated 结论（函数末尾那一处）
    assert src.count("mode=Mode.VALIDATED") == 1, (
        "构造 validated 结论的地方不止一处，解锁路径必须唯一")


def test_no_pyside_and_no_engine_import():
    """本模块必须与 PySide6 和分析包都无关（§3.4）：
    前者保证它能在无显示环境被测，后者是外壳的进程边界。

    **按 AST 看真实 import，不按字符串搜**——文档里写「不许 import PySide6」
    这句话本身不构成违规，用子串匹配会把注释当代码（第一版就这么误报了）。
    """
    import ast
    src = (ROOT / "desktop/app/services/calibration.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:                       # 相对 import
                imported.add(f".{node.module or ''}")
            elif node.module:
                imported.add(node.module.split(".")[0])
    # 只许标准库这几个
    allowed = {"hashlib", "json", "dataclasses", "enum", "pathlib", "__future__",
               "ast", "inspect"}
    assert imported <= allowed, f"calibration.py 多了非标准库 import：{imported - allowed}"
    for forbidden in ("PySide6", "depressionplex"):
        assert forbidden not in imported, f"calibration.py import 了 {forbidden}"
