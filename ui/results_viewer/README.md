# DP-060 只读结果查看器（ui/results_viewer）

把 DP-059 冻结的 27 个试次「软件不动时长 ↔ 人工不动时长」对比表画出来看偏差的内部工具。
**只读查看器：页面一个口径数字都不自己算**，所有均值、偏差、G8 判定全部直接读 CSV 现成的列。
页面唯一做的计算是筛选后的行数计数，以及画图必需的坐标换算。

## 怎么打开（两条路径）

### 路径一（主路径）：直接打开 + 拖入 CSV

1. 双击 `index.html`（`file://` 直接打开即可，断网可用）。
2. 把 `data/frozen/DP-059_软件人工对比_2026-09-07.csv` 拖到页面任意位置（或点「选择 CSV 文件」）。

`file://` 下浏览器禁止 `fetch()` 读本地文件，所以拖拽是主路径。

### 路径二：`http.server` + URL 参数

```bash
cd /Users/dylanchen2000/Work/dp-ui-wt   # 仓库根目录
python3 -m http.server 8000
```

浏览器打开：

```
http://localhost:8000/ui/results_viewer/?csv=../../data/frozen/DP-059_软件人工对比_2026-09-07.csv
```

页面会自动加载该 CSV。

## 看什么

- **顶部告示条**：摘自 `data/frozen/README.md` 的三条告示（快照非真值、人工非金标准、bias 非误差）。
- **表格**：27 行全列出；点列表头排序（升/降切换，空值恒排末尾）；支持有效性、G8 达标、
  有无人工评分、`trial_id` 关键字筛选；`g8_pass=False` 的行标红（读自 CSV 的 `g8_pass` 列）。
- **散点图**：x = `human_immobility_mean_s`，y = `software_immobility_s`，带 `y = x` 参考线。
- **Bland–Altman 图**：x = 两者均值（绘图坐标），y = `bias_s`（直接读 CSV 列），带 `y = 0` 参考线。
  图注写明画了几行、漏了几行、为什么漏。
- **详情面板**：点任意一行（或图上任意一点），右侧滑出该试次 21 列原样值。

## 每一列是什么意思（口径照抄 `data/frozen/README.md`，不重新解释）

| 列 | 含义 |
|---|---|
| `trial_id` | 试次编号（剂量组-隔间） |
| `assay` | 实验类型（本批均为 TST） |
| `validity_status` | 试次有效性（valid / unknown） |
| `scored` | 软件是否产出评分 |
| `software_immobility_s` | 软件不动时长（秒），DP-053 落地时那版代码跑的快照 |
| `software_immobility_raw_s` | 软件不动时长原始值（秒） |
| `software_mobility_s` | 软件可动时长（秒） |
| `software_mobility_bouts` | 软件判的可动回合数 |
| `unknown_frames_window` | 窗口内未知帧数 |
| `unknown_fraction_window` | `unknown_frames_window / window_frames` |
| `n_human_scorers` | 人工评分员人数 |
| `human_immobility_mean_s` | 人工不动时长均值（秒）：`status == accepted` 行的 `immobility_s` 算术平均（T2 档） |
| `human_immobility_min_s` / `human_immobility_max_s` | 人工评分的最小 / 最大值（秒） |
| `human_range_s` | max − min，**评分员之间的分歧**，不是置信区间 |
| `human_scorers` | 评分员名单，竖线分隔 |
| `human_playback_rates` | 评分时播放倍速，竖线分隔 |
| `bias_s` | `software_immobility_s − human_immobility_mean_s`（软件 − 人工平均），**不是误差**，谁错未归因（DP-055） |
| `abs_bias_s` | 偏差绝对值（秒） |
| `g8_pass` | `abs(bias_s) <= 17.7`（G8 门槛，DP-047 定）；本页只读此列，不自行判定 |
| `note` | 备注（空值原因等） |

## 空值规矩（DP-032，出过事的那一类）

- 空值一律渲染为「—」，**绝不渲染成 0**；悬停显示该行的 `note`。
- 排序时空值恒排末尾（升序降序都在末尾）。
- 空值不参与任何计数以外的运算，也不画到图上；图注会交代哪几行没画、为什么。
- 本批 27 行中有 1 行（`20mg_3周-ch4`）软件未产出且无人工评分，全表唯一，用于检验空值渲染。

## 已知限制

- **v1 没有逐秒时间线**：软件端尚未导出逐秒数据（那是派工方的活，不在本单范围）。
- 两张图基于**加载文件的全部行**绘制，不随表格筛选联动；表格筛选只影响表格。
- 页面不持久化任何状态，刷新后需重新拖入 CSV（或用 `?csv=` 参数）。

## 开发夹具

`fixture_假数据.csv` 是离线开发用的合成数据（`trial_id` 均为 `合成-chN`），**不是结果，不许当结果读**。
