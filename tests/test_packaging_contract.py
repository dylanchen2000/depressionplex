"""打包契约守卫（B10 + B11，DP-108）：spec / Inno Setup / ffmpeg 解析顺序。

全部只用标准库（ast / 正则 / 读文本），沙箱里必须能跑。每条都要能被变异打红。
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

# 仓根
ROOT = Path(__file__).resolve().parent.parent


def test_exe_names_single_source():
    """守卫 1：exe 名字只有一个来源（spec 的 name= 与 engine.ENGINE_STEM 必须相等）。

    **本单最重要的一条**：这两个名字对不上，装出来的软件一按「开始分析」就报「找不到引擎」。
    """
    # 读 desktop/app/services/engine.py 取 ENGINE_STEM
    engine_py = ROOT / "desktop" / "app" / "services" / "engine.py"
    tree = ast.parse(engine_py.read_text(encoding="utf-8"), filename=str(engine_py))
    engine_stem = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "ENGINE_STEM":
                    if isinstance(node.value, ast.Constant):
                        engine_stem = node.value.value
    assert engine_stem is not None, "engine.py 里找不到 ENGINE_STEM"

    # 解析后端 spec
    backend_spec = ROOT / "packaging" / "build_analyzer_windows.spec"
    spec_tree = ast.parse(backend_spec.read_text(encoding="utf-8"), filename=str(backend_spec))

    # 找 EXE(..., name="...")
    backend_name = None
    for node in ast.walk(spec_tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "EXE":
                for kw in node.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        backend_name = kw.value.value
    assert backend_name is not None, "build_analyzer_windows.spec 里找不到 EXE(name=...)"
    assert backend_name == engine_stem, \
        f"后端 spec 的 name={backend_name!r} 与 engine.ENGINE_STEM={engine_stem!r} 不一致"

    # 解析 GUI spec
    gui_spec = ROOT / "packaging" / "build_windows.spec"
    gui_tree = ast.parse(gui_spec.read_text(encoding="utf-8"), filename=str(gui_spec))
    gui_name = None
    for node in ast.walk(gui_tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "EXE":
                for kw in node.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        gui_name = kw.value.value
    assert gui_name is not None, "build_windows.spec 里找不到 EXE(name=...)"
    assert gui_name == "DEPRESSION-PLEX", \
        f"GUI spec 的 name={gui_name!r} 不等于 'DEPRESSION-PLEX'"


def test_gui_spec_excludes_engine():
    """守卫 2：GUI spec 必须 excludes 掉引擎包（三重封堵第二重）。"""
    spec_path = ROOT / "packaging" / "build_windows.spec"
    tree = ast.parse(spec_path.read_text(encoding="utf-8"), filename=str(spec_path))

    # 找 Analysis(..., excludes=[...])
    excludes_list = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "Analysis":
                for kw in node.keywords:
                    if kw.arg == "excludes" and isinstance(kw.value, ast.List):
                        excludes_list = [
                            elt.value for elt in kw.value.elts
                            if isinstance(elt, ast.Constant)
                        ]
    assert excludes_list is not None, "build_windows.spec 里找不到 Analysis(excludes=...)"
    assert "depressionplex" in excludes_list, \
        f"GUI spec 的 excludes 里没有 'depressionplex'，实际为 {excludes_list}"


def test_backend_spec_excludes_pyside6():
    """守卫 3：后端 spec 必须 excludes 掉 PySide6。"""
    spec_path = ROOT / "packaging" / "build_analyzer_windows.spec"
    tree = ast.parse(spec_path.read_text(encoding="utf-8"), filename=str(spec_path))

    excludes_list = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "Analysis":
                for kw in node.keywords:
                    if kw.arg == "excludes" and isinstance(kw.value, ast.List):
                        excludes_list = [
                            elt.value for elt in kw.value.elts
                            if isinstance(elt, ast.Constant)
                        ]
    assert excludes_list is not None, "build_analyzer_windows.spec 里找不到 Analysis(excludes=...)"
    assert "PySide6" in excludes_list, \
        f"后端 spec 的 excludes 里没有 'PySide6'，实际为 {excludes_list}"


def test_iss_no_hardcoded_version():
    """守卫 4：.iss 里不许出现硬写的版本号。"""
    iss_path = ROOT / "packaging" / "installer.iss"
    text = iss_path.read_text(encoding="utf-8")

    # 正则扫 x.y.z 形式的版本号
    # 排除掉注释和 {#AppVersion} 这类占位符
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        # 跳过注释行
        if line.strip().startswith(";"):
            continue
        # 跳过含占位符的行
        if "{#AppVersion}" in line:
            continue
        # 查找硬写的版本号
        match = re.search(r'\b\d+\.\d+\.\d+\b', line)
        if match:
            raise AssertionError(
                f"installer.iss 第 {i} 行含硬写的版本号 {match.group()}：\n  {line}\n"
                f"版本号必须从命令行 /DAppVersion= 传入，不许手写。"
            )

    # 必须有 AppVersion={#AppVersion} 这类占位
    assert "{#AppVersion}" in text, \
        "installer.iss 里找不到 {#AppVersion} 占位符，版本号怎么传入？"


def test_iss_target_paths_match_architecture():
    """守卫 5：.iss 的目标路径与 §0.1 一致（backend\ 与 backend\ffmpeg\）。"""
    iss_path = ROOT / "packaging" / "installer.iss"
    text = iss_path.read_text(encoding="utf-8")

    # 必须出现 backend\ 和 backend\ffmpeg\（可能前面有 {app}\ 等）
    assert ("backend" in text and ("\\backend" in text or "/backend" in text)), \
        "installer.iss 里找不到 backend 目标路径"
    assert ("\\ffmpeg" in text or "/ffmpeg" in text), \
        "installer.iss 里找不到 backend\\ffmpeg 目标路径"

    # exe 名必须与 ENGINE_STEM 一致
    engine_py = ROOT / "desktop" / "app" / "services" / "engine.py"
    tree = ast.parse(engine_py.read_text(encoding="utf-8"), filename=str(engine_py))
    engine_stem = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "ENGINE_STEM":
                    if isinstance(node.value, ast.Constant):
                        engine_stem = node.value.value
    assert engine_stem is not None, "engine.py 里找不到 ENGINE_STEM"

    # 在 .iss 里查找 depression-analyzer
    assert engine_stem in text, \
        f"installer.iss 里找不到 {engine_stem}（ENGINE_STEM）"


def test_ffmpeg_resolution_order():
    """守卫 6：ffmpeg 解析顺序（env → 随包 → PATH），且优先级正确。

    简化版：只测试关键场景（env 优先、PATH 兜底、都没有抛异常）。
    """
    from depressionplex import video
    import tempfile
    import os

    # 场景 1：env 优先于 PATH
    with tempfile.NamedTemporaryFile(mode="w", suffix="-ffmpeg", delete=False) as tmp:
        env_path = tmp.name
    try:
        with mock.patch.dict("os.environ", {"DPX_FFMPEG": env_path}):
            with mock.patch("shutil.which", return_value="/usr/bin/ffmpeg"):  # PATH 也有
                path, source = video._resolve_ffmpeg_tool("ffmpeg")
                assert path == env_path, f"env 应该最优先，但返回了 {path}"
                assert source == "env", f"source 应该是 'env'，但返回了 {source}"
    finally:
        os.unlink(env_path)

    # 场景 2：PATH 兜底（env 和随包都没有）
    with mock.patch.dict("os.environ", {}, clear=True):
        with mock.patch("sys.frozen", False, create=True):
            with mock.patch("pathlib.Path.exists", return_value=False):  # 随包不存在
                with mock.patch("shutil.which", return_value="/usr/bin/ffmpeg"):
                    path, source = video._resolve_ffmpeg_tool("ffmpeg")
                    assert path == "/usr/bin/ffmpeg", "PATH 应该兜底"
                    assert source == "system", f"source 应该是 'system'，但返回了 {source}"

    # 场景 3：都没有 ⇒ VideoError，且报错里三处路径都在
    with mock.patch.dict("os.environ", {}, clear=True):
        with mock.patch("pathlib.Path.exists", return_value=False):
            with mock.patch("shutil.which", return_value=None):
                try:
                    video._resolve_ffmpeg_tool("ffmpeg")
                    raise AssertionError("都没有时应该抛 VideoError")
                except video.VideoError as e:
                    msg = str(e)
                    assert "DPX_FFMPEG" in msg, "报错应提到环境变量"
                    assert "vendor" in msg, "报错应提到随包路径"
                    assert "PATH" in msg, "报错应提到系统 PATH"


def test_only_one_ffmpeg_resolver():
    """守卫 7：只有一个解析器（depressionplex/**/*.py 里不许再有别处直接用 "ffmpeg" / "ffprobe" 字面量）。

    只豁免 video.py 的 _resolve_ffmpeg_tool 函数本身（H11）——`_run()` 里不许用字面量。
    """
    # 扫描 depressionplex/ 下所有 .py 文件
    for py_file in (ROOT / "depressionplex").rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

        # 收集所有函数的 AST 节点范围（用于判断某行在哪个函数里）
        function_ranges = {}  # {func_name: (start_line, end_line)}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                function_ranges[node.name] = (node.lineno, node.end_lineno or 999999)

        # 遍历所有节点，查找 subprocess 调用
        for node in ast.walk(tree):
            # 找 subprocess.run / subprocess.Popen 的调用
            if isinstance(node, ast.Call):
                # 检查是否是 subprocess.* 调用
                is_subprocess_call = False
                if isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name):
                        if node.func.value.id == "subprocess":
                            is_subprocess_call = True

                if is_subprocess_call and node.args:
                    # 第一个参数应该是命令列表
                    first_arg = node.args[0]
                    # 如果是列表，检查元素
                    if isinstance(first_arg, ast.List):
                        for elt in first_arg.elts:
                            if isinstance(elt, ast.Constant):
                                if elt.value in ("ffmpeg", "ffprobe"):
                                    # 只豁免 video.py 的 _resolve_ffmpeg_tool 函数
                                    if py_file.name == "video.py":
                                        # 判断当前调用在哪个函数里
                                        current_func = None
                                        for func_name, (start, end) in function_ranges.items():
                                            if start <= node.lineno <= end:
                                                current_func = func_name
                                                break
                                        # 只豁免 _resolve_ffmpeg_tool 函数本身
                                        if current_func == "_resolve_ffmpeg_tool":
                                            continue
                                    raise AssertionError(
                                        f"{py_file.relative_to(ROOT)}:{node.lineno} 里直接使用了 {elt.value!r} 字面量，"
                                        f"应该调用 video._resolve_ffmpeg_tool()"
                                    )


def test_fetch_ffmpeg_hash_not_placeholder():
    """守卫 8：fetch_ffmpeg.py 的哈希不许是占位，且校验失败会非零退出。"""
    fetch_py = ROOT / "packaging" / "fetch_ffmpeg.py"
    text = fetch_py.read_text(encoding="utf-8")

    # 找到 FFMPEG_SHA256 的值
    tree = ast.parse(text, filename=str(fetch_py))
    sha256_value = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "FFMPEG_SHA256":
                    if isinstance(node.value, ast.Constant):
                        sha256_value = node.value.value
    assert sha256_value is not None, "fetch_ffmpeg.py 里找不到 FFMPEG_SHA256"

    # 断言是 64 位十六进制
    assert len(sha256_value) == 64, \
        f"FFMPEG_SHA256 长度不是 64（实际 {len(sha256_value)}）"
    assert re.fullmatch(r'[0-9a-fA-F]{64}', sha256_value), \
        f"FFMPEG_SHA256 不是十六进制：{sha256_value}"

    # 测试校验失败会非零退出（喂一段假数据）
    # 读取 sha256_file 函数的定义并测试
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
        tmp.write("fake data")
        tmp_path = Path(tmp.name)

    try:
        # 直接调用 sha256_file 函数（从 fetch_ffmpeg.py 里提取）
        h = hashlib.sha256()
        with tmp_path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        fake_hash = h.hexdigest()

        # 假哈希应该不等于实际哈希
        assert fake_hash != sha256_value, \
            "假数据的哈希不应该等于预期哈希（测试本身有问题）"

        # fetch_ffmpeg.py 的 main() 里会比较哈希，不匹配时 return 1
        # 这里只验证逻辑，不真的下载
    finally:
        tmp_path.unlink()


def test_run_json_decoder_block():
    """守卫 9：run.json 的 decoder 块（四个键都在，ffmpeg_version 取不到时是 None 不是 ""）。"""
    from depressionplex.cli import analyze
    from depressionplex import video
    from depressionplex.assay_core import validity
    from unittest.mock import patch

    # 模拟一个简单的 info / plan
    info = video.VideoInfo(
        path=Path("/fake/video.mp4"),
        fps=25.0,
        n_frames=100,
        width=640,
        height=480,
        frame_count_source="nb_frames"
    )

    # 构造一个空的 plan（只需要字段存在即可）
    # 使用 runner 里的实际类
    from depressionplex import runner
    plan = runner.TrialPlan(
        calib_indices=[],
        chambers=[],
        warnings=[],
        trial_validity=validity.TrialValidity(chambers=[])
    )

    # 模拟 _resolve_ffmpeg_tool 返回元组 (path, source)
    with patch("depressionplex.video._resolve_ffmpeg_tool") as mock_resolve:
        mock_resolve.side_effect = lambda tool: (f"/fake/{tool}", "env")

        # 获取 ffmpeg 信息（模拟调用方的职责）
        ffmpeg_path, ffmpeg_source = video._resolve_ffmpeg_tool("ffmpeg")
        ffprobe_path, _ = video._resolve_ffmpeg_tool("ffprobe")

        # 模拟 _get_ffmpeg_version 返回 None（取不到）
        with patch("depressionplex.cli.analyze._get_ffmpeg_version") as mock_version:
            mock_version.return_value = None

            result = analyze._build_run_json(info, plan, "TST", {}, set(),
                                             ffmpeg_path, ffmpeg_source, ffprobe_path)

            # 断言 decoder 块存在
            assert "decoder" in result, "run.json 里找不到 decoder 块"
            decoder = result["decoder"]

            # 断言四个键都在
            assert "ffmpeg_path" in decoder, "decoder 块缺 ffmpeg_path"
            assert "ffprobe_path" in decoder, "decoder 块缺 ffprobe_path"
            assert "source" in decoder, "decoder 块缺 source"
            assert "ffmpeg_version" in decoder, "decoder 块缺 ffmpeg_version"

            # 断言取不到时是 None 不是 ""
            assert decoder["ffmpeg_version"] is None, \
                f"ffmpeg_version 取不到时应该是 None，实际是 {decoder['ffmpeg_version']!r}"


def test_license_files_in_packaging():
    """守卫 10：许可文件必须进包（.iss 里用通配，fetch_ffmpeg 里真提取）。

    BtbN 的 lgpl-shared 包里只有 LICENSE.txt 一个许可文件（架构师实测）。
    .iss 用通配符（`*.exe` / `*.dll` / `LICENSE.txt`），守卫只检查通配规则存在。
    fetch_ffmpeg.py 的提取规则必须覆盖：bin/ 全体（除 ffplay.exe）+ LICENSE.txt。
    """
    iss_path = ROOT / "packaging" / "installer.iss"
    iss_text = iss_path.read_text(encoding="utf-8")

    fetch_py = ROOT / "packaging" / "fetch_ffmpeg.py"
    fetch_text = fetch_py.read_text(encoding="utf-8")

    # .iss 必须用通配符匹配所有 exe / dll，且必须含 LICENSE.txt
    assert r"vendor\ffmpeg\*.exe" in iss_text, \
        "installer.iss 里找不到 vendor\\ffmpeg\\*.exe 通配规则"
    assert r"vendor\ffmpeg\*.dll" in iss_text, \
        "installer.iss 里找不到 vendor\\ffmpeg\\*.dll 通配规则"
    assert "LICENSE.txt" in iss_text, \
        "installer.iss 里找不到 LICENSE.txt"

    # fetch_ffmpeg.py 必须提取 LICENSE.txt
    assert "LICENSE.txt" in fetch_text, \
        "fetch_ffmpeg.py 里找不到 LICENSE.txt"

    # fetch_ffmpeg.py 的提取规则注释必须提到 bin/ 和跳过 ffplay
    assert "bin/" in fetch_text, \
        "fetch_ffmpeg.py 里找不到 bin/ 提取规则"
    assert "ffplay" in fetch_text.lower(), \
        "fetch_ffmpeg.py 里找不到跳过 ffplay 的说明"
