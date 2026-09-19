"""DP-136 · FST 独立研究入口（research entry）。

**这是什么**：一条不借 TST 判据、不借 CSI 程序/DT/结果就能跑的研究路径。
吃真实 FST 录像（媒体时间轴原样保留），产出**研究诊断**：每杯的分割质量、
动物可见性、候选特征、叠加短片。**不产出**正式 CSV、不动 TST 冻结口径、
不借用 TST 的发布/标定资质。

**这不是什么**：不是产品分析通道。本包任何输出都带
`protocol_alignment` 与几何 `confirmed=False` 的显式标记，
在时间窗与几何被人工确认之前，这些数字**只能**当诊断看。

三条硬边界（Spec A §4 A1 / §6 / §8）：

1. **路径隔离**——本包不 import `depressionplex.runner`、
   `depressionplex.assay_core.rules`、`depressionplex.csi`，
   不调胶带走廊/悬挂点/背光面板那套 TST 专属判据。
   机器守卫见 `isolation.audit_package` 与 `isolation.tst_forbidden_raising`
   （测试 `tests/test_fst_isolation.py`）。
   也不许"只在末尾判一下 `assay == 'FST'`、前面继续跑悬挂点与胶带走廊"。
2. **时间不猜**——三个时钟分开记；t0（入水）未取得时
   `protocol_alignment="unknown"`，诊断走媒体时间轴，
   **绝不**把录像第 120 秒当入水后第 120 秒，也**绝不**自动截最后 4 分钟。
3. **几何不猜**——杯体/水线只出**提案**（`confirmed=False`），
   绝不把没讲清出处的 CLB 数字换算成坐标。人工确认之前，
   正式解释与发布验收受限（Spec A §6.2 第 4 行）。

复用的范式无关件：`video.*`（解码）、`assay_core.silhouette.*`、
`assay_core.segment.otsu_threshold/label_components`、
`assay_core.geometry.GeometryEnvelope`（FST 角色已预留：tank + water_surface）、
`assay_core.rad.*`（刚体/关节分解）。
"""

from __future__ import annotations

#: 本包的 schema 版本。诊断 JSON 的第一行就是它，读的人先对版本。
SCHEMA_VERSION = "fst-research-v1"

#: 研究诊断的用途标记。任何下游想把本包数字当正式结果用，先读这一行。
PURPOSE = "research_diagnostics_only"
