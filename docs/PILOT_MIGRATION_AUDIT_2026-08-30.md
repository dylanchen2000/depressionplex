# Pilot 标注迁移数据质量审计（2026-08-30）

> 审计对象：`/Users/dylanchen2000/Work/heavy/depression/悬尾` 中 1 个 CSV 与 4 个 JSON
> 权威规则：[`ANNOTATION_CONTRACT_V2.md`](ANNOTATION_CONTRACT_V2.md) 与当前
> `depressionplex.annotations` validator / legacy CSV parser
> 身份基线：[`PILOT_SOURCE_MANIFEST_2026-08-29.md`](PILOT_SOURCE_MANIFEST_2026-08-29.md)
> 操作边界：只读检查；没有修改、移动、改名或重新保存任何源标注/视频。

## 1. 结论

**当前可进入正式 pilot 的 canonical V2 文件为 0/5；正式双标 chamber-trial 为 0/12。**

- 5/5 源标注文件的重新计算 SHA-256 与现有 manifest 完全一致，原始身份基线可信。
- 共检查 885 个 interval。允许值错误 0、帧边界错误 0、JSON interval 缺 mouse 0、同值相邻
  未合并 0。
- 4/4 JSON 都是未版本化的 legacy schema 2：`analysis_window=[0,0]`，且每份都缺少
  15 个 canonical 必需字段，包括 video SHA-256、窗口确认和完成状态。
- 发现 1 组完全重复与 7 组同轨重叠，导致两个文件的时间状态不唯一。
- 旧 7 列 CSV 有 138 个数据行、9 个 TST 轨道；行级枚举、索引、闭区间时长与候选 25 fps
  换算均一致，但它没有 trial/assay/video/mouse/window 等身份元数据。严格 importer 按设计拒绝。
- 两份自报 `assay=FST` 的局部文件继续从 TST pilot 隔离。两份自报 TST 的 JSON 只能视为
  **候选双标对**；其 `trial` 字符串不相等且没有 video hash，不能运行正式 agreement。

最小安全动作是：保持源文件不动；先取得权威 assignment/video/window 记录，再生成
`metadata_repaired=true`、`analysis_window_confirmed=false`、`completed=false` 的新迁移草稿；
所有重复/重叠必须由标注员裁决，不能自动取第一条。

## 2. 数据集与预期 grain

### 2.1 预期 grain

canonical interval 的预期 grain 为：

```text
(video.sha256, trial, mouse, annotator, track, interval)
```

其中 `interval` 是闭区间 `[start,end]`；正式计算窗口是独立的半开区间
`[analysis_start,analysis_end_exclusive)`。文件级 candidate key 至少需要
`video.sha256 + trial + mouse + annotator`，同一 track/mouse 内不得存在重复或重叠 interval。

当前五份文件都无法建立完整 grain：四份 JSON 没有 `video`，CSV 还同时缺 trial、assay、
annotator、mouse 和 window。文件名只用于定位原件，**不作为正式身份字段**。

### 2.2 文件与 SHA-256

| 文件 | SHA-256 | manifest 复核 |
|---|---|---|
| `10mg 2周_张咸明 (1).json` | `44392d582ba98ac82163f97b4eb98a2a890766201fb5af9465754290eaf65a7b` | 一致 |
| `10mg 2周_徐乐彤.json` | `b59528c9f31f6d3ec50b337ca09881e15c783a9b966ce5420377ef903c5636b2` | 一致 |
| `20mg 1周_张咸明 (3).json` | `4289556ee52602f42a9f1756e7680fdfc99c94e5502f9bda8ab3258ee8da5041` | 一致 |
| `20mg 一周_徐乐彤.json` | `2da08b46a090480b7022c376bd4ed80156bce9fc94c9cc71b21d95f2299eeace` | 一致 |
| `10mg 2周_陈璇_mouse1.csv` | `86edaf857869f91b826a5f634a62b7a596922be89e83092f9ae728afcbafdc93` | 一致 |

## 3. 每文件概况

`confirmed` 和 `completed` 的“缺失”不同于 `false`：legacy 文件没有留下可审计声明。

| 文件 | schema | assay（文件自报） | fps | n_frames | analysis_window | confirmed | completed | tracks | intervals | 当前 validator/parser 结论 |
|---|---:|---|---:|---:|---|---|---|---:|---:|---|
| `10mg 2周_张咸明 (1).json` | 2 | FST | 25 | 9,562 | `[0,0]` | 缺失 | 缺失 | 10/10 | 51 | 33 个 validator errors；非正式 |
| `10mg 2周_徐乐彤.json` | 2 | FST | 25 | 9,562 | `[0,0]` | 缺失 | 缺失 | 10/10 | 75 | 39 个 errors，含 6 个重叠；隔离 |
| `20mg 1周_张咸明 (3).json` | 2 | TST | 25 | 11,470 | `[0,0]` | 缺失 | 缺失 | 9/9 | 350 | 33 个 validator errors；候选迁移 |
| `20mg 一周_徐乐彤.json` | 2 | TST | 25 | 11,470 | `[0,0]` | 缺失 | 缺失 | 9/9 | 271 | 35 个 errors，含 1 重复 + 1 重叠；隔离待裁决 |
| `10mg 2周_陈璇_mouse1.csv` | — | 缺失 | 缺失 | 缺失 | 缺失 | 缺失 | 缺失 | 9 | 138 | strict import 拒绝；diagnostic legacy parse 通过 |

四份 JSON 的 33 个共同 validator errors 中，有一部分是同一根因的“字段缺失 + 固定值不符”
双重报告；不能把 33 当作 33 个独立业务问题。更稳定的完整性口径是：每份仅出现 11/26 个
canonical 必需顶层字段，缺失 15/26：

```text
format, tool_version, primitive_set_version, rubric_version,
interval_semantics, analysis_window_semantics,
annotator_role, assignment_id, video, analysis_window_confirmed,
blind, completed, created_at, updated_at, metadata_repaired
```

四份 JSON 都有 `pool=validate`、`prefill=false`，但没有 `blind`、独立评分角色、assignment 或
完成时间，因此不能把“validate 文件名义”解释成已经满足盲标。

### 3.1 每轨 interval 数

| 文件 | 每轨 interval 数 |
|---|---|
| `10mg 2周_张咸明 (1).json` | `head_neck_motion=1; fore_motion=2; hind_motion=1; trunk_deforming=1; fore_wall_upstroke=10; body_translation=4; body_axis=14; wall_contact=10; waterline_state=7; visibility=1` |
| `10mg 2周_徐乐彤.json` | `head_neck_motion=1; fore_motion=1; hind_motion=1; trunk_deforming=2; fore_wall_upstroke=10; body_translation=4; body_axis=15; wall_contact=17; waterline_state=23; visibility=1` |
| `20mg 1周_张咸明 (3).json` | `head_neck_motion=77; fore_motion=72; hind_motion=73; trunk_deforming=61; whole_body_swing=24; touch_wall=0; tail_grasp=0; axis_orient=42; visibility=1` |
| `20mg 一周_徐乐彤.json` | `head_neck_motion=58; fore_motion=47; hind_motion=47; trunk_deforming=54; whole_body_swing=29; touch_wall=1; tail_grasp=1; axis_orient=33; visibility=1` |
| `10mg 2周_陈璇_mouse1.csv` | `head_neck_motion=24; fore_motion=26; hind_motion=20; trunk_deforming=32; whole_body_swing=32; touch_wall=1; tail_grasp=1; axis_orient=1; visibility=1` |

## 4. 检查结果

### 4.1 结构与域值

| 文件 | 非法枚举 | 越界/反向 interval | 重复对 | 重叠对 | 相邻同值对 | mouse 问题 |
|---|---:|---:|---:|---:|---:|---|
| `10mg 2周_张咸明 (1).json` | 0 | 0 | 0 | 0 | 0 | 0 |
| `10mg 2周_徐乐彤.json` | 0 | 0 | 0 | 6 | 0 | 0 |
| `20mg 1周_张咸明 (3).json` | 0 | 0 | 0 | 0 | 0 | 0 |
| `20mg 一周_徐乐彤.json` | 0 | 0 | 1 | 1 | 0 | 0 |
| legacy CSV | 0 | 0 | 0 | 0 | 0 | 源 138 行全部缺 mouse 列；需外部元数据 |

精确冲突证据：

- `20mg 一周_徐乐彤.json`
  - `whole_body_swing`, mouse 1：`[12,60]=true` 完全重复 2 次。
  - `fore_motion`, mouse 1：`[3802,3860]=marked` 与 `[3858,3888]=marked` 重叠 3 帧。
- `10mg 2周_徐乐彤.json` 的 `wall_contact`, mouse 4 有 6 组不同值重叠：
  `[48,55]∩[50,56]`、`[81,99]∩[90,97]`、`[110,122]∩[113,120]`、
  `[137,142]∩[142,144]`、`[154,160]∩[160,163]`、`[168,176]∩[175,176]`。

这些不是可安全自动合并的“同值相邻”。尤其 `wall_contact` 重叠区同时有 `body` 与
`forepaw`，自动取第一条会改变真值。

### 4.2 categorical / orient coverage

因为四份 JSON 的正式窗口均无效，**无法对正式 analysis window 宣称 coverage 通过或失败**。
下表仅用 `[0,n_frames)` 做只读诊断，不能用于选择一个有利的 9,000 帧窗口：

| 文件 | 全文件诊断 coverage |
|---|---|
| `10mg 2周_张咸明 (1).json` | `body_translation/body_axis/waterline_state/visibility` 各 201/9,562（2.10%）；`wall_contact` 84/9,562（0.88%） |
| `10mg 2周_徐乐彤.json` | `body_translation/body_axis` 各 201/9,562；`waterline_state` 200/9,562；`wall_contact` 98/9,562（1.02%）；`visibility` 9,562/9,562 |
| `20mg 1周_张咸明 (3).json` | `visibility` 11,470/11,470；`axis_orient` 11,259/11,470，缺 211 帧：`[282,425]` 与 `[2328,2394]` |
| `20mg 一周_徐乐彤.json` | `axis_orient` 与 `visibility` 均 11,470/11,470 |
| legacy CSV（仅诊断 extent） | `axis_orient` 与 `visibility` 均覆盖 `[0,9660]`；正式窗口和 n_frames 仍未绑定 |

两份 FST 文件的多数 categorical 轨道仅覆盖最前约 201 帧，支持 manifest 中“局部/练习”
分类。TST 张咸明文件的 211 帧 axis 缺口是否落入正式窗口，只能在权威窗口确认后判断；禁止
为了避开缺口而反推窗口。

### 4.3 legacy CSV 解析

源 CSV 是精确的 7 列 legacy header：

```text
track,interval_index,start,end,value,duration_frames,duration_sec
```

- 138 个数据行，9 个轨道；末行没有换行符，但标准 CSV reader 可完整读出，不造成数据丢失。
- `interval_index` 在每轨内均连续；`duration_frames=end-start+1` 全部 138/138 成立。
- 以行内 `duration_sec` 反推的候选 25 fps，138/138 行均在 parser 容差内一致；源文件仍然
  没有权威 `fps` 字段。
- `import_csv()` 明确抛出 `LegacyCSVError`：缺少权威 trial/video/mouse/window 元数据。
- `migrate_legacy_csv()` 在**不写文件**的 diagnostic-only envelope 下通过行级与轨道结构检查；
  该 envelope 使用 `UNCONFIRMED` 身份、按最大 end 得到的诊断 extent 9,661，以及零 hash
  占位，绝不构成迁移或视频绑定。正式迁移必须重新提供权威 metadata 与 repair provenance。

## 5. 身份候选，不是绑定

下表只记录可供人工核对的候选。候选依据不能替代 assignment 记录、视频字节 hash 与现场
试验记录。

| 标注文件 | manifest 中的候选视频 | 机械证据 | 决策 |
|---|---|---|---|
| legacy CSV | `10mg 2周.mp4`, SHA `8a977084…c3ba3b` | CSV 最大 end=9,660；候选视频 ffprobe 为 9,661 帧/25 fps | **需人工确认** assay、mouse、video 与窗口；不绑定 |
| 两份自报 TST JSON | `20mg 1周 1-3+20 2周1.mp4`, SHA `11b2361a…0fd55` | 两份均自报 11,470 帧/25 fps；候选视频也是 11,470 帧/25 fps | **需人工确认**；且 trial 分别为 `20mg 1周` / `20mg 一周`，strict preflight 不相等 |
| 两份自报 FST JSON | 无 TST 候选 | 自报 FST/9,562 帧；7 个 manifest TST 视频帧数均不等于 9,562 | 从 TST pilot 隔离；只可候选 FST practice，需另找权威 FST 视频 |

ffprobe 只证明数值相容，不证明文件来源相同；只有 `video.sha256` 与权威 assignment 能完成
绑定。

## 6. 按严重度的发现与下游风险

| 严重度 | 发现与证据 | 下游风险 | 处置 |
|---|---|---|---|
| **Critical** | 0/5 文件具有完整 grain；4/4 JSON 的窗口为 `[0,0]`，CSV 没有窗口；5/5 均无可用 video hash | 无法确定哪一帧、哪只鼠、哪段 360 秒属于同一 trial；评分、κ、训练 join 均不可审计 | 全部保持非正式；先取得权威身份/窗口记录 |
| **Critical** | 1 组重复 + 7 组同轨重叠，涉及 2 个文件 | 同一 `(track,mouse,frame)` 有多个值或重复记录；展开逐帧时可能双计数或依赖“第一条”顺序 | 标注员逐条裁决；迁移器禁止自动选择 |
| **High** | 每份 JSON 缺 15/26 个必需字段；strict validator 报 33–39 个 errors | schema=2 被误当 canonical V2.1，会绕过版本、盲法、assignment 与 provenance 门 | 只能显式 legacy migration；输出 repaired、unconfirmed、incomplete 草稿 |
| **High** | 两份 FST 文件位于 TST pilot 源目录；其行为轨道多数只标约 201/9,562 帧 | 按目录 glob 摄取会造成 assay 污染；把局部练习当完整 trial 会严重偏置分布 | 从 TST 输入清单排除，原件原位保留 |
| **High** | 候选 TST 双标的 `trial` 字符串不相等且均无 video hash | strict agreement 无法确认是同一 chamber-trial；强行配对可能比较不同试次 | 人工确认稳定 trial ID 和视频 SHA 后分别迁移 |
| **High** | 候选 TST 张咸明 `axis_orient` 全文件缺 211 帧；FST categorical coverage 大面积缺失 | 若缺口进入确认窗口，completed 硬门失败；静默用默认值会制造姿态真值 | 窗口确认后重检；需要时回到标注员补标 |
| **Medium** | legacy CSV 行级结构干净，但 138/138 行没有 mouse 字段且所有 identity 依赖文件名 | 单鼠候选看似可用，实际无法安全 join；易把命名约定当真值 | 只在权威 metadata 明确 mouse 后迁移 |
| **Medium** | legacy schema 2 与 canonical V2.1 共用数字 `schema=2`，但旧文件无 format/version | 仅检查 schema 数字的下游会发生 schema drift | 所有入口同时校验 format + 全部版本常量 |

置信度：SHA、字段缺失、枚举/边界、重复/重叠为直接字节解析，**高置信度**；视频对应关系与
正式窗口仅是候选，**不作结论**。

## 7. migration / quarantine 决策

| 文件 | 当前决策 | 晋级前必须完成 |
|---|---|---|
| `10mg 2周_陈璇_mouse1.csv` | `QUARANTINE_METADATA`；结构可迁移候选 | 权威确认 trial/assay/annotator/mouse/video SHA/fps/n_frames/9,000 帧窗口/assignment/盲法；以新文件生成 repaired draft |
| `20mg 1周_张咸明 (3).json` | `MIGRATION_CANDIDATE_AFTER_IDENTITY_CONFIRMATION` | 确认 stable trial ID、video SHA、assignment、盲法、窗口；确认窗口后复查 axis coverage |
| `20mg 一周_徐乐彤.json` | `QUARANTINE_CONFLICT` | 除上述身份确认外，人工裁决 swing 重复与 fore overlap；禁止自动去重 |
| `10mg 2周_张咸明 (1).json` | `QUARANTINE_NON_TST_PRACTICE` | 不进入 TST；若保留为 FST train/practice，需确认 FST 视频与局部窗口并重新标明用途 |
| `10mg 2周_徐乐彤.json` | `QUARANTINE_NON_TST_PRACTICE_CONFLICT` | 不进入 TST；另需人工裁决 6 组 wall_contact 重叠 |

迁移输出必须使用新文件名、保留 source path/SHA、写完整 provenance，并以
`analysis_window_confirmed=false`、`completed=false` 开始。没有现存记录可以倒推出
`blind=true`；若盲法证据不存在，只能进入 train/practice，不能进入正式 pilot。

## 8. 需要人工确认的开放项

1. CSV 的 assay、mouse、annotator ID 与对应原视频 SHA-256。
2. 两份自报 TST JSON 是否确属同一视频/同一 chamber-trial，以及统一的 stable `trial`。
3. 每个 TST trial 的 9,000 帧窗口起点；不能默认 `[0,9000)` 或按 coverage 选择。
4. assignment ID、标注员是否独立、是否确实未见模型输出/对方标注。
5. TST 徐乐彤的 2 组冲突和 FST 徐乐彤的 6 组冲突应保留哪一个值或如何重画。
6. 两份 FST 局部文件对应的 FST 视频及其是否仅为共同练习。

## 9. 可复跑方法

从仓库根目录执行。以下命令均只读；validator 预期对 legacy JSON 返回非零。

### 9.1 SHA 与 manifest 对照

```bash
annotation_source_dir='/Users/dylanchen2000/Work/heavy/depression/悬尾'
shasum -a 256 "$annotation_source_dir"/*.json "$annotation_source_dir"/*.csv
```

### 9.2 当前 canonical validator

```bash
annotation_source_dir='/Users/dylanchen2000/Work/heavy/depression/悬尾'
for annotation_file in "$annotation_source_dir"/*.json; do
  python3 -m depressionplex.cli.annotation_v2 validate "$annotation_file" --allow-draft
done
```

审计中的 error 计数直接来自：

```python
from depressionplex.annotations.contract import validation_errors
errors = validation_errors(document, require_completed=True)
```

### 9.3 legacy CSV 严格拒绝与显式迁移入口

```bash
python3 -c "from depressionplex.annotations.csv_v2 import import_csv; import_csv('/Users/dylanchen2000/Work/heavy/depression/悬尾/10mg 2周_陈璇_mouse1.csv')"
```

预期抛出 `LegacyCSVError`。只有取得人工确认的 metadata/provenance JSON 后，才允许运行：

```bash
python3 -m depressionplex.cli.annotation_v2 migrate-csv SOURCE.csv NEW_DRAFT.json \
  --metadata AUTHORITATIVE_METADATA.json \
  --provenance REPAIR_PROVENANCE.json
```

禁止为了“让命令通过”而从文件名生成这两个输入文件。

### 9.4 interval 检查算法

对每份源文件按 `(track,mouse,start,end)` 排序，逐条执行：

1. `value in TRACK_DEFS[track].values`；
2. `0 <= start <= end < n_frames`；
3. 当前 start `<=` 前一 end 为 overlap；起止完全相同为 duplicate；
4. 当前 start `==` 前一 end `+1` 且 value 相同为 adjacent-equal；
5. categorical/orient coverage 用 interval 与候选窗口求并集；窗口未确认时只报诊断，不验收。

源视频帧数诊断使用：

```bash
ffprobe -v error -select_streams v:0 -count_frames \
  -show_entries stream=avg_frame_rate,nb_read_frames,width,height \
  -show_entries format=duration,size -of json SOURCE.mp4
```

## 10. 审计边界

- 本报告没有生成迁移文件，没有改变源 SHA，也没有运行正式 agreement。
- 未使用文件名推断正式 assay、mouse、video 或 analysis window。
- categorical coverage 的全文件数字只用于发现风险，不代表正式窗口覆盖率。
- 当前目录没有时间分区或可靠采集时间字段，因此未做趋势分析；本次暴露的是格式版本漂移，
  不是时间序列漂移。
