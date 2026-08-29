# Legacy 标注零返工恢复政策 V2.2

> 状态：**P0 规范性恢复政策（Normative）**
> 生效日期：2026-08-30
> 对应版本：`tool_version=2.2.0`、
> `primitive_set_version=depressionplex-primitives-v2.1.0`、
> `rubric_version=depressionplex-rubrics-v2.1.0`

## 1. 一句话决策

**同事已经完成的全长标注原样保留，不裁成 360 秒、不重新标；缺失的机器字段由恢复工具生成，
标准 360 秒结果如有需要，由软件以后从全长标注自动派生。**

恢复的目标是抢救已有人工信息，而不是把历史文件伪装成新的独立盲标。原始 JSON、CSV、视频和
SHA manifest 始终只读；恢复只生成有 provenance 的新文件。

## 2. 本批数据边界

本批 TST 恢复输入严格为 3 份：

1. `10mg 2周_陈璇_mouse1.csv`；
2. `20mg 1周_张咸明 (3).json`；
3. `20mg 一周_徐乐彤.json`。

另两份 JSON 的文件内容自报 `assay=FST`：

- `10mg 2周_张咸明 (1).json`；
- `10mg 2周_徐乐彤.json`。

它们只是放错了 `悬尾/` 目录，继续原位只读保留并从 TST 恢复批次隔离；这不是 TST 标注错误，
也不要求 TST 标注同事处理。若以后恢复 FST，使用单独的 FST 视频绑定与冲突报告。

## 3. 全长优先，360 秒由软件派生

- V2.2 的正式 TST `analysis_window` 允许 **不少于 9,000 帧**（25 fps 下不少于 360 秒）。
- 本批已经标注的 9,661 帧和 11,470 帧时间轴直接完整保留；恢复时使用全长半开窗口
  `[0,9661)` 或 `[0,11470)`，不删除 360 秒以外的 interval。
- 如果产品、论文或客户报告只需要标准 360 秒，软件在计算阶段从已恢复的全长窗口派生一个
  9,000 帧子窗。派生结果必须记录源窗口、子窗 `[start,end_exclusive)`、选择方法和
  rubric 版本；它是结果视图，不是新的人工标注文件。
- 子窗选择规则在 trial 输出实现中统一版本化。规则未定不阻塞全长恢复，也不得让标注员先行
  猜测或返工。

这样可以同时保留全部人工信息和标准 6 分钟可比性。任何下游都禁止为了获得 9,000 帧而改写
源 interval。

## 4. 四个容易误解的字段

这些字段是软件审计字段，不是要求客户补填的动物档案：

| 字段 | 通俗含义 | 生成方式 |
|---|---|---|
| `trial` | 内部稳定试次 ID；用来让两位标注员的同一隔间对得上 | 恢复工具根据 `video.sha256 + chamber` 确定性生成；显示名称不同不影响同一性 |
| `video` | 实际视频文件的机器身份证，包括文件名、字节数、时长和 SHA-256 | 工具读取选定视频后自动计算；客户不手填 hash |
| `mouse` | 当前画面中的隔间号 1–4 | 来自标注文件已有 mouse；legacy CSV 的单鼠隔间由恢复任务配置，不是动物个体档案 |
| `assignment_id` | 我们为本次恢复/标注任务生成的审计编号 | 恢复工具确定性生成并写 provenance；不要求客户提供历史工单 |

缺少这些历史字段不阻塞恢复。恢复器必须把“机器生成”和“源文件原有”分开记录，禁止声称
自动生成的字段是客户原始元数据。若一个源文件可能对应多个视频/隔间且无法唯一消歧，问题交给
内部数据管理员处理，不退回标注员重画行为轨道。

## 5. 恢复后的用途边界

所有 legacy 恢复结果统一写：

```text
pool=train
annotator_role=legacy_rater
blind=false
metadata_repaired=true
```

未知盲法不能伪造为 `blind=true`。恢复结果可以：

- 用作训练数据；
- 运行带有醒目标记的诊断一致性分析；
- 调试 importer、rubric 和 trial 输出；
- 作为后续 SOP 校准的证据。

恢复结果不可以：

- 计入 12 例正式独立盲标 pilot；
- 通过改写 role/pool/blind 冒充 validate gold truth；
- 单独证明 κ 验收或最终模型精度达标。

正式 blind pilot 与 legacy 恢复是两条并行账：恢复保护已有投入，pilot 验证未来流程。

## 6. 不返工的结构修复规则

### 6.1 可以自动修复

以下操作不改变逐帧语义，可以由恢复器确定性完成：

1. **同轨、同 mouse、同值**的完全重复、重叠或相邻 interval 取并集并合并；
2. TST `axis_orient` 没有标到的帧显式填 `unknown`，不得猜成 `down`、`level` 或 `up`；
3. 从视频自动计算 identity，从现有轨道恢复 n_frames/fps 一致性检查；
4. 把 legacy 全长窗口写成 `[0,n_frames)`；
5. 生成 stable `trial`、`assignment_id`、时间戳和完整 repair provenance。

每项自动修复都必须记录 source SHA、修复前后 interval 数、union/fill 的帧数及恢复器版本。

### 6.2 不能自动决定

同一 track/mouse 的**不同值** interval 若重叠，恢复器不能选择第一条、最后一条或按优先级
覆盖。它必须输出精确冲突帧段，交由人工裁决；只隔离受影响文件/范式，不扩大为全批返工。

当前 TST 三份源中，已发现的重复/重叠都是同值，可自动 union；两份 FST 文件中的
`wall_contact=body` 与 `wall_contact=forepaw` 重叠属于异值冲突，但它们已从 TST 批次隔离。

## 7. 恢复输出与可追溯性

每个恢复输出必须：

1. 使用新文件名，绝不覆盖原件；
2. 引用 `PILOT_SOURCE_MANIFEST_2026-08-29.md` 中的源路径与 SHA-256；
3. 写明 recovery policy、工具/原语/rubric 版本；
4. 记录自动生成的 identity、窗口和每项 normalization；
5. 单独列出仍存在的异值冲突；
6. 能由同一源文件和同一恢复参数重复生成等价逐帧状态。

恢复完成的判断不是“变成正式 gold truth”，而是：原有行为标注全部可读、时间轴唯一、来源可追，
并且无需标注同事重新打开视频。

## 8. 本批验收判据

- 3/3 TST 源文件可生成 V2.2 train/legacy 恢复结果；
- 恢复后保留 9,661 或 11,470 帧的完整标注窗口；
- TST 同值重复/重叠全部 deterministic union，axis 空洞全部显式 `unknown`；
- 两份 FST JSON 不进入 TST 输出清单；
- 原始 5 份标注与视频 SHA 均不变化；
- 恢复过程不向同事发起裁剪、补填机器字段或重标请求；
- 正式 blind pilot 计数仍独立为 0/12，直到产生新的 validate/independent_rater 数据。
