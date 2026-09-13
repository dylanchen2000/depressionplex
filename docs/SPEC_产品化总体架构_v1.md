# DEPRESSION-PLEX 产品化总体架构 v1（DP-095）

> 起因：2026-09-13 道俊要求「在这么多工作的基础上，形成正式产品软件」，并定分工：
> Capy 作为架构师负责总体框架与疑难问题，对最终产品负责；大量消耗 token 且机械的活外派。
> 本文是架构裁决文件，不是进度表。改本文需在 `docs/ISSUES.md` 留记录。

## 0. 结论先行（三句）

1. **产品外壳现在就能做，而且必须现在做**——引擎的对外接口（`trial_report` / CSV 字段 /
   `TrialValidity`）已经稳定，外壳不碰任何真值口径，是纯机械活，可外派。
2. **引擎精度还不够挂"计量仪器"的名**——G7 读数 r = 0.7641 对门槛 0.818，G8 只过 5/26，
   平均 |bias| 35.15 s（DP-055）。这不是调参能补的：误差不是偏移（均值 +1.74 s、斜率 0.933），
   是两组方向相反的失效各占 52% / 48%。
3. 所以产品从第一天起就**分两个发布态**：**研究模式**（Research，现在可交付）与
   **计量模式**（Validated，G7/G8 通过后才解锁）。**这是本文最重要的一条架构决策**——
   它让产品化与精度攻关并行，且不需要任何一方说谎。

## 1. 现状判定（哪些能产品化，哪些不能）

| 块 | 状态 | 证据 |
|---|---|---|
| 分析引擎端到端可跑 | **可用** | DP-053：`video.py` / `maskseq.py` / `runner.py` / `cli/analyze.py`，视频进、trial 级数字出 |
| 依赖足迹 | **极轻** | `pyproject.toml` 核心只 `numpy>=1.24`；全仓第三方 import 只有 numpy（+ `subprocess` 调 ffmpeg）。无 torch / onnx / cv2 强依赖 |
| 每个数字带分母 | **已实现** | `cli/analyze.py` 的 CSV 字段含 `scorable_frames` / `unknown_frames_window` / `validity_status`；证据冲突出 `unknown` 不猜 |
| 排除态处理 | **已实现** | 排除态隔间不产数字，只产 `score_gate` 报警行（DP-028 N1/N3） |
| 单位不变量 | **已实现** | 阈值一律物理单位，像素/帧常数只许进噪声底台账 |
| 精度门 G7/G8 | **未过** | DP-055；θ_mob = 0.0175 需在 T1 上重标 |
| FST 计分窗口 | **未定** | DP-057/DP-074：7 段 FST 实长 362.20–467.56 s，`ASSAY_WINDOWS` 里的 `(120, 360)` 假设录像从入水开始，未验证。**agent 不许替道俊选** |
| T1 精标条件 | **缺一件** | DP-027①：秒表工具还没有 seek / 逐帧步进 ⇒ 真值层做不出来。**这是精度轨的唯一硬阻塞** |
| CSI 参数互通 | **接近打通** | DP-094：`.SET` 表头随孔位数变长已修（PR #82），13 个数还剩 32 种排列未收口，缺一份「13 个数互不相同」的 `.SET` |
| 桌面外壳 / 安装包 / 报告 / 审计 | **零** | 仓内只有 `ui/results_viewer/index.html`（720 行）和两个 `tools/` HTML，都是研发工装，不是产品 |

## 2. 产品定义

| 项 | 定义 |
|---|---|
| 产品名 | **DEPRESSION-PLEX**（Gene&I PHENOME 产品线，与 DRUGEFFECT-PLEX 同族） |
| 范式 | FST 强迫游泳 + TST 悬尾 |
| 形态 | **Windows 桌面 .exe 安装包**，纯 CPU、离线、不监听端口、U 盘可交付 |
| 对标 | CSI DepressionScan（不是抄，是**可比 + 可审计**：CSI 不给分母，我们给） |
| 客户机 | Windows 优先（客户实验室都是 Win）；macOS 只做开发自用 |
| 数据不出本机 | 无云、无遥测；审计文件落 `user_data_dir()` |

## 3. 总体架构（分层 + 复用边界）

```
┌─────────────────────────────────────────────────────────┐
│ L6 产品外壳  desktop/  (PySide6)                         │
│   页面：欢迎 / 新建实验向导 / 队列 / 复核 / 结果 / 导出 / 自检 │
│   服务：experiment_store · audit_log · provenance ·      │
│         exporter(xlsx/pdf) · self_check · csi_set_io     │
├─────────────────────────────────────────────────────────┤
│ L5 指标与导出  assay_core/trial_report.py · timeline.py   │  ← 已有
│ L4 CSI 兼容 bout 流水  assay_core/bouts.py                │  ← 已有
│ L3 规则引擎  assay_core/rules.py                          │  ← 已有
│ L2 轮廓几何 + RAD  assay_core/{geometry,rad,silhouette}.py │  ← 已有
│ L1 分割  assay_core/segment.py · validity.py              │  ← 已有
│ L0 几何语义图  assay_core/primitives.py                    │  ← 已有
├─────────────────────────────────────────────────────────┤
│ I/O 层  video.py(ffmpeg) · maskseq.py · runner.py         │  ← 已有
└─────────────────────────────────────────────────────────┘
```

**铁律：L6 只许通过 `runner` / `trial_report` 的公开数据类与 L0–L5 说话，不许 import
`assay_core` 内部函数，不许在外壳里重算任何指标。** 外壳里出现第二套算法 = 立即拒收。
理由是 DP-054 那类事故（报告层自己算分母，打印出 198.9%）只能靠边界杜绝。

### 3.1 从 `drugeffectscan` 复用什么（已核实存在）

姊妹仓 `dylanchen2000/drugeffectscan` 有一套跑通的同族外壳，**照抄结构、不照抄业务**：

| 复用 | 来源 | 说明 |
|---|---|---|
| 目录骨架 | `desktop/{main.py,app/{main_window,pages,services,styles,widgets,workers,utils}}` | 一比一照搬 |
| VI 深色皮肤 | `desktop/app/styles/dark.qss`（39 KB）+ `colors.py` | 同公司 VI v2.0，直接用 |
| **模式徽章** | `styles/mode_badge.qss` | 正是本文 §4 双发布态需要的现成件 |
| 跨平台七条约束 | `desktop/README.md` | `resource_path()` / `user_data_dir()` / CPU-only / `spawn` / `QDesktopServices` |
| 审计与溯源 | `services/{audit_log,audit_log_paths,audit_metadata_registry,provenance,model_card}.py` | GLP 要件，接口照搬 |
| 自检页 | `pages/self_check_page.py` + `services/self_check.py` | 对应我方 P2 硬门（对比度 / 噪声底 / 面积抖动） |
| 导出 | `services/{exporter,exporter_pdf}.py` | 只借框架，报告内容全部重写 |
| 打包链 | `build_windows.spec` + `installer/{build.ps1,drugeffect-plex.iss,languages/ChineseSimplified.isl}` + `.github/workflows/windows-*.yml` | **GUI 与分析后端分别 freeze、后端并入 `backend/`** 这个模式直接继承 |
| 字体资产 | `app/assets/fonts/{Inter,DMSerifDisplay,JetBrainsMono}` | 同族观感 |

**不复用**：`htr_*`（HTR 抽头模型）、`topscan_*`、`result_package_v2*_loader`、
`fusion_demo`、`b_inference_page`、`models_page` —— 那些是 DrugEffectScan 的业务与模型仓，
DepressionPlex 没有神经网络模型，**不许把 onnxruntime / ultralytics 拖进依赖**。

### 3.2 依赖清单（锁死，这是产品化最大的结构性优势）

```
PySide6==6.7.3        # 与 drugeffectscan 同版，踩过的坑不重踩
numpy==1.26.4
openpyxl==3.1.5       # xlsx 导出
fpdf2==2.8.1          # pdf 报告
# 无 onnxruntime、无 opencv、无 torch、无 scipy
# ffmpeg：随包分发单个可执行文件，只用于解码（DP-053：ffmpeg 只许在 assay_core 之外）
```

预期安装包体积**远小于** DRUGEFFECT-PLEX（后者要带模型权重与 ONNX 运行时）。

## 4. 双发布态（核心架构决策）

产品在任何时刻都必须**自己知道**它有没有资格报秒数。

| 发布态 | 解锁条件 | 界面表现 | 导出表现 |
|---|---|---|---|
| **研究模式** Research | 默认 | 顶栏黄色徽章「研究用途 · 未计量标定」；结果页每个秒数旁挂分母与 `validity_status` | 报告首页强制印一段未标定声明 + 当前 θ_mob 与其标定来源；文件名带 `_research` |
| **计量模式** Validated | `G7 r ≥ 0.818` 且 `G8 |bias| ≤ 17.7 s` 在冻结数据集上复现通过 | 绿色徽章 + 验收批次号 | 印验收批次号、数据集哈希、θ_mob 冻结值 |

实现方式：**一个随包分发的只读 `calibration.json`**（θ_mob、验收批次、数据集哈希、G7/G8 读数、
签发时间），启动时由 `services/self_check.py` 校验哈希；缺失或哈希不符 ⇒ **降到研究模式，不是报错退出**，
但徽章变红「标定文件不可信」。**外壳不许有任何路径能把模式手动切到 Validated。**

理由：这是 DP-054 / DP-052 / DP-071 三次教训的共同结论——**静默兜底比报错危险，但假装有资质比两者都危险**。
CSI 就是不给分母，我们的差异化只有在自己不越界时才成立。

## 5. 两条并行轨道与关键路径

### 轨道 A：精度（我自己干，不外派）

WORKFLOW §8 定的四类不外派：真值口径 / 验收门 / 契约裁决 / 失败归因。轨道 A 全在里面。

```
DP-027① 秒表 seek+逐帧步进  ──┐
DP-057/074 FST 窗口裁决(道俊) ─┼→ T1 精标（双评+仲裁）→ θ_mob 在 T1 上重标(LOVO-CV, 仍只拟合 1 个标量)
DP-075 BL 把尾/胶带算进体长 ──┘                                    ↓
                                                        G7/G8 复读 → 解锁计量模式
```

已裁决、不再是候选路径（避免重复劳动）：
- **DP-071 距离场残差：实现了但不换。** 26 试次 A/B 帧级 AUC 中位 −0.017、正向仅 1/26，
  判据跑前写死 ⇒ `DEFAULT_RESIDUAL_MODE` 保持 `binary_xor`，新口径留而不用。
- **DP-073 BL² 分母：保持 `per_trial`。** 最佳候选 pooled J 只 +0.0044（要 ≥ +0.02）、
  pooled AUC −0.0019 ⇒ 落在「分不出」档。且好处集中在 4 个试次，
  ρ(|ΔJ|, |BL 偏离同录像中位|) = 0.765 ⇒ 同录像中位是在遮坏 BL，不是在提高判别力。
- 上面两条把矛头指向 **DP-075**：`trial_body_length` 量的是「体长 + 尾/胶带」
  （`30mg_2周-ch2` BL 49.06 px vs 同录像 25.75 / 28.54，隔间宽仅约 95 px；y 跨度 +89% 而面积只 +33%、
  填充率 0.60→0.41 ⇒ 多出的是约 24×2.4 px 细长竖直附属物）。**清干净 BL 是精度轨的第一顺位技术活。**

### 轨道 B：产品化（可外派，机械且边界清楚）

不依赖轨道 A 的任何结论，因为研究模式不报资质。

## 6. 模块清单与派工边界

| # | 模块 | 谁干 | 可机器验收的产出 |
|---|---|---|---|
| B1 | `desktop/` 骨架：main.py + main_window + 侧边栏 + dark.qss + paths.py | **外派** | `python main.py` 起窗、页面可切、不监听端口、启动 < 2 s |
| B2 | 实验向导（范式选择 / 视频导入 / 隔间数 / 悬挂点或水面确认 / 计分窗口） | **外派**（窗口默认值由我给死） | 向导产出一份 `experiment.json`，字段与 `runner.TrialPlan` 一一对上 |
| B3 | 分析队列 + 进度 + 取消（`workers/analysis_worker.py`，`spawn`） | **外派** | 跑完产出与 `cli/analyze.py --csv` **逐位相同**的 CSV |
| B4 | 结果页：trial 表 + 分母列 + `validity_status` + 报警行 | **外派** | 每个秒数旁必须有分母，缺一列即拒收 |
| B5 | 时间线复核视图（逐帧掩膜叠加 + 事件带） | **外派** | 与 `timeline.py` 输出一致；不许在外壳里重算事件 |
| B6 | 导出：xlsx + pdf 报告 + 审计包 | **外派**（报告文案我写） | 报告必印分母、θ_mob、发布态声明；pdf 可无字体报错生成 |
| B7 | 自检页：对比度 ≥ 100 灰阶且 ≥ 2×、分割噪声底、面积抖动 p90 | **外派** | 复现 P2 硬门读数（对比度 208.5 / 6.77×；噪声底 0.43 px；p90 = 0.0035） |
| B8 | `calibration.json` 契约 + 模式徽章 + 哈希校验 | **我自己干**（这是资质边界） | 篡改标定文件 ⇒ 必须降级且变红，测试直接踩这条路径 |
| B9 | CSI 参数互通：读 + **生成** `.SET` | **我自己干**（逆向裁决） | 三份 `.SET` 全部正确解析；生成的文件被 CSI 自己读回 |
| B10 | 打包：`build_windows.spec` + Inno Setup + Actions | **外派** | Actions 出 `DEPRESSION-PLEX-Setup-x.y.z.exe`，客户机双击可装可跑 |
| B11 | ffmpeg 随包分发与路径解析 | **外派** | 断网、无系统 ffmpeg 的干净 Win 机上能解码 |

派工单一律按 WORKFLOW §8 七段写，分支名由我给死，输入绝对路径由我先确认存在。

## 7. 里程碑

| 里程碑 | 内容 | 判定 |
|---|---|---|
| **M0** 骨架 | B1 | Win + Mac 双端起窗，进 Actions |
| **M1** 能跑完一场 | B2 + B3 + B4 | 一段真视频从导入到 trial 表，CSV 与 CLI 逐位相同 |
| **M2** 能交付研究版 | B6 + B7 + B8 + B10 + B11 | 客户机装上、跑完、导出带声明的报告；徽章为黄 |
| **M3** 可复核 | B5 + B9 | 复核视图与 `timeline` 一致；`.SET` 双向互通 |
| **M4** 计量版 | 轨道 A 完成 → 签发 `calibration.json` | 徽章转绿，报告印验收批次号 |

**M2 不等 M4。** 研究版先进客户实验室换真实反馈与真实素材，是本项目最缺的东西
（现有 27 试次全是 T2 层，且素材是多年前的客户旧料）。

## 8. 需要道俊裁决的事（agent 不许替他选）

1. **FST 计分窗口**（DP-057/DP-074）：录像起点是否为入水？还是按每段实长各自定窗？
   这条不定，FST 一侧的所有秒数都不许出。
2. **M2 研究版是否现在就发客户**：发，就能换到新素材与真实反馈；发，就必须接受客户看到黄徽章。
3. **秒表工具 v1.6（含 seek/逐帧）什么时候换给正在评分的两位同事**——他们手上是 v1.4，中途换有协调成本。
4. **重评的 4 个试次算哪一遍**（DP-077 遗留的真值口径）。
5. **`.SET` 收口所需的那份文件**：13 个数互不相同的 `.SET`，不用跑分析，填参数保存即可。
6. **逆向工程报告的位置**：本沙箱无 Bridge，`/Users/dylanchen2000/Work/depression抑郁绝望` 与
   `C:\反向软件\work\Depressionscan_HR_reverse\report\2026-09-09_逆向工程-DepressionSuite-report.md`
   都不可达；在 `depressionplex` 仓全部 ref 里搜 逆向/reverse/Depressionscan_HR **零命中**
   ⇒ 位置**未确认**（不等于不存在），需道俊提供。

## 9. 风险

| 风险 | 应对 |
|---|---|
| 研究版被当计量版用 | §4 的徽章 + 报告强制声明 + 无手动切换路径 |
| 外派 agent 在外壳里重算指标 | §3 边界铁律 + 派工单 §5「不许做什么」明写 + 验收比对 CLI 逐位相同 |
| 精度轨卡在 T1（人力密集） | M2 先交付，不让产品化被真值层堵死 |
| 打包踩 Mac 打不出 .exe | 继承 drugeffectscan 结论：日常在联想 Win 机打，里程碑用 Actions windows-latest |
| 素材只有旧料、只有 T2 层 | M2 进客户现场换新素材；背光采集是最高杠杆项（README 已定 ≥100 灰阶 / ≥2×） |
