# -*- mode: python ; coding: utf-8 -*-
"""分析后端 (depressionplex/cli 子命令分发器) 的 PyInstaller spec。

入口是 analyzer_entry.py（专职薄壳），它调用 depressionplex.cli.main()。
不许用 __init__.py 当入口：PyInstaller 会把入口当 __main__ 执行，__package__ 为空，
里面的 `from . import X` 在冻结后必然 ImportError（DP-108 run 34826444142 实测）。

架构 §3.4 三重封堵第三重：`excludes=["PySide6"]`。
后端不需要 PySide6，装出来体积才不会翻倍。
"""

block_cipher = None

# 仓根。必须用 SPECPATH（本 spec 所在目录 = packaging/）的 parent，
# 不能用 Path(".").resolve()：workflow 的 cwd 就是 packaging/，那样 pathex
# 会指到 packaging/ 自己，hiddenimport depressionplex.cli 全部 not found，
# 冻结 exe `from depressionplex.cli import main` 失败，--help 直接 rc=1
# （run 34920623069 实测）。
import sys
from pathlib import Path
repo_root = Path(SPECPATH).resolve().parent

a = Analysis(
    ["analyzer_entry.py"],
    pathex=[str(repo_root)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "depressionplex",
        "depressionplex.assay_core",
        "depressionplex.cli",
        "depressionplex.cli._stdio",
        "depressionplex.runner",
        "depressionplex.video",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6"],  # 后端不需要 GUI 库
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
    name="depression-analyzer",  # 必须与 engine.ENGINE_STEM 相同（守卫 1 验证）
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # CI 无 upx；有/无 upx 产出不同二进制；UPX 压 Qt DLL 已知启动崩/杀软误报
    console=True,  # 后端是 CLI，需要控制台
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
    name="depression-analyzer",  # 文件夹名也要与 ENGINE_STEM 一致
)
