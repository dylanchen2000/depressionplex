# DepressionPlex 标注契约 V2

> 状态：**P0 规范性文件（Normative）**
> 生效日期：2026-08-29
> 唯一正式工具：`tools/annotation/DepressionPlex_annotation_tool_v2.html`
> 源模型：仓库外 `annotation_tool(2).html` 的 V2 独立轨道设计（原文件只读保留）
> 关键字：**必须**、**禁止**、**应**、**可以**分别表示硬约束、硬禁令、默认要求和可选项。

## 1. 版本与适用范围

以下常量共同标识本契约，任何一个不一致都视为不同数据契约：

| 字段 | 固定值 |
|---|---|
| `schema` | `2` |
| `format` | `depressionplex.annotation.v2` |
| `tool_version` | `2.1.0` |
| `primitive_set_version` | `depressionplex-primitives-v2.0.0` |
| `rubric_version` | `depressionplex-rubrics-v2.0.0` |

本文件同时覆盖 TST 与 FST；当前 pilot 只验收 TST。原语是真值层，academic / CSI / ours
等 rubric 是派生层。标注员只标原语，禁止在原语标注中直接写最终行为类别或软件预测分数。

### 1.1 唯一采纳版本

- **V2 正式采用**：不同原语各自拥有独立时间轨道，区间边界互不绑定。
- **V1 retired**：schema 1、旧 `cli/annotate.py` 和共享 bout 原语格式不得再产生正式标注。
- **V3 retired**：共享 segment/共享边界的 `annotation_tool.html` 不采用，不得导入正式 pilot。
- retired 格式只可作为只读历史输入；迁移后也必须保留原文件，且迁移结果通过本契约全部硬门后
  才能进入分析池。

## 2. 时间与独立轨道语义

### 2.1 两种区间语义不得混用

- 原语 interval 使用整数帧闭区间 **`[start, end]`**；持续帧数为
  `end - start + 1`。
- `analysis_window` 使用半开区间 **`[start, end_exclusive)`**；持续帧数为
  `end_exclusive - start`。
- JSON 必须同时写 `analysis_window_semantics: "half_open"`。`[0, 0]`、空窗口及未显式
  确认的默认窗口均为硬错误。
- JSON 必须写 `analysis_window_confirmed`。新建或迁移时默认 `false`；只有标注员依据试验
  方案显式设置并确认窗口后才能改为 `true`。草稿可以保存，但 completed、统计导出和
  agreement 均要求该值为 `true`。
- 所有帧号基于原视频、从 0 开始；interval 必须满足
  `0 <= start <= end < n_frames`，窗口必须满足
  `0 <= start < end_exclusive <= n_frames`。
- 统计、κ 和评分只使用 interval 与 `analysis_window` 的交集。窗口外标注可以保留，但不得
  进入正式统计。

当前批次为 25 fps；正式 TST pilot 的 6 分钟计分窗必须恰为 9000 帧。窗口起点必须由试验
方案或人工确认并写入，禁止从文件长度静默猜测。

### 2.2 独立轨道

数据键为 `(video.sha256, trial, mouse, track)`：

1. 不同轨道可以任意重叠，例如 `trunk_deforming=true` 与
   `whole_body_swing=true` 可以同时成立。
2. 同一 mouse 的同一轨道禁止区间重叠；完全重复也是重叠，必须使文件无法完成/导出。
3. 同轨相邻且同值的区间必须合并为一个 canonical interval。
4. 每个 interval 必须显式带 `mouse`；禁止用“缺少 mouse 即当前鼠”的隐式语义。
5. 多鼠文件可以使用，但校验、导出、评分和一致性统计必须逐鼠进行，禁止把多个 mouse 的
   时间轴合并。

`level` 和 `bool` 轨道可用稀疏表示：未覆盖帧分别取 `none` 和 `false`。`cat` / `orient`
轨道没有隐式默认值；对 `completed=true` 的文件，它们必须在计分窗内逐帧恰好覆盖一次。

## 3. 原语表

### 3.1 TST

| track | 中文语义 | 类型 | 允许值 | 稀疏默认值 |
|---|---|---|---|---|
| `head_neck_motion` | 头颈部运动 | level | `none`, `subtle`, `marked` | `none` |
| `fore_motion` | 前肢运动 | level | `none`, `subtle`, `marked` | `none` |
| `hind_motion` | 后肢运动 | level | `none`, `subtle`, `marked` | `none` |
| `trunk_deforming` | 躯干动态形变 | bool | `false`, `true` | `false` |
| `whole_body_swing` | 整体钟摆摆动 | bool | `false`, `true` | `false` |
| `touch_wall` | 触壁/触悬挂杆 | bool | `false`, `true` | `false` |
| `tail_grasp` | 前爪抓尾 | bool | `false`, `true` | `false` |
| `axis_orient` | 身体轴朝向 | orient | `down`, `level`, `up` | 无，必须覆盖 |
| `visibility` | 可见性 | cat | `clear`, `occluded`, `uncertain` | 无，必须覆盖 |

### 3.2 FST

| track | 中文语义 | 类型 | 允许值 | 稀疏默认值 |
|---|---|---|---|---|
| `head_neck_motion` | 头颈部运动 | level | `none`, `subtle`, `marked` | `none` |
| `fore_motion` | 前肢运动 | level | `none`, `subtle`, `marked` | `none` |
| `hind_motion` | 后肢运动 | level | `none`, `subtle`, `marked` | `none` |
| `trunk_deforming` | 躯干动态形变 | bool | `false`, `true` | `false` |
| `fore_wall_upstroke` | 前肢扒壁 | bool | `false`, `true` | `false` |
| `body_translation` | 整体位移方向 | cat | `none`, `horizontal`, `upward`, `downward` | 无，必须覆盖 |
| `body_axis` | 身体轴姿态 | cat | `horizontal`, `oblique`, `vertical` | 无，必须覆盖 |
| `wall_contact` | 缸壁接触 | cat | `none`, `forepaw`, `body` | 无，必须覆盖 |
| `waterline_state` | 水线状态 | cat | `nose_above`, `head_partial`, `head_submerged`, `body_submerged` | 无，必须覆盖 |
| `visibility` | 可见性 | cat | `clear`, `occluded`, `uncertain` | 无，必须覆盖 |

`subtle` 与 `marked` 是不同真值，不能在保存时折叠；`whole_body_swing` 是纯观察原语，
不能因同时存在主动运动而改写；`trunk_deforming` 只记录动态形变，不能把静态弯曲姿势记为
`true`。所有 rubric 派生都必须先检查 `visibility`：只有 `clear` 帧可以进入行为分类，
`occluded` 或 `uncertain` 帧统一派生为 `unknown`，禁止当作原语默认阴性或行为 `none`。

## 4. Canonical JSON

JSON 是权威记录。下例只展示字段结构；正式文件的 `tracks` 必须包含对应 assay 的全部轨道。
字段名和类型不可自行改写：

```json
{
  "schema": 2,
  "format": "depressionplex.annotation.v2",
  "tool_version": "2.1.0",
  "primitive_set_version": "depressionplex-primitives-v2.0.0",
  "rubric_version": "depressionplex-rubrics-v2.0.0",
  "interval_semantics": "closed",
  "trial": "stable-trial-id",
  "assay": "TST",
  "annotator": "rater-id",
  "annotator_role": "independent_rater",
  "assignment_id": "assignment-id",
  "blind": true,
  "pool": "validate",
  "prefill": false,
  "fps": 25,
  "n_frames": 9661,
  "video": {
    "basename": "source.mp4",
    "size_bytes": 123456,
    "duration_sec": 386.44,
    "sha256": "64-lowercase-hex"
  },
  "active_mice": [1],
  "analysis_window": [100, 9100],
  "analysis_window_semantics": "half_open",
  "analysis_window_confirmed": true,
  "created_at": "2026-08-29T09:00:00+08:00",
  "updated_at": "2026-08-29T10:00:00+08:00",
  "completed": true,
  "completed_at": "2026-08-29T10:00:00+08:00",
  "metadata_repaired": false,
  "tracks": {
    "head_neck_motion": [
      {"mouse": 1, "start": 150, "end": 210, "value": "subtle"}
    ]
  }
}
```

硬约束：

- `trial` 必须是稳定 ID，显示名称变化不得改变它；同一视频不同隔间由 `mouse` 区分。
- `annotator_role` 在 V2.1 中固定为 `independent_rater`；`pool=train` 可以有预填或非盲练习，
  但不得借此改写角色字段或进入正式 agreement。
- `video.sha256` 必须由实际加载的视频字节计算；`n_frames` 和 `fps` 必须经解码器/ffprobe
  核对。仅凭浏览器 `duration * fps` 向下取整不具权威性。
- `active_mice` 必须非空、去重、升序，只能使用当前四隔间布局中的整数 `1..4`；interval
  中的 `mouse` 必须属于它。
- `analysis_window_confirmed` 必须为布尔值；新建/迁移默认 `false`。点击显式设窗并确认后
  才能为 `true`；后续修改 fps、n_frames、视频身份或窗口任一端点必须自动重置为 `false`。
- `assay` 只能为 `TST` 或 `FST`，`tracks` 必须与对应 profile 完全一致；禁止混入另一范式
  的轨道。
- 时间为带时区的 ISO 8601。`completed=true` 时必须有 `completed_at`，且全部结构硬门通过；
  修改已完成文件必须更新 `updated_at` 并重新完成确认。
- `metadata_repaired=false` 表示全部元数据在原生 V2 工作流中取得；历史修复规则见第 8 节。

## 5. CSV 交换格式

CSV 是按 mouse 导出、方便审核与交换的派生格式，**JSON 始终是权威源**。正式 V2 CSV
必须是 enriched CSV，每行重复足以验证身份的关键元数据，并至少包含：

```text
schema,format,tool_version,primitive_set_version,rubric_version,trial,assay,
annotator,annotator_role,assignment_id,blind,pool,prefill,completed,
video_basename,video_size_bytes,video_duration_sec,video_sha256,fps,n_frames,
active_mice,analysis_start,analysis_end_exclusive,analysis_window_semantics,
analysis_window_confirmed,mouse,
track,interval_index,start,end,value,duration_frames,duration_sec,
created_at,updated_at,completed_at,metadata_repaired,provenance
```

- CSV 必须遵循 RFC 4180；`active_mice` 和 `provenance` 用 JSON 字符串并正确转义。
- 一份 CSV 只能含一个 `video.sha256 + trial + annotator + mouse`；所有行的元数据必须完全一致。
  从多鼠 JSON 导出时必须显式选定一只 mouse，CSV 内的 `active_mice` 只写该 mouse。
- `duration_frames` 必须等于 `end-start+1`，`duration_sec` 只作显示，不作为重建真值。
- 导入 CSV 时必须先完整校验，再生成新的 canonical JSON；不得直接以 CSV 参与 pilot 统计。
- 旧 7 列 CSV
  `track,interval_index,start,end,value,duration_frames,duration_sec` 是 legacy 交换文件，缺少
  身份元数据，不能单独进入正式数据集；迁移时必须外部补齐元数据并记录 repair provenance。

## 6. 盲标硬门

`pool=validate` 或进入双人 pilot 的文件必须同时满足：

1. `prefill=false`、`blind=true`、`annotator_role="independent_rater"`、
   `completed=true`；缺一即拒绝。
2. 两名标注员不得是规则设计者；不得接触软件事件、模型分数、对方文件或对方顺序。
3. `assignment_id` 必须能关联到任务分配清单；两人独立顺序由分配清单审计。
4. 训练池可以预填，但任何 `prefill=true` 或 `pool=train` 文件都不得计算正式 κ。
5. agreement 入口在计算前必须验证：schema/format/全部版本字段、video SHA-256、trial、assay、
   mouse、fps、n_frames、analysis window、`analysis_window_confirmed=true`、pool、blind、
   prefill、completed 全部匹配/合规。
6. 多鼠文件必须选择同一个 mouse 后计算，禁止展开全部 interval 后混算。

一次共同练习用于对齐 SOP，标记为 train/practice，不计入 12 例，也不得作为正式 κ 输入。

## 7. 结构校验与 agreement

### 7.1 完成文件的硬错误

下列任一情况必须阻止 `completed=true`、正式导出和 agreement：

- 缺少必需元数据、版本不符、视频 SHA-256 不是 64 位小写十六进制；
- `analysis_window` 为空、越界、仍为 `[0,0]`、没有 `half_open` 声明或
  `analysis_window_confirmed` 不是 `true`；
- interval 非整数、越界、`start>end`、value 不在枚举内、mouse 未声明；
- 同 mouse/track 重叠或重复；
- `cat`/`orient` 在计分窗内有空洞；
- assay 与轨道 profile 不一致；
- JSON、视频解码和声明的 fps/n_frames/时长互相矛盾。

相邻同值 interval 是 canonicalization 错误，必须合并后完成。单段超过 10 秒可以提示复核，
但不能仅凭时长判错。

### 7.2 指标口径

所有指标逐 mouse 计算；正式汇总将 12 个 trial 的合规计分窗按 trial 边界拼接，不能把不同
mouse 当成同一试次。报告必须包含：样本帧数、双方非默认率、union prevalence、混淆矩阵、
原始一致率和 Cohen's κ。

- `level`：主指标为 `none/subtle/marked` 三分类、非加权 Cohen's κ；同时报
  线性加权 κ 作为有序严重度诊断，并另报 `none` vs (`subtle` 或 `marked`) 的二分类 κ。
  加权或二分类结果都不能替代三级精确结果。
- `bool`：按 `false/true` 报 Cohen's κ。
- `cat` / `orient`：按完整枚举报非加权 Cohen's κ 和逐类支持度。
- `visibility` 自身在完整计分窗比较；其他原语另报双方均为 `clear` 的 scoreable-frame 指标，
  并显式报告每人的 `occluded/uncertain` 比例，禁止静默丢帧。
- 若双方合并后只出现一个类别（例如全窗均为 `clear` 或 `down`），κ 属退化情形。
  报告必须标记 `degenerate=true`、将 κ 写为 `null`、将验收 gate 设为 N/A，并依据原始
  一致率与各类支持度解读；禁止把全同类别宣称为有效的 κ 通过。
- 非默认 union prevalence `<5%` 的稀有二分类原语不设 κ 硬门，改报原始一致率与
  `PABAK = 2*Po - 1`，并逐条裁决所有阳性和分歧帧；禁止用退化 κ 宣称失败或通过。

### 7.3 12 例 TST pilot 验收

pilot 必须满足以下全部条件：

1. 两名独立盲标员；共同练习 1 例不计数。
2. **12 个不重复 chamber-trial**，唯一键为 `(video.sha256, mouse)`，来自至少 3 个视频。
3. 每例使用相同且已确认的 6 分钟计分窗；样本同时覆盖明显活动段和静止段，并包含至少
   1 个已知 detached 例。
4. 12 例均有两份通过全部结构硬门的 canonical JSON；不得用 legacy CSV 或局部练习替代。
5. 对 union prevalence `>=5%` 的常见原语，κ 必须 `>=0.80`。`level` 轨道的三级 κ 与
   active 二级 κ 均必须达标；任一未达标即暂停扩量，先修订 SOP 并重做受影响样本。
6. 稀有原语按上一节报告 raw agreement + PABAK，不用 κ 作闸门；所有稀有阳性和分歧必须
   完成人工裁决并留痕。
7. 报告必须同时给逐 trial 诊断和 12 例汇总；禁止删除低一致性 trial 后重算。

通过该 pilot 只表示 P1 标注可扩量，不表示模型阈值或 trial 级结果已经达到路线图最终验收。

## 8. 迁移与原始数据保护

1. 原始 JSON/CSV/视频一律只读保留；先生成 SHA-256 清单，禁止原地覆盖或“修完另存同名”。
   当前批次的只读基线见 `PILOT_SOURCE_MANIFEST_2026-08-29.md`。
2. 修复结果写新文件，设置 `metadata_repaired=true`，并增加：

   ```json
   "provenance": {
     "source_paths": ["original-name.json"],
     "source_sha256": ["..."],
     "repaired_fields": ["analysis_window"],
     "repair_reason": "authoritative protocol record",
     "repaired_by": "operator-id",
     "repaired_at": "2026-08-29T12:00:00+08:00",
     "migration_tool_version": "..."
   }
   ```

3. assay、mouse、计分窗、视频身份等语义字段只能由权威记录或人工确认补齐，禁止从文件名或
   区间分布静默猜测。迁移文件必须以 `analysis_window_confirmed=false` 开始；不能确认的文件
   进入 quarantine，不进入 pilot。
4. 重叠/重复区间不得自动选择“第一条”；必须输出冲突清单，由标注员裁决。
5. V1/V3 转换只能生成候选迁移文件，不能改变其 retired 身份；通过 V2 校验与人工确认后才
   能晋级为正式数据。

## 9. 当前数据基线（2026-08-29）

以下是迁移起点，不是合规认证；逐文件证据与处置见
`PILOT_MIGRATION_AUDIT_2026-08-30.md`：

- `悬尾/` 有 7 个 TST 视频、4 隔间，共 28 个 chamber-trial。
- 目前只有 1 个完整双标 TST chamber-trial，可用于诊断但不满足 12 例 pilot；另有 1 个
  单人 V2 CSV。
- 当前双标按旧工具“非 none 即 active”折叠后的 κ：头颈 0.663、前肢 0.714、后肢
  0.693、躯干 0.669、整体摆动 0.530，全部低于 0.80。三级运动 κ 更低，说明
  `subtle/marked` 边界必须先校准。
- 现有 schema 2 JSON 的 `analysis_window` 为 `[0,0]`；现有 HTML 加载视频时更新文档值但
  未同步运行态起止变量，保存时会写回 `[0,0]`。这些文件在窗口被权威修复前一律不合规。
- 已发现完全重复和同轨重叠 interval；旧工具只警告、不阻止完成。
- 现有单人 7 列 CSV 的 interval 结构可读，但缺少 assay、video SHA-256、mouse、计分窗和
  版本等身份字段，必须按第 8 节迁移。
- `悬尾/` 中两份自报 FST、只标约 8 秒的 `10mg 2周` JSON 属错目录/练习数据，不计入
  TST pilot。

因此当前状态是 **P0 契约、权威工具、机器校验/importer/agreement 与 rubric 派生实现已收口，
存量数据仍待显式迁移；P1 契约合规正式样本为 0/12（另有 1 对 legacy 双标可作诊断）**。
κ 达标前禁止扩大正式标注规模；这不妨碍继续完善 trial 级输出骨架，但所有模型阈值仍须保持
provisional。
