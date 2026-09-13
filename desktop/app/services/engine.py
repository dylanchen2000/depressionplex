"""引擎命令解析与 argv 拼装（纯函数，不依赖 PySide6）。

架构 §3.4：GUI 与引擎之间是进程边界。本模块只管「怎么找到引擎可执行文件」和
「怎么拼出完整命令行」，不起进程、不解析输出——那些在 QueuePage 里。
"""

import os
import shlex
import sys
from pathlib import Path


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
    if "videos" not in exp or video_index >= len(exp["videos"]):
        raise ValueError(f"video_index {video_index} 越界或 videos 字段不存在")

    video = exp["videos"][video_index]
    video_path = Path(video["path"])
    output_dir = Path(exp["output_dir"])

    # 视频名（去扩展名）
    video_stem = video_path.stem

    # 五个输出路径
    csv_path = output_dir / f"{video_stem}.csv"
    timeline_csv_path = output_dir / f"{video_stem}_timeline.csv"
    run_json_path = output_dir / f"{video_stem}_run.json"
    report_path = output_dir / f"{video_stem}_report.txt"

    # 检查是否已存在（不许覆盖）
    for p in [csv_path, timeline_csv_path, run_json_path, report_path]:
        if p.exists():
            raise FileExistsError(
                f"输出文件已存在，不许覆盖：{p}。"
                f"请删除或移走已有文件，或更改输出目录。"
            )

    # 拼装参数
    argv = engine_command()
    argv.append(str(video_path.resolve()))
    argv.extend(["--assay", exp["assay"]])
    argv.extend(["--chambers", str(exp.get("chambers", 4))])
    argv.extend(["--csv", str(csv_path.resolve())])
    argv.extend(["--timeline-csv", str(timeline_csv_path.resolve())])
    argv.extend(["--run-json", str(run_json_path.resolve())])
    argv.append("--progress-json")

    # 可选参数（为 null 的整个参数不传）
    if exp.get("trial_prefix") is not None:
        argv.extend(["--trial-prefix", exp["trial_prefix"]])

    if exp.get("calib_frames") is not None:
        argv.extend(["--calib-frames", str(exp["calib_frames"])])

    if exp.get("body_area_prior") is not None:
        argv.extend(["--body-area-prior", str(exp["body_area_prior"])])

    return argv
