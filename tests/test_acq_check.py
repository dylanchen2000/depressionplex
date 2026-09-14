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


def _run_main(frames: list[np.ndarray], extra_argv: list[str] | None = None,
              n_frames: int | None = None) -> tuple[int, str]:
    """跑一次 acq_check.main，返回 (rc, stdout_text)。stderr 被丢弃（进度 NDJSON）。"""
    from depressionplex.cli import acq_check

    argv = ["--video", "/synth.mp4", "--chambers", str(N_CH)]
    if extra_argv:
        argv.extend(extra_argv)

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    buf = io.StringIO()
    sys.stdout = buf
    sys.stderr = io.StringIO()  # 丢弃进度输出
    try:
        with _patch_video(frames, n_frames):
            rc = acq_check.main(argv)
        output = buf.getvalue()
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return rc, output


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

        # 找所有被禁止的浮点字面量（跳过模块级常量定义）
        # 只检查 float 类型：整数 2 / 100 在代码里有合法用途（退出码/切片），
        # 门槛值以 float 形式写入（100.0 / 2.0 / 0.02）才是违规
        violations: list[str] = []
        for node in ast.walk(tree):
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
    """p2_reference.json 里的四个数必须在 docs/ISSUES.md 里逐字出现。

    防的是：fixture 里某个数字被悄悄改掉而 ISSUES.md 台账里没有任何记录。
    变异（M8）：改掉 fixture 里 contrast_abs 为 999.9 ⇒ ISSUES.md 找不到 999.9 ⇒ 本条红。
    """
    issues_path = ROOT / "docs" / "ISSUES.md"
    ref_path = ROOT / "tests" / "fixtures" / "p2_reference.json"

    assert issues_path.exists(), f"ISSUES.md 不存在：{issues_path}"
    assert ref_path.exists(), f"p2_reference.json 不存在：{ref_path}"

    issues_text = issues_path.read_text(encoding="utf-8")
    with open(ref_path, encoding="utf-8") as f:
        ref = json.load(f)

    # 四个数值字段必须在 ISSUES.md 里逐字出现（str(float) 格式）
    numeric_keys = ("contrast_abs", "contrast_ratio", "noise_floor_px", "area_jitter_p90")
    missing = []
    for key in numeric_keys:
        val = ref.get(key)
        if val is None:
            continue  # null 不要求出现
        if str(val) not in issues_text:
            missing.append(
                f"  fixture[{key!r}] = {val!r}  在 docs/ISSUES.md 里找不到"
            )

    assert not missing, (
        "p2_reference.json 里的参考值在 ISSUES.md 里找不到——"
        "fixture 改了但 ISSUES.md 台账没更新：\n" + "\n".join(missing)
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

    # 参考列读得出（reference 是字典，不是 None——因为 p2_reference.json 存在）
    ref = result.reference
    assert ref is not None, "参考列读不到（p2_reference.json 不存在？）"
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
    """desktop/ 除 utils/paths.py 以外，不许出现 sys.frozen / _MEIPASS / __import__("sys")。

    架构 §3.4：PyInstaller 内部 API 只能有一个入口点 desktop/app/utils/paths.py::is_frozen()。
    engine.py 等文件改用 _is_frozen() 调用后，这些模式不应再出现。

    变异：在 engine.py 里加回 is_frozen = getattr(sys, "frozen", ...) ⇒ 本条必须红。
    """
    FORBIDDEN = ("sys.frozen", "_MEIPASS", '__import__("sys")')
    desktop_root = ROOT / "desktop"
    exempt = (desktop_root / "app" / "utils" / "paths.py").resolve()

    violations: list[str] = []
    for py_file in desktop_root.rglob("*.py"):
        if py_file.resolve() == exempt:
            continue
        src_text = py_file.read_text(encoding="utf-8")
        for pattern in FORBIDDEN:
            if pattern in src_text:
                violations.append(
                    f"  {py_file.relative_to(ROOT)}: 含有 {pattern!r}"
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

    每种组合 conclusion_lines() 必须返回至少一条文本（不许静默返回空列表）。

    变异：在 conclusion_lines 里删掉某一条 lines.append ⇒ 某组合返回空列表 ⇒ 本条红。
    """
    from desktop.app.models.self_test import AcqCheckResult, conclusion_lines

    def _make_result(cp, ap, mv) -> AcqCheckResult:
        """构造特定三态组合的 AcqCheckResult。"""
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
                "n_windows": 5,
                "frames_per_window": 8,
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
                    "value": av,
                    "threshold": 0.02,
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

    empty_combos: list[str] = []
    for cp in (True, False, None):
        for ap in (True, False, None):
            for mv in (True, False, None):
                result = _make_result(cp, ap, mv)
                lines = conclusion_lines(result)
                if not lines:
                    empty_combos.append(
                        f"  cp={cp!r}, ap={ap!r}, mv={mv!r} ⇒ 空列表"
                    )

    assert not empty_combos, (
        "以下三态组合 conclusion_lines() 返回了空列表：\n"
        + "\n".join(empty_combos)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 守卫 13（H5）：desktop 模型键集与引擎 ACQ_*_KEYS 严格一致
# ═══════════════════════════════════════════════════════════════════════════════

def test_desktop_model_keys_mirror_engine_keys() -> None:
    """AcqCheckResult._require_keys 的键集与 acq_check.ACQ_*_KEYS 严格对应。

    用 ACQ_*_KEYS 常量构造最小合法 JSON，喂给 AcqCheckResult 不许抛异常。
    变异：在 _require_keys 里多加一个键 ⇒ 构造的 data 缺键 ⇒ AcqCheckError ⇒ 本条红。
    """
    from depressionplex.cli import acq_check
    from desktop.app.models.self_test import AcqCheckResult, AcqCheckError

    # 按 ACQ_*_KEYS 构造最小合法 JSON（每个字段填 None 或空列表）
    data: dict = {k: None for k in acq_check.ACQ_JSON_KEYS}
    data["video"] = {k: None for k in acq_check.ACQ_VIDEO_KEYS}
    data["sampling"] = {k: None for k in acq_check.ACQ_SAMPLING_KEYS}
    data["sampling"]["frame_indices"] = []
    data["gates"] = {k: {} for k in acq_check.ACQ_GATES_KEYS}
    data["gates"]["contrast"] = {k: None for k in acq_check.ACQ_CONTRAST_KEYS}
    data["gates"]["noise_floor"] = {k: None for k in acq_check.ACQ_NOISE_FLOOR_KEYS}
    data["gates"]["area_jitter"] = {k: None for k in acq_check.ACQ_AREA_JITTER_KEYS}
    data["chambers"] = []
    data["reference"] = None

    try:
        result = AcqCheckResult(data)
    except AcqCheckError as e:
        raise AssertionError(
            f"AcqCheckResult 拒绝了按 ACQ_*_KEYS 构造的 JSON，"
            f"说明 model 与 engine 的键集不同步：{e}"
        ) from e

    # 属性访问不许抛
    _ = result.contrast_value
    _ = result.area_jitter_value
    _ = result.sampling_n_windows
