# `incoming/` —— 已保全、尚未入库的人工评分导出

## 这个目录是干什么的

这里的文件是**评分员交回的原始导出**，一个字节都没改过。它们原来散落在两个
**git 看不见**的地方：

| 原位置 | 为什么 git 看不见 |
|---|---|
| `data/`（仓库根下的 data 目录） | `.gitignore` 里 `data/*` 把整层挡掉，只放行 `data/human_scores/` 和 `data/frozen/` |
| `~/Downloads/` | 根本不在仓库里 |

也就是说这些**不可再生的人工真值**此前只存在于一台机器的一个目录里，
`git status` 也看不到它们。**本目录的唯一目的就是先把它们保住**（DP-088）。

## 和 `raw/` 的区别（重要）

| | `raw/` | `incoming/`（本目录） |
|---|---|---|
| 是否进分析基线 | **是**，`build_table()` 读它 | **否**，任何代码都不读 |
| 是否已核过口径/重复/窗口 | 是 | **否** |

**入库是 DP-082 的事，不是本次的事。** 入库会把 `n_trials == 57` 这条基线挪动，
必须单独一个 PR、带着新数字和理由做。本次只做保全，**零行为变化**。

## 已知问题（入库前必须由道俊裁决，不许 agent 自己挑）

1. **TST 09-07 有 4 份重复导出**：`..._partial4of27.json` 和
   `..._partial4of27 (1).json`（张咸明那份括号前有空格，徐乐彤那份没有——
   浏览器重复下载的产物）。**哪一份算数是 DP-040 / DP-076 的未决项。**
   本目录**两份都留**，原名不动，不许合并、不许删。
2. **FST 09-07 那批（`*_2026-09-07_*`）读数不可用**，理由见 DP-085：
   28 条记录里 11 条不动占比 <3%，换后 4 分钟窗口仍 10 条。
   **不作废、不删**，归"旧口径"档，只用于验证口径修正的效果（道俊 2026-09-08：
   原语真值那批数据绝对不能放弃）。
3. **所有 `mobile_seconds` 都虚高**：每按键多约 0.115 秒（DP-086）。
   分析要按 `holds` 取并集自己重算，**不要采信 `mobile_seconds`**。
   `.csv` 里没有 `holds`，所以 CSV 只能当核对用，不能当数据源。

## 清单

| 文件 | 原位置 | 字节 | sha256(前16) | records |
|---|---|---|---|---|
| `human_scores_FST_张咸明_2026-09-08_partial8of28_023111Z.csv` | `~/Downloads/` | 733 | `b843f895e341a2ca` |  |
| `human_scores_FST_徐乐彤_2026-09-08_partial8of28_023656Z.csv` | `~/Downloads/` | 734 | `9a9d961887fe99a5` |  |
| `human_scores_FST_陈璇_2026-09-08_partial8of28_030748Z.csv` | `~/Downloads/` | 707 | `d78d28301a03da3a` |  |
| `human_scores_TST_张咸明_2026-09-08_partial4of27_034944Z.csv` | `~/Downloads/` | 451 | `32158f7a5c19b145` |  |
| `human_scores_TST_徐乐彤_2026-09-08_partial4of27_041551Z.csv` | `~/Downloads/` | 449 | `e4f9eee82b2e80f0` |  |
| `timer_audit_FST_张咸明_2026-09-08_partial8of28_023111Z.json` | `~/Downloads/` | 18696 | `2a580fc2e725c021` | 8 |
| `timer_audit_FST_徐乐彤_2026-09-08_partial8of28_023656Z.json` | `~/Downloads/` | 15442 | `a3580407ad2b8705` | 8 |
| `timer_audit_FST_陈璇_2026-09-08_partial8of28_030748Z.json` | `~/Downloads/` | 17329 | `e1def64e6522e959` | 8 |
| `timer_audit_TST_张咸明_2026-09-08_partial4of27_034944Z.json` | `~/Downloads/` | 14966 | `ae0e8c466868d22c` | 4 |
| `timer_audit_TST_徐乐彤_2026-09-08_partial4of27_041551Z.json` | `~/Downloads/` | 12729 | `0d4c7cf0d35409f9` | 4 |
| `human_scores_FST_张咸明_2026-09-07_partial12of28.csv` | `data/` | 1004 | `d7cfa6e61fb981c3` |  |
| `human_scores_FST_徐乐彤_2026-09-07_partial12of28.csv` | `data/` | 1005 | `651be6c70c42b906` |  |
| `human_scores_FST_陈璇_2026-09-07_partial16of28.csv` | `data/` | 1301 | `be65dd3a45f07378` |  |
| `human_scores_张咸明_2026-09-07_partial4of27 (1).csv` | `data/` | 405 | `b04fe54cae397585` |  |
| `human_scores_张咸明_2026-09-07_partial4of27.csv` | `data/` | 368 | `056f8b35451624b4` |  |
| `human_scores_徐乐彤_2026-09-07_partial4of27(1).csv` | `data/` | 404 | `ea4c265d620b08b9` |  |
| `human_scores_徐乐彤_2026-09-07_partial4of27.csv` | `data/` | 367 | `17855a8e9e04a894` |  |
| `timer_audit_FST_张咸明_2026-09-07_partial12of28.json` | `data/` | 27355 | `f39f1fe6d069dab8` | 12 |
| `timer_audit_FST_徐乐彤_2026-09-07_partial12of28.json` | `data/` | 23398 | `bb3937befec0214c` | 12 |
| `timer_audit_FST_陈璇_2026-09-07_partial16of28.json` | `data/` | 30756 | `a1745188c0785e0b` | 16 |
| `timer_audit_张咸明_2026-09-07_partial4of27 (1).json` | `data/` | 13464 | `8aa830ed9779b1b0` | 4 |
| `timer_audit_张咸明_2026-09-07_partial4of27.json` | `data/` | 12244 | `9b9ad0cd47e6dbd7` | 4 |
| `timer_audit_徐乐彤_2026-09-07_partial4of27(1).json` | `data/` | 11176 | `8100746df307b338` | 4 |
| `timer_audit_徐乐彤_2026-09-07_partial4of27.json` | `data/` | 14263 | `02223ed306ef1686` | 4 |

共 **24** 个文件。sha256 记在这里，是为了将来任何人都能验证"这份文件没被动过"。

## 规矩

1. **本目录只增不改。** 任何清洗、去重、改名都在下游做，不动这里的原件。
2. 新导出交回来时，**先放进这里并补上表格里的一行**，再考虑入库。
3. 不许把 `.mp4` 等原始录像放进来（`.gitignore` 的扩展名规则在本目录同样生效）。
