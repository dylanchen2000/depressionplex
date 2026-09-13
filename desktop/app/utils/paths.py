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
