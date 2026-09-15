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
        # 目标目录**不带 desktop/ 前缀**：resource_path() 的相对基准就是 desktop/
        # （冻结后换成 sys._MEIPASS），所以 desktop/app/styles/dark.qss 的包内位置
        # 必须是 app/styles/。写成 "desktop/app/styles" 时冻结后查不到，
        # 自检打「皮肤缺失」返回 2 —— run 34930864774 第 13 步实测就是这个日志，
        # 只是那一步当时吞了退出码，连着三次假的「构建成功」。守卫 9 盯这条对账。
        ("../desktop/app/styles/dark.qss", "app/styles"),
        ("../desktop/app/styles/colors.py", "app/styles"),
        # 随包中文字体（DP-110）。offscreen 平台插件在 Windows 上枚举到 0 个系统字体
        # （实测 run 34932358712），随包 + addApplicationFont 是唯一普适的机制。
        # 目标目录名 fonts 必须与 export_pdf.BUNDLED_FONT_SUBDIR 一致。
        # 先跑 python3 packaging/fetch_font.py 把这两个文件拉到位，否则 PyInstaller 报错。
        ("../vendor/fonts/NotoSansSC-Regular.otf", "fonts"),
        # OFL 1.1 正文：再分发必须带许可（法律边界，不是可选项）
        ("../vendor/fonts/LICENSE-NotoSansSC.txt", "fonts"),
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
