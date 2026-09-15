"""跨平台路径：资源定位 + 用户数据目录（源码运行与 PyInstaller 冻结两种情形）。

两条约定，都吃过教训才写死的：

1. **取路径的函数默认不建目录**（`create=False`）。原写法在 getter 里 `mkdir`，
   于是「打印一下诊断信息」也会在客户机 home 下留目录——自检是诊断，不许有副作用。
   真要写文件的地方显式传 `create=True`。
2. **目录名里不放 `&`**。`Gene&I` 在 Windows 上是合法目录名，但 `cmd` 把 `&` 当命令
   分隔符，支持脚本里一条 `cd %LOCALAPPDATA%\\Gene&I\\...` 会当场断成两条命令；
   Inno Setup 与 .bat 同理。所以磁盘上用 `GeneI`（界面上照常写 Gene&I）。
"""

import os
import sys
from pathlib import Path

APP_NAME = "DEPRESSION-PLEX"
ORG_DIR = "GeneI"          # 磁盘用名，见上文第 2 条
APP_DIR_POSIX = "depression-plex"

#: 随包标定文件的文件名。**全仓只许这一处出现这个字面量**，由
#: `tests/test_badge_view.py::test_calibration_filename_literal_in_exactly_one_file`
#: 盯着。今天引它的只有启动自检（`main_window.py`）；签发脚本与安装脚本还不存在，
#: 它们落地时也必须引这个常量而不是自己再写一遍——**文档里的承诺没有守卫就是假承诺**，
#: 所以这句话配了守卫才敢写。
CALIBRATION_FILENAME = "calibration.json"

#: 随包中文字体（DP-110）。文件名与包内目录名放在这里、不放在 `export_pdf.py`：
#: 它们是**路径契约**——`packaging/build_windows.spec` 的 `datas` 目标目录必须跟
#: `BUNDLED_FONT_SUBDIR` 一字不差，由
#: `tests/test_report_model.py::test_bundled_font_in_installer_spec` 双向对账。
#: `export_pdf.py` 只负责「哪个 family 名能渲中文」，不负责路径。
BUNDLED_FONT_FILENAME = "NotoSansSC-Regular.otf"
BUNDLED_FONT_SUBDIR = "fonts"


def is_frozen() -> bool:
    """是不是 PyInstaller 冻结后的可执行文件。"""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_path(relative_path: str) -> Path:
    """随包资源的绝对路径，`relative_path` 相对 `desktop/`。

    冻结后落在 `sys._MEIPASS`，所以 **B10 的 spec 必须把资源放到包根的同名相对位置**
    （例如 `app/styles/dark.qss` ⇒ `datas=[("desktop/app/styles/dark.qss", "app/styles")]`）。
    对不上时自检会因「皮肤缺失」返回 2，而不是安静地用默认灰皮。
    """
    if is_frozen():
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent.parent.parent   # desktop/
    return base / relative_path


def bundled_calibration_path() -> Path:
    """随包标定文件应当在的位置。**返回路径，不判断存在**。

    存不存在由 `services/calibration.py` 判——那里「该有却没有」和「不该有却有」
    是两种不同的结论（都红，但原因不同），在这里提前判会把它们合并成一种。

    **冻结时用 `sys.executable` 的目录，不用 `resource_path`（`sys._MEIPASS`）。**
    这不是随便挑的：`_MEIPASS` 在 onefile 下是每次启动都重建的临时目录，
    one-folder 下指向 `_internal/`。标定文件是**资质凭据**，要跟 exe 一起被签名、
    被安装脚本放在用户看得见、能被 IT 审计到的位置（`{app}\\calibration.json`），
    而不是藏在一个临时目录里让人以为它不存在。
    """
    if is_frozen():
        return Path(sys.executable).parent / CALIBRATION_FILENAME
    # 源码运行：仓根（本文件在 desktop/app/utils/ 下，上溯三级）
    return Path(__file__).resolve().parents[3] / CALIBRATION_FILENAME


def bundled_font_dir() -> Path:
    """随包中文字体所在目录。**故意不走 `resource_path()`**，理由见下。

    这份资源在源码树里不在 `desktop/` 下：由 `packaging/fetch_font.py` 下载到仓根的
    `vendor/fonts/`（8.3 MB 二进制不进 git），冻结后由 spec 的 `datas` 放到包根的
    `fonts/`。`resource_path()` 的基准是 `desktop/`，表达不了「源码树在仓根、包内在包根」
    这种两侧不同名的资源——硬套就要写 `resource_path("../vendor/fonts")`，
    那在冻结后会指到 `_MEIPASS` 的**外面**去，而且源码模式下碰巧是对的，
    正好是最难查的那种错。

    PyInstaller 内部 API 仍然只有本模块这一个入口点（架构 §3.4），由
    `tests/test_acq_check.py::test_no_frozen_internals_in_desktop` 盯着。
    """
    if is_frozen():
        return Path(sys._MEIPASS) / BUNDLED_FONT_SUBDIR
    # 源码运行：仓根 vendor/fonts（跑一次 `python3 packaging/fetch_font.py` 落地）
    return Path(__file__).resolve().parents[3] / "vendor" / BUNDLED_FONT_SUBDIR


def _ensure(path: Path, create: bool) -> Path:
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _local_app_data() -> Path:
    """Windows 的 `%LOCALAPPDATA%`；取不到才退回 home 拼路径。

    直接拼 `home/AppData/Local` 在被重定向的漫游配置（域账号、企业镜像盘）下是错的。
    """
    env = os.environ.get("LOCALAPPDATA")
    return Path(env) if env else Path.home() / "AppData" / "Local"


def user_data_dir(*, create: bool = False) -> Path:
    """用户数据目录（配置、队列状态、审计）。"""
    if sys.platform == "win32":
        base = _local_app_data() / ORG_DIR / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path.home() / ".local" / "share" / APP_DIR_POSIX
    return _ensure(base, create)


def user_log_dir(*, create: bool = False) -> Path:
    """日志目录。"""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs" / APP_NAME
    else:
        base = user_data_dir() / "logs"
    return _ensure(base, create)


def user_cache_dir(*, create: bool = False) -> Path:
    """缓存目录（可被清空而不影响结果，**不许往这里放唯一副本**）。"""
    if sys.platform == "win32":
        base = _local_app_data() / ORG_DIR / APP_NAME / "Cache"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / APP_NAME
    else:
        base = Path.home() / ".cache" / APP_DIR_POSIX
    return _ensure(base, create)
