"""采集自检测试（DP-109 B7）。

10 条守卫，每条都要能被变异打红。
变异脚本：/tmp/mutate_b7.py。

**不许依赖真解码（ffmpeg 在 CI 上不保证有）**。合成帧走 tests/synth.py；
解码那一层用 test_progress_runjson.py 的 monkey-patch 路子绕开。
"""

from __future__ import annotations

import ast
import io
import json
import sys
from pathlib import Path
from typing import Callable

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── 合成帧工具（与 test_runner.py 同款）───────────────────────────────────────
H = 268
W_CH = 95
W_PILLAR = 4
N_CH = 4
FPS = 25.0

MOVE, STILL = "move", "still"


def _chamber(kind: str, i: int) -> np.ndarray:
    """单隔间 ROI：高对比（顶框 10，面板带 250，胶带列 44-49=15，动物=20）。"""
    g = np.full((H, W_CH), 250.0)
    g[0:70, :] = 10.0            # 顶框（高对比静态结构，用于噪声底）
    g[H - 9:, :] = 10.0          # 底框
    g[70:150, 44:50] = 15.0      # 胶带
    if kind == MOVE:
        top = 150 if i % 2 == 0 else 158
        col = 36 + 3 * (i % 6)
        g[top:190, col:col + 12] = 20.0
    else:  # STILL
        g[152:182, 18:30] = 20.0
    return g


def _frame(kinds: tuple, i: int) -> np.ndarray:
    w = N_CH * W_CH + (N_CH + 1) * W_PILLAR
    g = np.full((H, w), 10.0)
    for k, kind in enumerate(kinds):
        c0 = W_PILLAR + k * (W_CH + W_PILLAR)
        g[:, c0:c0 + W_CH] = _chamber(kind, i)
    return g


def _make_frames(kinds: tuple, n: int) -> list[np.ndarray]:
    return [_frame(kinds, i) for i in range(n)]


def _no_panel_frame() -> np.ndarray:
    """完全均匀的灰色帧，panel_band 找不到。"""
    return np.full((H, W_CH * N_CH + W_PILLAR * (N_CH + 1)), 128.0)


# ── monkey-patch 视频解码 ─────────────────────────────────────────────────────

def _patch_video(frames: list[np.ndarray], n_frames: int | None = None):
    """返回 context manager：把 depressionplex.video 换成合成数据。"""
    import contextlib
    from depressionplex import video as V
    from depressionplex.video import VideoInfo

    real_n = n_frames if n_frames is not None else len(frames)
    h, w = frames[0].shape if frames else (H, 400)
    fake_info = VideoInfo(
        path=Path("/synth.mp4"),
        fps=FPS,
        n_frames=real_n,
        width=w,
        height=h,
        frame_count_source="nb_frames",
    )

    orig_probe = V.probe
    orig_frames_at = V.frames_at

    @contextlib.contextmanager
    def _ctx():
        def _probe(path):
            return fake_info

        def _frames_at(info, indices):
            return [frames[i % len(frames)] for i in indices]

        V.probe = _probe
        V.frames_at = _frames_at
        try:
            yield fake_info
        finally:
            V.probe = orig_probe
            V.frames_at = orig_frames_at

    return _ctx()


def _run_main_streams(frames: list[np.ndarray], extra_argv: list[str] | None = None,
                     n_frames: int | None = None,
                     probe_exc: BaseException | None = None) -> tuple[int, str, str]:
    """跑一次 acq_check.main，返回 (rc, stdout, stderr)。

    stderr 也留着：DP-120 之后外壳要拿它取引擎自己那句报错原文，
    「引擎说了什么」本身就是被守卫盯的东西。

    probe_exc 不为 None 时把 `video.probe` 换成抛这个异常的版本——
    那是引擎 rc=1（解码失败）唯一的真实入口，不许拿手写 stderr 冒充。
    """
    from depressionplex.cli import acq_check

    argv = ["--video", "/synth.mp4", "--chambers", str(N_CH)]
    if extra_argv:
        argv.extend(extra_argv)

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    sys.stdout = out_buf
    sys.stderr = err_buf
    try:
        with _patch_video(frames, n_frames):
            if probe_exc is not None:
                from depressionplex import video as V

                def _raise(path):
                    raise probe_exc

                V.probe = _raise      # _patch_video 的 finally 会还原
            rc = acq_check.main(argv)
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return rc, out_buf.getvalue(), err_buf.getvalue()


def _run_main(frames: list[np.ndarray], extra_argv: list[str] | None = None,
              n_frames: int | None = None) -> tuple[int, str]:
    """跑一次 acq_check.main，返回 (rc, stdout_text)。stderr 丢弃（老调用方用这个）。"""
    rc, out, _err = _run_main_streams(frames, extra_argv, n_frames)
    return rc, out


# ═══════════════════════════════════════════════════════════════════════════════
# 守卫 1：门槛只有一个来源（AST 守卫）
# ═══════════════════════════════════════════════════════════════════════════════

def test_single_gate_source_ast() -> None:
    """四个文件不许出现字面量 100/2.0/0.02；计算文件必须以 Load 方式用三个常量名。

    注意：probe_frames.py 有 `MOVING_RESIDUAL: float = 0.02`（模块级常量定义），
    这是定义不同用途的命名常量（值与 GATE_AREA_JITTER_P90 相同但语义不同），
    本守卫允许模块级常量定义，只禁止在表达式中直接使用字面量。

    派工单 §3.1 要求 models/self_test.py 和 pages/self_test.py 也使用 Load 常量，
    但这两个文件在进程边界外（不许 import depressionplex），无法直接引用常量。
    本守卫对桌面文件只检查"无字面量"，对引擎 CLI 文件同时检查 Load 引用。
    （矛盾之处见交付报告，照着错的做不算免责——此处取合理解释。）
    """
    check_files = [
        ROOT / "depressionplex" / "assay_core" / "segment.py",
        ROOT / "depressionplex" / "cli" / "acq_check.py",
        ROOT / "depressionplex" / "cli" / "probe_frames.py",
        ROOT / "desktop" / "app" / "pages" / "self_test.py",
        ROOT / "desktop" / "app" / "models" / "self_test.py",
    ]
    load_files = [
        ROOT / "depressionplex" / "cli" / "acq_check.py",
        ROOT / "depressionplex" / "cli" / "probe_frames.py",
    ]
    gate_const_names = {"GATE_CONTRAST_ABS", "GATE_CONTRAST_RATIO", "GATE_AREA_JITTER_P90"}
    # 只禁止浮点字面量形式（100.0 / 2.0 / 0.02）
    # 整数 2、100 在代码中合法出现于退出码、列表切片等，不是门槛字面量
    forbidden_float_values = {100.0, 2.0, 0.02}

    for fpath in check_files:
        assert fpath.exists(), f"找不到文件 {fpath}"
        src = fpath.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(fpath))

        # 精确白名单：只豁免 probe_frames.MOVING_RESIDUAL 定义行（数值恰好与
        # GATE_AREA_JITTER_P90 相同但语义不同）和 segment.py 的三个 GATE_ 常量定义行。
        # 其他文件：空白名单——门槛值字面量一律禁止。
        allowed_const_ids: set[int] = set()
        _probe_only = {"MOVING_RESIDUAL"}
        _segment_only = {"GATE_CONTRAST_ABS", "GATE_CONTRAST_RATIO", "GATE_AREA_JITTER_P90"}
        if fpath.name == "probe_frames.py":
            _allowed_names = _probe_only
        elif fpath.name == "segment.py":
            _allowed_names = _segment_only
        else:
            _allowed_names = set()
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign):
                if (len(stmt.targets) == 1
                        and isinstance(stmt.targets[0], ast.Name)
                        and stmt.targets[0].id in _allowed_names
                        and isinstance(stmt.value, ast.Constant)):
                    allowed_const_ids.add(id(stmt.value))
            elif isinstance(stmt, ast.AnnAssign):
                if (isinstance(stmt.target, ast.Name)
                        and stmt.target.id in _allowed_names
                        and isinstance(stmt.value, ast.Constant)):
                    allowed_const_ids.add(id(stmt.value))

        # 对 segment.py 只扫 contrast_report 函数体，其余模块代码不扫
        if fpath.name == "segment.py":
            contrast_func = None
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == "contrast_report":
                    contrast_func = node
                    break
            if contrast_func is None:
                raise AssertionError("segment.py: 找不到 contrast_report 函数")
            nodes_to_scan = list(ast.walk(contrast_func))
        else:
            nodes_to_scan = list(ast.walk(tree))

        # 找所有被禁止的浮点字面量（跳过模块级常量定义）
        # 只检查 float 类型：整数 2 / 100 在代码里有合法用途（退出码/切片），
        # 门槛值以 float 形式写入（100.0 / 2.0 / 0.02）才是违规
        violations: list[str] = []
        for node in nodes_to_scan:
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                val = node.value
                if val in forbidden_float_values and id(node) not in allowed_const_ids:
                    violations.append(
                        f"  line {node.lineno}: 浮点字面量 {node.value!r}"
                    )
        assert not violations, (
            f"{fpath.relative_to(ROOT)} 出现了禁止的门槛字面量\n"
            + "\n".join(violations)
            + "\n豁免白名单：probe_frames.MOVING_RESIDUAL 定义行、segment.py 三个 GATE_ 常量定义行"
        )

    # 引擎 CLI 文件必须以 ast.Load 方式引用三个常量
    for fpath in load_files:
        src = fpath.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(fpath))
        loaded: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id in gate_const_names:
                    loaded.add(node.id)
        missing = gate_const_names - loaded
        assert not missing, (
            f"{fpath.relative_to(ROOT)} 缺少以 ast.Load 引用的常量：{sorted(missing)}"
        )

    # 字符串常量中不许包含门槛数值的十进制写法（DP-054 形状：屏幕写一个数、实际按另一个判）
    # 五种渲染逐一查子串：str(v)、f"{v}"、f"{v:.0f}"、f"{v:.1f}"、f"{v:.4f}"
    # 只保留含小数点且长度 >= 3 的形式，避免 "0" / "2" / "100" 等整数形式产生大量误报
    from depressionplex.assay_core.segment import (
        GATE_CONTRAST_ABS, GATE_CONTRAST_RATIO, GATE_AREA_JITTER_P90,
    )
    from depressionplex.cli.probe_frames import MOVING_RESIDUAL
    gate_vals = [GATE_CONTRAST_ABS, GATE_CONTRAST_RATIO, GATE_AREA_JITTER_P90, MOVING_RESIDUAL]
    gate_val_strs: set[str] = set()
    for val in gate_vals:
        for fmt in (str(val), f"{val}", f"{val:.0f}", f"{val:.1f}", f"{val:.4f}"):
            # 过滤掉整数形式（无小数点），避免 "0"/"2"/"100" 等在代码字符串中无处不在
            if "." in fmt:
                gate_val_strs.add(fmt)
    str_violations: list[str] = []
    for fpath in check_files:
        src = fpath.read_text(encoding="utf-8")
        tree2 = ast.parse(src, filename=str(fpath))
        # segment.py 与浮点字面量守卫一样，只扫 contrast_report 函数体
        if fpath.name == "segment.py":
            _cf = None
            for _n in ast.walk(tree2):
                if isinstance(_n, ast.FunctionDef) and _n.name == "contrast_report":
                    _cf = _n
                    break
            str_nodes = list(ast.walk(_cf)) if _cf else []
        else:
            str_nodes = list(ast.walk(tree2))
        for node in str_nodes:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                s = node.value
                for vs in gate_val_strs:
                    if vs in s:
                        str_violations.append(
                            f"  {fpath.relative_to(ROOT)} line {node.lineno}: "
                            f"字符串含门槛数值字符串 {vs!r}"
                        )
                        break  # 同一节点只报一次
    assert not str_violations, (
        "检查文件里有字符串常量包含门槛数值——"
        "屏幕写着一个数、实际按另一个判（DP-054 形状）：\n"
        + "\n".join(str_violations)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 守卫 2a-c：三个门常量各自独立影响判定（三条独立守卫，各打一个变异靶）
# ═══════════════════════════════════════════════════════════════════════════════

def test_gate_contrast_abs_actually_used() -> None:
    """把 GATE_CONTRAST_ABS 临时改到 1e9，高对比帧的 passes_gate 必须变假。

    变异（M2a）：contrast_report 里把 GATE_CONTRAST_ABS 改回字面量 100.0 ⇒ 本条红。
    """
    from depressionplex.assay_core import segment as S

    orig = S.GATE_CONTRAST_ABS
    try:
        S.GATE_CONTRAST_ABS = 1e9
        frame = _make_frames((STILL, STILL, STILL, STILL), 1)[0]
        rep = S.contrast_report(frame)
        assert rep.get("passes_gate", 0.0) == 0.0, (
            "GATE_CONTRAST_ABS=1e9 后 passes_gate 应变假——"
            "若仍为真说明 contrast_report 用了字面量而非常量"
        )
    finally:
        S.GATE_CONTRAST_ABS = orig

    # 复验：恢复后高对比帧应通过
    frame = _make_frames((STILL, STILL, STILL, STILL), 1)[0]
    rep_ok = S.contrast_report(frame)
    assert S.GATE_CONTRAST_ABS == orig


def test_gate_contrast_ratio_actually_used() -> None:
    """把 GATE_CONTRAST_RATIO 临时改到 1e9，高对比帧的 passes_gate 必须变假。

    变异（M2b）：contrast_report 里把 GATE_CONTRAST_RATIO 改回字面量 2.0 ⇒ 本条红。
    """
    from depressionplex.assay_core import segment as S

    orig = S.GATE_CONTRAST_RATIO
    try:
        S.GATE_CONTRAST_RATIO = 1e9
        frame = _make_frames((STILL, STILL, STILL, STILL), 1)[0]
        rep = S.contrast_report(frame)
        assert rep.get("passes_gate", 0.0) == 0.0, (
            "GATE_CONTRAST_RATIO=1e9 后 passes_gate 应变假——"
            "若仍为真说明 contrast_report 用了字面量而非常量"
        )
    finally:
        S.GATE_CONTRAST_RATIO = orig

    assert S.GATE_CONTRAST_RATIO == orig


def test_gate_area_jitter_actually_used() -> None:
    """把 acq_check.GATE_AREA_JITTER_P90 临时改到 -0.001，STILL 帧跑完 rc 应为 0 且 passed=False。

    -0.001 而非 0.0：STILL 帧的抖动 p90 精确等于 0.0（帧帧相同），
    0.0 <= 0.0 = True——门槛正好卡在分界上不能区分「常量被读到」与「恰好相等」。
    用 -0.001 保证任何非负抖动值都会 fail，只有真的读常量才能变红。

    变异（M2c）：acq_check._run_acq_check 里把 GATE_AREA_JITTER_P90 改回字面量 0.02 ⇒ 本条红。
    """
    from depressionplex.cli import acq_check

    orig = acq_check.GATE_AREA_JITTER_P90
    frames = _make_frames((STILL, STILL, STILL, STILL), 80)
    try:
        acq_check.GATE_AREA_JITTER_P90 = -0.001  # 门槛=-0.001 ⇒ 任何 >=0 的抖动都不通过
        rc, out = _run_main(frames)
    finally:
        acq_check.GATE_AREA_JITTER_P90 = orig

    assert rc == 0, f"门槛=-0.001 时 rc 仍应为 0（测到了读数），得到 rc={rc}"
    import json
    data = json.loads(out)
    assert data["gates"]["area_jitter"]["passed"] is False, (
        "GATE_AREA_JITTER_P90=-0.001 时 area_jitter.passed 应为 False——"
        "若仍为 True 说明 _run_acq_check 用了字面量而非常量"
    )
    assert acq_check.GATE_AREA_JITTER_P90 == orig, "常量未正确恢复"


# ═══════════════════════════════════════════════════════════════════════════════
# 守卫 3：JSON 键集固定（缺、多都红）
# ═══════════════════════════════════════════════════════════════════════════════

def test_json_key_set_is_fixed() -> None:
    """acq_check 输出的每层 JSON 键集必须与 ACQ_*_KEYS 常量完全一致。"""
    from depressionplex.cli import acq_check

    kinds = (STILL, STILL, STILL, STILL)
    frames = _make_frames(kinds, 80)  # 足够多帧
    rc, out = _run_main(frames)
    assert rc == 0, f"rc 应为 0，得到 {rc}，输出：{out[:200]}"
    data = json.loads(out)

    # 顶层
    assert set(data.keys()) == set(acq_check.ACQ_JSON_KEYS), (
        f"顶层键不符：得到 {sorted(data.keys())}，期望 {sorted(acq_check.ACQ_JSON_KEYS)}"
    )
    # video
    assert set(data["video"].keys()) == set(acq_check.ACQ_VIDEO_KEYS)
    # sampling
    assert set(data["sampling"].keys()) == set(acq_check.ACQ_SAMPLING_KEYS)
    # gates
    assert set(data["gates"].keys()) == set(acq_check.ACQ_GATES_KEYS)
    assert set(data["gates"]["contrast"].keys()) == set(acq_check.ACQ_CONTRAST_KEYS)
    assert set(data["gates"]["noise_floor"].keys()) == set(acq_check.ACQ_NOISE_FLOOR_KEYS)
    assert set(data["gates"]["area_jitter"].keys()) == set(acq_check.ACQ_AREA_JITTER_KEYS)
    # chambers[]
    if data["chambers"]:
        for ch in data["chambers"]:
            assert set(ch.keys()) == set(acq_check.ACQ_CHAMBER_KEYS), (
                f"chambers 项键不符：{sorted(ch.keys())} vs {sorted(acq_check.ACQ_CHAMBER_KEYS)}"
            )

    # reference 层键集对账（ACQ_REFERENCE_KEYS 不许是零读者）
    ref = data.get("reference")
    assert ref is not None, (
        "reference 不应为 None——P2_REFERENCE 随包走，改完 A4 后永远可用"
    )
    missing_ref = [k for k in acq_check.ACQ_REFERENCE_KEYS if k not in ref]
    extra_ref = [k for k in ref if k not in acq_check.ACQ_REFERENCE_KEYS]
    assert not missing_ref, f"reference 层缺少键：{missing_ref}（ACQ_REFERENCE_KEYS 不完整）"
    assert not extra_ref, f"reference 层多出键：{extra_ref}（ACQ_REFERENCE_KEYS 漏声明了这些键）"


# ═══════════════════════════════════════════════════════════════════════════════
# 守卫 4：门不通过 ⇒ 退出码 0（不是 2！）
# ═══════════════════════════════════════════════════════════════════════════════

def test_gate_fail_gives_rc_0_not_2() -> None:
    """对比度门不通过时 rc=0（「测到了读数」，非「测不出」）。

    通过 monkey-patch contrast_report 注入已知低对比读数，避免合成帧的像素分布
    依赖问题——测试目标是 acq_check.main 的退出码路由，不是分割算法本身。

    变异：把「不通过」改成 rc=2 ⇒ 本条必须红。
    """
    from depressionplex.assay_core import segment as S

    # 注入低对比读数（abs_diff=40 < 100，ratio=1.2 < 2.0）
    _LOW_CONTRAST_REPORT = {
        "threshold": 150.0,
        "background_mean": 200.0,
        "animal_mean": 160.0,
        "abs_diff": 40.0,      # < GATE_CONTRAST_ABS=100
        "ratio": 1.25,         # < GATE_CONTRAST_RATIO=2.0
        "dark_from_mask": 1.0,
        "passes_gate": 0.0,    # 不通过
    }

    orig_contrast = S.contrast_report
    # 同时需要 find_chambers 返回非空（才能走完到 rc=0）
    orig_chambers = S.find_chambers
    S.contrast_report = lambda g: _LOW_CONTRAST_REPORT.copy()
    S.find_chambers = lambda g, **kw: [(0, 94)]  # 一个假隔间

    kinds = (STILL, STILL, STILL, STILL)
    frames = _make_frames(kinds, 80)
    try:
        rc, out = _run_main(frames)
        data = json.loads(out)
    finally:
        S.contrast_report = orig_contrast
        S.find_chambers = orig_chambers

    assert rc == 0, (
        f"对比度不达标时 rc 应为 0（测到了读数），得到 rc={rc}。"
        "把「不通过」映射成非零码会让 GUI 显示「程序崩了」"
    )
    assert data["gates"]["contrast"]["passed"] is False, (
        "低对比读数的 contrast.passed 应为 False"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 守卫 5：测不出 ⇒ rc=2，读数是 null 不是 0
# ═══════════════════════════════════════════════════════════════════════════════

def test_no_panel_gives_rc_2_and_null_values() -> None:
    """完全无面板带的帧：rc=2，gates.contrast.value is None（不是 0）。

    变异：把测不出的读数兜底成 0 ⇒ `gates.contrast.value is None` 断言红。
    """
    no_panel = _no_panel_frame()
    frames = [no_panel] * 80

    rc, out = _run_main(frames)
    data = json.loads(out)

    assert rc == 2, f"无面板带时 rc 应为 2，得到 rc={rc}"
    assert data["gates"]["contrast"]["value"] is None, (
        "无法测量时 contrast.value 必须是 null，不许兜底成 0"
    )
    assert data["gates"]["area_jitter"]["value"] is None, (
        "无法测量时 area_jitter.value 必须是 null"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 守卫 6：同一素材两次跑 JSON 逐位相同（除 generated_at）
# ═══════════════════════════════════════════════════════════════════════════════

def test_deterministic_output() -> None:
    """同一素材跑两次，JSON 逐位相同（除 generated_at），frame_indices 出现且相同。

    生成时间戳必须是**单独一个键**（不许嵌进别的字符串里）——
    如果时间戳嵌在别的字段里，这条依然会红（因为两次时间不同，比对失败）。
    """
    kinds = (STILL, STILL, STILL, STILL)
    frames = _make_frames(kinds, 80)

    _, out1 = _run_main(frames)
    _, out2 = _run_main(frames)

    d1 = json.loads(out1)
    d2 = json.loads(out2)

    # generated_at 必须是独立字段
    assert "generated_at" in d1
    assert "generated_at" in d2

    # 除 generated_at 外完全相同
    def _strip_ts(d: dict) -> dict:
        return {k: v for k, v in d.items() if k != "generated_at"}

    assert _strip_ts(d1) == _strip_ts(d2), (
        "两次跑同一素材，除 generated_at 外 JSON 应逐位相同"
    )

    # frame_indices 存在且两次相同
    fi1 = d1["sampling"]["frame_indices"]
    fi2 = d2["sampling"]["frame_indices"]
    assert isinstance(fi1, list) and len(fi1) > 0, "frame_indices 不能为空"
    assert fi1 == fi2


# ═══════════════════════════════════════════════════════════════════════════════
# 守卫 7：判定隔间是残差最低那个
# ═══════════════════════════════════════════════════════════════════════════════

def test_best_chamber_is_lowest_residual() -> None:
    """造三个隔间，残差人为拉开：STILL/STILL/MOVE。
    判定隔间应为 STILL 类的其中一个（残差最低），而 MOVE 隔间的读数仍在 chambers[]。

    变异：把"选残差最低"改成"选第一个"或"选最高" ⇒ 若 MOVE 被选中本条红。
    """
    from depressionplex.cli import acq_check

    # 3 隔间：ch1=STILL, ch2=STILL, ch3=MOVE（残差高）
    N = 3
    W_FRAME = N * W_CH + (N + 1) * W_PILLAR

    def _frame3(i: int) -> np.ndarray:
        g = np.full((H, W_FRAME), 10.0)
        for k, kind in enumerate((STILL, STILL, MOVE)):
            c0 = W_PILLAR + k * (W_CH + W_PILLAR)
            g[:, c0:c0 + W_CH] = _chamber(kind, i)
        return g

    frames = [_frame3(i) for i in range(80)]
    h, w = frames[0].shape

    from depressionplex import video as V
    from depressionplex.video import VideoInfo

    orig_probe = V.probe
    orig_frames_at = V.frames_at
    fake_info = VideoInfo(
        path=Path("/synth3.mp4"), fps=FPS, n_frames=len(frames),
        width=w, height=h, frame_count_source="nb_frames"
    )

    def _probe(path): return fake_info
    def _fa(info, idxs): return [frames[i % len(frames)] for i in idxs]

    V.probe = _probe
    V.frames_at = _fa
    old_out, old_err = sys.stdout, sys.stderr
    buf = io.StringIO()
    sys.stdout = buf
    sys.stderr = io.StringIO()
    try:
        rc = acq_check.main(["--video", "/synth3.mp4", "--chambers", "3"])
        out = buf.getvalue()
    finally:
        V.probe = orig_probe
        V.frames_at = orig_frames_at
        sys.stdout = old_out
        sys.stderr = old_err

    data = json.loads(out)

    # 选中的隔间必须是 STILL 类（ch1 或 ch2，索引 1 或 2）
    selected = data["gates"]["area_jitter"]["chamber"]
    assert selected is not None, "应能选出判定隔间"
    assert selected in {1, 2}, (
        f"判定隔间应为 STILL 类（残差最低），得到隔间 {selected}（ch3=MOVE 残差高）"
    )

    # 全部 3 个隔间的读数都在 chambers[]
    chamber_indices = {ch["index"] for ch in data["chambers"]}
    assert chamber_indices == {1, 2, 3}, (
        f"chambers[] 应包含全部 3 个隔间，得到 {sorted(chamber_indices)}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 守卫 8：冻结表与文档一致（防止 fixture 改了参考值而文档没跟）
# ═══════════════════════════════════════════════════════════════════════════════

def test_p2_reference_matches_issues_md() -> None:
    """p2_reference.json（对账凭证）与 P2_REFERENCE 常量逐字段相同，
    且四个数值在 docs/ISSUES.md 里逐字出现。

    防的是：
    - fixture 里某个数字被悄悄改掉而常量没跟（M8 变异）；
    - ISSUES.md 台账没有记录参考读数；
    - fixture 键被删掉或改名（A10 fix：先断言键存在）。
    """
    from depressionplex.assay_core.p2_reference import P2_REFERENCE

    issues_path = ROOT / "docs" / "ISSUES.md"
    ref_path = ROOT / "tests" / "fixtures" / "p2_reference.json"

    assert issues_path.exists(), f"ISSUES.md 不存在：{issues_path}"
    assert ref_path.exists(), f"p2_reference.json 不存在：{ref_path}"

    issues_text = issues_path.read_text(encoding="utf-8")
    with open(ref_path, encoding="utf-8") as f:
        fixture = json.load(f)

    # fixture 与 P2_REFERENCE 逐字段相同（对账凭证不许悄悄漂移）
    numeric_keys = ("contrast_abs", "contrast_ratio", "noise_floor_px", "area_jitter_p90")
    fixture_errors: list[str] = []
    for key in numeric_keys:
        # A10 fix：先断言键存在，不许 get+None 豁免
        assert key in fixture, (
            f"p2_reference.json 缺少必须的键 {key!r}——"
            "删键或改名是一种「悄悄改掉」，比改值更彻底"
        )
        assert key in P2_REFERENCE, f"P2_REFERENCE 缺少键 {key!r}"
        if fixture[key] != P2_REFERENCE[key]:
            fixture_errors.append(
                f"  fixture[{key!r}]={fixture[key]!r} != P2_REFERENCE[{key!r}]={P2_REFERENCE[key]!r}"
            )
    assert not fixture_errors, (
        "p2_reference.json 与 P2_REFERENCE 常量不同步：\n" + "\n".join(fixture_errors)
    )

    # 四个数值必须在 ISSUES.md 里逐字出现
    missing_in_issues: list[str] = []
    for key in numeric_keys:
        val = P2_REFERENCE[key]
        # val is not None (all four are concrete floats)
        if str(val) not in issues_text:
            missing_in_issues.append(
                f"  P2_REFERENCE[{key!r}] = {val!r}  在 docs/ISSUES.md 里找不到"
            )
    assert not missing_in_issues, (
        "P2_REFERENCE 里的参考值在 ISSUES.md 里找不到——台账没更新：\n"
        + "\n".join(missing_in_issues)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 守卫 9：页面 / 模型不许引入 numpy/math/depressionplex
# ═══════════════════════════════════════════════════════════════════════════════

def test_no_pyside_and_no_engine_import_in_desktop_files() -> None:
    """pages/self_test.py 和 models/self_test.py 不许 import numpy/statistics/math/depressionplex。

    这是对 test_desktop_boundary.py 的扩展（DP-100 口径），专门盯住这两个新文件。
    页面 / 模型只做显示和模型映射，不许把引擎包引进来。
    """
    files_to_check = [
        ROOT / "desktop" / "app" / "pages" / "self_test.py",
        ROOT / "desktop" / "app" / "models" / "self_test.py",
    ]
    forbidden_prefixes = ("numpy", "statistics", "math", "depressionplex", "assay_core")

    for fpath in files_to_check:
        assert fpath.exists(), f"找不到文件 {fpath}"
        tree = ast.parse(fpath.read_text(encoding="utf-8"))

        violations: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in forbidden_prefixes:
                        violations.append(f"  line {node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    if top in forbidden_prefixes:
                        violations.append(f"  line {node.lineno}: from {node.module} import ...")

        assert not violations, (
            f"{fpath.relative_to(ROOT)} 不许 import 计算库或引擎包：\n"
            + "\n".join(violations)
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 守卫 10：端到端——真跑一次 main，用其输出喂 models/self_test.py
# ═══════════════════════════════════════════════════════════════════════════════

def test_end_to_end_main_feeds_model() -> None:
    """真跑一次 acq_check.main([...]) 产出 JSON，用它喂 AcqCheckResult，
    断言三行门都读得出、参考列读得出、诊断表行数 == 隔间数。

    不许拿手写的假 JSON 当唯一夹具（DP-102 的教训）。
    """
    from desktop.app.models.self_test import AcqCheckResult, AcqCheckError

    kinds = (STILL, STILL, STILL, STILL)
    frames = _make_frames(kinds, 80)

    rc, out = _run_main(frames)
    assert rc == 0, f"端到端跑出 rc={rc}，期望 0"
    assert out.strip(), "stdout 不能为空"

    # 喂给模型（不许抛 AcqCheckError）
    try:
        result = AcqCheckResult.from_json_str(out)
    except AcqCheckError as e:
        raise AssertionError(f"模型解析失败：{e}") from e

    # 三行门都读得出
    _ = result.contrast_value        # 可以是 None，但属性访问不许抛
    _ = result.contrast_threshold_abs
    _ = result.contrast_passed
    _ = result.noise_floor_value
    _ = result.noise_floor_n_frames
    _ = result.area_jitter_value
    _ = result.area_jitter_threshold
    _ = result.area_jitter_passed

    # 参考列读得出（reference 是字典，不是 None——P2_REFERENCE 随包走，永远可用）
    ref = result.reference
    assert ref is not None, "参考列读不到（P2_REFERENCE 应随包走，永远可用）"
    assert "contrast_abs" in ref, "reference 缺少 contrast_abs"
    assert "area_jitter_p90" in ref, "reference 缺少 area_jitter_p90"

    # 诊断表行数 == 隔间数
    chambers = result.chambers
    assert len(chambers) == N_CH, (
        f"诊断表行数应为 {N_CH}（隔间数），得到 {len(chambers)}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 附加守卫：帧数不足 ⇒ rc=2（明确行为，不许悄悄少抽）
# ═══════════════════════════════════════════════════════════════════════════════

def test_insufficient_frames_gives_rc_2() -> None:
    """素材帧数少于 _MIN_FRAMES 时，rc=2（明确拒绝，不许悄悄用少量帧自检）。

    派工单 §4 第 4 条要求：「若素材短于 5×8 + 余量 帧会怎样，要有明确行为」。
    """
    from depressionplex.cli.acq_check import _MIN_FRAMES

    kinds = (STILL, STILL, STILL, STILL)
    frames = _make_frames(kinds, 10)  # 远少于最小要求

    rc, out = _run_main(frames, n_frames=10)
    assert rc == 2, (
        f"帧数不足（10 < {_MIN_FRAMES}）时 rc 应为 2，得到 rc={rc}"
    )

# ═══════════════════════════════════════════════════════════════════════════════
# 守卫 11（H1）：desktop/ 不许出现 sys.frozen / _MEIPASS / __import__("sys")
# ═══════════════════════════════════════════════════════════════════════════════

def test_no_frozen_internals_in_desktop() -> None:
    """desktop/ 除 utils/paths.py 以外，不许出现任何 PyInstaller frozen 内部检查。

    架构 §3.4：PyInstaller 内部 API 只能有一个入口点 desktop/app/utils/paths.py::is_frozen()。

    禁令（字符串常量 + AST 双查）：
    - 不许有值为 "frozen" 或 "_MEIPASS" 的字符串常量；
    - 不许有对 sys 的属性访问名为 frozen 或 _MEIPASS；
    - 不许有 __import__ 调用。

    变异：在 engine.py 里加 `is_frozen = bool(getattr(sys, "frozen", False))` ⇒ 本条红。
    """
    desktop_root = ROOT / "desktop"
    exempt = (desktop_root / "app" / "utils" / "paths.py").resolve()

    violations: list[str] = []
    for py_file in desktop_root.rglob("*.py"):
        if py_file.resolve() == exempt:
            continue
        src_text = py_file.read_text(encoding="utf-8")
        tree = ast.parse(src_text, filename=str(py_file))
        rel = py_file.relative_to(ROOT)

        for node in ast.walk(tree):
            # 禁 __import__ 调用
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "__import__":
                    violations.append(f"  {rel}: line {node.lineno}: __import__ 调用")

            # 禁 sys.frozen / sys._MEIPASS 属性访问
            if isinstance(node, ast.Attribute):
                if (isinstance(node.value, ast.Name)
                        and node.value.id == "sys"
                        and node.attr in ("frozen", "_MEIPASS")):
                    violations.append(
                        f"  {rel}: line {node.lineno}: sys.{node.attr} 属性访问"
                    )

            # 禁字符串常量 "frozen" 或 "_MEIPASS"
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in ("frozen", "_MEIPASS"):
                    violations.append(
                        f"  {rel}: line {node.lineno}: 字符串常量 {node.value!r}"
                    )

    assert not violations, (
        "desktop/ 文件出现了禁止的 PyInstaller 内部 API（只许在 utils/paths.py 里）：\n"
        + "\n".join(violations)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 守卫 12（H2）：conclusion_lines 对所有 27 种三态组合永不返回空列表
# ═══════════════════════════════════════════════════════════════════════════════

def test_conclusion_lines_never_empty() -> None:
    """contrast_passed × area_jitter_passed × area_jitter_moving 各三态，共 27 种组合。

    每种组合 conclusion_lines() 必须：
    - 返回至少一条文本（不许静默返回空列表）；
    - 「全部指标通过」只许在三门都明确通过时出现；
    - 每个 False 的门至少有一条对应的报警；
    - 每个 None 的门（无法测量）至少有一条含「量不了」或「无法测量」的话。

    变异：
    - M5  删掉「对比度不达标」整条 lines.append ⇒ cp=False 时无报警 ⇒ 本条红；
    - M14 删掉「面积抖动超标」整条 lines.append ⇒ ap=False+mv!=True 时无报警 ⇒ 本条红；
    - M6  删掉兜底「全部指标通过」⇒ 全通过组合输出空或其他句子 ⇒ 本条红；
    - 修改兜底条件（去掉 area_jitter_passed is True）⇒ 部分 None 组合错误出现「全部通过」⇒ 本条红。
    """
    from desktop.app.models.self_test import AcqCheckResult, conclusion_lines

    def _make_result(cp, ap, mv) -> AcqCheckResult:
        cv = None if cp is None else (80.0 if cp is False else 210.0)
        cr = None if cp is None else (1.5 if cp is False else 6.8)
        av = None if ap is None else (0.03 if ap is False else 0.002)
        data = {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "video": {
                "path": "/fake.mp4", "fps": 25.0,
                "n_frames": 100, "width": 400, "height": 268,
            },
            "sampling": {
                "n_windows": 5, "frames_per_window": 8,
                "frame_indices": list(range(40)),
            },
            "gates": {
                "contrast": {
                    "value": cv, "ratio": cr,
                    "threshold_abs": 100.0, "threshold_ratio": 2.0,
                    "passed": cp,
                },
                "noise_floor": {"value": 0.5, "n_frames": 40},
                "area_jitter": {
                    "value": av, "threshold": 0.02,
                    "passed": ap,
                    "chamber": None if ap is None else 1,
                    "chamber_residual": None if ap is None else 0.01,
                    "moving": mv,
                },
            },
            "chambers": (
                [] if ap is None
                else [{"index": 1, "area_jitter_p90": av, "rad_residual": 0.01}]
            ),
            "reference": None,
        }
        return AcqCheckResult(data)

    errors: list[str] = []
    for cp in (True, False, None):
        for ap in (True, False, None):
            for mv in (True, False, None):
                result = _make_result(cp, ap, mv)
                lines = conclusion_lines(result)
                joined = "\n".join(lines)
                combo = f"cp={cp!r}, ap={ap!r}, mv={mv!r}"

                # 必须非空
                if not lines:
                    errors.append(f"  [{combo}] ⇒ 空列表")
                    continue

                # 「全部指标通过」只许在三门都明确通过时出现
                all_pass_phrase = "全部指标通过"
                expected_all_pass = (cp is True and ap is True and mv is not True)
                if all_pass_phrase in joined and not expected_all_pass:
                    errors.append(
                        f"  [{combo}] 出现「全部指标通过」但有门未明确通过"
                    )
                if expected_all_pass and all_pass_phrase not in joined:
                    errors.append(
                        f"  [{combo}] 三门全明确通过但没有「全部指标通过」"
                    )

                # 每个 False 的门必须有对应报警
                # 注：cp=None 时函数早返回（无面板时其余门都测不到），面积抖动报警不会出现
                if cp is False and "对比度" not in joined:
                    errors.append(f"  [{combo}] cp=False 但没有对比度相关的报警")
                if ap is False and mv is not True and cp is not None and "面积抖动" not in joined:
                    errors.append(f"  [{combo}] ap=False+mv!=True 但没有面积抖动相关的报警")

                # 每个 None 的门必须有「量不了」或「无法测量」
                if cp is None and not any(w in joined for w in ("量不了", "无法测量")):
                    errors.append(f"  [{combo}] cp=None 但没有「量不了/无法测量」")
                if ap is None and not any(w in joined for w in ("量不了", "无法测量")):
                    errors.append(f"  [{combo}] ap=None 但没有「量不了/无法测量」")

    assert not errors, (
        "conclusion_lines() 口径错误（不只是「非空」，要求口径正确）：\n"
        + "\n".join(errors)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 守卫 13（H5）：desktop 模型键集与引擎 ACQ_*_KEYS 严格一致
# ═══════════════════════════════════════════════════════════════════════════════

def test_desktop_model_keys_mirror_engine_keys() -> None:
    """AcqCheckResult 的 _MODEL_*_KEYS 与 acq_check.ACQ_*_KEYS 严格双向相等。

    一面只照一个方向的镜子不是镜子：引擎加键+模型没跟（M4 改名、M12 少要）都要红。

    变异：
    - M4  ACQ_JSON_KEYS 加 "extra" ⇒ 引擎侧多键 ⇒ 本条红；
    - M12 模型 _MODEL_*_KEYS 少一个键 ⇒ 模型侧少键 ⇒ 本条红。

    **这条 docstring 上一版写的是「模型 _require_keys 少一个键 ⇒ 本条红」，那是假的**：
    第三轮复核实测，六个 `_require_keys` 调用点当时各写一份行内字面量元组，
    改行内元组（产品真行为变了）本条全绿，改常量（产品行为没变）本条才红 ——
    镜子照的是一份产品不看的名册。现在常量是 `_require_keys` 的唯一来源，
    由下面那条 AST 守卫钉着，这两句承诺才对得上。
    """
    from depressionplex.cli import acq_check
    from desktop.app.models import self_test as M

    layer_pairs = [
        ("顶层", acq_check.ACQ_JSON_KEYS, M._MODEL_JSON_KEYS),
        ("gates", acq_check.ACQ_GATES_KEYS, M._MODEL_GATES_KEYS),
        ("gates.contrast", acq_check.ACQ_CONTRAST_KEYS, M._MODEL_CONTRAST_KEYS),
        ("gates.noise_floor", acq_check.ACQ_NOISE_FLOOR_KEYS, M._MODEL_NOISE_FLOOR_KEYS),
        ("gates.area_jitter", acq_check.ACQ_AREA_JITTER_KEYS, M._MODEL_AREA_JITTER_KEYS),
        ("sampling", acq_check.ACQ_SAMPLING_KEYS, M._MODEL_SAMPLING_KEYS),
    ]

    errors: list[str] = []
    for layer_name, engine_keys, model_keys in layer_pairs:
        e_set = set(engine_keys)
        m_set = set(model_keys)
        if e_set != m_set:
            errors.append(
                f"  层 {layer_name!r}：引擎有 {sorted(e_set - m_set)} 而模型没有；"
                f"模型有 {sorted(m_set - e_set)} 而引擎没有"
            )

    assert not errors, (
        "acq_check.ACQ_*_KEYS 与 desktop model _MODEL_*_KEYS 不同步（双向对账失败）：\n"
        + "\n".join(errors)
    )


def test_require_keys_only_takes_model_key_constants() -> None:
    """`_require_keys()` 的键集实参只许是 `_MODEL_*_KEYS` 常量，不许写行内元组。

    没有这一条，守卫 13 会**再一次**变成装饰：只要有人图省事把某一层的键集写回
    行内元组，那一层的双向对账就当场失明，而守卫 13 照样绿（它比的是常量）。
    第三轮复核实测过这个洞：改行内元组 ⇒ 18 条全绿；改常量 ⇒ 红 1。
    **守卫要盯产品真正走的那条路，不是盯一份平行的声明。**
    """
    model_py = ROOT / "desktop" / "app" / "models" / "self_test.py"
    tree = ast.parse(model_py.read_text(encoding="utf-8"), filename=str(model_py))

    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "_require_keys"]
    assert calls, "模型里一个 _require_keys 调用都没有——键集校验丢了"

    offenders: list[str] = []
    for call in calls:
        if len(call.args) < 2:
            offenders.append(f"  line {call.lineno}: 参数不足，看不出键集从哪来")
            continue
        keys_arg = call.args[1]
        if not (isinstance(keys_arg, ast.Name) and keys_arg.id.startswith("_MODEL_")):
            shape = type(keys_arg).__name__
            offenders.append(
                f"  line {call.lineno}: 键集实参是 {shape}，不是 _MODEL_*_KEYS 常量"
            )

    assert not offenders, (
        "_require_keys 的键集必须引 _MODEL_*_KEYS 常量，否则守卫 13 的双向对账照的是"
        "一份产品不看的名册：\n" + "\n".join(offenders)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 守卫 14（A3）：ISSUES.md 编号不重复
# ═══════════════════════════════════════════════════════════════════════════════

def test_issues_md_no_duplicate_numbers() -> None:
    """docs/ISSUES.md 里表格行首 DP-\\d+ 编号不许重复。

    只检查表格行首（`| DP-NNN |` 开头的行），不检查行内容里的交叉引用——
    那样会把"DP-032 提到了 DP-005"算成 DP-005 重复出现，是误报。

    防的是：新行用了已存在的编号，导致 open 阻塞项被 done 行盖住。
    变异：ISSUES.md 里加一行重复编号 ⇒ 本条红。
    """
    import re
    issues_path = ROOT / "docs" / "ISSUES.md"
    assert issues_path.exists(), f"ISSUES.md 不存在：{issues_path}"
    text = issues_path.read_text(encoding="utf-8")
    # 只匹配表格行首：| DP-NNN | 开头的行
    nums = re.findall(r"^\| DP-(\d+) \|", text, re.MULTILINE)
    seen: dict[str, int] = {}
    dups: list[str] = []
    for n in nums:
        seen[n] = seen.get(n, 0) + 1
    for n, cnt in seen.items():
        if cnt > 1:
            dups.append(f"  DP-{n} 出现 {cnt} 次")
    assert not dups, (
        "docs/ISSUES.md 里有重复行首编号（open 阻塞项可能被 done 行盖住）：\n"
        + "\n".join(dups)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 守卫 15（A9）：四个门槛数值与 ISSUES.md 台账逐字对账
# ═══════════════════════════════════════════════════════════════════════════════

def test_gate_thresholds_match_issues_md() -> None:
    """GATE_CONTRAST_ABS / GATE_CONTRAST_RATIO / GATE_AREA_JITTER_P90 / MOVING_RESIDUAL
    四个常量的「常量名: 值」字面对账字符串必须在 docs/ISSUES.md 里逐字出现（DP-117 行）。

    防的是：把门槛放松十倍（如 100→10、0.02→0.2）422 条测试全绿——
    守卫 1-3 证明「常量被读了」，这条证明「常量的值是对的」。
    两者缺一，不能形成闭环。

    为什么用「名: 值」而不是 str(值)：
    "0.2" 在 ISSUES.md 里有 11 处，纯子串匹配对 0.02→0.2 的变异不灵敏。
    "GATE_AREA_JITTER_P90: 0.02" 只出现在 DP-117 冻结行，改值就找不到。

    变异（M9/M10/M15b）：
    - GATE_CONTRAST_ABS 100.0 → 10.0 ⇒ "GATE_CONTRAST_ABS: 10.0" 不在 ISSUES.md ⇒ 本条红；
    - GATE_AREA_JITTER_P90 0.02 → 0.2 ⇒ "GATE_AREA_JITTER_P90: 0.2" 不在 ISSUES.md ⇒ 本条红；
    - MOVING_RESIDUAL 0.02 → 0.2 ⇒ "MOVING_RESIDUAL: 0.2" 不在 ISSUES.md ⇒ 本条红。
    """
    from depressionplex.assay_core.segment import (
        GATE_CONTRAST_ABS, GATE_CONTRAST_RATIO, GATE_AREA_JITTER_P90,
    )
    from depressionplex.cli.probe_frames import MOVING_RESIDUAL

    issues_path = ROOT / "docs" / "ISSUES.md"
    assert issues_path.exists(), f"ISSUES.md 不存在：{issues_path}"
    issues_text = issues_path.read_text(encoding="utf-8")

    gate_constants = {
        "GATE_CONTRAST_ABS": GATE_CONTRAST_ABS,
        "GATE_CONTRAST_RATIO": GATE_CONTRAST_RATIO,
        "GATE_AREA_JITTER_P90": GATE_AREA_JITTER_P90,
        "MOVING_RESIDUAL": MOVING_RESIDUAL,
    }
    missing: list[str] = []
    for name, val in gate_constants.items():
        # 检查「常量名: 值」字面对账字符串，避免 str(val) 子串命中不相关行
        anchor = f"{name}: {val}"
        if anchor not in issues_text:
            missing.append(
                f"  {anchor!r}  在 docs/ISSUES.md（DP-117 行）里找不到"
            )
    assert not missing, (
        "门槛常量数值未在 ISSUES.md 台账文字中出现——"
        "改门槛是科学决定，必须同步更新 DP-117 行：\n"
        + "\n".join(missing)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 守卫 20（B7 收口）：placeholders.py 里不许留已经被真页面顶掉的占位类
# ═══════════════════════════════════════════════════════════════════════════════

def test_no_orphan_placeholder_pages() -> None:
    """`pages/placeholders.py` 里定义的每个页面类，都必须出现在 `PAGE_CLASSES` 里。

    防的是：一页从占位换成真实现（B7 的 `SelfCheckPage` → `AcqCheckPage`）之后，
    空壳留在仓里没人 import。它不会让任何测试变红，但下一个人照侧栏名字找「自检页」
    会先摸到那个只写着「本页由 B7 交付」的空壳，然后照它改——改完发现界面上没反应。
    七页名册只许有一份（`PAGE_ORDER` / `PAGE_CLASSES`），占位类是名册外的第二个答案。

    变异：把 `SelfCheckPage` 那个类加回 placeholders.py ⇒ 本条红。
    """
    ph = ROOT / "desktop" / "app" / "pages" / "placeholders.py"
    mw = ROOT / "desktop" / "app" / "main_window.py"
    ph_tree = ast.parse(ph.read_text(encoding="utf-8"), filename=str(ph))
    defined = [n.name for n in ph_tree.body if isinstance(n, ast.ClassDef)]
    assert defined, "placeholders.py 里一个类都没有——文件形状变了，这条守卫要重写"

    mw_src = mw.read_text(encoding="utf-8")
    mw_tree = ast.parse(mw_src, filename=str(mw))
    registered: set[str] = set()
    for node in ast.walk(mw_tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "PAGE_CLASSES" for t in node.targets
        ):
            assert isinstance(node.value, ast.Dict), "PAGE_CLASSES 不是字面 dict 了"
            registered = {
                v.id for v in node.value.values if isinstance(v, ast.Name)
            }
    assert registered, "main_window.py 里找不到 PAGE_CLASSES 的字面 dict"

    orphans = sorted(set(defined) - registered)
    assert not orphans, (
        "placeholders.py 里有没人用的占位页类（真页面上线后忘了删）：\n"
        + "\n".join(f"  {name}" for name in orphans)
        + "\n七页名册只许有一份，占位类是名册外的第二个答案。"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 守卫 12–18（DP-120）：自检页接真子进程
# ═══════════════════════════════════════════════════════════════════════════════

def test_acq_check_argv_lives_in_engine_py() -> None:
    """argv 必须在 services/engine.py 里拼，页面里不许出现子命令字面量。

    为什么这条要在：`test_packaging_contract.py::test_cli_subcommand_registry` 第 3 段
    只扫 engine.py 里 `argv.append("<子命令>")` 这一种形状。同一条 argv 写在页面里
    照样跑得动，但子命令名拼错、或名册里那一行被删掉时，**不会有任何东西红**。

    变异：把 engine.py 的 `argv.append("acq-check")` 改成 `argv.extend([...])`
    或把整条 argv 搬进页面 ⇒ 本条红。
    """
    eng = ROOT / "desktop" / "app" / "services" / "engine.py"
    tree = ast.parse(eng.read_text(encoding="utf-8"), filename=str(eng))
    appended: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "append"
                and isinstance(node.value.func.value, ast.Name)
                and node.value.func.value.id == "argv"
                and node.value.args
                and isinstance(node.value.args[0], ast.Constant)):
            val = node.value.args[0].value
            if isinstance(val, str):
                appended.add(val)
    assert "acq-check" in appended, (
        "engine.py 里找不到 `argv.append(\"acq-check\")` 这一种形状。"
        f"当前扫到的是 {sorted(appended)}。"
        "子命令名册的对账守卫只认这个形状，换成 extend 就等于把它绕开了"
    )

    page = ROOT / "desktop" / "app" / "pages" / "self_test.py"
    page_src = page.read_text(encoding="utf-8")
    assert "acq-check" not in page_src, (
        "页面里出现了子命令字面量 'acq-check' —— argv 只许在 engine.py 拼"
    )
    assert "acq_check_argv" in page_src, "页面没在用 engine.acq_check_argv"
    assert "result_from_process" in page_src, "页面没在用 result_from_process 判退出码"
    assert "QProcess(" in page_src, (
        "页面没有真的起子进程（DP-120 之前这里是 `# TODO B10+` 占位）"
    )
    assert "exit_code ==" not in page_src and "exit_code in (" not in page_src, (
        "页面自己在比退出码 —— 退出码怎么解读是判断，判断在模型层"
    )


def test_acq_check_argv_shape() -> None:
    """argv 形状：引擎前缀 + acq-check + --video 绝对路径 + --chambers。

    变异：把 `Path(text).resolve()` 改回 `text` ⇒ 相对路径那条断言红
    （源码模式下子进程 cwd 是仓根，相对路径会被解到别处去，然后报「解码失败」）。
    """
    from desktop.app.services import engine as eng

    prefix = eng.engine_command()
    argv = eng.acq_check_argv("relative_name.mp4", 6)
    assert argv[:len(prefix)] == prefix, f"引擎前缀不对：{argv[:len(prefix)]}"
    assert argv.count("acq-check") == 1, f"子命令应出现一次：{argv}"
    assert argv[len(prefix)] == "acq-check", "子命令必须紧跟在引擎前缀后面"

    i = argv.index("--video")
    assert argv[i + 1] == str(Path("relative_name.mp4").resolve()), (
        f"--video 必须是绝对路径，得到 {argv[i + 1]!r}"
    )
    j = argv.index("--chambers")
    assert argv[j + 1] == "6", f"--chambers 应为 '6'，得到 {argv[j + 1]!r}"

    for bad in ("", "   "):
        try:
            eng.acq_check_argv(bad, 4)
        except ValueError:
            pass
        else:
            raise AssertionError(f"空路径 {bad!r} 应当抛 ValueError")

    for bad_n in (0, -1, True, "4", 2.0):
        try:
            eng.acq_check_argv("/x/y.mp4", bad_n)  # type: ignore[arg-type]
        except ValueError:
            pass
        else:
            raise AssertionError(f"隔间数 {bad_n!r} 应当抛 ValueError")


def test_exit_codes_mirror_engine() -> None:
    """外壳那份退出码常量必须与引擎真跑出来的 rc 一致（镜像 + 对账）。

    外壳不许 import 引擎（架构 §3.4），所以常量必然是第二份；
    第二份唯一能不撒谎的办法就是拿真 rc 来对。
    变异：把 EXIT_NOT_MEASURABLE 改成 3 ⇒ 本条红。
    """
    from desktop.app.models import self_test as M

    kinds = (STILL, STILL, STILL, STILL)
    rc_ok, _out, _err = _run_main_streams(_make_frames(kinds, 80))
    assert rc_ok == M.EXIT_MEASURED, f"正常一跑 rc={rc_ok}，镜像里写的是 {M.EXIT_MEASURED}"

    rc_nm, _out, _err = _run_main_streams(_make_frames(kinds, 10), n_frames=10)
    assert rc_nm == M.EXIT_NOT_MEASURABLE, (
        f"帧数不足 rc={rc_nm}，镜像里写的是 {M.EXIT_NOT_MEASURABLE}"
    )

    rc_dec, _out, _err = _run_main_streams(
        _make_frames(kinds, 80), probe_exc=RuntimeError("摄像头没插上")
    )
    assert rc_dec == M.EXIT_DECODE_FAILED, (
        f"解码失败 rc={rc_dec}，镜像里写的是 {M.EXIT_DECODE_FAILED}"
    )


def test_rc2_is_a_result_not_a_failure() -> None:
    """退出码 2（测不出）必须变成一份结果，不是一次失败。

    rc=2 带着完整 JSON：读数是 null、判定是「无法测量」。把非零码一律当
    「自检程序崩了」，等于把「你这段素材测不出来」这条结论吃掉。
    变异：让 result_from_process 只认 rc=0 ⇒ 本条红。
    """
    from desktop.app.models.self_test import result_from_process

    kinds = (STILL, STILL, STILL, STILL)
    rc, out, err = _run_main_streams(_make_frames(kinds, 10), n_frames=10)
    assert rc == 2, f"这一跑本该 rc=2，得到 {rc}"

    result = result_from_process(rc, out, err)          # 不许抛
    assert result.contrast_value is None, "测不出的读数必须是 None，不是 0"
    assert result.area_jitter_passed is None, "测不出时判定必须是 None（无法测量）"
    assert result.chambers == [], "测不出时不该有逐隔间读数"


def test_rc0_gate_fail_is_a_result_not_a_failure() -> None:
    """门不通过（rc=0）同样是一份结果，且 contrast_passed 必须是 False。

    变异：把 result_from_process 改成「JSON 里有 passed=False 就抛」⇒ 本条红。
    """
    from depressionplex.assay_core import segment as S
    from desktop.app.models.self_test import result_from_process

    _LOW = {
        "threshold": 150.0, "background_mean": 200.0, "animal_mean": 160.0,
        "abs_diff": 40.0, "ratio": 1.25, "dark_from_mask": 1.0, "passes_gate": 0.0,
    }
    orig_contrast = S.contrast_report
    orig_chambers = S.find_chambers
    S.contrast_report = lambda g: _LOW.copy()
    S.find_chambers = lambda g, **kw: [(0, 94)]
    try:
        rc, out, err = _run_main_streams(_make_frames((STILL,) * 4, 80))
    finally:
        S.contrast_report = orig_contrast
        S.find_chambers = orig_chambers

    assert rc == 0, f"门不通过时 rc 应为 0，得到 {rc}"
    result = result_from_process(rc, out, err)          # 不许抛
    assert result.contrast_passed is False, "门不通过要显示为不通过"


def test_rc1_carries_engine_own_words() -> None:
    """解码失败（rc=1）要抛出人话，且**带着引擎自己那句原文**。

    只说一句「自检失败」的报错，等于让人拿着一句废话去查一个解码问题。
    变异：把报错改成不含 engine_error_text ⇒ 本条红。
    """
    from desktop.app.models.self_test import AcqCheckError, result_from_process

    rc, out, err = _run_main_streams(
        _make_frames((STILL,) * 4, 80), probe_exc=RuntimeError("摄像头没插上")
    )
    assert rc == 1, f"probe 抛异常时 rc 应为 1，得到 {rc}"
    assert "摄像头没插上" in err, f"引擎没把原因写进 stderr：{err[:200]!r}"

    try:
        result_from_process(rc, out, err)
    except AcqCheckError as exc:
        msg = str(exc)
    else:
        raise AssertionError("rc=1 必须抛 AcqCheckError")

    assert "视频解码失败" in msg, f"报错没说清是解码失败：{msg}"
    assert "摄像头没插上" in msg, f"报错没带上引擎原话：{msg}"


def test_from_json_str_raises_acq_check_error_only() -> None:
    """读不懂的输入一律 AcqCheckError —— 不许把 json.JSONDecodeError 漏给外壳。

    自检页只 except AcqCheckError；漏一个 JSONDecodeError 出去，
    客户看到的就是崩溃而不是一句「引擎这次没给出 JSON」。
    变异：把 from_json_str 里的 try/except 去掉 ⇒ 本条红。
    """
    from desktop.app.models.self_test import AcqCheckError, AcqCheckResult

    for bad in ("", "这不是 JSON", "[1, 2, 3]", "null"):
        try:
            AcqCheckResult.from_json_str(bad)
        except AcqCheckError:
            pass
        except Exception as exc:  # noqa: BLE001 —— 就是要抓住漏出去的那一类
            raise AssertionError(
                f"{bad!r} 抛的是 {type(exc).__name__}，不是 AcqCheckError：{exc}"
            ) from exc
        else:
            raise AssertionError(f"{bad!r} 应当抛 AcqCheckError")


def test_garbage_stdout_and_unknown_rc_are_not_swallowed() -> None:
    """stdout 不是 JSON、或退出码不在约定内：都要抛，且把证据带上。

    变异：任一分支改成 `return AcqCheckResult({})` 或只抛一句「失败」⇒ 本条红。
    """
    from desktop.app.models.self_test import AcqCheckError, result_from_process

    try:
        result_from_process(0, "这不是 JSON，是引擎崩溃时打的一行字", "")
    except AcqCheckError as exc:
        msg = str(exc)
        assert "这不是 JSON" in msg, f"报错没把 stdout 片段带上：{msg}"
    else:
        raise AssertionError("stdout 不是 JSON 时必须抛")

    try:
        result_from_process(7, "", '{"status": "算着呢"}')
    except AcqCheckError as exc:
        msg = str(exc)
        assert "7" in msg, f"报错没说清是哪个退出码：{msg}"
    else:
        raise AssertionError("未约定的退出码必须抛")


def test_status_text_unwraps_engine_progress_lines() -> None:
    """引擎的进度行是 `{"status": ...}`，摆到界面上之前必须解成人话。

    真跑一次拿真 stderr，走 StderrPump，再走 status_text —— 不许拿手写的行当夹具。
    变异：让 status_text 原样返回 ⇒ 本条红。
    """
    from desktop.app.services import progress as prog

    rc, _out, err = _run_main_streams(_make_frames((STILL,) * 4, 80))
    assert rc == 0

    pump = prog.StderrPump()
    events = pump.feed(err.encode("utf-8"))
    log_lines = [e for e in events if isinstance(e, prog.LogLine)]
    assert log_lines, f"引擎一行 stderr 都没出：{err[:200]!r}"
    assert any(e.text.strip().startswith("{") for e in log_lines), (
        "引擎的进度行不再是 JSON 形状了——本条守卫要跟着重写"
    )

    texts = [prog.status_text(e) for e in log_lines]
    assert all(not txt.startswith("{") for txt in texts), (
        f"status_text 没把 JSON 解开，界面上会出现一行 JSON：{texts}"
    )
    assert "计算采集指标" in texts, f"引擎的进度原话没带出来：{texts}"
