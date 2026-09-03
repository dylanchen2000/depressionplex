# Issue 台账（DP-###）

> GitHub remote 尚未建立，issue 暂用本文件管理。规程见 `docs/WORKFLOW.md`。
> 编号只增不复用。状态：`open` / `doing` / `done` / `blocked` / `wontfix`。

| # | 状态 | 归属 | 分支 | 标题 |
|---|---|---|---|---|
| DP-001 | done | Capy | — | 核心骨架：几何语义图 + RAD + CSI bout 流水线 |
| DP-002 | done | Capy | — | S1 阈值分割 + 采集端/噪声底体检，P2 硬门通过 |
| DP-003 | **blocked** | 道俊 | — | 建 GitHub 私有仓库 `dylanchen2000/depressionplex`；沙箱无法 push，靠 bundle 交付 |
| DP-004 | **blocked** | 道俊 | — | 定义并落盘计分窗口 `t0_frame` + `fps`（`10mg 2周.mp4` 实测 386.4 s 但窗口 360 s，26 s 无记录）。**不定则比对不能开始** |
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
