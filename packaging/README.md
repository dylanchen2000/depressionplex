# DEPRESSION-PLEX Windows 打包

## 本机复现构建（Windows）

**前提条件**：
- Windows 10/11 x64
- Python 3.11
- PyInstaller
- Inno Setup 6（安装后 `iscc` 在 PATH 里）

**步骤**：

```bash
# 1. 安装依赖
pip install PySide6==6.7.3 pyinstaller numpy>=1.24

# 2. 下载并校验 LGPL 版 ffmpeg
python packaging/fetch_ffmpeg.py

# 3. 构建 GUI（one-folder）
cd packaging
pyinstaller build_windows.spec

# 4. 构建后端（one-folder）
pyinstaller build_analyzer_windows.spec

# 5. 读取版本号
python -c "import sys; sys.path.insert(0, '..'); from depressionplex.cli.analyze import _version_from_pyproject; import pathlib; print(_version_from_pyproject(pathlib.Path('../pyproject.toml').read_text()))"

# 6. 生成安装包（替换 x.y.z 为实际版本号）
iscc /DAppVersion=x.y.z installer.iss

# 产物：DEPRESSION-PLEX-Setup-x.y.z.exe
```

## 产物结构树

安装后的目录树（到第二层）：

```
C:\Program Files\DEPRESSION-PLEX\
├── DEPRESSION-PLEX.exe           # GUI 主程序
├── backend\
│   ├── depression-analyzer.exe   # 分析后端（与 engine.ENGINE_STEM 一致）
│   ├── *.pyd, *.dll              # numpy 等依赖（one-folder）
│   └── ffmpeg\
│       ├── ffmpeg.exe
│       ├── ffprobe.exe
│       ├── LICENSE.txt           # ffmpeg LGPL 许可
│       ├── COPYING.LGPLv2.1.txt
│       └── COPYING.LGPLv3.txt
├── *.pyd, *.dll                  # PySide6 等依赖（one-folder）
└── desktop\
    └── app\
        └── styles\
            ├── dark.qss
            └── colors.py
```

## 已知限制

### 1. 安装包无代码签名

**后果**：Windows SmartScreen 会在首次安装时弹出警告「Windows 已保护你的电脑」，
用户需点击「更多信息」→「仍要运行」才能继续。

**原因**：代码签名证书需要购买（约 $300/年）且需要法人身份验证。研究版优先交付功能，
暂不购买证书。

**缓解**：在交付给客户时附带说明文档，告知这是预期行为，不是病毒。

### 2. ffmpeg 哈希占位

`packaging/fetch_ffmpeg.py` 里的 `FFMPEG_SHA256` 当前是占位符。
在首次真实构建前，需要：

1. 手动下载一次 `FFMPEG_URL` 指向的 zip
2. 计算实际 SHA256（`sha256sum` / `certutil -hashfile`）
3. 替换 `fetch_ffmpeg.py` 里的占位符
4. commit 后再构建

**为什么不预先填**：沙箱里无法下载外部文件，必须在有网络的 CI / 本地构建机上首次运行后回填。

### 3. 杀毒软件误报

**可能后果**：部分杀毒软件（尤其是 Defender）可能将 `depression-analyzer.exe`
标记为可疑，因为它是 PyInstaller 打包的 Python 可执行文件，且会启动子进程、读写文件。

**缓解**：
- 使用 LGPL 版 ffmpeg（不是 GPL 版），避免许可证问题
- 在交付文档里说明产品行为（离线、不监听端口、不联网）
- 如有必要，提交样本给杀毒厂商加白名单

### 4. 本地构建未验证

本单交付时，沙箱里无 Windows / 无 PyInstaller / 无 Inno Setup，**本地构建路径完全未验证**。
真实可用性的验证只有一条：**GitHub Actions 上 `build-windows` 工作流能否产出安装包**。

客户机上「双击可装可跑」这条只有真 Windows 机器能验，**本单未验证**。
