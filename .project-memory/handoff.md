# DepressionPlex 交接摘要

> **快照，不是日志。** 每次收工覆盖写。状态细节看 `docs/STATUS.md`，逐项台账看 `docs/ISSUES.md`。
> 本轮：**Account B**，2026-09-15。

## 本轮做完了什么

接手 A 账号停在半路的桌面端产品线（四个 PR 全部 `CONFLICTING`、一份复核意见随 A 的沙箱丢了、
一个 PR 从来没被复核过），**把 #98 → #97 → #96 → #95 四个 PR 全部合进 main**，
M2 第一次真的产出可安装、能导出中文 PDF 的 Windows 安装包。

`main` = `710203e`。三条工作流在它上面全绿：
`Tests 34940934911` / `Desktop Self-Test 34940934935` / `Build Windows Installer 34941011458`。
artifact `DEPRESSION-PLEX-Setup-1.0.0`，102,578,102 字节。

## 本轮的关键决策（会影响后面的人怎么干）

| 决策 | 为什么 |
|---|---|
| **中文字体随包带 Noto Sans SC（OFL）** | 用户拍板。实测：windows-latest offscreen 下系统 family 枚举 **0** 个，而 `addApplicationFont()` 照样注册成功 ⇒ 随包是**唯一**在各种情形下都成立的机制，不是「兜底」 |
| **字体报错必须区分三种原因** | 「一个字体都没枚举到」和「这台机器没有中文字体」是两件事，说成一件就是最难查的那类失败。第三种原因两个 runner 上都造不出来，只有单测，已在 workflow 注释里写明 |
| **A 账号丢掉的 `B7_复核意见3_DP-109.md` 按「重新做一遍」处理** | 只知道它有 R1–R5、R5 是合入门槛，内容不可恢复。假装续写等于编造 |
| **A 的 DP-108 C4 原判错了**：`desktop/app/utils/stdio.py` 与 `depressionplex/cli/_stdio.py` **不许合成一份** | 那是进程边界两侧各一份（架构 §3.4，外壳不许 import `depressionplex`） |
| **run.json 的 `decoder` 用嵌套形状**（按工具 path/source/version + `mixed_source`） | 扁平只有一个 `source` 键，表达不了两工具来源不同 |
| **C13 不是缺陷，且不开新号** | 标定状态只有 `main_window.py:65` 一个判定点，各页从 `window().calibration_status` 取结论。当时说要开的 DP-118 已被别的事占号 |
| **本机测试判据换成 3.11** | Mac 的 `python3` 是 3.9.6，`desktop/` 的 PEP 604 在它上面造 52 条假红 |

## 下一步（谁接手都从这里开始）

1. **DP-120**：自检页接真子进程，argv 在 `engine.py` 里拼。
2. 批 B 派工（徐乐彤 8 場自盲重评，09-15 之后、新 seed）。
3. 标注工具 v1.7 四条修正 —— **派工前一次做对，不好让标注员返工**。
4. 三条要用户拍板的：DP-040/DP-076 四份重复 TST 重评算哪一份、缺那份 13 个 Motion 值互不相同的 `.SET`、CSI 空杯 99.923% 怎么进 G10。

## 常用命令

```bash
# 本机全量测试（必须用 3.11 的 venv，Mac 自带 3.9 会造 52 条假红）
/tmp/dpx311/bin/python run_tests.py        # 期望：通过 573 失败 0

# 单跑某几条守卫（tests 不是包）
/tmp/dpx311/bin/python -c "import sys; sys.path[:0]=['.','tests']; \
import importlib; m=importlib.import_module('test_packaging_contract'); m.<守卫名>()"

# 手动触发 Windows 构建
gh workflow run build-windows.yml --ref main
gh run list --workflow build-windows.yml --limit 3

# 变异证明的写法见 docs/派工单/B6_复核意见4_DP-110.md §4：
# try/finally 还原 + 逐字节校验，一条永不还原的变异会架空它后面所有守卫
```

## 环境坑（踩过的）

- Bridge 上 `.git` 在 worktree 里是**文件**不是目录 ⇒ commit message 用 `git commit -F <file>`。
- Bridge 走 zsh：`--include=*.py` 要加引号，裸 `==` 会被当命令，`cat -A` 在 macOS 上不支持。
- 沙箱单次命令 120 s 上限 ⇒ 长命令 `nohup … > /tmp/log` 再 grep。
- 沙箱**没有 PySide6** ⇒ `desktop/app/pages/*.py` 本地只能 AST 解析，**页面能否构造只认 `desktop-selftest` CI**。
