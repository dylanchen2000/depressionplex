# Issue 台账（DP-###）

> **2026-09-03 更正**：GitHub remote **一直是通的**（`dylanchen2000/depressionplex`，私有，
> 8-24 已建，8-25 已 push，PR #1–#8 已合并）。沙箱经 HTTPS + `~/.git-credentials` 可读可写；
> 只有 SSH（22 端口 ssh 客户端握手超时）和 `gh` CLI（`~/.config` 不是目录）不可用。
> 本文件继续用作**中文 issue 台账**，与 GitHub PR 并行，不再声称"remote 尚未建立"。规程见 `docs/WORKFLOW.md`。
> 编号只增不复用。状态：`open` / `doing` / `done` / `blocked` / `wontfix`。

| # | 状态 | 归属 | 分支 | 标题 |
|---|---|---|---|---|
| DP-001 | done | Capy | — | 核心骨架：几何语义图 + RAD + CSI bout 流水线 |
| DP-002 | done | Capy | — | S1 阈值分割 + 采集端/噪声底体检，P2 硬门通过 |
| DP-003 | **done** | 道俊 | — | GitHub 私有仓库 `dylanchen2000/depressionplex` 8-24 已建、8-25 已 push。**沙箱经 HTTPS 可 push**（`~/.git-credentials` 有 ghp_ token，repo 全权限）。~~靠 bundle 交付~~ 不再需要 |
| DP-004 | **open（已降级）** | 道俊 | — | **比对口径已由数据自证，不再阻塞**：43 个试次中有 holds 的 39 个，**最大 hold 结束时间无一超过 360.00 s**，且多个恰好等于 `360.00` ⇒ 工具在 360 s 硬收口，评分员实际只看了文件时间 **[0, 360]**。软件按同一区间算即可，`immobility = 360 − mobile` 对这批数据成立。**剩下的是科学问题不是工程问题**：文件时间 0 是否等于"小鼠悬起那一刻"？若录制早于悬挂，则人和软件都把布置期算进去了（不伤 r，但绝对 immobility 与文献不可比）。**只需目检 7 个视频的第 1 帧，几分钟** |
| DP-005 | **blocked** | 道俊 | — | 确认 `30mg_2周-ch4` 为何缺失（28→27）；若为预剔的已知脱落试次须放回，否则 G10 零正样本 |
| DP-006 | open | 工具作者 | — | 秒表工具：hold 按视频时间插入并合并，不追加到数组尾部（4 位评分员共 12 个试次数组乱序自嵌套） |
| DP-007 | open | 工具作者 | — | 秒表工具：每按键多算 **0.121 s 墙钟**（7 个独立估计 0.110–0.131，CV 6.5%）。改在视频时钟上取 keydown/keyup |
| DP-008 | open | 工具作者 | — | 秒表工具：提供「撤销」语义；现在回看重评只能加不能减 |
| DP-009 | open | 工具作者 | — | 秒表工具：补 `completed_at`，使实际作业顺序可查（`presentation_order` ≠ 实际观看顺序） |
| DP-010 | open | 工具作者 | — | 秒表工具：导出失败时不要回退到内部状态。张的导出报错后只能贴 localStorage，**且在 4096 字符处被截断，14 个试次只剩 3 个可用** |
| DP-011 | open | 工具作者 | — | 秒表工具：`mobile==0 且 holds==[] 且 unscoreable==false` 必须拒绝导出（区分「没评」与「评出 0」） |
| DP-012 | open | 实现 agent | `feat/human-agreement` | PR 1：人工评分校验器 + 并集重算。**不算任何一致性指标**。见 `SPEC_人工比对与验收_v2.md` §9 |
| DP-013 | open | 实现 agent | `feat/human-agreement` | PR 2：LOVO-CV 框架（7 折，只拟合 θ_mob 一个标量），先用合成真值跑通 |
| DP-014 | open | 实现 agent | `feat/human-agreement` | PR 3：正式 G1–G10 报告。前置：DP-004 + 配对数据齐 |
| DP-015 | done | Capy | `chore/repo-hygiene` | 分支/issue/ignore 规程；修 `.gitignore` 让人工评分文件能入库 |
| DP-016 | open | 道俊 | — | 评分口径不统一是当前最大噪声源：4 位评分员在动段中位 1.08–3.36 s，总时长 115–196 s，**同 13 个试次 ICC 仅 0.818**。要不要做一次统一口径的校准会 |
| DP-017 | open | 道俊 | — | 陈璇后 7 个试次自行改用 **1.0x** 实时速（前 6 个 0.5x）。同一评分员批内倍速变更，须定：接受并单列，还是重评 |
| DP-018 | open | Capy | `spike/hysteresis-bouts` | 评估 bout 判定改为**双阈值滞回**（θ_enter/θ_exit，参考 TopScan 开始规则/停止规则分离）。代价是自由参数从 1 个变 2 个，会削弱 G2 |
| DP-019 | open | Capy | — | 与 EthoPlex 事件规则编辑器对齐规则表达式契约（AND/OR/N-of-M + 时长约束 + 滞回），三产品线共用一套 schema |
| DP-020 | **done（早已完成，我出单时不知道）** | 实现 agent | `feat/tape-corridor` → PR #1 | 胶带走廊标定几何。**8-24 就做完并已合入 `main`**：暗频率法标定、A 批 2/4→**4/4**、B 批与基线逐位一致、7 个视频跨视频泛化全过、验收 A1 判据本身被证明写错并已修为 v1.1。测试 49→74。我 9-03 出派工单时读的是 8-24 的本地快照，未 fetch 远端，把已完成项当首要任务派出去了 |
| DP-021 | done | Capy | `chore/repo-hygiene` | SOP v1.1 补按键颗粒度规则 + 倍速一致性 + 导出方式。**不追溯已评 43 个试次**，适用 FST/新批次/重评 |
| DP-022 | **wontfix** | — | — | 原为 DP-020 抽帧上传。DP-020 已完成且**在 Mac 侧用全部 7 个视频验过**，本条作废 |
| DP-023 | open | Capy | — | **架构侧流程缺陷**：我 9-03 出派工单前没有 `git fetch`，用 8-24 本地快照当现状，导致派工单 A 轨（DP-020）派的是已完成的活。**规程补一条：任何出单/写 STATUS 前必须先 fetch origin 并读远端 STATUS**。已写入 `WORKFLOW.md` |
| DP-024 | open | Capy | — | 目检 7 个 TST 视频第 1 帧，确认文件时间 0 是否等于悬挂完成时刻（DP-004 的科学侧）。若不是，需给每个视频记 `t_suspend`，并在报告里声明绝对 immobility 的口径 |
