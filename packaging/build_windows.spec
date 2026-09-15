# -*- mode: python ; coding: utf-8 -*-
"""GUI (desktop/) 的 PyInstaller spec。

架构 §3.4 三重封堵第二重：`excludes=["depressionplex"]`。
GUI 不 import 引擎，只起子进程。谁偷偷 import 了，冻出来的 GUI 一运行就炸。
"""

block_cipher = None

# 仓根（desktop/ 的上一级）
import sys
from pathlib import Path
repo_root = Path(".").resolve()

a = Analysis(
    ["../desktop/main.py"],
    pathex=[str(repo_root)],
    binaries=[],
    datas=[
        # VI 深色皮肤（已于 2026-09-13 搬入本仓）
        ("../desktop/app/styles/dark.qss", "desktop/app/styles"),
        ("../desktop/app/styles/colors.py", "desktop/app/styles"),
    ],
    hiddenimports=[
        "desktop.app",
        "desktop.app.main_window",
        "desktop.app.pages",
        "desktop.app.services",
        "desktop.app.services.engine",
        "desktop.app.models",
        "desktop.app.models.experiment",
        "desktop.app.styles",
        "desktop.app.utils",
        "desktop.app.utils.paths",
        "desktop.app.widgets",
        "desktop.app.workers",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["depressionplex"],  # 三重封堵第二重：GUI 不打包引擎
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DEPRESSION-PLEX",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # CI 无 upx；有/无 upx 产出不同二进制；UPX 压 Qt DLL 已知启动崩/杀软误报
    console=False,  # 不要控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,  # 同上：不用 UPX
    upx_exclude=[],
    name="DEPRESSION-PLEX",
)
