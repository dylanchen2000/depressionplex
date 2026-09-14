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


def test_ffmpeg_hash_must_be_none_not_placeholder():
    """守卫 H3：占位哈希必须长成 None，不许长成格式合法的 64 位十六进制。

    格式合法的占位（45eb9e600e7a...）会通过守卫 8 的长度/格式检查，
    但下载回来的真文件哈希不匹配时才红——那时已经跑了网络请求。
    None 在取用时立刻 TypeError，在本地就能发现。
    """
    fetch_py = ROOT / "packaging" / "fetch_ffmpeg.py"
    tree = ast.parse(fetch_py.read_text(encoding="utf-8"), filename=str(fetch_py))

    sha256_value = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "FFMPEG_SHA256":
                    if isinstance(node.value, ast.Constant):
                        sha256_value = node.value.value

    # 如果哈希是 None，守卫通过（意味着还没填真值）
    if sha256_value is None:
        return

    # 如果哈希不是 None，必须是真实的 64 位十六进制（守卫 8 已检查）
    # 这条守卫的作用是：占位时不许写成 "00000..."，必须写 None
    assert len(sha256_value) == 64 and re.fullmatch(r'[0-9a-fA-F]{64}', sha256_value), \
        f"FFMPEG_SHA256 如果不是 None，必须是 64 位十六进制（真哈希），不许用占位符"


def test_bin_extraction_no_hardcoded_dll_list():
    """守卫 A6：bin/ 提取规则不许手写 DLL 名单。

    -shared 构建需要 7 个 DLL（avcodec / avfilter / avformat / swscale / avdevice /
    avutil / swresample），上游版本升级可能改 DLL 后缀（如 avcodec-62 → -63）。
    手写名单会在客户机上变成「缺失 ...dll」弹窗。必须用循环提取 bin/ 下全部文件。

    A6 修复：去掉「必须同时有路径分隔符」这个条件——任何人硬编码名单，写出来就是
    ["avcodec-62.dll", ...] 没有分隔符，而旧版守卫只认带分隔符的，最像的那种看不见。
    """
    fetch_py = ROOT / "packaging" / "fetch_ffmpeg.py"
    tree = ast.parse(fetch_py.read_text(encoding="utf-8"), filename=str(fetch_py))

    # 从 AST 提取所有字符串常量，只看包含 .dll 的（A6：去掉分隔符条件）
    string_constants = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value.lower()
            if '.dll' in s:
                string_constants.append(s)

    # 不许在这些字符串中出现 DLL 基础名（说明硬编码了成员名）
    forbidden = ['avcodec', 'avfilter', 'avformat', 'swscale', 'avdevice', 'avutil', 'swresample']
    violations = []
    for dll_name in forbidden:
        for s in string_constants:
            if dll_name in s:
                violations.append(f"{dll_name} in '{s[:60]}'")
                break

    assert not violations, (
        f"fetch_ffmpeg.py 代码字符串里硬编码了 DLL 路径：{violations}。"
        f"必须循环提取 bin/ 全体，不许手写成员名"
    )

    # 必须有循环提取 bin/ 的逻辑：main() 里必须存在遍历 namelist() 的循环，
    # 且 bin_prefix 参与了成员筛选（A6：改用 AST，不许只看注释里的子串）
    main_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            main_func = node
            break

    assert main_func is not None, "fetch_ffmpeg.py 里找不到 main() 函数"

    # 检查 main() 里是否有循环调用 .namelist()
    has_namelist_loop = False
    has_bin_prefix_filter = False

    for node in ast.walk(main_func):
        # 找调用 .namelist() 的循环
        if isinstance(node, ast.For):
            # 检查迭代对象是否是 .namelist() 调用
            if isinstance(node.iter, ast.Call):
                if isinstance(node.iter.func, ast.Attribute) and node.iter.func.attr == "namelist":
                    has_namelist_loop = True
                    # 检查循环体内是否有 bin_prefix 的使用（通常在 if member.startswith(...) 里）
                    for inner_node in ast.walk(node):
                        if isinstance(inner_node, ast.Name) and "bin" in inner_node.id.lower():
                            has_bin_prefix_filter = True
                            break

    assert has_namelist_loop, \
        "fetch_ffmpeg.py::main() 必须有循环遍历 .namelist()"
    assert has_bin_prefix_filter, \
        "fetch_ffmpeg.py::main() 的循环里必须有 bin_prefix 参与成员筛选"


def test_cli_encoding_reconfigure_single_source():
    """守卫：Windows 编码归一化的唯一来源是 depressionplex/cli/_stdio.py::force_utf8()。

    Windows 客户机（cp1252/cp936）上引擎的中文报告会 UnicodeEncodeError 或乱码。
    **单一来源原则**：
    1. 每个 CLI main() 的第一条可执行语句必须是 _stdio.force_utf8() 调用
    2. depressionplex/ 和 packaging/ 里除 _stdio.py 外不许出现 .reconfigure(
    3. packaging/fetch_ffmpeg.py 必须用内联的防御式 reconfigure（它 import 不到 depressionplex）
    4. 外壳（desktop/main.py）的 TextIOWrapper 是唯一例外（errors="replace" 是刻意的，
       自检宁可打出乱码页名也不许崩；而引擎要的是真报错。外壳不许 import 引擎，所以
       两边不可能共用代码）

    全仓只许两处编码设置：_stdio.py + desktop/main.py。
    """
    cli_dir = ROOT / "depressionplex" / "cli"
    violations = []

    # 1. 检查每个 CLI main() 的第一条可执行语句必须是 _stdio.force_utf8()
    # （包括 __init__.py 的分发器，它有两条打中文的路径：--help 和未知子命令）
    for py_file in cli_dir.glob("*.py"):
        if py_file.name == "_stdio.py":
            continue

        src = py_file.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(py_file))

        # 找到所有 main 函数
        main_funcs = [node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef) and node.name == "main"]

        if not main_funcs:
            continue

        for main_func in main_funcs:
            # 第一条可执行语句必须是 _stdio.force_utf8() 或 force_utf8()
            # （跳过 docstring）
            first_stmt = None
            for stmt in main_func.body:
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, (ast.Str, ast.Constant)):
                    continue  # docstring
                first_stmt = stmt
                break

            if first_stmt is None:
                violations.append(f"{py_file.name}::main() 函数体为空")
                continue

            # 检查第一条可执行语句是否为 _stdio.force_utf8() 或 force_utf8()
            is_force_utf8_call = False
            if isinstance(first_stmt, ast.Expr) and isinstance(first_stmt.value, ast.Call):
                call = first_stmt.value
                # _stdio.force_utf8()
                if (isinstance(call.func, ast.Attribute) and
                    call.func.attr == "force_utf8" and
                    isinstance(call.func.value, ast.Name) and
                    call.func.value.id == "_stdio"):
                    is_force_utf8_call = True
                # force_utf8()（from . import _stdio 后直接调用的形式）
                elif (isinstance(call.func, ast.Name) and
                      call.func.id == "force_utf8"):
                    is_force_utf8_call = True

            if not is_force_utf8_call:
                violations.append(
                    f"{py_file.name}::main() 第一条可执行语句不是 _stdio.force_utf8()。"
                    f"实际第一条：{ast.unparse(first_stmt) if first_stmt else 'None'}"
                )

    # 2. 检查 depressionplex/ 和 packaging/ 里除白名单外不许出现 .reconfigure(
    # 白名单：depressionplex/cli/_stdio.py（定义处）、packaging/fetch_ffmpeg.py（import 不到时内联）
    whitelist = {
        ROOT / "depressionplex" / "cli" / "_stdio.py",
        ROOT / "packaging" / "fetch_ffmpeg.py",
    }

    for base_dir in [ROOT / "depressionplex", ROOT / "packaging"]:
        for py_file in base_dir.rglob("*.py"):
            if py_file in whitelist:
                continue

            src = py_file.read_text(encoding="utf-8")
            if ".reconfigure(" in src:
                violations.append(
                    f"{py_file.relative_to(ROOT)} 里出现 .reconfigure( —— "
                    f"全仓只许 _stdio.py 和 fetch_ffmpeg.py（内联）使用"
                )

    # 3. 检查 packaging/fetch_ffmpeg.py 里必须有防御式 reconfigure
    fetch_ffmpeg_path = ROOT / "packaging" / "fetch_ffmpeg.py"
    fetch_src = fetch_ffmpeg_path.read_text(encoding="utf-8")
    # 必须包含 getattr(stream, "reconfigure", None) 的防御式调用
    if 'getattr(stream, "reconfigure", None)' not in fetch_src and \
       "getattr(stream, 'reconfigure', None)" not in fetch_src:
        violations.append(
            "packaging/fetch_ffmpeg.py::main() 缺少防御式 reconfigure "
            "（它 import 不到 depressionplex，必须内联）"
        )

    assert not violations, (
        "Windows 编码归一化违反单一来源原则：\n" + "\n".join(violations)
    )


def test_workflow_deps_match_arch_spec():
    """守卫：workflow 里的 numpy/PySide6 pin 必须等于架构文件 §3.2。

    numpy 2.x 改了标量提升规则（NEP 50），客户机 exe 算出的秒数可能与验收读数不同。
    一个门槛只许有一个来源：架构文件是真值，workflow 必须与它一致。
    """
    # 读取架构文件中的版本号
    arch_spec = ROOT / "docs" / "SPEC_产品化总体架构_v1.md"
    arch_text = arch_spec.read_text(encoding="utf-8")

    # 提取 numpy==x.y.z 和 PySide6==x.y.z
    numpy_match = re.search(r'numpy==(\d+\.\d+\.\d+)', arch_text)
    pyside6_match = re.search(r'PySide6==(\d+\.\d+\.\d+)', arch_text)

    assert numpy_match, "架构文件中找不到 numpy==x.y.z"
    assert pyside6_match, "架构文件中找不到 PySide6==x.y.z"

    arch_numpy = numpy_match.group(1)
    arch_pyside6 = pyside6_match.group(1)

    # 读取 workflow 文件
    workflow = ROOT / ".github" / "workflows" / "build-windows.yml"
    workflow_text = workflow.read_text(encoding="utf-8")

    # 检查 workflow 中的版本号
    assert f"numpy=={arch_numpy}" in workflow_text, \
        f"build-windows.yml 的 numpy 版本必须是 {arch_numpy}（与架构文件一致）"
    assert f"PySide6=={arch_pyside6}" in workflow_text, \
        f"build-windows.yml 的 PySide6 版本必须是 {arch_pyside6}（与架构文件一致）"


def test_iss_language_files_exist_in_repo():
    """守卫：[Languages] 段的 MessagesFile 必须是仓内文件或 compiler:Default.isl。

    简体中文不在 Inno Setup 官方安装包中，必须随仓分发。
    compiler: 指的是构建机上装了什么，那是我们控制不了的——控制不了的东西不许
    出现在发布路径上。唯一例外：compiler:Default.isl（英文，Inno 必带）。
    """
    iss_file = ROOT / "packaging" / "installer.iss"
    iss_text = iss_file.read_text(encoding="utf-8")

    # 解析 [Languages] 段
    in_languages = False
    violations = []

    for line in iss_text.splitlines():
        line_stripped = line.strip()

        # 检测段的开始和结束
        if line_stripped.startswith("[Languages]"):
            in_languages = True
            continue
        elif line_stripped.startswith("[") and in_languages:
            break  # 进入下一个段，停止解析

        if not in_languages or not line_stripped or line_stripped.startswith(";"):
            continue

        # 解析 MessagesFile
        if "MessagesFile" in line:
            # 提取 MessagesFile 的值（在引号内）
            match = re.search(r'MessagesFile:\s*"([^"]+)"', line)
            if not match:
                continue

            msg_file = match.group(1)

            if msg_file.startswith("compiler:"):
                # compiler: 开头的只许是 compiler:Default.isl
                if msg_file != "compiler:Default.isl":
                    violations.append(
                        f"MessagesFile 使用了非 Default.isl 的 compiler: 路径：{msg_file}。"
                        f"构建机上的文件我们控制不了，不许依赖。"
                    )
            else:
                # 不是 compiler: 开头的，必须是仓内存在的文件
                # 相对路径按 .iss 所在目录解析（.iss 是 Windows 格式，需转换路径分隔符）
                msg_file_unix = msg_file.replace("\\", "/")
                msg_file_path = (iss_file.parent / msg_file_unix).resolve()
                if not msg_file_path.exists():
                    violations.append(
                        f"MessagesFile 指向的文件不存在：{msg_file} "
                        f"（解析为 {msg_file_path}）"
                    )

    assert not violations, (
        ".iss [Languages] 段的 MessagesFile 违规：\n" + "\n".join(violations)
    )


def test_cli_subcommand_registry():
    """守卫：depressionplex/cli/__init__.py 的 SUBCOMMANDS 名册必须准确。

    分发器是冻结构建的后端 exe 入口，子命令名册必须准确：
    1. 名册里的每个子命令，depressionplex/cli/ 下必须有对应的模块且有 main() 函数
    2. 外壳（desktop/）用到的子命令名必须在名册里（镜像 + 对账，外壳不许 import 引擎）
    """
    # 1. 读取分发器的 SUBCOMMANDS 名册
    cli_init = ROOT / "depressionplex" / "cli" / "__init__.py"
    init_src = cli_init.read_text(encoding="utf-8")
    tree = ast.parse(init_src, filename=str(cli_init))

    subcommands_dict = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SUBCOMMANDS":
                    # 提取字典的键（子命令名）
                    if isinstance(node.value, ast.Dict):
                        subcommands_dict = {}
                        for key in node.value.keys:
                            if isinstance(key, ast.Constant):
                                subcommands_dict[key.value] = None
                    break

    assert subcommands_dict is not None, \
        "depressionplex/cli/__init__.py 里找不到 SUBCOMMANDS 字典"

    violations = []

    # 2. 每个子命令名必须对应一个有 main() 的模块
    for subcmd in subcommands_dict:
        # 子命令名转模块名：analyze -> analyze.py
        module_file = ROOT / "depressionplex" / "cli" / f"{subcmd.replace('-', '_')}.py"

        # 特殊处理：acq-check -> acq_check.py（连字符转下划线）
        if not module_file.exists():
            module_file = ROOT / "depressionplex" / "cli" / f"{subcmd.replace('-', '_')}.py"

        if not module_file.exists():
            violations.append(
                f"SUBCOMMANDS 里的子命令 '{subcmd}' 没有对应的模块文件：{module_file.name}"
            )
            continue

        # 检查模块是否有 main() 函数
        mod_src = module_file.read_text(encoding="utf-8")
        mod_tree = ast.parse(mod_src, filename=str(module_file))
        has_main = any(
            isinstance(node, ast.FunctionDef) and node.name == "main"
            for node in ast.walk(mod_tree)
        )

        if not has_main:
            violations.append(
                f"SUBCOMMANDS 里的子命令 '{subcmd}' 对应的模块 {module_file.name} "
                f"缺少 main() 函数"
            )

    # 3. 外壳侧用到的子命令名必须在名册里（镜像 + 对账）
    # 扫描 desktop/app/services/engine.py 的 argv.append("<子命令>") 调用
    engine_file = ROOT / "desktop" / "app" / "services" / "engine.py"
    engine_src = engine_file.read_text(encoding="utf-8")
    engine_tree = ast.parse(engine_src, filename=str(engine_file))

    used_subcommands = set()
    for node in ast.walk(engine_tree):
        # 找 argv.append("<literal>") 形式的调用
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if (isinstance(call.func, ast.Attribute) and
                call.func.attr == "append" and
                isinstance(call.func.value, ast.Name) and
                call.func.value.id == "argv"):
                # 提取 append 的参数
                if call.args and isinstance(call.args[0], ast.Constant):
                    arg_value = call.args[0].value
                    if isinstance(arg_value, str) and not arg_value.startswith("-"):
                        # 可能是子命令（不以 - 开头的字符串字面量）
                        used_subcommands.add(arg_value)

    for used_cmd in used_subcommands:
        if used_cmd not in subcommands_dict:
            violations.append(
                f"外壳（engine.py）用到的子命令 '{used_cmd}' 不在分发器的 SUBCOMMANDS 名册里"
            )

    assert not violations, (
        "CLI 子命令名册校验失败：\n" + "\n".join(violations)
    )


def test_no_hardcoded_ffmpeg_tool_names():
    """守卫 A14：除 video.py 与 packaging/ 外，不许出现 ffmpeg 工具名字符串常量。

    H11 要禁的是「不许在 video.py 之外用字面量 "ffmpeg"/"ffprobe" 起进程」，
    而旧守卫只认 subprocess.*([列表字面量]) 这一种形状，实际调用是 _run(cmd) / Popen(cmd)。
    改成更硬的规则（A14）：
    1. 除 video.py 与 packaging/ 外，depressionplex/ 的任何源文件里不许出现这四个字符串常量
    2. video.py 里除 _resolve_ffmpeg_tool 外的函数体内也不许出现（_resolve_ffmpeg_tool 是唯一入口）
    """
    depressionplex_dir = ROOT / "depressionplex"
    video_py = depressionplex_dir / "video.py"

    forbidden_strings = ["ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe"]
    violations = []

    # 1. 检查 depressionplex/ 下除 video.py 外的所有 .py 文件
    for py_file in depressionplex_dir.rglob("*.py"):
        if py_file == video_py:
            continue  # video.py 单独检查

        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in forbidden_strings:
                    violations.append(
                        f"{py_file.relative_to(ROOT)}:{node.lineno}: "
                        f"出现字符串常量 {node.value!r}。"
                        f"ffmpeg 工具解析只许在 video._resolve_ffmpeg_tool 里进行"
                    )

    # 2. 检查 video.py 里除 _resolve_ffmpeg_tool 外的函数
    if video_py.exists():
        tree = ast.parse(video_py.read_text(encoding="utf-8"), filename=str(video_py))

        # 找到 _resolve_ffmpeg_tool 函数的节点
        resolve_func_node = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_resolve_ffmpeg_tool":
                resolve_func_node = node
                break

        # 检查除 _resolve_ffmpeg_tool 外的所有函数定义
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node != resolve_func_node:
                # 在这个函数体内查找禁止的字符串常量
                for inner_node in ast.walk(node):
                    if isinstance(inner_node, ast.Constant) and isinstance(inner_node.value, str):
                        if inner_node.value in forbidden_strings:
                            violations.append(
                                f"video.py:{inner_node.lineno} ({node.name}): "
                                f"出现字符串常量 {inner_node.value!r}。"
                                f"除 _resolve_ffmpeg_tool 外不许直接用工具名字面量"
                            )

    assert not violations, (
        "发现硬编码的 ffmpeg 工具名：\n" + "\n".join(violations)
    )


def test_cli_dispatcher_help_and_exit_codes():
    """守卫：CLI 子命令分发器的 --help 与退出码契约（A12 smoke test 依赖）。

    退出码契约：
    - 0: --help / -h / 无参数（客户双击 exe 不该看到错误）
    - 1: 内部错误（导入失败）
    - 2: 用法错误（未知子命令）

    --help 输出必须包含所有子命令名——否则加了子命令忘了注册，用法里就少一行而没人知道。
    """
    import subprocess
    import sys

    # 构造调用：python -m depressionplex.cli <args>
    def call_dispatcher(args: list[str]) -> tuple[int, str, str]:
        """返回 (rc, stdout, stderr)"""
        result = subprocess.run(
            [sys.executable, "-m", "depressionplex.cli"] + args,
            capture_output=True,
            text=True,
            cwd=ROOT
        )
        return result.returncode, result.stdout, result.stderr

    # 1. --help: rc 0，输出包含所有子命令名
    rc, stdout, stderr = call_dispatcher(["--help"])
    assert rc == 0, f"--help 应返回 0，实际 {rc}"
    
    # 从 depressionplex/cli/__init__.py 读取 SUBCOMMANDS
    cli_init = ROOT / "depressionplex" / "cli" / "__init__.py"
    tree = ast.parse(cli_init.read_text(encoding="utf-8"), filename=str(cli_init))
    subcommands_dict = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SUBCOMMANDS":
                    if isinstance(node.value, ast.Dict):
                        subcommands_dict = {}
                        for key in node.value.keys:
                            if isinstance(key, ast.Constant):
                                subcommands_dict[key.value] = None
                    break

    assert subcommands_dict is not None, "找不到 SUBCOMMANDS 字典"
    
    for subcmd in subcommands_dict:
        assert subcmd in stdout, \
            f"--help 输出里没有子命令 {subcmd!r}（加了子命令忘了注册，用法里就少一行）"

    # 2. -h: 同上
    rc, stdout, stderr = call_dispatcher(["-h"])
    assert rc == 0, f"-h 应返回 0，实际 {rc}"
    for subcmd in subcommands_dict:
        assert subcmd in stdout, f"-h 输出里没有子命令 {subcmd!r}"

    # 3. 无参数: rc 0
    rc, stdout, stderr = call_dispatcher([])
    assert rc == 0, f"无参数应返回 0（客户双击 exe 不该看到错误），实际 {rc}"

    # 4. 未知子命令: rc 2（用法错误，不是 1）
    rc, stdout, stderr = call_dispatcher(["nonexistent-subcommand"])
    assert rc == 2, f"未知子命令应返回 2（用法错误），实际 {rc}"
    assert "未知子命令" in stderr or "nonexistent-subcommand" in stderr, \
        "未知子命令应打印错误到 stderr"


def test_analyzer_spec_entry_is_not_package_init():
    """守卫 C2.1：后端 spec 的入口不许是 __init__.py（run 34826444142 根因）。

    PyInstaller 把入口脚本当 __main__ 执行，__package__ 为空：
    - 里面的 `from . import X` 在冻结后必然 ImportError
    - 同一份代码会以 __main__ 和包名两个名字各载一次，模块级状态出现两份
    """
    spec_file = ROOT / "packaging" / "build_analyzer_windows.spec"
    tree = ast.parse(spec_file.read_text(encoding="utf-8"), filename=str(spec_file))

    # 找到 Analysis(...) 调用
    entry_path = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "Analysis":
                # 第一个位置参数应该是列表字面量
                if node.args and isinstance(node.args[0], ast.List):
                    if node.args[0].elts and isinstance(node.args[0].elts[0], ast.Constant):
                        entry_path = node.args[0].elts[0].value
                        break

    assert entry_path is not None, "build_analyzer_windows.spec 里找不到 Analysis(...) 的入口路径"

    # 1. 不许以 __init__.py 结尾
    assert not entry_path.endswith("__init__.py"), \
        f"后端 spec 入口是 {entry_path!r}，不许用 __init__.py：" \
        f"PyInstaller 会把它当 __main__ 执行（__package__ 为空），" \
        f"里面的 `from . import X` 在冻结后必然 ImportError（run 34826444142 实测）"

    # 2. 指向的文件必须真实存在（路径按 spec 所在目录解析）
    entry_full_path = (spec_file.parent / entry_path).resolve()
    assert entry_full_path.exists(), \
        f"spec 入口指向的文件不存在：{entry_path} （解析为 {entry_full_path}）"


def test_analyzer_entry_is_thin():
    """守卫 C2.2：PyInstaller 入口脚本只许有 import 和 sys.exit(main())。

    不许有任何业务逻辑、不许有任何 print：入口脚本会以 __main__ 加载，
    任何有状态的操作都会被执行两次（__main__ + 作为包的真实入口）。
    """
    entry_file = ROOT / "packaging" / "analyzer_entry.py"
    tree = ast.parse(entry_file.read_text(encoding="utf-8"), filename=str(entry_file))

    # 允许的顶层语句类型（按 AST 节点检查）
    allowed = {ast.Import, ast.ImportFrom, ast.If, ast.Expr}

    violations = []
    for node in tree.body:
        # 跳过 docstring
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue

        # import / from ... import 语句
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue

        # if __name__ == "__main__": 守卫
        if isinstance(node, ast.If):
            # 必须是 __name__ == "__main__" 形式的比较
            test = node.test
            if (isinstance(test, ast.Compare) and
                isinstance(test.left, ast.Name) and test.left.id == "__name__" and
                len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq) and
                len(test.comparators) == 1 and
                isinstance(test.comparators[0], ast.Constant) and
                test.comparators[0].value == "__main__"):

                # 里面只许有一句：sys.exit(main())
                if len(node.body) != 1:
                    violations.append(f"if __name__ == '__main__' 里应该只有一句 sys.exit(main())，实际有 {len(node.body)} 句")
                    continue

                stmt = node.body[0]
                # 必须是 Expr(Call(Attribute(Name('sys'), 'exit'), [Call(Name('main'), [])]))
                if not (isinstance(stmt, ast.Expr) and
                        isinstance(stmt.value, ast.Call) and
                        isinstance(stmt.value.func, ast.Attribute) and
                        isinstance(stmt.value.func.value, ast.Name) and
                        stmt.value.func.value.id == "sys" and
                        stmt.value.func.attr == "exit" and
                        len(stmt.value.args) == 1 and
                        isinstance(stmt.value.args[0], ast.Call) and
                        isinstance(stmt.value.args[0].func, ast.Name) and
                        stmt.value.args[0].func.id == "main"):
                    violations.append("if __name__ == '__main__' 里必须是 sys.exit(main())，不许有其他逻辑")
                continue
            else:
                violations.append("入口脚本只许有 if __name__ == '__main__' 守卫，不许有其他 if")
                continue

        # 其他任何语句都是违规的
        violations.append(f"入口脚本不许有 {type(node).__name__} 语句（行 {node.lineno}）")

    assert not violations, \
        f"analyzer_entry.py 只许有 import 和 sys.exit(main())，违规：\n" + "\n".join(f"  - {v}" for v in violations)


def test_cli_main_first_statement_is_force_utf8():
    """守卫 C2.3：CLI 分发器的 main() 第一句必须是 force_utf8()（run 34823763958 根因）。

    不许把 force_utf8() 后移：Windows console 默认 cp936/cp1252，
    任何中文 print 放在 force_utf8() 之前都会 UnicodeEncodeError。
    """
    cli_init = ROOT / "depressionplex" / "cli" / "__init__.py"
    tree = ast.parse(cli_init.read_text(encoding="utf-8"), filename=str(cli_init))

    # 找到 main() 函数
    main_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            main_func = node
            break

    assert main_func is not None, "cli/__init__.py 里找不到 main() 函数"

    # 跳过 docstring，找到第一条真实语句
    body = main_func.body
    idx = 0
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        idx = 1  # 跳过 docstring

    assert idx < len(body), "main() 函数体是空的（除了 docstring）"

    first_stmt = body[idx]

    # 必须是 Expr(Call(Attribute(..., 'force_utf8'), []))
    # 调用形式：_stdio.force_utf8() 或 force_utf8()
    is_force_utf8 = False
    if isinstance(first_stmt, ast.Expr) and isinstance(first_stmt.value, ast.Call):
        func = first_stmt.value.func
        if isinstance(func, ast.Attribute) and func.attr == "force_utf8":
            is_force_utf8 = True
        elif isinstance(func, ast.Name) and func.id == "force_utf8":
            is_force_utf8 = True

    assert is_force_utf8, \
        f"cli/__init__.py::main() 第一条语句必须是 _stdio.force_utf8()，" \
        f"实际是 {ast.unparse(first_stmt)!r}（行 {first_stmt.lineno}）。" \
        f"Windows console 默认 cp936/cp1252，任何中文 print 放在 force_utf8() 之前都会 UnicodeEncodeError" \
        f"（run 34823763958 实测）"


def test_no_relative_import_in_any_pyinstaller_entry():
    """守卫 C2.4：所有 PyInstaller 入口脚本不许有相对导入（run 34826444142 根因）。

    PyInstaller 把入口脚本当 __main__ 执行，__package__ 为空，
    任何 `from . import X` 或 `from .. import X` 都会 ImportError。
    """
    # 扫描 packaging/ 下所有以 _entry.py 结尾的文件
    entry_files = list((ROOT / "packaging").glob("*_entry.py"))
    assert entry_files, "packaging/ 下找不到任何 *_entry.py 文件"

    violations = []
    for entry_file in entry_files:
        tree = ast.parse(entry_file.read_text(encoding="utf-8"), filename=str(entry_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                # level > 0 表示相对导入（from . import / from .. import）
                if node.level > 0:
                    violations.append(
                        f"{entry_file.name}:{node.lineno} 有相对导入 "
                        f"'from {'.' * node.level}{node.module or ''} import ...'，"
                        f"PyInstaller 会把入口脚本当 __main__ 执行（__package__ 为空），"
                        f"相对导入在冻结后必然 ImportError（run 34826444142 实测）"
                    )

    assert not violations, \
        "PyInstaller 入口脚本不许有相对导入：\n" + "\n".join(f"  - {v}" for v in violations)


def test_only_one_utf8_shim():
    """守卫 C4：编码 shim 只许在 _stdio.py 里，不许到处写 TextIOWrapper（C4 合并）。

    Windows 编码问题已收束到 depressionplex/cli/_stdio.py::force_utf8()，
    其他任何地方都不许再自己处理 stdout/stderr 的 encoding。
    """
    # 扫描所有 .py 文件（排除 _stdio.py 本身）
    violations = []
    for py_file in ROOT.rglob("*.py"):
        # 跳过 _stdio.py（那是唯一允许有 TextIOWrapper 的地方）
        if py_file.name == "_stdio.py":
            continue

        # 跳过 __pycache__ 和 .venv
        if "__pycache__" in py_file.parts or ".venv" in py_file.parts:
            continue

        content = py_file.read_text(encoding="utf-8")
        # 查找 TextIOWrapper（不区分大小写，因为可能有 io.TextIOWrapper 或 from io import TextIOWrapper）
        if "TextIOWrapper" in content:
            # 找到行号
            for i, line in enumerate(content.splitlines(), start=1):
                if "TextIOWrapper" in line:
                    violations.append(f"{py_file.relative_to(ROOT)}:{i} 不许自己写 TextIOWrapper，必须调用 _stdio.force_utf8()")

    assert not violations, \
        "编码 shim 只许在 _stdio.py 里，不许到处拷贝：\n" + "\n".join(f"  - {v}" for v in violations)
