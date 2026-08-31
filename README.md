# DepressionPlex

抑郁/绝望行为分析软件。覆盖两个范式：

- **FST** 强迫游泳实验（Forced Swim Test）
- **TST** 悬尾实验（Tail Suspension Test）

Gene&I Scientific · PHENOME 产品线

## 项目不变量（硬规矩，2026-08-25 评审升格）

**单位不变量**：任何阈值都必须用物理单位表达，并以实测标度归一化。
空间用 BL（体长），时间用秒或相关物理周期（如钟摆周期）。**禁止出现以
像素或帧为单位的阈值常数。**

同族 bug 出现过三次才立这条：+30 行收口（像素伪装成 BL 相对量）、ω 阈值
弧度/帧（50fps 把真钟摆判没）、Passive Swing gap 帧数常数（换帧率失效）。
逐个修是打地鼠；不变量把一类错误变成审查期就能挡住的东西。它直接支撑 D2
卖点（阈值跨实验室可比）——CSI 的软肋恰是阈值无绝对含义。

**配套测试约定**：每个阈值必须有一个标度不变性测试——同一物理现象在不同
分辨率（或不同帧率）下必须得到同一判定。范本：tests/test_rules.py 的
「1 Hz 钟摆 25/50 fps 都判 PassiveSwing」。

**传感器噪底台账**：少数常数确实是传感器级噪底（像素量化、分割噪声），
允许存在但必须显式记账于此，不许伪装成判定阈值：

| 常数 | 位置 | 实测依据 |
|---|---|---|
| `_MIN_AREA_FLOOR_PX = 8` | segment.py | 分割噪声 0.43 px（P2 结论二）；仅作 BL² 相对下限的地板 |
| `_AREA_FLOOR = 20` | validity.py | 同上；面积中位数前的去噪地板 |
| 面板带亮占比 0.85/0.70 | segment.py | 行带定位的占比门槛，非动物判定阈值；0.70 扩带另有收口约束 |

## 技术路线（定调，勿偏离）

主表征是**剪影轮廓 + 几何语义图 + 规则引擎**，Pose 仅作辅助。感知层按成本从低到高分级：

```
S1  背光 + 阈值分割          ← 默认主力。零标注、可审计、不引入模型抖动
S2  学习式分割（YOLO-seg / U-Net）  ← 仅在 S1 实测不达标处启用
S3  背景减除                 ← 仅为复现 CSI 数值
```

**S2 只有在同一段素材上实测优于 S1 时才启用。** 禁止因"深度学习更先进"而默认上 S2。

### 为什么不用纯 Pose

immobility 判定是**低运动量区间**，而 pose 误差是尖峰式的（单点跳变、左右后爪互换、头尾翻转），
噪声底会高于要测的信号。轮廓误差有界平滑，且被测量本身就是「区域关系 + 形状变化」。
纯 pose 路线在 DrugEffect 与 EthoPlex 已两次证伪。

### 采集端是最高杠杆

CSI 剪影干净的根因不在算法，在采集：笼后背光板让动物成暗剪影。实测对比度
**110 灰阶差 / 2.27×**，固定阈值即可分割（其单笼位分辨率仅 360×240 照样够用）。

因此硬件规格第一条是背光板，量化验收：**动物与背景绝对灰度差 ≥ 100、比值 ≥ 2×**。

## 分层架构

| 层 | 内容 |
|---|---|
| L0 | 几何语义图：固定编号的多边形/线/点 + 语义角色。颜色仅显示，不参与计算 |
| L1 | 前景分割 → 剪影（S1/S2/S3 分级） |
| L2 | 轮廓几何量：BL 归一化、RAD 刚体-关节分解、身体轴自适应分区、掩膜内光流 |
| L2' | Pose 辅助：头尾定向、部位归属、翻转 QC（可缺失） |
| L3 | 统一时间-空间规则引擎 + AND/OR/N-of-M 组合 |
| L4 | CSI 兼容 bout 后处理流水线 |
| L5 | 指标 / 复核 / 导出；证据冲突输出 `unknown`，不强猜不插值 |

## 核心算法：RAD 刚体-关节分解

TST 金标准要求排除「因先前挣扎惯性产生的钟摆式摆动」。CSI 的标量 blob 运动量在原理上
分不开被动摆动与主动挣扎；剪影域可以：

```
① 由轮廓矩估计帧间刚体+尺度变换 T
② 把上一帧剪影按 T warp 到当前帧
③ 刚体分量 = T（平移、旋转角速度、尺度）     → 钟摆 / 水流带动
④ 关节残差 = area(warp(S_prev) XOR S_cur)/BL²  → 真正的主动动作
```

**已知盲区**：XOR 只看轮廓变化，剪影包络内部的肢体运动（水下尤甚）看不见，
需配**掩膜内稠密光流**互补。

> RAD 不是本项目原创：DrugEffect `pose_arena_core.py` 已实现身体平移/旋转/头部刚性残差
> （含 lag 1 / lag 4 双时标）。本项目移植其数学到剪影域并沿用双时标。

## 参考实现与上游

| 来源 | 取什么 |
|---|---|
| EthoPlex **Shared Assay Core v0.1** | 几何语义图 + 规则原语的唯一参考实现。分支 `codex/epm-ko3-sar-dar-confirmation-v0-1@37a80ed` |
| **DrugEffectPlex** | 工程纪律：ABC 三臂对照执行器、冻结特征契约 JSON、质量门（置空特征但保留 unit）、GBM 决策层 |
| CSI 官方手册 ×3 | 参数口径与 bout 流水线顺序（对标 + 兼容模式） |
| Can et al. 2012 JoVE | TST 人工评分金标准 |

## 精度靶

| 层级 | 目标 | 对标 |
|---|---|---|
| 采集（前置门） | 动物/背景灰度差 ≥100、比值 ≥2× | CSI 实测 110 / 2.27× |
| 分割 | **轮廓面积逐帧抖动 ≤ 2% BL²**（比 IoU 更关键） | CSI 无此类指标 |
| **Trial 级（最重要）** | immobility 总时长 vs 人工 Pearson **r ≥ 0.95** | EthoVision 仅 0.58–0.78 |
| 逐帧 | FST balanced acc ≥ 88%、TST ≥ 92% | DSAAN 88.7% / 92.5% |

## 开发状态

见 `docs/STATUS.md`。标注层只采用 `docs/ANNOTATION_CONTRACT_V2.md` 中定义的
V2.2 独立轨道契约（tool 2.2.0 / primitives 2.1.0 / rubrics 2.1.0）；V1 共享 bout 与
V3 共享 segment 均已 retired。权威浏览器工具为
`tools/annotation/DepressionPlex_annotation_tool_v2.html`。

历史标注执行 `docs/LEGACY_RECOVERY_POLICY_V2_2.md`：保留 9,661/11,470 帧全长时间轴，
不要求同事裁成 360 秒或重新标注；标准 360 秒指标如有需要，由软件从全长标注派生。
恢复结果固定为 train/legacy_rater/non-blind，可训练和诊断，但与正式 12 例盲标 pilot 分账。

存量标注的只读身份与迁移结论分别见
`docs/PILOT_SOURCE_MANIFEST_2026-08-29.md` 和
`docs/PILOT_MIGRATION_AUDIT_2026-08-30.md`；恢复规则见
`docs/LEGACY_RECOVERY_POLICY_V2_2.md`。
本批 3 份已恢复的 TST JSON、provenance、运行 manifest 与非正式一致性报告保存在
`/Users/dylanchen2000/Work/heavy/depression/recovered_annotations_v2.2/tst`。
同一目录下的 `disagreement_review_v1/` 是首轮双标分歧复核包：11 个 mouse1 短片
共 66.00 秒，覆盖五个优先 SOP 轨道的 11/11 个实际分歧**无向**标签对；这不等于
对全部 17 个 A→B 方向逐一验收。同事只需填写 `review_decisions.csv`，其中
`boundary_reviewable=no` 的 4 行只裁语义/强度，不裁起止边界；不改原标注、不重看
11,470 帧。该包仍为 legacy 诊断，
`formal=false / gate=N/A`。

首轮共同裁决已于 2026-08-31 回收。当前校准口径与 11 个例子见
`docs/SOP_TST_共同裁决校准_v0.1.md`。其中 C003、C008、C010、C011 的完整边界复核包在
`/Users/dylanchen2000/Work/heavy/depression/recovered_annotations_v2.2/tst/boundary_review_round2_v1`：
共 4 个片段、63.60 秒，前后各带 50 帧上下文，右上角显示原视频绝对帧号；同事无需
重看整段视频。C002 已明确拆成轻微/明显两个区间。C003/C008/C011 因原边界栏不可填写，
备注中的多段区间已转为结构化记录；同事已确认这些区间是在原标注页面对原视频逐帧
判断所得，因此按完整边界收口，不再要求返工。

V2 机器校验入口：

```bash
python3 -m depressionplex.cli.annotation_v2 validate annotation.json --video source.mp4
python3 -m depressionplex.cli.annotation_v2 agree rater-a.json rater-b.json
python3 -m depressionplex.cli.annotation_v2 export-csv annotation.json annotation.csv --mouse 1
python3 -m depressionplex.cli.annotation_v2 recover-csv legacy.csv recovered.json \
  --metadata generated-metadata.json --provenance generated-provenance.json
python3 -m depressionplex.cli.annotation_v2 recover-json legacy.json recovered.json \
  --metadata generated-metadata.json --provenance generated-provenance.json
python3 -m depressionplex.cli.annotation_v2 agree recovered-a.json recovered-b.json \
  --mouse 1 --diagnostic
python3 -m depressionplex.cli.annotation_review \
  --annotation-a recovered-a.json --annotation-b recovered-b.json \
  --agreement-report diagnostic-agreement.json --video source.mp4 \
  --output-dir disagreement_review_v1
```

`annotation_review` 会生成完整分歧清单、首轮分层抽样队列、逐帧精确 MP4、可填写裁决表、
已执行分析笔记本和 SQLite 快照。需要自包含 `report.html` 时，再传
`--report-builder /path/to/deliver_portable_artifact.mjs`；报告输入始终是同包内 canonical
`artifact.json`，不是另写一套 HTML。

完整规划的源文档保留在工作区根目录
`DepressionPlex_算法模型规划与研发路线图_v0.5.docx`。
