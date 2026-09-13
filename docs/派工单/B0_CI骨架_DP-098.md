# 派工单 B0：CI 骨架（DP-098）

> 出单人：Capy（架构师）。2026-09-13。规程见 `docs/WORKFLOW.md` §8，七段齐全，缺一段不许发。
> **本单是所有外派件的第一件**，理由见 `docs/SPEC_产品化总体架构_v1.md` §6.0：
> 沙箱装不了 PySide6（pip 自身 I/O 故障）、无显示、本仓一个 CI 都没有
> ⇒ **在 CI 存在之前，桌面外壳的任何产出都无法验收。**

## 1. 目标（一句话）

给 `depressionplex` 建两个 GitHub Actions workflow —— 一个跑引擎的 284 项测试，
一个证明 **PySide6 6.7.3 在 ubuntu(offscreen) 与 windows-latest 上装得上、能无显示起
`QApplication`** —— 并把「CI 文件不许被悄悄删掉」做成一条守卫测试。对应 **DP-098**。

**先读懂这一句再动手**：本单**不写任何桌面外壳代码**。`desktop/` 目录属于 B1，
本单只负责把「验收工具」造出来。所以第二个 workflow 在 `desktop/main.py` 还不存在时
必须**优雅跳过**那一步，而不是失败。

## 2. 机器 / 目录 / 分支

| 项 | 值 |
|---|---|
| 机器 | 本沙箱（就是你现在这台） |
| 目录 | `/home/node/a0/workspace/2315a51b-85c4-46a5-bffe-146b458fd5b6/workspace/depressionplex` |
| 分支 | **`ci/dp098-actions-skeleton`**，从 `origin/main` 起。**分支名派工方给死，不许自己起名** |
| 基点 | `git fetch origin && git checkout -b ci/dp098-actions-skeleton origin/main` |

`origin` 远端**已带凭据**，`git push -u origin ci/dp098-actions-skeleton` 直接可用。
**不许把 remote URL（含 token）打印进任何文件、日志、commit message 或 PR 正文。**

## 3. 输入文件（绝对路径，存在性我已逐一确认过）

| 路径 | 用途 |
|---|---|
| `…/workspace/depressionplex/run_tests.py` | 引擎测试入口。`TEST_MODULES` 是元组，末项是元守卫 `test_run_tests_registry` |
| `…/workspace/depressionplex/scripts/closeout_check.sh` | 收工守卫，必须 exit 0 |
| `…/workspace/depressionplex/docs/WORKFLOW.md` | §8 派工规范、§1.1 叠加 PR 的坑 |
| `…/workspace/depressionplex/docs/SPEC_产品化总体架构_v1.md` | §6.0 为什么先做 CI；§3 第二条铁律（无显示可跑） |
| `…/workspace/depressionplex/pyproject.toml` | 核心依赖只有 `numpy>=1.24`。**不要改它** |
| `~/.git-credentials` | 里面的 token 用于调 GitHub REST API（`gh` CLI 在本沙箱不可用：`~/.config` 是个文件） |

**已核过的环境事实，不用你再查**：沙箱无 `ffmpeg`/`ffprobe`，而 284 项测试照样全绿
⇒ **CI 上不需要装 ffmpeg**，只需要 numpy。沙箱 numpy 2.4.6，`pyproject` 只约束 `>=1.24`。

## 4. 步骤（每步都有可机器判定的产出）

### 步骤 0（先做，可能直接把本单变成阻塞报告）

我已推了一个探针 workflow 到 `tmp/ci-scope-probe`，运行 ID **34737273006**。
已核实：PAT 有 `workflow` scope（push 成功）、本仓 Actions `enabled: true / allowed_actions: all`、
姊妹仓 `drugeffectscan` 2026-09-08 的 `windows-latest` 任务 success 且秒级起跑。
**但这个探针排队 5 分钟以上没起跑，原因未确认**（账号是 free plan，私有仓分钟数可能已耗尽，
但查账单的 API 已 410 迁移，我查不到）。

你要做的：

```bash
TOK=$(sed -n 's|https://\([^:]*\):\([^@]*\)@github.com|\2|p' ~/.git-credentials | head -1)
# 轮询该 run，最多 15 分钟，每 45 s 一次，打出 status / conclusion
```

**产出**：一张 `时间 / status / conclusion` 的表，以及最终判定二选一：

- **A：起跑了**（`status` 变 `in_progress` 或 `completed`）⇒ 继续步骤 1–4。
- **B：15 分钟仍 `queued`** ⇒ **步骤 1–3 照做**（写 workflow 是廉价的，且是唯一能复用的产出），
  但**不许声称验收通过**。回报里必须写明「Actions 未起跑，两个 workflow 的 run URL 与状态如下（逐个列出）」。
  **这不是你的失败，是执行者不可用**，我会把它升成道俊的裁决项。

**严禁**：为了让本单「看起来过了」而给 workflow 加 `continue-on-error`、`|| true`、
或者把验收判据改成「YAML 语法正确」。**没起跑就是没验收**，照实报。

### 步骤 1 `.github/workflows/tests.yml`

- 触发：`on: [push, pull_request]`，不加分支过滤（我们靠分支验收，不是只靠 main）
- 单个 job，`runs-on: ubuntu-latest`，`actions/checkout@v4` + `actions/setup-python@v5`（3.11）
- 装依赖：只装 numpy，用 `pip install "numpy>=1.24"`。**不许装 pytest、cv2、scipy、torch**
- 跑：`python3 run_tests.py`
- **必须让非零退出码传出去**（`run_tests.py` 失败时返回非 0，别用 `set +e` 或管道吞掉）

**可机器判定的产出**：该 workflow 的一次运行日志里出现 `通过 284  失败 0`
（若你在步骤 3 加了守卫测试，则是 **285**），且 run 的 `conclusion == "success"`。

### 步骤 2 `.github/workflows/desktop-selftest.yml`

- 触发同上
- `strategy.matrix.os: [ubuntu-latest, windows-latest]`，`fail-fast: false`
- 装：`pip install PySide6==6.7.3`（版本写死，与 `drugeffectscan` 同版，踩过的坑不重踩）
- 环境：`QT_QPA_PLATFORM: offscreen`（两个 OS 都设）
- **第一步（现在就有意义的那一步）**：跑一段内联 Python，打印 `PySide6.__version__`、
  构造一个 `QApplication`、立刻 `quit()`、退出码 0。这一步证明**运行器 + 安装链 + 无显示启动**都通。
- **第二步（为 B1 预留）**：仅当 `desktop/main.py` 存在时才跑
  `python -m desktop.main --self-test`（或 `python desktop/main.py --self-test`，与 B1 约定一致）。
  用 `if: hashFiles('desktop/main.py') != ''` 做条件，**文件不存在时这一步跳过，不是失败**。
  在 job 末尾打印一行明确的 `desktop/ 尚不存在，已跳过自检（B1 未交付）`，免得以后有人把「跳过」读成「通过」。

**可机器判定的产出**：两个 OS 的 job 都 `success`，日志里都出现 `PySide6 6.7.3` 与
`QApplication offscreen OK`（这两个字符串你自己在内联脚本里打）。

### 步骤 3 守卫测试 `tests/test_ci_workflows.py`

纯文本检查，不需要 PySide6、不需要网络。至少四条：

1. `.github/workflows/tests.yml` 存在，且内容里出现 `run_tests.py`
2. `.github/workflows/desktop-selftest.yml` 存在，且 matrix 里同时出现 `ubuntu-latest` 与 `windows-latest`
3. 两个 workflow 里**都不出现** `continue-on-error` 与 `|| true`
   （理由：这两样是「绿灯造假」最常见的两种写法，一旦混进来，CI 就从验收工具退化成装饰）
4. `desktop-selftest.yml` 里出现 `QT_QPA_PLATFORM` 且值为 `offscreen`

然后把 `"test_ci_workflows"` 加进 `run_tests.py` 的 `TEST_MODULES`
（**不加会被元守卫 `test_run_tests_registry` 直接判红**，那是它的设计）。

### 步骤 4 顺手删掉我的探针分支

```bash
git push origin --delete tmp/ci-scope-probe
```

**产出**：`git ls-remote --heads origin tmp/ci-scope-probe` 无输出。
（探针的 `probe.yml` 只在那条分支上，从没进 main，所以删分支就是删干净。）

## 5. 不许做什么

1. **不许改 `depressionplex/` 包里任何一行代码**（`assay_core/`、`runner.py`、`cli/`、`csi/` 全部）。本单是纯 CI。
2. **不许改任何阈值、验收门常量、冻结值**（θ_mob、G1–G11 的数字、`ASSAY_WINDOWS`）。
   这些是「验收门」类，WORKFLOW §8 明写**一律不派**。
3. **不许改 `pyproject.toml`** 的依赖约束（包括不许把 numpy 钉成某个确切版本）。
4. **不许写任何 `desktop/` 下的文件**——那是 B1，不是本单。写了就是越界，会被整单退回。
5. **不许 `git add -A`**，必须逐条列路径。
6. **不许在 workflow 里加 `continue-on-error` / `|| true` / `if: always()` 之类掩盖失败的写法。**
7. **不许把 `python3 run_tests.py` 换成 `pytest`**（沙箱 pytest 所在 site-packages 有 I/O 故障，
   自带 runner 是刻意选择，见 `run_tests.py` 文档串）。
8. **不许提交 `data/` 下的任何文件**，也不许提交任何视频、音频、大图。
9. **不许把含 token 的 remote URL 写进文件、日志、commit message 或 PR 正文。**
10. **不许在回报里用「应该能过」「预计绿」这类措辞。** 只报 API 返回的 `conclusion` 字符串原文。
    查不到就写「未确认」——`docs/../CLAUDE.md` 的事实回答铁律：**查不到 ≠ 没有**。

## 6. 验收命令

```bash
python3 run_tests.py        # 必须全绿；加了步骤 3 的守卫后应为 285 通过 / 0 失败
```

外加两条只能靠 API 判的：`tests.yml` 与 `desktop-selftest.yml` 各自最近一次 run 的
`conclusion` 字符串（**原文粘贴**，允许是 `null`/`queued`——那就是步骤 0 的分支 B）。

## 7. 交付即落地（照抄，不许省）

```bash
python3 run_tests.py                      # 必须全绿，不绿就不 commit
git add .github/workflows/tests.yml .github/workflows/desktop-selftest.yml \
        tests/test_ci_workflows.py run_tests.py docs/ISSUES.md
git commit -m "ci(DP-098): 建 CI 骨架，外壳有了唯一的验收执行者"
git push -u origin ci/dp098-actions-skeleton
# gh CLI 在本沙箱不可用（~/.config 是文件），PR 用 REST API 开：
#   POST https://api.github.com/repos/dylanchen2000/depressionplex/pulls
#   Authorization: Bearer <~/.git-credentials 里的 token>
#   {"title":"...","head":"ci/dp098-actions-skeleton","base":"main","body":"..."}
bash scripts/closeout_check.sh             # 必须 exit 0
```

顺手在 `docs/ISSUES.md` 末尾追加一行 DP-098 台账（格式照最后几行）。
**注意 `docs/ISSUES.md` 在多条分支上都在末尾追加行，合并时会有一处平凡冲突，属正常。**

**回报必须给出五项，缺任一项视为未交付**：

1. commit sha
2. 分支名
3. PR 链接
4. `python3 run_tests.py` 的通过/失败数（原文那一行）
5. 两个 workflow 各自最近一次 run 的 **URL + `conclusion` 字符串原文**
