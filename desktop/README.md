# DEPRESSION-PLEX Desktop Application

桌面外壳，基于 PySide6 构建的跨平台 GUI。

## 如何运行

### 开发模式

从仓库根目录运行（仓根必须是当前工作目录）：

```bash
python -m desktop.main
```

### 自检模式

无显示环境下的自检（用于 CI 验收）：

```bash
QT_QPA_PLATFORM=offscreen python -m desktop.main --self-test
```

自检会构造所有页面，打印环境信息和启动耗时，退出码 0 表示成功。

## 目录结构

```
desktop/
├── __init__.py              空文件（desktop 必须是真包）
├── main.py                  入口，含 --self-test
├── requirements.txt         依赖清单（仅 PySide6==6.7.3）
├── README.md                本文件
└── app/
    ├── __init__.py
    ├── main_window.py       主窗口 + 左侧导航
    ├── pages/
    │   ├── __init__.py
    │   └── placeholders.py  七个占位页
    ├── styles/              已存在的皮肤（不在本单改动）
    │   ├── dark.qss
    │   └── colors.py
    ├── utils/
    │   ├── __init__.py
    │   └── paths.py         resource_path / user_data_dir / user_log_dir / user_cache_dir
    ├── widgets/
    │   └── __init__.py      本单留空
    └── workers/
        └── __init__.py      本单留空（B3 用）
```

## 本单交付内容（B1，DP-099）

本单交付了可在 GitHub Actions 上验收的外壳骨架：

- 主窗口和侧边栏导航
- 七个占位页（欢迎、新建实验、分析队列、结果、复核、导出、自检）
- 跨平台路径工具（`paths.py`）
- 无显示自检入口（`--self-test`）
- 深色主题皮肤加载（`dark.qss`，由 B0 搬入）

### 占位页说明

除"欢迎"页外，其余六个页面均为占位实现，将由后续派工单交付：

| 页面 | 状态 | 后续归属 |
|------|------|----------|
| 欢迎 | 已实现 | — |
| 新建实验 | **占位** | B2 交付 |
| 分析队列 | **占位** | B3 交付 |
| 结果 | **占位** | B4 交付 |
| 复核 | **占位** | B5 交付 |
| 导出 | **占位** | B6 交付 |
| 自检 | **占位** | B7 交付 |

## 跨平台约束

本外壳遵循以下跨平台约束，保证在 Windows / macOS / Linux 上一致行为：

| # | 约束 | 理由 |
|---|------|------|
| 1 | **资源定位只许走 `resource_path()`**，不许在别处用 `__file__` 拼路径 | 冻结后 `__file__` 指向临时解压目录，只有 `sys._MEIPASS` 可靠 |
| 2 | **写文件只许写 `user_data_dir()` 下面**，不许写安装目录 | Windows 上非管理员用户对 `Program Files` 只读；macOS `.app` 包有签名校验 |
| 3 | **多进程只用 `spawn` 启动**（`multiprocessing.set_start_method('spawn')`） | macOS / Windows 默认 `spawn`；Linux 默认 `fork` 会导致 Qt 状态不一致 |
| 4 | **外部链接只用 `QDesktopServices.openUrl()`**，不许 `os.system` / `subprocess` 调浏览器 | 跨平台默认浏览器路径不统一；`QDesktopServices` 由 Qt 处理平台差异 |
| 5 | **字体只按 family 名引用，必须带 fallback**（如 `font-family: "Microsoft YaHei", "PingFang SC", sans-serif;`） | 不同平台预装字体不同；打包字体 .ttf 会增大安装包体积且有授权风险 |
| 6 | **所有文件路径用 `pathlib.Path`**，不用字符串拼接 | Windows 用反斜杠，Linux/macOS 用正斜杠；`Path` 自动处理分隔符 |
| 7 | **不许 import 本仓分析包**（`depressionplex` / `assay_core`），见架构 `SPEC_产品化总体架构_v1.md` §3.4 | 外壳与引擎之间是**进程边界**：GUI 一个 exe，分析后端另一个 exe。外壳只起子进程 + 读文件，不直接调用分析代码 |

## 开发注意事项

### 导入形式

包内一律**绝对导入** `from desktop.app.… import …`，不许出现：
- 相对导入跨层（如 `from ..app import …`）
- `sys.path.insert(...)` 魔法
- 顶层 `app` 包（本仓根下已有 `depressionplex` 包，避免命名冲突）

### 依赖约束

外壳**只许依赖 PySide6**，不许引入：
- onnxruntime / opencv / torch / scipy（属于分析引擎，不属于外壳）
- 任何除 PySide6 外的第三方库（除非后续派工单明确许可）

这些约束由守卫测试 `tests/test_desktop_boundary.py` 机械检查。

## 验收

本单的验收判据（缺一条即退回）：

1. **GitHub Actions 上 `desktop-selftest.yml` 转绿**，ubuntu(offscreen) 与 windows-latest 两个 job 都绿，且日志里能看到 `SELF-TEST OK pages=7 total_ms=…`
2. 自检报告里七个页面全部出现，总启动耗时 < 2000 ms
3. `python3 run_tests.py` 全绿且总数 = 原有 + 新增的
4. 守卫测试的四条规则（进程边界 / 依赖白名单 / 禁 sys.path / 入口对齐 CI）每条都验证过能独立失败
