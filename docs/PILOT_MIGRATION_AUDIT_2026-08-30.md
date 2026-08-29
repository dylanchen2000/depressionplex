# Pilot 标注 V2.2 恢复审计（2026-08-30）

> 源目录：`/Users/dylanchen2000/Work/heavy/depression/悬尾`
> 恢复契约：[`ANNOTATION_CONTRACT_V2.md`](ANNOTATION_CONTRACT_V2.md)
> 零返工政策：[`LEGACY_RECOVERY_POLICY_V2_2.md`](LEGACY_RECOVERY_POLICY_V2_2.md)
> SHA 基线：[`PILOT_SOURCE_MANIFEST_2026-08-29.md`](PILOT_SOURCE_MANIFEST_2026-08-29.md)
> 操作边界：源标注和视频只读；recovery 只写入独立目录
> `/Users/dylanchen2000/Work/heavy/depression/recovered_annotations_v2.2/tst`，不覆盖、移动或重命名原件。

## 1. 结论

**本批 3/3 份 TST 源已按 V2.2 全长恢复，不需要同事裁剪、补机器字段或重新标注。**

- TST 输入明确为 1 CSV + 2 JSON；另 2 JSON 文件内容自报 FST，只是错放目录，已从 TST
  恢复清单隔离。
- 5/5 源标注 SHA-256 与 manifest 一致；源文件没有发生变化。
- 共审计 885 个原始 interval。非法枚举 0、帧越界 0、JSON interval 缺 mouse 0、相邻同值
  未合并 0。
- 三份 TST 已用实际视频 identity 和全长窗口落盘，并全部通过
  `validate RECOVERED.json --video SOURCE.mp4`：
  - CSV：138 → 138 intervals，无 union、无 axis fill；窗口 `[0,9661)`；
  - 张咸明 JSON：350 → 352 intervals，新增 2 段 `axis_orient=unknown`，共 211 帧；窗口
    `[0,11470)`；
  - 徐乐彤 JSON：271 → 269 intervals，自动 union 1 个完全重复和 1 个同值重叠；窗口
    `[0,11470)`。
- 恢复结果固定为 `pool=train / annotator_role=legacy_rater / blind=false / completed=true`，
  可训练、调试和诊断，但不计入正式盲标 pilot。正式 pilot 仍独立为 0/12。

V2.2 的关键变化是：旧文件缺失的是机器审计字段，不是行为标注本身。恢复器负责生成稳定 ID、
计算视频 SHA、保留全长、做不改变逐帧语义的 normalization；未知盲法不伪造，正式 gold truth
仍由未来的新盲标 pilot 提供。

## 2. 数据集、grain 与身份

### 2.1 预期 grain

canonical interval 的预期 grain 是：

```text
(video.sha256, trial, mouse, annotator, track, interval)
```

`interval` 为闭区间 `[start,end]`，`analysis_window` 为半开区间
`[start,end_exclusive)`。V2.2 恢复后：

- `video` 由工具读取实际文件并计算 basename/size/duration/SHA-256；
- `trial` 是内部稳定试次 ID，不是客户显示名称；
- `mouse` 是画面隔间号，不是动物档案；
- `assignment_id` 是我们生成的恢复任务审计 ID。

这些字段不要求客户补填，机器生成值会在 provenance 中明确标记。

### 2.2 本批确定的 TST 恢复身份

| 源 | stable trial | mouse | 绑定视频 | 视频 SHA-256 |
|---|---|---:|---|---|
| `10mg 2周_陈璇_mouse1.csv` | `tst-8a9770847d48-m1` | 1 | `10mg 2周.mp4` | `8a9770847d48493103fa234f13aa0790670f35150cadb8f1ed4364f6f1c3ba3b` |
| `20mg 1周_张咸明 (3).json` | `tst-11b2361a85c2-m1` | 1 | `20mg 1周 1-3+20 2周1.mp4` | `11b2361a85c27cc5e1a023b7396b1438032524d51623aa24d47f9b77ac80fd55` |
| `20mg 一周_徐乐彤.json` | `tst-11b2361a85c2-m1` | 1 | 同上 | 同上 |

最后两份的 legacy `trial` 分别写“1周/一周”；恢复后不沿用字形差异，而使用同一 stable ID，
从而建立同一 video/chamber 的诊断双标关系。

视频机器信息：

| 视频 | fps | n_frames | duration_sec | size_bytes |
|---|---:|---:|---:|---:|
| `10mg 2周.mp4` | 25 | 9,661 | 386.44 | 30,305,363 |
| `20mg 1周 1-3+20 2周1.mp4` | 25 | 11,470 | 458.8 | 35,847,587 |

## 3. 原始文件概况

| 文件 | schema | assay（源自报） | fps | n_frames | 原 analysis_window | tracks | intervals | V2.2 处置 |
|---|---:|---|---:|---:|---|---:|---:|---|
| `10mg 2周_张咸明 (1).json` | 2 | FST | 25 | 9,562 | `[0,0]` | 10/10 | 51 | 非 TST；原位隔离 |
| `10mg 2周_徐乐彤.json` | 2 | FST | 25 | 9,562 | `[0,0]` | 10/10 | 75 | 非 TST；异值冲突隔离 |
| `20mg 1周_张咸明 (3).json` | 2 | TST | 25 | 11,470 | `[0,0]` | 9/9 | 350 | 全长自动恢复 |
| `20mg 一周_徐乐彤.json` | 2 | TST | 25 | 11,470 | `[0,0]` | 9/9 | 271 | 同值 union 后全长恢复 |
| `10mg 2周_陈璇_mouse1.csv` | — | legacy CSV 无字段 | 行内时长对应 25 | extent 9,661 | 无字段 | 9 | 138 | 全长自动恢复 |

四份 JSON 是旧工具生成的未版本化 schema 2，共同只有 11/26 个 V2.2 必需顶层字段；每份缺少
15 个版本/身份/审计字段：

```text
format, tool_version, primitive_set_version, rubric_version,
interval_semantics, analysis_window_semantics,
annotator_role, assignment_id, video, analysis_window_confirmed,
blind, completed, created_at, updated_at, metadata_repaired
```

这 15 项不再作为“退回客户补填”的清单。recovery 生成它们，并保留 source SHA、生成依据和
normalization 统计。原文件中的 `pool=validate` 也不会被当作盲法证据；恢复统一降格为 train。

### 3.1 每轨原始 interval 数

| 文件 | 每轨 interval 数 |
|---|---|
| `10mg 2周_张咸明 (1).json` | `head_neck=1; fore=2; hind=1; trunk=1; fore_wall=10; translation=4; body_axis=14; wall_contact=10; waterline=7; visibility=1` |
| `10mg 2周_徐乐彤.json` | `head_neck=1; fore=1; hind=1; trunk=2; fore_wall=10; translation=4; body_axis=15; wall_contact=17; waterline=23; visibility=1` |
| `20mg 1周_张咸明 (3).json` | `head_neck=77; fore=72; hind=73; trunk=61; swing=24; touch_wall=0; tail_grasp=0; axis=42; visibility=1` |
| `20mg 一周_徐乐彤.json` | `head_neck=58; fore=47; hind=47; trunk=54; swing=29; touch_wall=1; tail_grasp=1; axis=33; visibility=1` |
| legacy CSV | `head_neck=24; fore=26; hind=20; trunk=32; swing=32; touch_wall=1; tail_grasp=1; axis=1; visibility=1` |

## 4. 数据质量检查

### 4.1 原始 interval 结构

| 文件 | 非法枚举 | 越界/反向 | 同值重复 | 同值重叠 | 异值重叠 | 相邻同值 |
|---|---:|---:|---:|---:|---:|---:|
| `10mg 2周_张咸明 (1).json` | 0 | 0 | 0 | 0 | 0 | 0 |
| `10mg 2周_徐乐彤.json` | 0 | 0 | 0 | 0 | 6 | 0 |
| `20mg 1周_张咸明 (3).json` | 0 | 0 | 0 | 0 | 0 | 0 |
| `20mg 一周_徐乐彤.json` | 0 | 0 | 1 | 1 | 0 | 0 |
| legacy CSV | 0 | 0 | 0 | 0 | 0 | 0 |

TST 徐乐彤的两个冲突都不改变逐帧值，可以自动 union：

- `whole_body_swing`, mouse 1：`[12,60]=true` 完全重复；
- `fore_motion`, mouse 1：`[3802,3860]=marked` 与 `[3858,3888]=marked` 重叠。

FST 徐乐彤的 `wall_contact` 有 6 组 `body`/`forepaw` 异值重叠：
`[48,55]∩[50,56]`、`[81,99]∩[90,97]`、`[110,122]∩[113,120]`、
`[137,142]∩[142,144]`、`[154,160]∩[160,163]`、`[168,176]∩[175,176]`。
它们不能自动决定，但文件已隔离在 FST 队列，不阻塞 TST。

### 4.2 axis 与 categorical coverage

- TST 张咸明 `axis_orient` 原始覆盖 11,259/11,470 帧，缺口为 `[282,425]` 和
  `[2328,2394]`，合计 211 帧。V2.2 明确填为 2 段 `unknown`，不猜姿态、不返工。
- TST 徐乐彤的 axis/visibility 已覆盖全长；CSV 的 axis/visibility 覆盖诊断 extent 全长。
- 两份 FST 文件多数 categorical 轨道只覆盖最前约 201/9,562 帧，再次说明它们是局部/练习
  文件，不应进入 TST。

### 4.3 legacy CSV 行级质量

- 精确 7 列 header，138 个数据行、9 个轨道；标准 CSV reader 可完整读出。
- 每轨 `interval_index` 连续，138/138 行满足 `duration_frames=end-start+1`。
- 138/138 行的 `duration_sec` 与 25 fps 一致。
- 旧 CSV 没有 mouse/assay/video/window 列；V2.2 恢复任务使用已确定的 mouse 1 和实际视频，
  不从 CSV 行内伪造这些字段。

## 5. V2.2 recovery 执行结果

本节直接使用当前 `recover_legacy_csv/recover_legacy_json`，写入新目录后用
实际视频执行严格 validator。详细运行记录和机器可读 manifest 分别在输出目录的
`RECOVERY_RUN_2026-08-30.md` 与 `recovery_run_manifest_v2.2.json`。

| 源 | 输入 interval | 同值 duplicate union | 同值 overlap union | axis unknown | 最终 interval | window | video 验证 |
|---|---:|---:|---:|---:|---:|---|---|
| CSV | 138 | 0 | 0 | 0 段 / 0 帧 | 138 | `[0,9661)` | 通过 |
| TST 张咸明 JSON | 350 | 0 | 0 | 2 段 / 211 帧 | 352 | `[0,11470)` | 通过 |
| TST 徐乐彤 JSON | 271 | 1 | 1 | 0 | 269 | `[0,11470)` | 通过 |

三份输出均为 `completed=true`、`analysis_window_confirmed=true`，并通过 V2.2 train/legacy
contract。这里的 completed 表示“恢复数据结构完整”，不表示 formal gold truth。

逐帧对比结果为 3/3 份 `unexpected_truth_mismatches=0`。张咸明文件中唯一新增的
211 帧是对原缺口显式写入 `axis_orient=unknown`，没有猜填姿态。

### 5.1 legacy 双标诊断

两份 11,470 帧恢复文件已生成
`tst-11b2361a85c2-m1__legacy-diagnostic-agreement-v2.2.json`。报告固定写
`mode=diagnostic / formal=false / gate_policy=N/A`，所有轨道门槛均为 N/A。三级精确 κ 仅作 SOP
校准诊断：头颈 0.395、前肢 0.422、后肢 0.447；躯干 0.669、整体摆动 0.530、身体轴朝向
0.492。这些数字不得当作正式 pilot 验收结果。

## 6. 严重度与下游风险

| 严重度 | 发现 | 影响 | V2.2 处置 |
|---|---|---|---|
| **Critical（仅正式用途）** | legacy 无可证明的盲法/独立 assignment | 不能用于正式 κ gate 或替代 12 例 pilot | 强制 train/legacy_rater/blind=false；正式 pilot 分账 |
| **High** | 2 个自报 FST 文件错放 TST 目录 | 目录 glob 可能造成 assay 污染 | 按文件内容/profile 隔离，不进入 TST 清单 |
| **High（FST 队列）** | FST 徐乐彤 6 组异值 wall_contact 重叠 | 同一帧存在两个类别，无法确定性恢复 | 仅在未来恢复 FST 时人工裁决；不阻塞 TST |
| **Medium** | TST 张咸明 axis 缺 211 帧 | 猜默认姿态会污染真值 | 显式填 `unknown` 并记录帧数 |
| **Medium** | legacy 缺 15 个版本/审计字段、原 window 为 `[0,0]` | 直接按 canonical 读取会失败 | 工具计算 identity、生成审计 ID、恢复全长窗口并写 provenance |
| **Low** | CSV 末行无换行符 | 不影响标准 parser，未丢行 | 保留原件；恢复输出使用 canonical serializer |

**TST recovery blocker：0。**需要人工处理的异值冲突全部位于已隔离的 FST 文件。

## 7. recovery / quarantine 决策

| 文件 | 决策 | 是否要求标注同事返工 |
|---|---|---|
| `10mg 2周_陈璇_mouse1.csv` | `RECOVER_V2_2_FULL_LENGTH` | 否 |
| `20mg 1周_张咸明 (3).json` | `RECOVER_V2_2_FULL_LENGTH_WITH_AXIS_UNKNOWN` | 否 |
| `20mg 一周_徐乐彤.json` | `RECOVER_V2_2_FULL_LENGTH_WITH_SAME_VALUE_UNION` | 否 |
| `10mg 2周_张咸明 (1).json` | `ISOLATE_SELF_REPORTED_FST` | 否；不属于 TST 工作 |
| `10mg 2周_徐乐彤.json` | `ISOLATE_FST_DIFFERENT_VALUE_CONFLICT` | TST 否；未来 FST 恢复才裁决 |

标准 360 秒结果的派生子窗选择规则仍需在 trial 输出中版本化，但这不阻塞 recovery，也不产生
任何人工重标任务。

## 8. 可复跑方法

### 8.1 SHA 复核

```bash
annotation_source_dir='/Users/dylanchen2000/Work/heavy/depression/悬尾'
shasum -a 256 "$annotation_source_dir"/*.json "$annotation_source_dir"/*.csv
```

### 8.2 V2.2 恢复 CLI

metadata/provenance 由内部恢复任务生成；客户不需要填写。正式执行会写新文件且拒绝覆盖：

```bash
python3 -m depressionplex.cli.annotation_v2 recover-csv SOURCE.csv RECOVERED.json \
  --metadata GENERATED_METADATA.json --provenance GENERATED_PROVENANCE.json

python3 -m depressionplex.cli.annotation_v2 recover-json SOURCE.json RECOVERED.json \
  --metadata GENERATED_METADATA.json --provenance GENERATED_PROVENANCE.json
```

metadata 的恢复硬值为：

```text
pool=train
annotator_role=legacy_rater
blind=false
analysis_window=[0,n_frames]
full_window_approved=true
fill_axis_orient_unknown=true
```

### 8.3 恢复后验证与诊断一致性

```bash
python3 -m depressionplex.cli.annotation_v2 validate RECOVERED.json --video SOURCE.mp4

python3 -m depressionplex.cli.annotation_v2 agree RATER_A_RECOVERED.json \
  RATER_B_RECOVERED.json --mouse 1 --diagnostic
```

`--diagnostic` 输出必须标为 `formal=false`，所有 pilot gate 为 N/A。

## 9. 审计边界

- 本报告没有覆盖或移动任何源文件；recovery 输出只落盘到上述独立目录。
- stable trial/video/mouse 映射来自本项目 V2.2 内部恢复决策，不要求客户重新提供。
- 严格 validator 验证 recovery 的结构与视频身份；不把 legacy 数据提升为 blind validation truth。
- 标准 360 秒子窗尚未派生；全长标注是保留信息最多的上游真值层。
