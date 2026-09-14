"""引擎命令解析与 argv 拼装（纯函数，不依赖 PySide6）。

架构 §3.4：GUI 与引擎之间是进程边界。本模块只管「怎么找到引擎可执行文件」和
「怎么拼出完整命令行」，不起进程、不解析输出——那些在 QueuePage 里。
"""

import os
import shlex
import sys
from pathlib import Path

from desktop.app.models.experiment import SCHEMA_KEYS, VIDEO_KEYS

#: 一个 item 的产出文件名。**只有这一处**按视频名派生文件名：
#: 原来 `build_argv`（查重名）与 `QueuePage._cleanup_outputs`（取消时删）各写一遍，
#: 两边任何一边改了后缀，另一边就会去查/去删一个不存在的文件而毫无声响。
OUTPUT_SUFFIXES = {
    "csv": "{stem}.csv",
    "timeline_csv": "{stem}_timeline.csv",
    "run_json": "{stem}_run.json",
    "report_txt": "{stem}_report.txt",     # 引擎 stdout（给人读的报告），由外壳写
}


def require_contract(exp: dict) -> None:
    """按 `experiment.json` 契约校验，键名对不上就当场抛。

    为什么要这么严：DP-102 第一版照着**旗标名**反推键名，把 `n_chambers` 读成
    `chambers`、把逐视频的 `trial_prefix` 读成顶层字段。两处都是 `.get(..., 默认值)`，
    于是用户在向导里选 6 个隔间，引擎照 4 个跑；逐段填的前缀一个都没传进去——
    **不报错、不告警，数字看着还挺正常**。这正是本仓最忌的那种失败。

    多出来的键也算错：将来 schema 加字段必须有人来改这里，不许静默忽略。
    """
    if not isinstance(exp, dict):
        raise ValueError(f"experiment.json 的顶层必须是对象，读到 {type(exp).__name__}")

    version = exp.get("schema_version")
    if version != "1":
        raise ValueError(
            f"不认识的 schema_version {version!r}：本版只读 '1'。"
            "读不懂的契约不许猜着跑——猜错的后果是拿别的参数出一份看着正常的数字。")

    missing = [k for k in SCHEMA_KEYS if k not in exp]
    extra = [k for k in exp if k not in SCHEMA_KEYS]
    if missing or extra:
        raise ValueError(
            f"experiment.json 的键与契约不符：缺 {missing}、多 {extra}。"
            f"契约键清单只有 desktop/app/models/experiment.py::SCHEMA_KEYS 一份")

    if not isinstance(exp["videos"], list) or not exp["videos"]:
        raise ValueError("experiment.json 的 videos 必须是非空列表")

    for i, v in enumerate(exp["videos"]):
        v_missing = [k for k in VIDEO_KEYS if k not in v]
        v_extra = [k for k in v if k not in VIDEO_KEYS]
        if v_missing or v_extra:
            raise ValueError(
                f"videos[{i}] 的键与契约不符：缺 {v_missing}、多 {v_extra}。"
                f"逐视频键清单见 experiment.py::VIDEO_KEYS")


def output_paths(exp: dict, video_index: int) -> dict[str, Path]:
    """一个 item 的四个产出路径（绝对路径）。拼 argv、查重名、取消清理都用这一份。"""
    require_contract(exp)
    if not 0 <= video_index < len(exp["videos"]):
        raise ValueError(f"video_index {video_index} 越界（共 {len(exp['videos'])} 段）")
    stem = Path(exp["videos"][video_index]["path"]).stem
    out = Path(exp["output_dir"])
    return {k: (out / tpl.format(stem=stem)).resolve() for k, tpl in OUTPUT_SUFFIXES.items()}


def engine_command() -> list[str]:
    """引擎调用前缀。冻结与源码两种情形；环境变量优先（给测试与现场排障用）。

    返回一个列表，后续拼上具体参数就是完整的 argv。

    Raises:
        FileNotFoundError: 找不到可执行文件时当场报错并说清找了哪个路径。
    """
    # 环境变量 DPX_ENGINE_CMD 优先（用 shlex.split 解析，支持带空格的路径）
    env_cmd = os.environ.get("DPX_ENGINE_CMD")
    if env_cmd:
        return shlex.split(env_cmd)

    # 判断是否冻结（PyInstaller）
    is_frozen = getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")

    if is_frozen:
        # 冻结后：与主程序同目录的 dp-engine.exe / dp-engine
        main_exe = Path(sys.executable)
        engine_name = "dp-engine.exe" if sys.platform == "win32" else "dp-engine"
        engine_path = main_exe.parent / engine_name

        if not engine_path.exists():
            raise FileNotFoundError(
                f"引擎可执行文件不存在：{engine_path}（冻结模式）。"
                f"预期与主程序 {main_exe} 在同一目录。"
            )
        return [str(engine_path)]

    else:
        # 源码运行：python -m depressionplex.cli.analyze，cwd 必须是仓根
        # 仓根 = desktop/ 的上一级（desktop/app/services/engine.py 在 desktop/ 下三层）
        repo_root = Path(__file__).resolve().parent.parent.parent.parent

        # 检查 depressionplex 包是否存在（简单验证：看 __init__.py）
        pkg_init = repo_root / "depressionplex" / "__init__.py"
        if not pkg_init.exists():
            raise FileNotFoundError(
                f"引擎包不存在：{repo_root / 'depressionplex'}（源码模式）。"
                f"预期仓根为 {repo_root}。"
            )

        return [sys.executable, "-m", "depressionplex.cli.analyze"]


def get_cwd() -> Path | None:
    """返回引擎子进程的 cwd。源码模式下必须是仓根，冻结模式下返回 None（用默认）。"""
    is_frozen = getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
    if is_frozen:
        return None
    else:
        return Path(__file__).resolve().parent.parent.parent.parent


def acq_check_argv(video: str, n_chambers: int) -> list[str]:
    """采集自检的完整 argv。

    引擎路径一律走 engine_command()，argv 只在这一处拼装（与 build_argv 做邻居）。
    退出码语义见 depressionplex/cli/acq_check.py §0.1：
      0 = 测到读数（通过/不通过），2 = 测不出，1 = 解码失败。

    Note: 如果 B10 已合入 main，engine_command() 会已被改过；
    本函数总是用 engine_command()，不自己再拼一次路径。
    """
    # 采集自检走单独的 CLI 模块（不复用 analyze）
    cmd = engine_command()
    # 将最后一个元素（analyze 模块名）替换成 acq_check 模块名
    # 源码模式：engine_command() 返回 [sys.executable, "-m", "depressionplex.cli.analyze"]
    # 冻结模式：engine_command() 返回 [str(engine_path)]，不含 -m
    is_frozen = getattr(__import__("sys"), "frozen", False)
    if is_frozen:
        # 冻结模式：dp-engine 接受 acq-check 子命令（B10 约定），直接传参
        argv = cmd + ["acq-check", "--video", str(video),
                      "--chambers", str(n_chambers), "--json"]
    else:
        # 源码模式：替换模块名
        argv = cmd[:-1] + ["depressionplex.cli.acq_check",
                           "--video", str(video),
                           "--chambers", str(n_chambers), "--json"]
    return argv


def build_argv(exp: dict, video_index: int) -> list[str]:
    """按 experiment.json 契约（DP-101 §2.1）拼一条视频的完整 argv。

    Args:
        exp: experiment.json 的全部内容
        video_index: 视频在 exp["videos"] 列表中的索引（0-based）

    Returns:
        完整的 argv（包含引擎前缀），可直接传给 QProcess.start() 或 subprocess

    Raises:
        FileExistsError: 目标输出文件已存在（不许覆盖）
        ValueError: 必需字段缺失或视频索引越界
    """
    paths = output_paths(exp, video_index)          # 里面已做契约校验
    video = exp["videos"][video_index]
    video_path = Path(video["path"])

    # 检查是否已存在（不许覆盖）。`report_txt` 也在内：它由外壳写引擎的 stdout，
    # 同样是本 item 的产出，留着旧的会让人把上一次的报告当成这一次的。
    for p in paths.values():
        if p.exists():
            raise FileExistsError(
                f"输出文件已存在，不许覆盖：{p}。"
                f"请删除或移走已有文件，或更改输出目录。"
            )

    # 拼装参数。键名一律照契约取，**不许 `.get(键, 默认值)`**：
    # 契约校验已经保证键都在，再写默认值只会在键名拼错时把错误盖住。
    argv = engine_command()
    argv.append(str(video_path.resolve()))
    argv.extend(["--assay", exp["assay"]])
    argv.extend(["--chambers", str(exp["n_chambers"])])
    argv.extend(["--csv", str(paths["csv"])])
    argv.extend(["--timeline-csv", str(paths["timeline_csv"])])
    argv.extend(["--run-json", str(paths["run_json"])])
    argv.append("--progress-json")

    # 可选参数（为 null 的整个参数不传）。
    # `trial_prefix` 是**逐视频**的字段（契约 `videos[i].trial_prefix`），不是顶层字段。
    if video["trial_prefix"] is not None:
        argv.extend(["--trial-prefix", video["trial_prefix"]])

    if exp["calib_frames"] is not None:
        argv.extend(["--calib-frames", str(exp["calib_frames"])])

    if exp["body_area_prior"] is not None:
        argv.extend(["--body-area-prior", str(exp["body_area_prior"])])

    return argv
