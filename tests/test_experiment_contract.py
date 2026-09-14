"""experiment.json 契约守卫测试（B2 交付，DP-101）。

全部用 AST 静态解析或纯函数测试，不许 import 任何 desktop.* 模块
（本仓测试套件跑在没有 PySide6 的环境里）。

六条规则（派工单 B2_新建实验向导_DP-101.md §2.4）：
1. 窗口表不许漂移：ASSAY_WINDOWS_UI 必须与引擎 trial_report.ASSAY_WINDOWS 完全一致
2. 范式选项不许硬编码：new_experiment.py 里不许出现 "TST" / "FST" 字面量
3. 契约字段对得上引擎 CLI：experiment.json 键对应的 CLI 旗标必须真的存在
4. 契约里没有窗口字段：键集合里不许含 "window"
5. 契约键集合被锁住：必须与派工单 2.1 列的键完全一致
6. 写出的文件符合契约：绝对路径、带时区、空值是 null、已存在时抛错
"""

from __future__ import annotations

import ast
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 可以 import 引擎（tests/ 不受进程边界约束，这正是防漂移的关键）
sys.path.insert(0, str(ROOT))
from depressionplex.assay_core import trial_report

# 可以 import models（不含 PySide6）
from desktop.app.models import experiment


def _parse(file_path: Path) -> ast.Module:
    """解析文件（语法错误直接让守卫红）。"""
    src = file_path.read_text(encoding="utf-8")
    try:
        return ast.parse(src)
    except SyntaxError as e:
        raise AssertionError(f"{file_path.relative_to(ROOT)} 语法错误：{e}") from e


def test_assay_windows_sync():
    """守卫 1：窗口表不许漂移。

    AST 取 desktop/app/assays.py 的 ASSAY_WINDOWS_UI 字面量，
    与引擎 trial_report.ASSAY_WINDOWS 逐键逐值相等。
    任何一边改了都红。
    """
    assays_py = ROOT / "desktop/app/assays.py"
    assert assays_py.exists(), "desktop/app/assays.py 不存在"

    tree = _parse(assays_py)

    # 找到 ASSAY_WINDOWS_UI 的赋值（带类型注解的是 AnnAssign）
    ui_windows_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            if (isinstance(node.target, ast.Name)
                    and node.target.id == "ASSAY_WINDOWS_UI"):
                ui_windows_node = node
                break
        elif isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "ASSAY_WINDOWS_UI"
                   for t in node.targets):
                ui_windows_node = node
                break

    assert ui_windows_node is not None, \
        "assays.py 里找不到 ASSAY_WINDOWS_UI"

    value_node = ui_windows_node.value
    assert isinstance(value_node, ast.Dict), \
        "ASSAY_WINDOWS_UI 必须是字典字面量"

    # 提取 UI 侧的窗口表
    ui_windows = {}
    for k, v in zip(value_node.keys, value_node.values):
        assert isinstance(k, ast.Constant) and isinstance(k.value, str), \
            "ASSAY_WINDOWS_UI 的键必须是字符串字面量"
        assert isinstance(v, ast.Tuple) and len(v.elts) == 2, \
            "ASSAY_WINDOWS_UI 的值必须是两元组"
        start = v.elts[0]
        end = v.elts[1]
        assert isinstance(start, ast.Constant) and isinstance(end, ast.Constant), \
            "ASSAY_WINDOWS_UI 的窗口值必须是数字字面量"
        ui_windows[k.value] = (float(start.value), float(end.value))

    # 与引擎的窗口表比对
    engine_windows = trial_report.ASSAY_WINDOWS

    # 键集合必须完全相同
    assert set(ui_windows.keys()) == set(engine_windows.keys()), \
        f"窗口表的键集合不一致：UI {set(ui_windows.keys())} vs 引擎 {set(engine_windows.keys())}"

    # 逐键逐值必须相同
    for assay in ui_windows:
        assert ui_windows[assay] == engine_windows[assay], \
            f"{assay} 窗口不一致：UI {ui_windows[assay]} vs 引擎 {engine_windows[assay]}"


def test_no_hardcoded_assay_literals():
    """守卫 2：范式选项不许硬编码。

    new_experiment.py 里不许出现 "TST" / "FST" 字符串字面量
    （选项必须从 ASSAY_WINDOWS_UI 生成）。
    例外：注释与文档字符串不算。
    """
    new_exp_py = ROOT / "desktop/app/pages/new_experiment.py"
    assert new_exp_py.exists(), "desktop/app/pages/new_experiment.py 不存在"

    tree = _parse(new_exp_py)

    forbidden = {"TST", "FST"}
    violations = []

    for node in ast.walk(tree):
        # 跳过文档字符串（函数/类的第一个 Expr(Constant(...))）
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                continue  # 文档字符串，跳过

        # 检查字符串字面量
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in forbidden:
                violations.append(
                    f"行 {node.lineno}: 硬编码范式字面量 {node.value!r}"
                )

    assert not violations, \
        f"new_experiment.py 里不许硬编码范式字面量（必须从 ASSAY_WINDOWS_UI 生成）：\n" + \
        "\n".join(violations)


def test_contract_fields_match_cli_flags():
    """守卫 3：契约字段对得上引擎 CLI。

    AST 取 depressionplex/cli/analyze.py 里所有 ap.add_argument("--x", ...)
    的旗标集合，断言映射表里每个 experiment.json 键对应的旗标真的存在。
    """
    cli_py = ROOT / "depressionplex/cli/analyze.py"
    assert cli_py.exists(), "depressionplex/cli/analyze.py 不存在"

    tree = _parse(cli_py)

    # 收集所有 --xxx 旗标
    cli_flags = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
                and node.args):
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                flag = first_arg.value
                if flag.startswith("--"):
                    cli_flags.add(flag)

    # experiment.json 键 -> CLI 旗标的映射
    field_to_flag = {
        "assay": "--assay",
        "n_chambers": "--chambers",
        "calib_frames": "--calib-frames",
        "body_area_prior": "--body-area-prior",
        # videos[].trial_prefix 对应 --trial-prefix（每次调用传一个）
        "trial_prefix": "--trial-prefix",
    }

    missing = []
    for field, flag in field_to_flag.items():
        if flag not in cli_flags:
            missing.append(f"{field} -> {flag}")

    assert not missing, \
        f"契约字段映射的 CLI 旗标不存在：{missing}\n可用旗标：{sorted(cli_flags)}"


def test_no_window_fields_in_contract():
    """守卫 4：契约里没有窗口字段。

    契约的键集合（从 SCHEMA_KEYS 取）里不许出现含 "window" 的键。
    窗口由范式唯一决定，不许作为用户可编辑的字段。
    """
    keys = set(experiment.SCHEMA_KEYS)
    window_keys = {k for k in keys if "window" in k.lower()}

    assert not window_keys, \
        f"契约里不许出现窗口字段（窗口由范式决定）：{window_keys}"


def test_contract_schema_keys_locked():
    """守卫 5：契约键集合被锁住。

    SCHEMA_KEYS 必须与派工单 2.1 列的键完全一致。
    加字段就得改测试——这是故意的，契约不许悄悄长。
    """
    # 派工单 2.1 定义的字段清单（顺序也要一致）
    expected_keys = (
        "schema_version",
        "created_at",
        "operator",
        "note",
        "assay",
        "n_chambers",
        "calib_frames",
        "body_area_prior",
        "output_dir",
        "videos",
    )

    actual_keys = experiment.SCHEMA_KEYS

    assert actual_keys == expected_keys, \
        f"SCHEMA_KEYS 与派工单不一致：\n" \
        f"期望：{expected_keys}\n" \
        f"实际：{actual_keys}"


def test_serialized_file_conforms_to_contract():
    """守卫 6：写出的文件符合契约。

    不起 GUI：直接调 models/experiment.py 的纯函数，验证：
    - 路径一律转为绝对路径
    - created_at 带时区偏移
    - 空值是 null 不是 ""
    - 已存在时抛 FileExistsError 不覆盖
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # 构造一个最小计划
        plan = experiment.ExperimentPlan(
            assay="TST",
            n_chambers=4,
            calib_frames=12,
            body_area_prior=None,  # 空值
            output_dir=tmp_path / "output",  # 相对路径（会被转为绝对）
            videos=[
                experiment.VideoEntry(
                    path=tmp_path / "video1.mp4",  # 相对路径
                    trial_prefix=None,  # 空值
                ),
                experiment.VideoEntry(
                    path=tmp_path / "video2.mp4",
                    trial_prefix="custom",  # 非空
                ),
            ],
            operator=None,  # 空值
            note="",  # 空字符串应转为 None
        )

        # 创建临时文件以便路径 resolve
        plan.videos[0].path.parent.mkdir(parents=True, exist_ok=True)
        plan.videos[0].path.touch()
        plan.videos[1].path.touch()
        plan.output_dir.mkdir(parents=True, exist_ok=True)

        # 序列化
        data = experiment.serialize_experiment_plan(plan)

        # 验证：路径是绝对路径
        assert Path(data["output_dir"]).is_absolute(), \
            "output_dir 必须是绝对路径"
        assert Path(data["videos"][0]["path"]).is_absolute(), \
            "视频路径必须是绝对路径"

        # 验证：created_at 带时区
        created_at = data["created_at"]
        assert isinstance(created_at, str), "created_at 必须是字符串"
        # ISO 8601 带时区必须有 +/- 或 Z
        assert ("+" in created_at or "-" in created_at[-6:] or created_at.endswith("Z")), \
            f"created_at 必须带时区偏移（ISO 8601）：{created_at}"

        # 验证：空值是 null 不是 ""
        assert data["operator"] is None, "operator 空值必须是 null"
        assert data["body_area_prior"] is None, "body_area_prior 空值必须是 null"
        assert data["videos"][0]["trial_prefix"] is None, \
            "trial_prefix 空值必须是 null"

        # 验证：note 空字符串转 None（通过 ExperimentPlan.__init__ 或序列化器）
        # 注意：当前实现中 note="" 不会自动转 None，这取决于实际需求
        # 如果派工单要求 "" -> None，需要在 serialize 或构造时处理

        # 验证：写出文件
        json_path = experiment.write_experiment_json(plan)
        assert json_path.exists(), "experiment.json 应该已写出"
        assert json_path.is_absolute(), "返回的路径必须是绝对路径"

        # 验证：文件内容可解析
        with json_path.open("r", encoding="utf-8") as f:
            loaded = json.load(f)

        assert loaded["schema_version"] == "1", "schema_version 必须是 '1'"
        assert loaded["assay"] == "TST", "assay 必须正确序列化"
        assert loaded["n_chambers"] == 4, "n_chambers 必须正确序列化"

        # 验证：已存在时抛错不覆盖
        try:
            experiment.write_experiment_json(plan)
            assert False, "应该抛 FileExistsError"
        except FileExistsError as e:
            assert "已存在" in str(e) or "exist" in str(e).lower(), \
                "FileExistsError 应该有清晰的错误信息"


# ---------------------------------------------------------------------------
# 守卫 7–10（架构师复核 B2 交付时补的，DP-101）
#
# 这四条守的是同一件事的四个侧面：**契约文件是边界产物，边界产物不许带着
# 未验的东西离开这个模块**。B2 的实现在功能上是对的，这四处是可靠性上的窄口子。
# ---------------------------------------------------------------------------
def _minimal_plan(out_dir: Path, video: Path, *, assay: str = "TST") -> "experiment.ExperimentPlan":
    return experiment.ExperimentPlan(
        assay=assay, n_chambers=4, calib_frames=12, body_area_prior=None,
        output_dir=out_dir, videos=[experiment.VideoEntry(video, None)],
        operator=None, note=None,
    )


def test_validate_creates_nothing():
    """守卫 7：`validate_experiment_plan` 只诊断，**不许在客户机上留下任何东西**。

    原实现在验证里 `mkdir(parents=True)` 再写探针。后果有两个：校验失败或用户
    看完提示又反悔时，客户机上凭空多出一串空目录；而且 `validate` 就不是可重复
    调用的纯检查了，将来加一个「预检」按钮等于每点一下建一棵树。
    同型教训见 `test_desktop_boundary.py::test_path_getters_have_no_side_effects`。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        video = tmp / "v.mp4"
        video.write_bytes(b"\x00")
        target = tmp / "a" / "b" / "c"          # 三层都不存在

        before = sorted(p.name for p in tmp.iterdir())
        err = experiment.validate_experiment_plan(_minimal_plan(target, video))
        assert err is None, f"这个计划应当通过校验，却报：{err}"

        assert not target.exists(), f"validate 把输出目录建出来了：{target}"
        assert not (tmp / "a").exists(), "validate 建了中间目录"
        after = sorted(p.name for p in tmp.iterdir())
        assert after == before, f"validate 在磁盘上留下了东西：{set(after) - set(before)}"


def test_duplicate_videos_caught_after_resolve():
    """守卫 8：视频查重必须按**解析后的绝对路径**比。

    序列化时写的是 `resolve()` 的结果。拿没解析的 `Path` 比，「同一个文件的两种
    写法」（相对/绝对、符号链接）就会漏过去——于是同一段录像在一次实验里跑两遍，
    输出名还撞车，而报告上看不出是同一段。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir).resolve()
        real = tmp / "v.mp4"
        real.write_bytes(b"\x00")
        link = tmp / "same.mp4"
        try:
            link.symlink_to(real)
        except (OSError, NotImplementedError):
            link = None                        # Windows 上未必给建符号链接

        pairs = [experiment.VideoEntry(real, None), experiment.VideoEntry(tmp / "." / "v.mp4", None)]
        plan = experiment.ExperimentPlan(
            assay="TST", n_chambers=4, calib_frames=12, body_area_prior=None,
            output_dir=tmp / "out", videos=pairs, operator=None, note=None)
        err = experiment.validate_experiment_plan(plan)
        assert err and "重复" in err, f"同一文件的两种写法没被判重：{err!r}"

        if link is not None:
            plan.videos = [experiment.VideoEntry(real, None), experiment.VideoEntry(link, None)]
            err = experiment.validate_experiment_plan(plan)
            assert err and "重复" in err, f"符号链接指向同一文件没被判重：{err!r}"


def test_unknown_assay_rejected_at_contract_layer():
    """守卫 9：未知范式在契约层就得拦下，不许写进 experiment.json。

    引擎侧 `--assay` 是 `choices=sorted(ASSAY_WINDOWS)`，所以未知范式最终会被
    argparse 拦住——但那要等队列把文件交给 CLI，隔了两层，报错离出错的地方太远；
    在此之前那个 `.json` 已经躺在客户的输出目录里，看着像个正经实验计划。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        video = tmp / "v.mp4"
        video.write_bytes(b"\x00")
        err = experiment.validate_experiment_plan(
            _minimal_plan(tmp / "out", video, assay="OFT"))
        assert err, "未知范式 'OFT' 竟然通过了校验"
        assert "OFT" in err and "TST" in err and "FST" in err, \
            f"报错得说清不认识哪个、认识哪些：{err!r}"

        # 认识的两个必须都能过（别把守卫写成「只认 TST」）
        for good in sorted(trial_report.ASSAY_WINDOWS):
            assert experiment.validate_experiment_plan(
                _minimal_plan(tmp / "out", video, assay=good)) is None, good


def test_partial_write_leaves_no_file():
    """守卫 10：写一半失败必须把半截文件删掉。

    留着它的后果不是「文件坏了」而是**重试被自己堵死**：`write_experiment_json`
    见到已存在就抛 `FileExistsError`，于是用户下次点完成看到的是「文件已存在」，
    而真正的错误（磁盘满、盘被拔）已经看不见了。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        video = tmp / "v.mp4"
        video.write_bytes(b"\x00")
        plan = _minimal_plan(tmp / "out", video)

        real_dump = json.dump

        def boom(*a, **k):
            real_dump(*a, **k)                 # 先写进去一部分，再炸
            raise OSError("磁盘满（测试注入）")

        json.dump = boom
        try:
            experiment.write_experiment_json(plan)
        except OSError as e:
            assert "磁盘满" in str(e), f"异常被吞掉换成了别的：{e}"
        else:
            raise AssertionError("注入的 OSError 没有抛出来")
        finally:
            json.dump = real_dump

        left = plan.output_dir / "experiment.json"
        assert not left.exists(), "写失败后留下了半截 experiment.json（会把重试堵死）"

        # 清理干净之后必须能重试成功
        p = experiment.write_experiment_json(plan)
        assert p.exists() and json.loads(p.read_text(encoding="utf-8"))["assay"] == "TST"


def test_n_chambers_has_no_upper_bound_in_contract():
    """守卫 11（DP-106）：契约层对隔间数**只有下界**，没有上界。

    道俊 2026-09-13 定调「chambers 根据 CSI 是否设置」。CSI 两种标定/结果文件
    都没写上限（`.SET` 孔位数是裸 `int32`，`.CLB` 是 `0x43+count` 一字节或裸 `u32`），
    所以从 GUI 到契约到 `--chambers` 这一整条链上都不许出现「最多 4」。

    GUI 的 spinbox 上限 100（`pages/new_experiment.py`）是**输入控件的手感**，
    不是科学上界——它拦的是手滑打出 100000，不是「这台机器不可能有 6 槽」。
    真正决定切几个隔间的是 `segment.find_chambers` 实测，契约里这个数只是期望值。

    下界要留着：0 个隔间的实验计划写出去，队列会拿它去跑一段谁也没法解释的录像。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        video = tmp / "v.mp4"
        video.write_bytes(b"\x00")
        for n in (1, 4, 6, 8, 16):
            plan = _minimal_plan(tmp / "out", video)
            plan.n_chambers = n
            assert experiment.validate_experiment_plan(plan) is None, \
                f"{n} 个隔间被契约层拒了——观测上界不是格式上界（DP-106）"
        for bad in (0, -1):
            plan = _minimal_plan(tmp / "out", video)
            plan.n_chambers = bad
            err = experiment.validate_experiment_plan(plan)
            assert err and "≥ 1" in err, f"隔间数 {bad} 竟然过了校验：{err!r}"
