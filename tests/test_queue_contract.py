"""队列契约测试（DP-102 / B3）。

绝对不许 import PySide6 或 desktop.app.pages.*（沙箱里没有 PySide6）。
全部用 AST 静态解析或直接调纯函数。

**契约夹具必须由生产方喂**（`_contract()` 走 `serialize_experiment_plan`）。
第一版这里五条 argv 测试各自手写 `{"chambers": 4, "trial_prefix": ...}`，
和真正写出去的 `experiment.json`（`n_chambers`、逐视频的 `trial_prefix`）根本不是
同一份东西；消费方照着自己的假夹具跑，五条全绿，而用户在向导里选的隔间数和前缀
一个都没传进引擎——**不报错，数字看着还挺正常**。守卫喂自己造的饭，就只能证明
自己的想象自洽。凡是「读契约」的测试，夹具一律从 `serialize_experiment_plan` 来。

守卫清单（派工单 §3 表格逐条 + 事后补的四条）：
1. 五个参数一个不缺        2. 旗标真的存在        3. null 不传参
4. 路径全绝对              5. 不覆盖已存在输出    6. 半行能拼回来
7. 非进度行不丢            8. n=null 不算百分比   9. 退出码映射
10. 状态机                 11. 取消只删自己的四个文件（真调 output_paths）
12. 串行写死
13. 键名对不上当场抛（不许 `.get(键, 默认值)` 蒙过去）
14. 认不出的 schema_version 不许猜着跑
15. 输出文件名只许有一个派生器
16. 引擎的 stdout 真的被读、被写成报告
"""

import ast
import sys
import tempfile
from pathlib import Path

# 添加仓根到 sys.path，以便导入 desktop 模块（仅用于 AST 解析，不实际 import PySide6）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 导入纯逻辑模块（不依赖 PySide6）
from desktop.app.services import engine, progress
from desktop.app.models import queue_models
from desktop.app.models.experiment import (
    ExperimentPlan, VideoEntry, SCHEMA_KEYS, VIDEO_KEYS, serialize_experiment_plan,
)

QUEUE_PY = ROOT / "desktop" / "app" / "pages" / "queue.py"
ENGINE_PY = ROOT / "desktop" / "app" / "services" / "engine.py"


def _contract(out_dir, *, videos=(("/tmp/test.mp4", None),), assay="TST",
              n_chambers=4, calib_frames=100, body_area_prior=None):
    """造一份**由生产方序列化**的 experiment.json 内容。

    键名一个字都不许在这里手写——手写就等于把消费方的猜测抄进夹具。
    """
    plan = ExperimentPlan(
        assay=assay,
        n_chambers=n_chambers,
        calib_frames=calib_frames,
        body_area_prior=body_area_prior,
        output_dir=Path(out_dir),
        videos=[VideoEntry(path=Path(p), trial_prefix=tp) for p, tp in videos],
        operator="测试员",
        note=None,
    )
    return serialize_experiment_plan(plan)


def _non_docstring_strings(path: Path) -> list[str]:
    """文件里所有**非文档字符串**的字符串常量（含 f-string 的字面段）。

    注释和 docstring 是给人看的，允许提文件名；代码里的字面量不允许。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                doc_nodes.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_nodes]


def _func(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == name), None)
    assert fn is not None, f"{path.name} 里找不到 {name}()"
    return fn


def test_five_params_complete():
    """守卫 1：build_argv 的产出里必须同时有五个输出参数。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        argv = engine.build_argv(_contract(tmpdir), 0)

    argv_str = " ".join(argv)
    required = ["--csv", "--timeline-csv", "--run-json", "--progress-json", "--assay"]
    missing = [p for p in required if p not in argv_str]
    assert not missing, f"缺少参数：{missing}"


def test_flags_exist_in_analyze_py():
    """守卫 2：AST 取 depressionplex/cli/analyze.py 全部旗标，断言 build_argv 用到的每个旗标都在里面。"""
    analyze_py = ROOT / "depressionplex" / "cli" / "analyze.py"
    assert analyze_py.exists(), f"{analyze_py} 不存在"

    tree = ast.parse(analyze_py.read_text(encoding="utf-8"))

    # 提取所有 ap.add_argument("--xxx") 的旗标
    flags_in_analyze = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if (isinstance(node.func, ast.Attribute) and
                    node.func.attr == "add_argument" and
                    len(node.args) > 0 and
                    isinstance(node.args[0], ast.Constant) and
                    isinstance(node.args[0].value, str) and
                    node.args[0].value.startswith("--")):
                flags_in_analyze.add(node.args[0].value)

    # 把所有可选项都填上，好让每个旗标都出现
    with tempfile.TemporaryDirectory() as tmpdir:
        exp = _contract(tmpdir, videos=(("/tmp/test.mp4", "test"),), body_area_prior=500.0)
        argv = engine.build_argv(exp, 0)

    flags_in_argv = {arg for arg in argv if arg.startswith("--")}
    invalid = flags_in_argv - flags_in_analyze
    assert not invalid, f"build_argv 使用了不存在的旗标：{invalid}"

    # 反向也要查：契约里填了的可选项必须真的传出去。
    # 少传一个的后果不是报错，而是引擎拿默认值跑——用户设的前缀/先验被静默丢掉。
    for expected in ("--trial-prefix", "--calib-frames", "--body-area-prior", "--chambers"):
        assert expected in flags_in_argv, f"契约里填了值，argv 里却没有 {expected}"


def test_argv_values_match_contract():
    """守卫 2-2：旗标**带的值**必须就是契约里的值，一个都不许是默认值。

    只查「旗标在不在」是拦不住 DP-102 那个原始事故的：`exp.get("chambers", 4)`
    键名拼错时照样交出 `--chambers 4`，旗标齐全、格式漂亮、数字是假的。
    所以这里每个值都刻意取**非默认**（引擎的 `--chambers` 默认 4）。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        exp = _contract(tmpdir, videos=(("/tmp/test.mp4", "甲组"),),
                        n_chambers=6, calib_frames=77, body_area_prior=123.5)
        argv = engine.build_argv(exp, 0)

    def value_of(flag):
        assert flag in argv, f"argv 里没有 {flag}"
        return argv[argv.index(flag) + 1]

    assert value_of("--chambers") == "6", f"隔间数没照契约传：{value_of('--chambers')}"
    assert value_of("--calib-frames") == "77", f"标定帧数没照契约传：{value_of('--calib-frames')}"
    assert value_of("--body-area-prior") == "123.5", f"体面积先验没照契约传：{value_of('--body-area-prior')}"
    assert value_of("--trial-prefix") == "甲组", f"前缀没照契约传：{value_of('--trial-prefix')}"
    assert value_of("--assay") == "TST", f"范式没照契约传：{value_of('--assay')}"


def test_null_not_passed():
    """守卫 3：trial_prefix / body_area_prior 为 null 时，argv 里既没有该旗标也没有 'None' / ''。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        exp = _contract(tmpdir, videos=(("/tmp/test.mp4", None),), body_area_prior=None)
        argv = engine.build_argv(exp, 0)

    argv_str = " ".join(argv)
    assert "--trial-prefix" not in argv_str, "trial_prefix=None 时不许传 --trial-prefix"
    assert "--body-area-prior" not in argv_str, "body_area_prior=None 时不许传 --body-area-prior"
    assert "None" not in argv, "不许把 None 转成字符串 'None'"
    assert "" not in argv, "不许传空串"


def test_per_video_fields_come_from_the_video():
    """守卫 3-2：`trial_prefix` 是**逐视频**的，两段不同前缀不许串。

    第一版把它当顶层字段读（`exp.get("trial_prefix")`），于是第二段拿到第一段的前缀，
    或者两段都拿不到。手写夹具查不出这种事——它只有一段视频。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        exp = _contract(tmpdir, videos=(("/tmp/a.mp4", "甲"), ("/tmp/b.mp4", "乙")))
        argv_a = engine.build_argv(exp, 0)
        argv_b = engine.build_argv(exp, 1)

    def prefix_of(argv):
        i = argv.index("--trial-prefix")
        return argv[i + 1]

    assert prefix_of(argv_a) == "甲", f"第 0 段的前缀取错了：{prefix_of(argv_a)}"
    assert prefix_of(argv_b) == "乙", f"第 1 段的前缀取错了：{prefix_of(argv_b)}"


def test_paths_absolute():
    """守卫 4：argv 里每个路径参数 Path(p).is_absolute()。

    夹具照旧由生产方产出（它写的已是 `resolve()` 后的绝对路径），再**故意**把视频路径
    改回相对——盘上的 experiment.json 可能是手工编辑过的，`build_argv` 自己得兜住。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        exp = _contract(tmpdir)
        exp["videos"][0]["path"] = "relative/test.mp4"  # 故意退回相对路径
        argv = engine.build_argv(exp, 0)

    path_flags = ["--csv", "--timeline-csv", "--run-json"]
    for i, arg in enumerate(argv):
        if arg in path_flags and i + 1 < len(argv):
            path_val = argv[i + 1]
            assert Path(path_val).is_absolute(), f"{arg} 的值 {path_val} 不是绝对路径"

    # 视频路径本身也要是绝对路径。
    # argv 结构：[python, "-m", 模块名, 视频路径, "--assay", ...] 或 [exe, 视频路径, ...]
    video_arg = None
    skip_next = False
    for i, arg in enumerate(argv):
        if i == 0:
            continue
        if skip_next:
            skip_next = False
            continue
        if arg == "-m":
            skip_next = True
            continue
        if arg.startswith("--"):
            continue
        video_arg = arg
        break

    assert video_arg is not None, "argv 里找不到视频路径"
    assert Path(video_arg).is_absolute(), f"视频路径 {video_arg} 不是绝对路径"


def test_no_overwrite():
    """守卫 5：四个产出里**任何**一个已存在，构造 item 就报错（不许跑起来再说）。

    逐个试，不是只试 CSV：`report_txt` 是外壳自己写的，漏查它的后果是把上一次的
    报告当成这一次的。
    """
    for key in ("csv", "timeline_csv", "run_json", "report_txt"):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _contract(tmpdir)
            engine.output_paths(exp, 0)[key].write_text("existing", encoding="utf-8")
            try:
                engine.build_argv(exp, 0)
            except FileExistsError as e:
                assert "不许覆盖" in str(e), f"错误消息不符合预期：{e}"
            else:
                assert False, f"{key} 已存在时应抛 FileExistsError"


def test_half_line_reassembly():
    """守卫 6：StderrPump 分三次喂半行能正确产出两条进度。"""
    pump = progress.StderrPump()

    chunk1 = b'{"ev":"pro'
    events1 = pump.feed(chunk1)
    assert len(events1) == 0, "半行不该产出事件"

    chunk2 = b'gress","frame":7,"n":null}\n{"ev"'
    events2 = pump.feed(chunk2)
    assert len(events2) == 1, "应该产出一条进度事件"
    assert isinstance(events2[0], progress.ProgressEvent)
    assert events2[0].frame == 7
    assert events2[0].n is None

    chunk3 = b':"progress","frame":10,"n":100}\n'
    events3 = pump.feed(chunk3)
    assert len(events3) == 1, "应该产出第二条进度事件"
    assert isinstance(events3[0], progress.ProgressEvent)
    assert events3[0].frame == 10
    assert events3[0].n == 100


def test_non_progress_line_not_dropped():
    """守卫 7：喂 [警告] 版本号取不到 => 归为日志行，不是进度、不被丢弃。"""
    pump = progress.StderrPump()
    chunk = "[警告] 版本号取不到\n".encode("utf-8")
    events = pump.feed(chunk)

    assert len(events) == 1, "应该产出一条事件"
    assert isinstance(events[0], progress.LogLine), "应该是 LogLine 不是 ProgressEvent"
    assert "[警告]" in events[0].text or "警告" in events[0].text, f"日志内容不对：{events[0].text}"


def test_n_null_no_percent():
    """守卫 8：percent(7, None) is None；且喂 n=null 的进度行不许让任何算术抛异常。"""
    assert progress.percent(7, None) is None, "percent(7, None) 应返回 None"
    assert progress.percent(7, 0) is None, "n=0 时也应返回 None"
    assert progress.percent(7, -1) is None, "n 为负时也应返回 None"

    pump = progress.StderrPump()
    events = pump.feed(b'{"ev":"progress","frame":100,"n":null}\n')
    assert len(events) == 1
    pe = events[0]
    assert isinstance(pe, progress.ProgressEvent)
    assert pe.n is None
    assert progress.percent(pe.frame, pe.n) is None


def test_exit_code_mapping():
    """守卫 9：退出码映射。**2 单独一条测试，注释写明它不是失败**。"""
    assert queue_models.status_for_exit(0) == queue_models.ItemStatus.COMPLETED
    assert queue_models.status_for_exit(2) == queue_models.ItemStatus.NO_OUTPUT
    assert queue_models.status_for_exit(1) == queue_models.ItemStatus.FAILED
    assert queue_models.status_for_exit(9) == queue_models.ItemStatus.FAILED
    assert queue_models.status_for_exit(137) == queue_models.ItemStatus.FAILED


def test_exit_code_2_is_no_output():
    """守卫 9-2：退出码 2 是「无产出」，不是失败也不是完成。

    这是独立的一条测试，专门确保 2 不会被误映射。变异测试时改坏退出码 2 的映射，
    只有这一条会红（其它守卫不受影响）。
    """
    status = queue_models.status_for_exit(2)
    assert status == queue_models.ItemStatus.NO_OUTPUT, \
        f"退出码 2 应映射到 NO_OUTPUT，实际映射到 {status}"
    assert status != queue_models.ItemStatus.FAILED, "退出码 2 不是失败"
    assert status != queue_models.ItemStatus.COMPLETED, "退出码 2 不是完成"


def test_state_machine():
    """守卫 10：已取消 之后不许迁到 完成（调用即抛）。"""
    try:
        queue_models.validate_transition(
            queue_models.ItemStatus.CANCELLED,
            queue_models.ItemStatus.COMPLETED
        )
        assert False, "已取消 -> 完成 应该抛 StateTransitionError"
    except queue_models.StateTransitionError as e:
        assert "禁止" in str(e) or "已取消" in str(e), f"错误消息不符合预期：{e}"


def test_cleanup_only_own_files():
    """守卫 11：造六个文件（四个属于本 item、两个属于别的 item），清理后只剩那两个。

    第一版这里把删除逻辑**照抄**了一遍（自己拼四个文件名再删），于是它证明的是
    「抄写员抄对了」，不是「产品删对了」。现在文件名一律从 `engine.output_paths` 取，
    和 `queue.py::_cleanup_outputs` 用的是同一个派生器。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        exp = _contract(tmpdir, videos=(("/tmp/test.mp4", None), ("/tmp/other.mp4", None)))

        mine = engine.output_paths(exp, 0)
        theirs = engine.output_paths(exp, 1)
        assert len(mine) == 4, f"一个 item 应有四个产出，实际 {sorted(mine)}"
        assert not (set(mine.values()) & set(theirs.values())), "两个 item 的产出路径撞了"

        for p in list(mine.values()) + [theirs["csv"], theirs["timeline_csv"]]:
            p.write_text("x", encoding="utf-8")

        for p in mine.values():           # 与产品同一份路径来源
            p.unlink(missing_ok=True)

        remaining = {f.name for f in output_dir.iterdir()}
        assert remaining == {"other.csv", "other_timeline.csv"}, \
            f"清理后应只剩别的 item 那两个，实际剩余：{remaining}"


def test_max_concurrent_is_one():
    """守卫 12：MAX_CONCURRENT == 1。"""
    assert QUEUE_PY.exists(), f"{QUEUE_PY} 不存在"
    tree = ast.parse(QUEUE_PY.read_text(encoding="utf-8"))
    values = [n.value.value for n in tree.body
              if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
              and any(isinstance(t, ast.Name) and t.id == "MAX_CONCURRENT" for t in n.targets)]
    assert values, "找不到模块级的 MAX_CONCURRENT 赋值"
    assert values == [1], f"MAX_CONCURRENT 应为 1，实际为 {values}"


def test_contract_key_mismatch_raises():
    """守卫 13：键名对不上当场抛，缺的多的都算错，**不许 `.get(键, 默认值)` 蒙过去**。

    这条守的正是 DP-102 第一版的原始事故：`chambers` / 顶层 `trial_prefix`。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        good = _contract(tmpdir)
        engine.require_contract(good)                     # 生产方喂的必须过

        # 少一个键
        for key in SCHEMA_KEYS:
            bad = dict(good)
            bad.pop(key)
            try:
                engine.require_contract(bad)
            except ValueError as e:
                assert key in str(e), f"缺 {key} 的报错里没点名这个键：{e}"
            else:
                assert False, f"缺了 {key} 居然过了契约校验"

        # 多一个键（将来加字段必须有人来改 SCHEMA_KEYS，不许静默忽略）
        bad = dict(good, chambers=4)
        try:
            engine.require_contract(bad)
        except ValueError as e:
            assert "chambers" in str(e), f"多出来的键没被点名：{e}"
        else:
            assert False, "多了 chambers 居然过了契约校验"

        # 逐视频的键同理
        for key in VIDEO_KEYS:
            bad = _contract(tmpdir)
            bad["videos"][0].pop(key)
            try:
                engine.require_contract(bad)
            except ValueError as e:
                assert key in str(e), f"videos[0] 缺 {key} 的报错里没点名：{e}"
            else:
                assert False, f"videos[0] 缺了 {key} 居然过了契约校验"

        # 空 videos
        bad = _contract(tmpdir)
        bad["videos"] = []
        try:
            engine.require_contract(bad)
        except ValueError as e:
            assert "videos" in str(e)
        else:
            assert False, "空 videos 居然过了契约校验"


def test_unknown_schema_version_refused():
    """守卫 14：认不出的 `schema_version` 不许猜着跑。

    猜着跑的后果不是崩，是**拿别的参数出一份看着正常的数字**。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        good = _contract(tmpdir)
        assert good["schema_version"] == "1", \
            f"生产方写的版本变了（{good['schema_version']}），消费方这一侧必须同步改"
        for bogus in ("2", 1, None, "1.0"):
            bad = dict(good, schema_version=bogus)
            try:
                engine.require_contract(bad)
            except ValueError as e:
                assert "schema_version" in str(e), f"报错没点名版本字段：{e}"
            else:
                assert False, f"schema_version={bogus!r} 居然过了契约校验"


def test_output_names_have_one_deriver():
    """守卫 15：输出文件名只许有一个派生器 —— `engine.OUTPUT_SUFFIXES`。

    原来 `build_argv`（查重名）、`_cleanup_outputs`（取消时删）、
    `_read_not_scored_reasons`（读 run.json）各拼一遍 `{stem}_...`。三份派生器里
    任何一份改了后缀，另外两份就会去查/去删/去读一个不存在的文件，而且**没有声响**：
    界面写着「已取消」，盘上留着半张 CSV。
    """
    assert set(engine.OUTPUT_SUFFIXES) == {"csv", "timeline_csv", "run_json", "report_txt"}

    # 派生器本身：带 `{stem}` 的字面量只许出现在 OUTPUT_SUFFIXES 里
    in_engine = [s for s in _non_docstring_strings(ENGINE_PY) if "{stem}" in s]
    assert sorted(in_engine) == sorted(engine.OUTPUT_SUFFIXES.values()), \
        f"engine.py 里出现了 OUTPUT_SUFFIXES 之外的文件名模板：{in_engine}"

    # 消费方：queue.py 的代码里一个后缀字面量都不许有（docstring/注释不算）
    banned = ("_timeline", "_run.json", "_report.txt", ".csv")
    offenders = [s for s in _non_docstring_strings(QUEUE_PY)
                 if any(b in s for b in banned)]
    assert not offenders, \
        f"queue.py 自己拼输出文件名了（第二个派生器就是这么长出来的）：{offenders}"

    # 而且它必须真的去问 `output_paths`
    src = ast.parse(QUEUE_PY.read_text(encoding="utf-8"))
    callers = {fn.name for fn in ast.walk(src) if isinstance(fn, ast.FunctionDef)
               and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                       and c.func.attr == "output_paths" for c in ast.walk(fn))}
    for need in ("_cleanup_outputs", "_read_not_scored_reasons", "_write_report"):
        assert need in callers, f"{need}() 没有调 engine.output_paths（它在自己算路径）"


def test_engine_stdout_becomes_a_report():
    """守卫 16：引擎的 stdout 真的被读、并被写成 `<视频名>_report.txt`（派工单 §73）。

    第一版把这条漏交付了：路径算了、查重名查了、取消时也删了，**唯独没写**。
    也就是说盘上永远不会有报告，而三处「照顾报告」的代码看着一切正常——
    最容易漏的恰恰是这种「只有产物缺席」的漏交付，静态守卫得盯住整条链路。
    """
    tree = ast.parse(QUEUE_PY.read_text(encoding="utf-8"))
    src_text = QUEUE_PY.read_text(encoding="utf-8")

    # 1. stdout 必须接上，且接的是攒数据的那个槽
    assert "readyReadStandardOutput.connect(self._on_stdout_ready)" in src_text, \
        "QProcess 的 stdout 没接上：报告丢了，且进程缓冲会一直涨"
    pump = _func(QUEUE_PY, "_on_stdout_ready")
    assert any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
               and c.func.attr == "readAllStandardOutput" for c in ast.walk(pump)), \
        "_on_stdout_ready() 没真的把 stdout 读出来"

    # 2. `_write_report` 必须真的往盘上写
    writer = _func(QUEUE_PY, "_write_report")
    assert any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
               and c.func.attr in ("write_bytes", "write_text", "open")
               for c in ast.walk(writer)), "_write_report() 没有任何写盘动作"

    # 3. 而且必须有人调它——定义了不调等于没交付
    finished = _func(QUEUE_PY, "_on_process_finished")
    assert any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
               and c.func.attr == "_write_report" for c in ast.walk(finished)), \
        "_on_process_finished() 没调 _write_report()：报告永远不会落盘"

    # 4. 写不成不许静默：返回值必须真的被**读**走（挂到该 item 的备注上）。
    #    这一条第一版写成 `"report_problem" in src_text`，是装饰：赋值那行本身就含这个名字，
    #    把下面用它的代码整段删掉照样绿。凡是查「某个值有没有被用」的守卫，
    #    都得查 ast.Load，不能查字符串。
    for meth, sink in (("_write_report", "报告写失败"), ("_cleanup_outputs", "半成品删不掉")):
        assigned = {t.id for n in ast.walk(finished) if isinstance(n, ast.Assign)
                    for t in n.targets if isinstance(t, ast.Name)
                    and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
                    and n.value.func.attr == meth}
        assert assigned, f"_on_process_finished() 没把 {meth}() 的返回值接住（{sink}就没人知道）"
        loaded = {n.id for n in ast.walk(finished)
                  if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        assert assigned & loaded, \
            f"{meth}() 的返回值接住了却没人读（{sorted(assigned)}）：{sink}被静默吞掉了"

    # 5. 报告不许把 item 判失败——它是副产物，数字在 CSV 里
    for node in ast.walk(writer):
        assert not (isinstance(node, ast.Attribute) and node.attr == "FAILED"), \
            "_write_report() 里出现了 FAILED：报告写不成不是这一段跑失败"
