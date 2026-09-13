# Depression Suite 实测数据验证与增量改进建议

> **覆盖更新（2026-09-10）**：新增材料已把覆盖扩展到 28/28 个 CSI 试次；请以[新增材料全量验证报告](2026-09-10_新增材料全量验证-DepressionSuite-report.md)中的聚合指标和优先级为准。本报告保留为调查历史和抽帧证据。

> 验证日期：2026-09-10  
> 关联基线：[FST/TST 逆向分析报告](2026-09-09_逆向工程-DepressionSuite-report.md)  
> 数据边界：用户授权 Mac 路径，只读清单、选择性复制与本地分析  
> 目标：用视频、人工标注和 CSI 导出检验已恢复算法，并给出现有相似软件的增量改造顺序  
> 方法：不执行原始 EXE，不改动远端文件，不把诊断子集外推为正式性能结论

## 1. 执行摘要

本轮实测把上一份静态逆向结论推进到了“算法—设置—实际误差方向”闭环。20 份 FST 单体 CSI 工作簿与 Bin 汇总中的槽号及所有行为时长完全一致；其中 12 条人工评分可匹配到 8 个独立试次。按试次先合并评分者后，CSI 移动时长相对人工的平均偏差为 `+8.87 s`、MAE 为 `14.57 s`、Pearson `r=0.958`。这些数值只能描述当前诊断子集。

最值得立即利用的结果不是相关系数，而是误差分层：带有 `wall_support_still` 人工标记的 6 条评分平均偏差 `+10.34 s`、MAE `17.89 s`；其余 6 条平均偏差 `-0.50 s`、MAE `6.67 s`。在 10 条可单调重建的计时轨迹中，贴壁子集的诊断 precision 为 `0.793`，非贴壁子集为 `0.946`。抽帧复核也显示，若干 CSI Escape/人工未移动窗口中动物持续靠壁、整体位置变化有限。这与逆向恢复出的 FST 高事件路径一致：当前设置会把完整身体的局部形变与水线相关特征直接送入很窄的 `.09/.11` 双阈值，再经 bout 合并放大为 Escape。

因此不建议重写现有软件。优先级最高的增量是：在现有 high/Escape 判定前增加“贴壁接触 + 低质心位移 + 有限轮廓形变”的联合门控；把转码几何映射和真实 FPS 纳入特征与后处理；同时输出原始得分和判定原因。第二优先级是把 TST 已有的刚体平移补偿思路移植成 FST 的可选特征，而不是替换现有分类链。最后用本轮定位的误报窗口和 TST 裁决窗口建立盲标回归集，再重调 `.09/.11`，避免在只有 8 个独立匹配试次时过拟合。

## 2. 范围与数据闭合

完整授权边界见 [scope.md](../scope.md)。远端只执行了文件清单、哈希、元数据和指定时间点解码；选择性复制仅覆盖小型标注、结果、配置、校准和已有复核材料，没有修改远端文件，也没有下载全部大视频。原始 Windows 目标程序仍保持静态-only。

| 数据层 | 只读清单 | 本轮实际用途 | 闭合状态 |
|---|---:|---|---|
| FST 视频 | 5 组源视频 + 5 组 CSI 转码 | 时长/分辨率/码率比较，代表误报窗口抽帧 | 5/5 对时长；源/转码哈希均记录 |
| CSI FST 导出 | 20 个单体 XLSX + 1 个 Bin 汇总 | bout、行为时长与槽号核对 | 20/20 完全一致 |
| 人工 FST 计时 | 13 条源记录 | 与 CSI 移动时长/时间轴对齐 | 12 条、8 个独立试次匹配 |
| CSI FST 设置 | 1 个 FSS3 SET，配套 CLB/BMP | 恢复实测参数而非程序默认值 | 1,611/1,611 bytes 完整解析 |
| TST 标注 | 3 个恢复后标注源、裁决材料 | 验证“刚体摆动 ≠ 主动形变”的需求 | 3/3 视频身份通过；无 CSI TST 结果 |

证据：[E-remote-inventory](../evidence/E-remote-inventory.md)、[E-remote-alignment](../evidence/E-remote-alignment.md)。

## 3. 实测 FST 设置与算法含义

实测 `FSS3` SET 由只读检查器完整消费，文件边界为 `1,611/1,611` 字节，无未解释尾部。4 个槽位的 `[component_size, contrast_1, contrast_2]` 分别为：

```text
[200, 80, 70]
[200, 70, 60]
[200, 80, 80]
[200, 60, 60]
```

关键行为设置如下：

| 项目 | 实测值 | 程序默认值 | 解释 |
|---|---:|---:|---|
| 术语模式 | Escape/Immobile | Escape/Immobile | 与导出一致 |
| Top/Bottom | Full / Full | Full / Full | 运动值可包含完整身体区域 |
| high-event | Full Body | Top Only | Escape 更容易被全身任一形变触发 |
| high / low motion | `0.11 / 0.09` | `1.0 / 0.5` | 中间 Swim 带仅宽 `0.02` |
| min length | `15 frames` | `15 frames` | 25 fps 时为 `0.60 s` |
| noise | `10 frames` | `2 frames` | 25 fps 时为 `0.40 s` |
| merge | `20 frames` | `30 frames` | 25 fps 时为 `0.80 s` |
| bin | `5 s` | `5 s` | 汇总窗口 |
| water / climb / max move | `15 / 10 / 15 / 2` | 同左 | 保留水线与攀爬分支 |

这不是说 `.09/.11` 必然错误，而是它与 Full Body high-event、每槽高 contrast 和 `noise=10` 联合作用后，当前数据中呈现出“Escape 占优、Swim 很少”的可解释机制。参数评估必须保持联动，不能单独改阈值。

FSS3 的固定字段顺序与变长对象边界已由 FST writer `0x0045C7E0`、loader `0x0045EDFC` 及嵌套序列化函数 `0x004C8E10/0x004C9990` 交叉确认。证据：[E-remote-settings](../evidence/E-remote-settings.md)、[E-static-fst](../evidence/E-static-fst.md)。

## 4. 人工计时与 CSI 结果

### 4.1 时长级对齐

| 切片 | n | CSI−人工均值 | MAE | RMSE | Pearson r |
|---|---:|---:|---:|---:|---:|
| 全部评分 | 12 | `+4.92 s` | `12.28 s` | `17.06 s` | `0.968` |
| 独立试次的评分者均值 | 8 | `+8.87 s` | `14.57 s` | `19.68 s` | `0.958` |
| `wall_support_still=true` | 6 | `+10.34 s` | `17.89 s` | `22.63 s` | `0.953` |
| `wall_support_still=false` | 6 | `-0.50 s` | `6.67 s` | `8.36 s` | `0.723` |

高相关并不代表绝对一致：相关系数主要反映试次排序，贴壁分层揭示了更直接的系统性方向。最突出的两个已匹配试次分别多报 `29.62 s` 和 `42.24 s`，均带贴壁静止标记。

### 4.2 探索性时间定位

只有时间戳单调、无回放重叠且累计器差异不超过 15 秒的 10 条轨迹进入定位分析：

| 切片 | n | TP | FP | FN | 诊断 precision | 诊断 recall |
|---|---:|---:|---:|---:|---:|---:|
| 全部可用 | 10 | `2810.69 s` | `387.79 s` | `248.66 s` | `0.879` | `0.919` |
| 贴壁 | 5 | `1118.20 s` | `291.04 s` | `173.89 s` | `0.793` | `0.865` |
| 非贴壁 | 5 | `1692.49 s` | `96.75 s` | `74.77 s` | `0.946` | `0.958` |

这些不是正式逐帧指标。人工记录来自播放器秒表/按住操作，点击延迟和累计差异最高为 `13.13 s`；表中的 precision/recall 只用于定位值得回看的窗口。

完整逐试次表和可复核 JSON 见 [remote_validation_comparison.md](../artifacts/remote_validation_comparison.md) 与 [remote_validation_comparison.json](../artifacts/remote_validation_comparison.json)。证据：[E-remote-alignment](../evidence/E-remote-alignment.md)。

## 5. 视频与转码影响

五组 FST 源视频与 CSI 转码保持完全相同的媒体时长，但分辨率从 `494×262`、`503×266` 或 `512×270` 统一/近似变成 `464×272` 或 `480×272`，总码率降低约 54–61%。配套 CLB 和背景图使用转码后的坐标系。

这一事实本身已验证；“它导致了多少分类误差”尚未验证。对当前算法而言仍需认真对待，因为 component size、最近邻点距、水线距离、climb height、MaxMove 和形态变化都直接或间接依赖像素几何。若实现只在转码图上运行，至少要把以下元数据写入每次结果：源/分析视频哈希、宽高、FPS、缩放/填充仿射、CLB/背景/SET/DT 哈希。所有像素距离特征应先除以槽宽、槽高或动物尺度。

视频清单见 [remote_video_manifest.md](../artifacts/remote_video_manifest.md)。证据：[E-remote-inventory](../evidence/E-remote-inventory.md)。

## 6. 代表窗口与 TST 设计反馈

FST 槽 3/4 的代表候选误报窗口持续靠近容器壁，整体位置和朝向变化有限；其中一个窗口轮廓变化更明显，适合作为“贴壁但可能仍有主动划动”的困难负样本。静帧不能证明没有肢体运动，因此结论依赖人工贴壁标记、时长偏差、时间定位和逆向算法四者共同支持。

![FST chamber 4 candidate windows](../artifacts/video_frames/fst_depressed_4_7_ch4_false_positive_windows.jpg)

TST 方面，两个视频哈希与标注恢复清单完全相符，3/3 恢复源通过严格视频绑定，未出现意外逐帧恢复差异。双人材料中，`head_neck_motion`、`hind_motion`、`fore_motion`、`whole_body_swing`、`trunk_deforming` 的分歧率分别为约 `33.39%`、`30.54%`、`29.00%`、`17.31%`、`14.41%`。这说明“去刚体摆动、保留主动形变”是正确的特征方向，但各原子标签本身不能被当成无噪声真值。

逆向恢复的 TST `UseMotionComp` 恰好提供了可复用骨架：`0x00496770` 先估计主导 x/y 平移并调整中心，`0x00497420` 再比较中心化后的径向形变。建议把这个思路作为 FST 的附加分数或门控输入，保留现有 Escape/Immobile 主干与回退开关。

![TST adjudicated no-swing interval](../artifacts/video_frames/tst_whole_body_swing_ch1.jpg)

证据：[E-remote-video](../evidence/E-remote-video.md)、[E-static-tst](../evidence/E-static-tst.md)、[E-remote-alignment](../evidence/E-remote-alignment.md)。

## 7. 面向现有软件的增量改造

### P0：先抑制可解释的贴壁假阳性

在现有 high/Escape 阈值之后、bout 合并之前加一个联合门控，不改变视频输入、分割器、DT、结果格式或 UI 主流程：

```text
raw_high = current_escape_score > high_threshold
wall_contact = distance(mask, tank_wall) < normalized_wall_band
low_translation = norm(center_t - center_t-1) / tank_diagonal < translation_limit
low_deformation = translation_compensated_shape_delta < deformation_limit

if raw_high and wall_contact and low_translation and low_deformation:
    downgrade to Swim/Immobile candidate, or require sustained limb evidence
else:
    keep current decision
```

门控必须是多特征联合，不能简单写成“碰壁就 Immobile”，否则会吞掉真实挣扎和攀爬。首版建议只记录 shadow decision，不改变正式结果；在盲标回归通过后再启用。

同时完成两个低风险修正：

- 所有像素距离按槽宽/高或动物尺度归一化，并保存源到分析视频的仿射关系。
- 把 `min/noise/merge` 从“裸帧数”改为配置秒数、运行时按实际 FPS 换算；25 fps 下本次 `15/10/20` 对应 `0.60/0.40/0.80 s`。

### P1：复用 TST 的刚体平移补偿

把主导平移估计和中心化形变作为可选特征注入现有 FST scorer：

1. 保持现有分割和身体部位点集；
2. 估计相邻帧的主导刚体位移；
3. 分离 `translation_score` 与 `deformation_score`；
4. 仅当高事件由足够形变或持续肢体证据支撑时保留 Escape；
5. 保留 feature flag 和旧算法回退。

这是一项局部 scorer 改造，不需要复制 CSI 的整个程序结构。TST 标注分歧较高，所以补偿值先作为连续特征/门控，不直接作为人工标签替代品。

### P1：让每个行为段可诊断

为每帧或每 bout 输出最少调试字段：`raw_score`、`high/low threshold`、`wall_contact`、`centroid_delta`、`shape_delta`、`water_relation`、`pre/postprocess_class`、触发的 `noise/min/merge` 规则。这样可以回答“为什么判 Escape”，也能避免只靠总时长盲调阈值。

### P2：盲标回归后再重调 `.09/.11`

建立固定回归集：本轮槽 3/4 的高 FP 窗口、非贴壁正确窗口、贴壁但确有主动运动的困难样本，以及 TST 已裁决的无摆动/摆动窗口。至少补齐 20/20 CSI 试次，每个试次由两名独立评分者给出 canonical event intervals；模型调参和最终评估按视频组隔离，避免同视频切片泄漏。

验收建议同时看：逐帧/事件 onset-offset、bout IoU、总移动时长 MAE、贴壁/非贴壁分层 FP，以及跨分辨率/转码版本稳定性。`.09/.11` 只在这个盲标流程中调整。

## 8. 实施顺序与最小接口

| 顺序 | 改动 | 影响面 | 默认策略 | 验收门槛 |
|---:|---|---|---|---|
| 1 | 记录几何/FPS/哈希和 raw reason | 输出/日志 | 始终开启 | 同输入可完整复现判定 |
| 2 | 时间参数按实际 FPS 换算 | 后处理 | 与旧 25/30 fps 行为兼容 | 跨 FPS 的秒级 bout 行为一致 |
| 3 | 贴壁联合门控 shadow mode | scorer 后、bout 前 | 不改正式输出 | 本轮贴壁 FP 降低且非贴壁 recall 不退化 |
| 4 | 刚体平移/形变双分数 | 特征层 | feature flag 关闭 | 盲集分层指标优于基线 |
| 5 | 启用门控并重调阈值 | 配置 | 版本化 SET/模型 | 独立视频组复测通过 |

建议新增一个窄接口，不动现有外围模块：

```text
BehaviorEvidence score_frame(
    PreviousMask prev,
    CurrentMask curr,
    TankCalibration tank,
    FrameContext context,
    ScoringConfig config
)
```

`BehaviorEvidence` 同时返回旧分数、平移分数、形变分数、贴壁状态、原始类别和门控后类别。旧 scorer 可直接适配为实现 A，新增 scorer 为实现 B；现有后处理和导出只消费最终类别，从而控制改动面。

可编辑验证反馈图见 [remote-validation-feedback-loop.mmd](remote-validation-feedback-loop.mmd)。本机缺少 `mmdc`，因此只交付 Mermaid 源，没有声称生成 SVG。

## 9. Evidence 链

| E-id | 来源 | 主要内容 | 工作项 |
|---|---|---|---|
| E-remote-inventory | 远端只读清单、mdls、SHA-256 | 文件覆盖、源/转码时长与几何、TST 视频身份 | WI-007 |
| E-remote-settings | FSS3 文件 + Ghidra writer/loader | 实测阈值、身体模式、完整文件边界 | WI-008 |
| E-remote-alignment | XLSX/CSV/JSON 本地只读对齐 | 20/20 导出闭合、时长误差、探索性时间定位 | WI-008 |
| E-remote-video | 授权视频定点解码和联系图 | FST 贴壁窗口、TST 无摆动裁决窗口 | WI-008 |
| E-static-fst | Ghidra 静态分析 | FST high/low、水线、身体选择与 bout 链 | WI-004 |
| E-static-tst | Ghidra 静态分析 | TST 刚体平移补偿与形变度量 | WI-004 |

## 10. Findings

### F-007
- title: 实测 FSS3 的窄双阈值与 Full Body high-event 会放大 Escape 候选
- severity: info
- category: reverse_algo
- status: validated
- evidence_ids: [E-remote-settings, E-static-fst]
- location: FSS3 SET；FST 0x00480A00/0x0047E610
- impact: `.09/.11` 的中间 Swim 带仅宽 0.02，且 high-event 使用全身；与每槽高 contrast、noise=10、merge=20 联动后可解释当前导出中 Escape 占优。
- confidence: high
- repro_steps: 用只读检查器解析 FSS3，核对 1,611 字节闭合；对照 writer/loader 和 FST scorer/postprocessor 反编译。
- remediation: 参数必须联动评估；先增加可诊断输出和分层回归，不直接单点调阈值。

### F-008
- title: 当前诊断子集的主要 FST 假阳性方向是贴壁支撑下的低整体位移
- severity: info
- category: reverse_algo
- status: validated
- evidence_ids: [E-remote-alignment, E-remote-video, E-static-fst]
- location: 已匹配 FST 试次的 wall_support_still 分层与代表槽 3/4 时间窗
- impact: 贴壁 6 条评分的平均偏差为 +10.34 s，非贴壁 6 条为 -0.50 s；探索性 precision 分别为 0.793 与 0.946，指向可优先处理的系统性误报。
- confidence: medium
- repro_steps: 运行对齐工具，核对分层统计；复核已记录时间窗联系图，并与 FST Full Body/水线/运动链交叉解释。
- remediation: 在 high/Escape 与 bout 合并之间加入贴壁接触、低质心位移和低补偿形变的联合门控；先 shadow mode。
- residual_risk: 仅 8 个独立匹配试次；秒表时间轴不是 canonical frame labels；静帧不能证明无肢体运动。

### F-009
- title: CSI 转码保持时长但改变像素几何和码率
- severity: info
- category: other
- status: validated
- evidence_ids: [E-remote-inventory, E-remote-settings]
- location: 5 组 FST 源/转码视频及其 CLB/BMP/SET
- impact: 依赖像素距离的分割、最近邻运动、水线和攀爬特征存在域偏移风险；当前数据能确认输入变化，尚不能单独量化其误差贡献。
- confidence: high
- repro_steps: 对照源/转码 mdls 元数据与 SHA-256；核对配套 CLB 坐标系和 SET 像素参数。
- remediation: 保存仿射/FPS/输入哈希，按槽或动物尺度归一化距离特征，并做跨转码版本回归。

### F-010
- title: TST 刚体平移补偿符合实测的摆动与主动形变分离需求
- severity: info
- category: reverse_algo
- status: validated
- evidence_ids: [E-static-tst, E-remote-alignment, E-remote-video]
- location: TST 0x00496770/0x00497420；视频绑定标注与 whole_body_swing 裁决窗口
- impact: 可把主导平移与中心化形变作为 FST 的增量特征，减少被动位移或支撑状态造成的高事件误报，而无需重写共同视觉前端。
- confidence: medium
- repro_steps: 复核 TST 平移直方图/中心化形变反编译，核对视频哈希绑定和裁决窗口抽帧。
- remediation: 以 feature flag 接入连续特征/门控，并保留旧 scorer 回退；不要把单个原子标注直接当无噪声真值。
- residual_risk: TST 原子标签的双人分歧率约 14.41–33.39%，且授权目录内没有 CSI TST 结果导出。

### F-011
- title: 当前覆盖足以定位改进方向，但不足以给出正式性能指标或最终阈值
- severity: info
- category: other
- status: validated
- evidence_ids: [E-remote-inventory, E-remote-alignment]
- location: 授权 FST/TST 数据覆盖与配对关系
- impact: 只有 8/20 个 FST 独立试次匹配人工评分、4 个试次有双评分者；TST 没有 CSI 输出，过早调参会造成选择偏差和过拟合。
- confidence: high
- repro_steps: 复核运行清单、配对计数、评分者覆盖和 TST 文件类型清单。
- remediation: 补齐 20/20 事件区间与至少双人盲标；按视频组隔离校准集和最终评估集。

## 11. Paths

### P-004
- title: 从实测分歧到现有 FST 软件增量修正的证据路径
- path_type: solve
- start: 授权视频、人工计时、CSI XLSX/FSS3/CLB/BMP 与 TST 原子标注
- goal: 不重写现有软件的可回退 scorer/门控改造和盲标验收集
- steps:
  1. action: 以视频/配置/结果哈希和媒体时长闭合输入身份 — evidence: E-remote-inventory, E-remote-settings — finding: F-009
  2. action: 将 20 份 CSI 单体导出与汇总、12 条人工评分对齐 — evidence: E-remote-alignment — finding: F-011
  3. action: 按 wall_support_still 分层误差并定位代表 FP 窗口 — evidence: E-remote-alignment, E-remote-video — finding: F-008
  4. action: 用逆向恢复的 FST Full Body/双阈值/bout 链解释放大机制 — evidence: E-static-fst, E-remote-settings — finding: F-007
  5. action: 复用 TST 刚体平移与中心化形变为可选门控特征 — evidence: E-static-tst, E-remote-video — finding: F-010
  6. action: shadow mode、分层盲标回归、feature flag 与旧 scorer 回退 — evidence: E-remote-alignment — finding: F-011
- residual_risks: 当前结果仍是小规模诊断验证；启用正式决策变更前必须完成独立视频级盲测。

## 12. 复现

本地只读对齐：

```powershell
$python = 'C:\Users\chend\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$case = 'C:\反向软件\work\Depressionscan_HR_reverse'
& $python "$case\tools\compare_remote_validation.py" "$case\artifacts\remote_snapshot" `
  --json-out "$case\artifacts\remote_validation_comparison.json" `
  --md-out "$case\artifacts\remote_validation_comparison.md"
```

解析实测 FSS3：

```powershell
& $python "$case\tools\depressionscan_inspect.py" `
  "$case\artifacts\remote_snapshot\9月10日集中标注\CSI分析强迫游泳数据\10mg 2周.SET"
```

工具与用途：CPython 3.12.14 + openpyxl 3.1.5 负责只读表格/JSON/CSV 对齐，Pillow 12.3.0 生成联系图，Ghidra 12.1.3 用于 FSS3 writer/loader 和 FST/TST 算法交叉验证。远端视频元数据使用 macOS `mdls`，指定帧由 AVFoundation 只读解码并通过标准输出返回。

## 13. 限制与下一验证门槛

- 12 条评分覆盖 8 个独立 FST 试次，不足以报告总体泛化性能。
- 4 个 FST 试次有双评分者，其余匹配试次只有一个评分者。
- 秒表按住记录不是 canonical frame labels，时间定位指标仅作诊断。
- TST 材料没有 CSI TST 结果，不能量化补偿算法相对 CSI 输出的收益。
- TST 原子标签存在较高评分者分歧；需把裁决后的事件区间与原始评分同时保存。
- 转码几何变化已确认，但其独立因果效应尚未通过同源视频 A/B 分析量化。
- 本轮未运行原始 EXE；所有可执行代码结论仍来自静态反编译与数据产物交叉验证。

达到下一门槛后才能确定正式阈值：20/20 FST 匹配、每个试次至少两名盲评者、canonical onset/offset、按视频组隔离的校准/测试集，以及源/转码双版本的相同模型 A/B。届时先比较“现有基线”“仅几何归一化”“贴壁联合门控”“门控 + 平移补偿”四个消融版本。
