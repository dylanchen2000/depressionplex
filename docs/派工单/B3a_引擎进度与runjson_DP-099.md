# 派工单 B3a：引擎侧进度回调 + `--progress-json` + `--run-json`（DP-099）

> 出单人：Capy（架构师）｜2026-09-13｜台账 `docs/ISSUES.md` DP-099
> 依据：`docs/SPEC_产品化总体架构_v1.md` §3.5（数据契约）
> **分支（写死，不许自己改名）**：`feat/dp099-engine-progress-runjson`

---

## 1. 背景（为什么有这一单）

桌面外壳与分析引擎之间是**进程边界**：外壳起 `cli/analyze.py` 的子进程，
读它写出的文件和它 stderr 上的进度行，**永远不 import 引擎**（架构文件 §3.4）。

这个模式现在缺两件东西，这一单就是补这两件：

| 缺什么 | 现状证据 | 后果 |
|---|---|---|
| 进度 | `cli/analyze.py` 全程只在**结束时**打印；`runner.segment_series`（`runner.py:147`）的扫帧循环里没有任何回调 | 一段 6 min 素材整段静默，队列页只能无限转圈 |
| 未产出数字的隔间 | `--csv` 按设计只写有报告的隔间（`analyze.py:118-125`），`skipped` 只出现在 stdout 的人读文本里 | **「排除态隔间只产报警行」这条外壳完全看不见** ⇒ 结果页会让那些隔间凭空消失 |

## 2. 要做什么（三件，都是**纯增量**）

### 2.1 `progress=` 关键字（`depressionplex/runner.py`）

给下面三个函数各加一个**只放在关键字位置**的参数 `progress: Callable[[int, int], None] | None = None`：

| 函数 | 行为 |
|---|---|
| `segment_series(frames, plan, *, progress=None)` | 扫帧循环里每处理完一帧调用 `progress(已处理帧数, 总帧数或 0)`。总帧数未知时传 `0`（`frames` 是 `Iterable`，不许为了拿总数把它读进内存） |
| `analyze_frames(..., progress=None)` | 原样透传给 `segment_series` |
| `analyze_video(..., progress=None)` | 透传，并把总帧数填成 `info.n_frames` |

**硬约束**：
- `progress=None` 时的行为必须与改动前**逐位相同**（不许因为加了 `if progress:` 就改变任何计算）。
- 不许在引擎里做节流、格式化、写文件、写 stderr —— 引擎只管**调**，节流和格式化在 2.2。
- 不许让回调影响控制流：回调抛异常就让它往上抛（**不许 `try: … except: pass` 吞掉**，
  静默兜底是本仓明令禁止的模式，见 DP-052/DP-054）。回调的返回值一律忽略（**不做协作式取消**）。

### 2.2 `--progress-json`（`depressionplex/cli/analyze.py`）

加一个 `--progress-json` 开关（`action="store_true"`，默认关）。开启时把进度写成
**NDJSON 到 stderr**（一行一个 JSON 对象，行尾 `\n`，写完立刻 flush）：

```json
{"ev":"progress","frame":1234,"n":9000}
```

- **必须写 stderr，不许写 stdout。** stdout 是人读报告，B3 的验收要拿它与 CLI 直跑逐位比对。
- **节流：至多约 1 行/秒**（用 `time.monotonic()` 判，不要用「每 N 帧」——不同素材 fps 差很多）。
  第一帧和最后一帧**必须各出一行**（否则外壳不知道跑起来了、也不知道扫帧结束）。
- 关闭时（默认）**一个字节都不许多写**。

### 2.3 `--run-json <path>`（`depressionplex/cli/analyze.py`）

写一份 JSON，**只装 CSV 故意不含的东西**。字段如下（键名照抄，不许自己改）：

```
schema_version   固定 "1"
tool_version     取 pyproject 里的版本号；取不到就填 "unknown"（**不许瞎编**）
assay            "TST" / "FST"
scoring_window_s [起, 止]，**从 trial_report.ASSAY_WINDOWS 取**，不许写字面量
video            {path(绝对), name, fps, n_frames, frame_count_source, width, height, duration_s}
calib_indices    标定抽帧下标（整数数组）
chambers         每个隔间一项：{index, col_range, width, corridor:{col_range,band_range,bl_est,sealed}|null,
                 suspension:[x,y]|null, source}
plan_warnings    plan.warnings 的字符串数组
chamber_validity 每项 {chamber, status, occupied_fraction, unsegmentable_fraction, note}
not_scored       **未产出数字的隔间**：每项 {chamber, reason}（reason 就是 runner 给的那句原文，不许改写）
```

**硬约束**：
- **不许出现任何 CSV 里已有的数字**（`immobility_s` / `mobility_s` / `scorable_frames` /
  `window_frames` / `unknown_frames_window` / `scored` / `gate_messages` 等一律不进 `run.json`）。
  一个数字两个序列化器 = 迟早漂移，本仓吃过这个教训（DP-054）。
  **必须有一个测试直接踩这条**：断言 `run.json` 递归展开后的所有键名与 `CSV_FIELDS` 的交集
  只允许 `assay` 与 `fps`（这两个是标识不是指标）。
- `not_scored` 为空时也要有这个键（写 `[]`），**不许省略键** —— 省略会让下游分不清
  「没有排除态隔间」和「这版没实现这个字段」。
- `numpy` 类型不能直接 `json.dump`：转成 Python `int/float`。
- 不给 `--run-json` 时不写任何文件。

## 3. 输入（绝对路径，我已确认存在）

| 用途 | 路径 |
|---|---|
| 仓 | `/home/node/a0/workspace/2315a51b-85c4-46a5-bffe-146b458fd5b6/workspace/depressionplex` |
| 要改的两个文件 | `depressionplex/runner.py`（259 行）、`depressionplex/cli/analyze.py`（147 行） |
| 契约来源 | `docs/SPEC_产品化总体架构_v1.md` §3.5 |
| 测试入口 | `run_tests.py`（**自带 runner，不是 pytest**） |
| 现成的无素材夹具 | `tests/` 里已有若干直接喂 `analyze_frames` 的用例，`grep -rn "analyze_frames" tests/` 找 |

## 4. 怎么验收（缺一条即退回）

1. `python3 run_tests.py` 全绿，且**测试数只增不减**（当前 284；新测试要注册进 `run_tests.py` 的
   `TEST_MODULES`，否则元守卫 `test_run_tests_registry` 会红）。
2. 新测试至少覆盖这五条，**每条都要能独立失败**：
   - 同一批合成帧，`progress=None` 与 `progress=<收集器>` 跑出的报告**逐位相同**；
   - 收集器收到的最后一次调用的第一个参数 == 帧数；
   - 回调抛异常时**异常会往上抛**（证明没被吞）；
   - `run.json` 的键与 `CSV_FIELDS` 的交集只有 `assay` / `fps`（2.3 那条硬约束）；
   - 存在未产出数字的隔间时，`run.json` 的 `not_scored` 里有它和原因原文。
3. 不带任何新参数跑一次 CLI，stdout 与改动前**逐位相同**（自己用 `git stash` 对照一次，把
   两次输出 `diff` 的结果贴进交付报告）。

## 5. 不许做什么

1. **不许改任何验收门常量、阈值、`ASSAY_WINDOWS`、`CSV_FIELDS`**（改一个字都算越界）。
2. **不许改 `analyze_video` 的既有语义**——尤其 fps 与帧数一律从文件读，**不许新增任何让调用方
   传 fps / 帧数的参数**（`runner.py:246-250` 写明了理由）。
3. **不许把进度写 stdout**，不许改任何既有 print 的文字。
4. **不许在引擎里 `try/except: pass`** 吞任何异常。
5. **不许做协作式取消**（不看回调返回值、不加 `cancel_event` 参数）。取消由外壳杀进程实现。
6. 不许动 `desktop/`（那是 B1）、不许动 `.github/workflows/`（那是 B0，已交付）。
7. 不许引入新的第三方依赖（`json` / `time` 是标准库，够了）。
8. 不许 `git add -A`；只 add 你真正改的文件。
9. 不许把含 token 的 remote URL 写进任何文件、日志、commit message 或 PR 正文。
10. 不许为了让本单「看起来过了」而放宽判据（例如把「逐位相同」改成「差不多」）。
    **跑不通就照实报，报了不算失败。**

## 6. 怎么交付

1. 在 `feat/dp099-engine-progress-runjson` 上提交（可多次），`git push -u origin <分支>`。
2. 开 PR，标题 `feat(DP-099): 引擎进度回调 + --progress-json + --run-json`，
   正文写清：改了哪几个函数、新测试各测什么、stdout 逐位相同的 diff 结果。
3. 在 `docs/ISSUES.md` **不要**新开台账行（DP-099 已有行，由我维护）。

## 7. 交付报告要回答的五件事

1. 新增/修改的文件清单与行数。
2. `python3 run_tests.py` 的最后两行原文（`通过 N 失败 M`）。
3. 不带新参数时 stdout 与改动前的 `diff` 结果（**逐位相同就说逐位相同，有差异就贴出来**）。
4. 你实际选的节流实现方式，以及第一帧/最后一帧那两行是怎么保证的。
5. **有没有哪一条判据你没做到**（有就直说，写清卡在哪；这一条比前四条重要）。
