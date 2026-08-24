# DepressionPlex STATUS

> 快照，不是日志。永远只反映当前状态。历史在 git commit 与 Obsidian 里。
> 最后更新：2026-08-24

## 主线目标

做出 FST（强迫游泳）+ TST（悬尾）的商业行为分析软件，参数口径对标 CSI
DepressionScan，并在三处超越它（见 README）。

**起点范式：TST**（无水面反光/折射、是背光板的理想场景、最强差异化点正在 TST）。

## 已完成

### P3 第一批：几何语义图 + RAD + CSI bout 流水线（2026-08-24）

三个核心模块 + 38 个自验证测试全绿，**不依赖真实视频即可验证**。

| 模块 | 内容 | 状态 |
|---|---|---|
| `assay_core/geometry.py` | 几何语义图。固定 primitive_id + 语义角色 + 坐标空间校验。FST 角色 `tank`/`tank_wall`/`water_surface`；TST 角色 `chamber`/`suspension_bar`/`suspension_point`。命名对齐 EthoPlex Shared Assay Core 以便后续并入 | 完成 |
| `assay_core/silhouette.py` | 剪影几何量：BL、主轴、伸展度、弯曲度、**孔洞计数**（尾巴攀爬拓扑信号）、身体长轴自适应分段、水面线上下分区 | 完成 |
| `assay_core/rad.py` | **RAD 刚体-关节分解**：躯干核心估刚体 → warp → XOR 残差 = 关节主动运动。含试次级 BL 归一化、lag1/lag4 双时标 | 完成 |
| `assay_core/bouts.py` | CSI 兼容 bout 流水线，严格按手册顺序：First Combine → Noise → Combination → Length → Section Size 分箱投票。四种 Score Method | 完成 |

### 关键实测基线（BL² 归一化残差）

| 序列 | min | p50 | p95 | max |
|---|---|---|---|---|
| 纯刚体钟摆（应≈0） | 0.00512 | 0.00704 | 0.01025 | 0.01025 |
| 纯关节运动（主动挣扎） | 0.02023 | 0.03320 | 0.06904 | 0.06908 |

**两个分布完全不重叠**（钟摆 max 0.01025 < 关节 min 0.02023，约 2× 间隙）。
这意味着存在单一阈值可无误分开被动摆动与主动挣扎——正是 TST 金标准的要求，
而 CSI 的标量 blob 运动量在原理上做不到。θ_mob 的参考起点：0.015–0.02。

> 合成用例刻意保守：躯干完全不动、只有一条 34px 细肢摆动。真实 TST 主动挣扎
> 是全身扭动 + 四肢，残差应远高于此。

### 开发过程中发现并修掉的两个真实缺陷

1. **XOR 精修会吃掉关节运动**。以最小 XOR 精修旋转角，会主动转动去匹配移位的
   肢体，把关节运动当刚体旋转吸收，区分度 5.2→4.3。已改为 `refine` 默认关闭。
2. **逐帧 BL 被肢体伸展污染**。躯干 60px 的剪影伸腿时全剪影主轴量到 76px，
   BL² 分母被抬高 1.6 倍，残差被系统性压低。已改为**试次级 BL（时间中位数）**。
   另外刚体拟合改在**腐蚀后的躯干核心**上做，避免肢体动就带偏质心与主轴。

## 正在进行

无。等下一步指令。

## 下一步（按优先级）

1. **拿真实 TST 视频跑通 S1 阈值分割**，实测「轮廓面积逐帧抖动 ≤ 2% BL²」这道硬门。
   这是整条轮廓路线成立的前提，必须早验，不能等到后期。
2. `flow.py`：掩膜内稠密光流。补 XOR 对「剪影包络内部运动」的盲区。
   需实测背光是否压掉纹理导致光流失效（已知张力）。
3. `rules.py`：TST 事件判定（Mobility / Immobility / Passive Swing / Tail Climbing /
   Forelimb-only），含 AND/OR/N-of-M 组合语义（Shared Assay Core 明确缺这块）。
4. 原语标注工具 + 原语表定稿（P0 未完成项，是最大人力瓶颈的前置）。

## 阻塞

| 阻塞项 | 说明 |
|---|---|
| **沙箱无 Bridge** | 读不到 Mac 的 `Work/depression抑郁绝望`（视频在那里），也无法在 Mac 上跑 git |
| **无法创建 GitHub 仓库** | 沙箱 `gh` 配置损坏（`~/.config` 不是目录）、SSH 私钥 I/O 错误、无 GH token |
| 沙箱 site-packages 损坏 | `cv2`/`scipy`/`pytest` 均 I/O 错误且无法重装（pip 本身也坏）。核心模块因此**只依赖 numpy**（这本身是好事），但真实视频解码需要 cv2，得在 Mac 或修好的环境里做 |

## 环境注意

- 测试跑法：`python3 run_tests.py`（自带 runner，不依赖 pytest）
- 核心模块只依赖 numpy，刻意不引入 OpenCV——视频 I/O 层才需要
