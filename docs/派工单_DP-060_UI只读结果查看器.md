# 派工单：UI 只读结果查看器（DP-060）

> 收件人：Capy 内 **grok-4.6** 执行 agent
> 派工方：Capy（架构师）｜日期 2026-09-07
> 依据：`docs/WORKFLOW.md` §8（七段缺一不发）

---

## 一、目标一句话 + issue

**做一个单文件网页，把 27 个试次的「软件不动时长 vs 人工不动时长」摆在一张表和两张图上，供内部看偏差。对应 `DP-060`。**

**这是只读查看器。它一个数都不许自己算**——所有口径已经在 `scripts/dp059_freeze_run_snapshot.py` 里算完并冻进 CSV（DP-059）。你的活是**画**，不是**算**。

---

## 二、哪台机器、哪个目录、哪条分支

| 项 | 值 |
|---|---|
| 机器 | MacBook Pro（经 Capy Bridge，Bridge ID `br_d659951397dfba30ba6b9227`） |
| 目录 | **`/Users/dylanchen2000/Work/dp-ui-wt`**（已由派工方建好的 git worktree） |
| 分支 | **`feat/ui-results-viewer`**（已建好、已 checkout、upstream 故意没设） |
| 基底 | `origin/main` @ `3d2892b` |

**分支名派工方给死，不许自己起别的名字，不许从别的分支切。**

三条关于目录的硬话：

1. **不许动 `/Users/dylanchen2000/Work/depression抑郁绝望/depressionplex`**（那是主工作区，派工方正在里面跑一个一小时的探针，你 `git checkout` 一下就把它掀了）。你只在 `~/Work/dp-ui-wt` 里干活。
2. 这两个目录是**同一个仓库的两个 worktree**，所以你 `git push` 出去的东西派工方能直接看到，不用来回拷。
3. **`git branch --unset-upstream` 是故意的**：如果留着上游指向 `origin/main`，一次 `git push` 就直接推到 main 了。第一次推必须写全 `git push -u origin feat/ui-results-viewer`。

### Bridge 怎么用（如果你的 session 里没有 Bridge，跳到本节末）

```python
import sys; sys.path.insert(0, '/tmp')
from br import call
CWD = "/Users/dylanchen2000/Work/dp-ui-wt"
call('terminal/exec', {'command': 'git status --short', 'cwd': CWD}, timeout=120)
call('files/read',  {'path': CWD + '/data/frozen/README.md'})
call('files/write', {'path': CWD + '/ui/results_viewer/index.html', 'content': '...'})
```

三条实测出来的坑：

- **`files/write` 的父目录必须先存在**，否则报「文件不存在」。先 `mkdir -p ui/results_viewer`。
- **zsh 会吃掉没引号的通配符和方括号**。稍微复杂的脚本一律 base64 传过去落地成文件再执行，别用 heredoc 硬塞进 RPC。
- **macOS 没有 `timeout` / `gtimeout`**，别写。

**如果你的 session 里没有 `/tmp/br.py`**：把做好的文件交到共享工作区（`./outputs/ui_results_viewer/`），并在回报里明确写「Bridge 不可用，文件已交到 outputs，请派工方落地」。**不要自己另起一个仓库，也不要把文件贴在聊天里。**

---

## 三、输入文件（绝对路径，存在性派工方已确认）

| 路径 | 是什么 |
|---|---|
| `/Users/dylanchen2000/Work/dp-ui-wt/data/frozen/DP-059_软件人工对比_2026-09-07.csv` | **主输入**，27 行，逐试次软件↔人工 |
| `/Users/dylanchen2000/Work/dp-ui-wt/data/frozen/README.md` | 口径说明，**必读**，界面上要引用它的三条告示 |
| `/Users/dylanchen2000/Work/dp-ui-wt/data/frozen/DP-059_软件全量快照_DP-053.csv` | 备用，软件端原始 27 行（v1 不必用） |

主输入的表头（**照这个写，不要猜列名**）：

```
trial_id,assay,validity_status,scored,software_immobility_s,software_immobility_raw_s,
software_mobility_s,software_mobility_bouts,unknown_frames_window,unknown_fraction_window,
n_human_scorers,human_immobility_mean_s,human_immobility_min_s,human_immobility_max_s,
human_range_s,human_scorers,human_playback_rates,bias_s,abs_bias_s,g8_pass,note
```

真实两行，抄来给你对照：

```
10mg_2周-ch1,TST,valid,True,143.56,153.4,216.44,16,1200,0.1333,2,216.10,193.04,239.17,46.13,王娟|陈璇,0.5,-72.54,72.54,False,
10mg_2周-ch2,TST,valid,True,240.92,246.68,119.08,25,2539,0.2821,2,238.14,237.70,238.58,0.88,王娟|陈璇,0.5,2.78,2.78,True,
```

**注意 `human_scorers` 列里有竖线 `|` 作分隔符**，别把它当 CSV 分隔符。

---

## 四、步骤，每步都有可机器判定的产出

### 步骤 1：目录与文件骨架

新建 **只有这两个文件**：

```
ui/results_viewer/index.html        ← 单文件网页，全部逻辑都在里面
ui/results_viewer/README.md         ← 怎么打开、看什么、已知限制
```

允许再加**一个**假数据夹具，用于离线开发：`ui/results_viewer/fixture_假数据.csv`，`trial_id` 必须明显是假的（例如 `合成-ch1`），并在文件第一行注释写「假数据，不许当结果读」。

**判定**：`git status --short` 只列这两到三个路径。

### 步骤 2：数据加载（两条路，都要有）

1. **文件选择 / 拖拽**：用户把 `DP-059_软件人工对比_2026-09-07.csv` 拖进页面即可。**这是主路径**，因为 `file://` 下 `fetch()` 读本地文件会被浏览器拦掉。
2. **URL 参数**：`?csv=../../data/frozen/DP-059_软件人工对比_2026-09-07.csv`，在 `python3 -m http.server` 下能直接加载。

**没加载到数据时，页面必须明说「还没有数据，请拖入 CSV」，不许显示一张空表让人误以为 27 个试次全是空的。**

**判定**：拖入 CSV 后表格出现 27 行；不给数据时看到那句提示。

### 步骤 3：表格

- 27 行全列出，列可点击排序（升/降切换）
- 筛选：`validity_status`、`g8_pass`（是/否/空）、有无人工评分、`trial_id` 关键字搜索
- 当前筛选下的**行数计数**（这是唯一允许你算的东西）
- **`abs_bias_s` 超过 17.7 的行要有视觉标记**（G8 门槛，从 CSV 的 `g8_pass` 列读，**不要自己比大小**）

**判定**：默认 27 行；按 `abs_bias_s` 降序时第一行是 `10mg_2周-ch1`（72.54）。

### 步骤 4：空值（这一步最容易出事，单独列）

CSV 里的空值有两种含义，**都不是 0**：

| 情况 | 表现 | 界面必须显示 |
|---|---|---|
| 软件没产出数字 | 偏差三列为空 | `—`，鼠标悬停显示 `note` |
| 没有人工评分 | 人工六列为空 | `—`，鼠标悬停显示 `note` |

三条硬规矩：

1. **空值一律渲染成 `—`，绝不许渲染成 `0`、`0.00`、`NaN`、`null`、`undefined`。**
2. **空值不许参与排序时当 0 用**：排序时空值恒定排在末尾（升序降序都在末尾）。
3. **空值不许参与任何计数以外的运算，也不许画到图上。**

> 为什么单独写一节：这条在项目里编号 DP-032，是已经出过事的那一类。`0 秒不动` 和 `没测到` 在数据上长得一模一样，但一个是「这只鼠一直在动」，另一个是「我们不知道」。混掉一次就能把结论拉歪。
>
> 27 行里**正好有 1 行**是这种：`20mg_3周-ch4`，唯一一只从没被任何人评过的鼠。**它就是你的检验样本。**
>
> **改正记录（DP-060-R，2026-09-07）**：本单初版把这行写成了 `30mg_2周-ch4`——**写错了**。`30mg_2周-ch4` 不在这份 CSV 里，它是「没有切片、待按结构柱几何重切」那一条（T-2 前置），和「从没被任何人评过」是两件事，被派工方混成了一件。执行方发现后停下来问，处置正确；**不许自己改名闷头干**——就算猜对了，也就取消了「派工单是判定依据」这件事本身。
>
> 同一次核对还发现**原判定本身验不出来**：`20mg_3周-ch4` 是**整行都空**（`validity=unknown`、`scored=False`、`unknown_fraction=1.0000`，软件六列、人工六列、`bias_s`、`g8_pass` 全空）。而「软件有数 × 人工空」这种半空行，27 行里**一行都没有**（派工方逐行核过）⇒ 空值渲染的**另一半**在真数据上无从检验。

**判定（这条要写进 PR 正文），改正后一共两条**：

1. **真数据**：贴出 `20mg_3周-ch4` 那一行的渲染结果——**软件列、人工列、`bias_s`、`g8_pass` 全部是 `—`**（`g8_pass` 尤其不许渲染成「不合格」，它是空，不是 False）；`note` 列原样显示「无人工评分 ⇒ 无法比对（不是 0，是没有）」；页面任何位置不得用 `0` 代替 `—`；这一行**不得进散点图与 Bland–Altman**（第三条硬规矩）。
2. **构造样本**（因为真数据没有半空行）：自己写一份 1–2 行的 CSV，软件列有数、人工列留空，放 `ui/results_viewer/tests/fixtures/`，贴渲染结果。**这份构造 CSV 绝不许放进 `data/frozen/`，也不许出现在任何非 tests 目录**——它是测试夹具，不是数据。

### 步骤 5：两张图

1. **散点图**：x = `human_immobility_mean_s`，y = `software_immobility_s`，画一条 `y = x` 参考线
2. **Bland–Altman**：x = 两者均值，y = `bias_s`（**直接读 CSV 的 `bias_s` 列，不许自己减**）

用原生 `<canvas>` 或内联 `<svg>` 手画。**不许引任何图表库、不许引 CDN。**

图上或图旁必须写明**画了几行、漏了几行、为什么漏**，例如「27 行中 26 行可画；1 行无人工评分，未画」。**静默少画一个点是不允许的。**

**判定**：散点图上 26 个点；页面上能看到那句「26 / 27」的说明。

### 步骤 6：告示条（不许省，不许改字）

页面顶部固定一条告示，把 `data/frozen/README.md` 的三条搬上去：

1. **这不是真值，是快照。** 软件那一列是 DP-053 落地时那版代码跑的，代码一改就旧。
2. **人工那一列也不是金标准。** 是两位评分员 **T2 档**的平均，两人极差中位 19.33 s、最大 66.79 s。**T1 精标才是定门锚点。**
3. **`bias_s` 是「软件 − 人工平均」，不是误差。** 谁错还没归因（DP-055 仍开着）。

**判定**：这三句在页面上，一字不少。

### 步骤 7：详情面板

点一行 → 侧栏或弹层，把该行**所有列原样列出**（列名 + 值），空值同样显示 `—`。

**判定**：点 `20mg_3周-ch4` 能看到 21 列全在（其中大部分是 `—`，这正是要看的：列**在**、值**空**，不是列消失）。

### 步骤 8：`ui/results_viewer/README.md`

写清：怎么打开（两条路径）、每一列什么意思（照 `data/frozen/README.md` 的口径表，不要自己重新解释）、**已知限制**（v1 没有逐秒时间线，因为软件端还没导出逐秒数据；那是派工方的活）。

---

## 五、不许做什么

1. **不许改 `ui/` 以外的任何文件。** 具体点名：`depressionplex/`、`tests/`、`scripts/`、`docs/`、`data/`、`tools/`、`run_tests.py`、`.gitignore`——**一个字都不许改**。
2. **不许自己算口径。** 不许自己算 bias、不许自己平均多个评分员、不许自己算 r 或 ICC、不许自己判 `g8_pass`。全部读 CSV 现成的列。
3. **不许改任何阈值或门槛常量**（17.7、0.818、0.0175 之类）。你只是显示它们。
4. **不许引 CDN、不许 `npm install`、不许 `pip install`、不许任何网络请求。** 页面在断网、`file://` 下必须能用。不许引网络字体。
5. **不许把真实 CSV 复制进 `ui/`。** 出现第二份数据就出现第二份真相。用拖拽加载。
6. **不许 `git add -A`。** 按路径列。
7. **不许提交视频、掩膜、模型权重。**
8. **不许用 emoji**（全局规范）。界面中文。
9. **发现数字看不懂或像是错的 ⇒ 停下来问派工方，不许自己「修正」数据、不许在 UI 里补算一个「更合理」的值。** 失败归因这一类不外派。
10. **不许合自己的 PR。** 开完等 review。

---

## 六、验收命令（都要跑，输出贴进 PR）

```bash
cd ~/Work/dp-ui-wt

# 1) 测试必须全绿（你没改 Python，应当原样通过）
python3 run_tests.py                      # 期望：通过 218 失败 0

# 2) 不许有任何网络引用（**必须无输出**）
grep -nE 'https?://|//cdn|<script[^>]+src=|<link[^>]+href="http' ui/results_viewer/index.html

# 3) 改动面必须只在 ui/ 下
git diff --name-only origin/main...HEAD    # 期望：只有 ui/results_viewer/ 下的文件

# 4) 目检
python3 -m http.server 8000
# 浏览器打开 http://localhost:8000/ui/results_viewer/?csv=../../data/frozen/DP-059_软件人工对比_2026-09-07.csv
```

第 2 条**必须无输出**。有输出就是引了外部资源，退回重做。

---

## 七、交付即落地（照抄，不许省）

```bash
cd ~/Work/dp-ui-wt
python3 run_tests.py                       # 必须全绿，不绿就不 commit
git add ui/results_viewer/index.html ui/results_viewer/README.md
# 若有夹具再加：git add ui/results_viewer/fixture_假数据.csv
git commit -m "feat(DP-060): 只读结果查看器 v1，软件↔人工逐试次对比"
git push -u origin feat/ui-results-viewer
gh pr create --repo dylanchen2000/depressionplex --base main \
  --head feat/ui-results-viewer --title "feat(DP-060): 只读结果查看器 v1" \
  --body-file /tmp/pr_body_dp060.md
bash scripts/closeout_check.sh             # 必须 exit 0
```

`gh` 只在 Mac 上有（token 不出机器），所以这几条必须**通过 Bridge 在 Mac 上执行**，不要在沙箱里跑。

### 回报必须给四项，缺一项视为未交付

1. commit sha
2. 分支名
3. PR 链接
4. 测试通过 / 失败数

**外加两项本单特有的证据**：

5. 第六节第 2 条 `grep` 的输出（必须为空）
6. `20mg_3周-ch4` 那一行的渲染结果（软件列/人工列/`bias_s`/`g8_pass` 全为 `—` 而不是 `0`或「不合格」），**外加**构造样本那一行（软件有数、人工空）的渲染结果

---

## 八、为什么这活值得单开一条支线（给执行者的背景，读了会做得更准）

软件现在**总量对得上、逐个试次对不上**：平均偏差只有 +1.74 s，但逐试次平均绝对偏差 **35.15 s**，26 个里只有 **5 个**达标。这不是"整体偏高"，是每个试次各自往两边偏——一个 -72.5，一个 +68.5，加起来抵消了。

派工方现在全靠在终端里读数字找规律，效率很低。**你做的这张表和这两张图，是用来看「偏差长什么样」的工具**：是随机散开，还是跟某个变量（unknown 占比、评分员、播放倍速、隔间位置）成系统关系。所以：

- **`unknown_fraction_window`、`human_range_s`、`human_scorers`、`human_playback_rates` 这几列要能排序、能筛**——它们是找规律的抓手，不是装饰。
- **一个点都不许漏、一个空值都不许填 0。** 你要是把那 1 行无人工评分的填成 0，散点图左下角会多出一个假点，而那个假点恰好会把相关系数往上抬——**这正是我们要防的那种错**。
