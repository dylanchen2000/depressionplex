# 开发规格 DP-094：CSI DepressionScan（FST）结果导入 + 人工对照

> 交给 agent 做。**本规格里的口径、字段名、金标准数字都是契约**，不许改、不许"优化"、不许自行加门槛。
> 任何一处对不上，**在 PR 正文里写清楚然后停下**，不要猜着往下写。

## 0. 背景（三句话）

> **2026-09-10 修订**：录像已从 5 段补齐到 **7 段 / 28 孔位**，Settings 面板全部拍到，
> `.SET` 已基本解开。**本次修订推翻了 §2、§4.3、§7.3 里的三处旧结论**，逐条标了「已修正」。
> 参数与格式的完整实录见 `docs/备忘_CSI_FST参数与格式_2026-09-10.md`，本规格只放契约。

CSI 已经跑完全部 **7 段** FST 录像、共 **28 个孔位**，导出了每孔位事件时间线（`.xlsx`）、
参数文件（`.SET`）、标定文件（`.CLB`）和汇总（`Bin导出数据.xlsx` 20 孔位、
`28只鼠bin导出数据.xlsx` 28 孔位）。
本模块把这些读进来，做成能和人工真值并排比的一张表。

**这 28 只鼠全部用同一份 `10mg 2周.SET`**（用户确认）——参数是冻结的，
所以不需要按录像分别读参数文件。

**人工侧不用做任何事** —— `depressionplex/human_agreement.py` 里的
`union_holds` / `mobile_union_s` / `immobility_s` 已经是唯一口径（DP-010 起）。
**本 PR 不许碰 `human_agreement.py`**，只许调用它。

## 1. 铁律

| # | 铁律 |
|---|---|
| R1 | CSI 侧唯一可用的不动时长是 **`Ranges` 口径**，即事件时间线里 `Immobile` 事件的 `Length` 之和 |
| R2 | **禁止使用 `Average of Frames and Ranges`**，也禁止使用 `Frames/Time`。理由见 §2，写进 docstring |
| R3 | 不许给 `.SET` / `.CLB` 里未经手册确认的偏移量起名字，一律叫 `unknown_off_<十进制偏移>` |
| R4 | 不许设任何门槛、不许判定"CSI 对/错"、不许把 CSI 当调参目标。**本模块只做对照列** |
| R5 | 不许改 `depressionplex/human_agreement.py`、`depressionplex/assay_core/`、`tools/timer/` |
| R6 | 只有 §6 列出的 3 个小文件允许作为测试夹具进 git，**`.BMP` 一律不许进 git**（每个 370 KB） |
| R7 | 不许 `git add -A` |

## 2. R2 的证据（不要重新论证，照做）

**先说最硬的一条证据**（本次修订新增）：CSI 自己的 General 面板上，
`Score Method` 的四个选项里 `Range Scores (Smoothed)` 后面标着 **`(BEST)`**，
而且这批数据就是用它跑的。**是 CSI 自己认定 `Ranges` 才是它的正式结论**，
`Frames/Time` 和 `Average of Frames & Ranges` 只是另两个可选口径的附带值。

部分 `.xlsx` 底部有一个 `Statistics` 块，长这样（`正常1-4（1）.xlsx`）：

```
Statistics                      | Escape | Immobile | Climb
Frames/Time:                    | 323.36 | 207.36   | 0.0
Ranges:                         | 363.40 |  21.76   | 0.0
Average of Frames and Ranges:   | 343.36 | 114.56   | 0.0
```

**⚠ 已修正：这个块不是每份都有。** 前 20 个孔位（5 段录像）有，
后 8 个孔位（`抑郁8-10`、`正常5+抑郁1-3`）**完全没有这个块**。
所以 §4.2 里跟 `statistics` 有关的自检**必须是「有才校验」**，缺了不算错。

已在**有这个块的那 20 个**孔位上实测确认三件事：

1. **`Average = (Ranges + Frames/Time) / 2`**，误差 ≤ 0.02 s。
   把一个"按段计"的量和一个"按帧计"的量做算术平均，**没有物理含义**，所以不能用。
2. **`Ranges` 三类（`Immobile` + `Swim` + `Escape`，个别场加 `PassDive`）之和 = 录像窗口**，
   每段录像的 4 个孔位这个和是同一个数：

   | 录像 | Ranges 合计（4 孔位） | 人工侧 `window_s` |
   |---|---|---|
   | 10mg 2周 | 382.44 / 382.44 / 382.44 / 382.16 | 382.48 |
   | 20mg 1周 | 380.36 / 380.64 / 380.64 / 380.64 | 380.68 |
   | 20mg 2周 | 382.04 / 382.32 / 382.32 / 382.32 | 382.36 |
   | 抑郁4-7 | 362.16 / 362.16 / 361.88 / 361.88 | 362.20 |
   | 正常1-4 | 394.68 / 394.68 / 394.04 / 394.68 | 394.72 |
   | 抑郁8-10 | 467.52 / 467.52 / 467.24 / 467.24 | 467.56 |
   | 正常5+抑郁1-3 | 365.52 / 365.52 / 365.52 / 365.52 | 365.56 |

   **所以 `Ranges` 是对时间的一个划分，和人工的"窗口 − 并集"同一个量纲。**
   28/28 全部成立，与人工 `window_s` 的差恒在 0.04–0.68 s 之间。
   （这个差有解释：CSI 的 Start/Stop 面板设的是 `Start = Current Position`、
   `Stop = After Analyzing for 0 seconds`，**窗口起点是操作者当时的播放位置**，
   没有任何自动对齐入水时刻的机制。）
3. `Frames/Time` 有场次达到整段窗口（`正常1-4（3）` = 394.04 = 全窗口），
   显然是"逐帧判定为不动的帧数"，没过最小段长/合段逻辑，与人工不可比。

**⚠ 已修正：`Climb` 不是恒为 0。** 旧版写「20 个孔位全部为 0.0，攀爬类从未触发」，
扩到 28 个孔位后这句不成立：

| 孔位 | `Climb` 时长 |
|---|---|
| 正常5+抑郁1-3（2） | 7.76 s |
| 正常5+抑郁1-3（4） | 12.68 s |
| 其余 26 个 | 0 |

所以 **`Climb` 是一个会真实触发的事件类，必须进 `CSI_EVENTS`**，
而且它出现时会占掉窗口的一部分（上面那两个孔位的四类之和才等于窗口）。
这件事在 §5 的报告里作为一行事实输出，**不要下结论**
（它和 `Escape` 的判定边界未知；面板上 `ClimbHeightThresh=10` / `ClimbMagnThresh=15`）。

## 3. 交付物

```
depressionplex/csi/__init__.py
depressionplex/csi/xlsx.py            # 只用标准库的最小 xlsx 读取
depressionplex/csi/fst_import.py      # 事件时间线 / Statistics / Bin / SET / CLB
depressionplex/cli/csi_fst_compare.py # 生成对照表
tests/test_csi_fst_import.py
tests/fixtures/csi_fst/...            # 见 §6
```

新增测试文件必须登记进 `run_tests.py` 的清单（repo 已有
`test_run_tests_registry.test_all_test_files_registered` 会卡你）。

## 4. 接口

### 4.1 `depressionplex/csi/xlsx.py`

只用 `zipfile` + `xml.etree.ElementTree`，**不许引入 openpyxl 或任何新依赖**。

```python
def read_sheet(path, sheet_index: int = 0) -> list[list[object]]:
    """返回按行的单元格值。数字返回 float，文本返回 str，空单元格返回 None。

    必须处理 sharedStrings.xml（CSI 的字符串都在共享表里）和稀疏行/列
    （用 r="B7" 之类的引用定位，不能按出现顺序数）。
    """
```

### 4.2 `depressionplex/csi/fst_import.py`

```python
#: 28 个孔位里实际出现过的全部事件名。`Climb` 在 2 个孔位触发（7.76 / 12.68 s），
#: 不许省掉；出现未列入的名字要抛 CsiParseError，不许静默丢弃。
CSI_EVENTS = ("Immobile", "Swim", "Escape", "Climb", "PassDive")

#: CSI 孔位号 → 我们的 chamber 号。证据：正常1-4 的 4 个孔位 Ranges-Immobile
#: 依次为 21.76 / 104.36 / 208.72 / 23.24，人工（陈璇 09-10）依次为
#: 43.62 / 109.29 / 215.75 / 37.90，大小顺序与量级都对齐，故取恒等映射。
#: 这是**只有一段录像支持的假设**，别处不许再假定，需要更多录像验证。
CSI_TANK_TO_CHAMBER = {1: 1, 2: 2, 3: 3, 4: 4}

#: CSI 文件名 → 清单里的录像名。CSI 那边 "20mg 1周" 中间有空格，清单里没有。
RECORDING_ALIASES = {"20mg 1周": "20mg1周"}

def parse_tank_filename(stem: str) -> tuple[str, int]:
    """从文件名主干取 (录像名, 孔位号)。

    **（N）后面可能还有人写的后缀**，实测存在 `抑郁8-10（4）空鼠`。
    所以正则是 `^(?P<rec>.+?)（(?P<tank>\\d)）(?P<suffix>.*)$`——
    **不许用 `$` 直接顶在 `）` 后面**，那样会漏掉空鼠那一份（而且是静默漏掉，
    最危险的一类 bug：空杯位悄悄消失，正好是我们要盯的那一场）。

    返回的录像名要过 RECORDING_ALIASES。suffix 不参与匹配，但要原样存进
    CsiTank.source_file 以便追溯。
    """

def parse_time_label(s: str) -> float:
    """CSI 的时间标签 → 秒。见过的格式：`0"`、`30"`、`1'39"`、`10'05"`。
    解析不出来抛 CsiParseError，**不许返回 0 兜底**。
    """

@dataclass(frozen=True)
class CsiEvent:
    from_s: float      # 注意：CSI 导出的 From/To 已被四舍五入到整秒，只能当参考
    to_s: float
    length_s: float    # 这一列是 0.04 s 的整数倍（25 fps），是唯一精确的量
    event: str

@dataclass(frozen=True)
class CsiTank:
    source_file: str
    recording: str     # 已过 RECORDING_ALIASES
    tank: int
    chamber: int
    events: tuple[CsiEvent, ...]
    ranges_s: dict[str, float]        # 事件名 → Length 之和（= Ranges 口径）
    statistics: dict[str, dict[str, float]]  # 'Frames/Time' / 'Ranges' / 'Average...' → {事件名: 值}
                                             # 块缺失时为空 dict（8/28 个孔位如此），只含 Escape/Immobile/Climb
    window_s: float                   # = sum(ranges_s.values())

def parse_tank_xlsx(path) -> CsiTank:
    """解析一个孔位的事件表。

    行结构：第 1 行表头 `From Time | To Time | Length | Event`；
    之后是事件行；**遇到第 0 列为 `Statistics` 的那一行就停止收事件**，
    该行第 1..3 列是事件名表头，往下每行第 0 列是度量名。

    **`Statistics` 块可能整个不存在**（28 个孔位里有 8 个没有）。
    没有时 `statistics` 返回空 dict，**不是错误**，下面带 ★ 的自检整体跳过。

    自检（不通过就抛 CsiParseError）：
      - 每个 length_s 是 0.04 的整数倍（容差 1e-6）
      - 出现的事件名都在 CSI_EVENTS 里
      - ★ `ranges_s[e]` 与 `statistics['Ranges'][e]` 相等（容差 0.02），对所有出现的 e
      - ★ `statistics['Average of Frames and Ranges'][e]`
        == (statistics['Ranges'][e] + statistics['Frames/Time'][e]) / 2（容差 0.02）

    注意 `Statistics` 块的列只有 `Escape | Immobile | Climb` 三列——
    **`Swim` 和 `PassDive` 不在里面**，所以 ★ 那两条只对块里出现的事件名校验，
    不许因为 `ranges_s` 里有 `Swim` 而报错。
    """

def immobile_ranges_s(tank: CsiTank) -> float:
    """CSI 侧唯一可用的不动时长。见 R1/R2。"""

def parse_bin_xlsx(path) -> list[dict]:
    """解析 Bin导出数据.xlsx。表头：
    `Trial ID | Tank ID | Events | Bouts1 | Total Bouts | Duration1(s) | Total Duration`。
    返回每 (trial_id, tank_id, event) 一条 dict。

    有两份：`Bin导出数据.xlsx`（5 段录像 / 20 个 trial）和
    `28只鼠bin导出数据.xlsx`（7 段 / 28 个 trial）。**trial 数不要写死**，按文件里实际的算。

    **Bin 里的 Trial ID 是 1..N 的序号，不带录像名**，所以：
    """

def match_bin_to_tanks(bin_rows, tanks: Sequence[CsiTank]) -> dict[int, CsiTank]:
    """把 Bin 的 Trial ID 对上录像/孔位——**靠时长匹配，不靠顺序猜**。

    对每个 Bin trial，取它各事件的 Total Duration 向量，和每个 CsiTank 的
    ranges_s 向量比；全部事件都在 0.02 s 内相等才算命中。
    要求：命中恰好一对一（N ↔ N，N 由文件决定）；不是一对一就抛 CsiParseError 并把
    冲突的 trial 号打出来。

    **注意空杯位那一场是个真实的歧义风险**：`抑郁8-10（4）` 只有 2 个事件
    （Immobile 466.88 + Swim 0.36），向量非常特殊，本来好匹配；
    但如果匹配时只比 `Immobile` 一项就会和别人撞。**必须比全部事件、包括零值项。**
    """
```

### 4.3 `.SET` 与 `.CLB`

```python
SET_MAGIC = b"FSS3"

def parse_set(path) -> dict:
    """解析 CSI 的 FST 参数文件。

    **本表已按 Settings 面板 7 页截图逐项核对过**（2026-09-10），
    不再是"按手册默认值猜偏移"。凡"依据 = 面板"的字段，界面上的值和字节完全吻合。

    | 偏移 | 类型 | 含义 | 本文件值 | 依据 |
    |---|---|---|---|---|
    | 0  | 4s      | magic `FSS3`  | —  | 实测 |
    | 4  | int32   | 孔位数        | 4  | 实测，与 4 个 xlsx 一致 |
    | 8+12i | int32×3 | 孔位 i 的 (Animal Size Threshold, Above Water Contrast, Under Water Contrast) | 见 §7.2 | **面板**（Current Tank 1 = 200/80/70 完全吻合） |
    | 56 | int32   | **未知**      | 18 | —— |
    | 60 | int32   | Frame Padding Size | 10 | 面板 |
    | 64 | int32   | Bkgd Gen Thresh    | 5000 | 面板 |
    | 68 | int32   | Only Change BG Above Water（布尔） | 1 | 面板（勾选） |
    | 72 | float32 | High-Side Cutoff     | 0.2  | 面板 |
    | 76 | float32 | Low-Side Cutoff      | 0.01 | 面板 |
    | 80 | float32 | Learning Memory Factor | 0.95 | 面板 |
    | 84–98 | 15 字节 | 布尔/单选打包区，**未定名** | 见 §7.2 | —— |
    | 99  | float32 | Struggle/Esc Thresh   | 0.11 | 面板 |
    | 103 | float32 | Float/Immobile Thresh | 0.09 | 面板 |
    | 107–158 | int32×12 + float32×1 | Motion Setting 的 13 个数，**逐项对应未定** | 见下 | 多重集吻合 |
    | 159/163/167 | int32 | **未知**（疑似单选索引） | 1/1/1 | —— |
    | 171/175 | int32 | **未知** | 0/0 | —— |
    | 179 | float32 | 哨兵 | −1.0 | 实测 |
    | 183 | int32   | 哨兵 | −1   | 实测 |
    | 187–1610 | 全零 | 保留 | 0 | 实测 |

    **⚠ 已修正**：旧版写「偏移 56 的 18 = Above/Under Water Contrast，手册默认 18 ✓」。
    **错的。** 面板上 Above = 80、Under = 70，两者都在每孔位三元组里。
    偏移 56 的 18 对不上面板任何字段，**保持 `unknown_off_56`**。

    **99 起不是 4 字节对齐**（前面 84–98 是 1 字节成员打包区）。按字节偏移读，
    不要假定 4 对齐，否则 0.11 / 0.09 都读不出来。

    107 起读出的 13 个数是 15, 10, 15, 2.0f, 5, 15, 15, 10, 20, 5, 10, 20, 5。
    面板上 Motion Setting 恰好也有 13 个数值字段
    （Struggle 组 20/15/10/5/5/15/10/15/2 + Float 组 20/15/10/5），
    **两个多重集完全相同**（20×2、15×4、10×3、5×3、2×1）。
    所以这 13 个字段确实都在这里，但**面板顺序 ≠ 文件顺序**。
    → 本 PR 把它们解析成 `motion_ints`（有序 list），
      **一个都不许单独命名**（见 R3）。定序要等第二份参数不同的 `.SET`。

    返回 dict，键名：
      `n_tanks`、`tank_triples`（list[tuple[int,int,int]]）、
      `frame_padding`、`bkgd_gen_thresh`、`only_change_bg_above_water`、
      `high_cutoff`、`low_cutoff`、`learning_memory`、
      `struggle_esc_thresh`、`float_immobile_thresh`、
      `motion_ints`（13 个，float32 那一项按 float 存）、
      `bool_block`（84–98 的 15 字节，bytes 原样）、
      其余一律 `unknown_off_56` / `unknown_off_159` 这种键名。

    magic 不对就抛 CsiParseError。
    """

def parse_clb(path) -> dict:
    """`.CLB` 是固定 308 字节。本阶段**只做无损转储**，不解释：
    返回 {'size': 308, 'sha256': ..., 'int32': [...77 个...], 'float32': [...77 个...]}。
    不许给任何字段起名字。
    """
```

## 5. 对照表 `depressionplex/cli/csi_fst_compare.py`

```
python3 -m depressionplex.cli.csi_fst_compare \
    --csi-dir <CSI 目录> \
    --human-dir data/human_scores/incoming \
    --out data/derived/fst_human_vs_csi.csv
```

按 `(recording, chamber)` 把 CSI 行和人工行连起来。人工行**只用
`human_agreement.load_audit_json` 出来的 `TrialRow`**，取 `immobility_s`。
人工同一场有多人多批 → **每人每批一行，不许平均、不许挑一个**。

输出列（顺序钉死）：

```
recording, chamber, trial_id,
scorer_id, batch_date, human_window_s, human_immobility_s,
human_n_hold_segments, human_holds_unsorted, human_naive_inflation_s,
csi_window_s, csi_immobile_ranges_s, csi_swim_s, csi_escape_s, csi_passdive_s,
csi_climb_ranges_s, csi_immobile_frames_time_s, csi_immobile_average_s,
delta_csi_minus_human_s, window_mismatch_s
```

- `batch_date` 从审计文件名里的 `_(\d{4}-\d{2}-\d{2})_` 取，取不到就报错
- `csi_immobile_frames_time_s` / `csi_immobile_average_s` **只作为体检列原样输出**，
  任何聚合都不许用它们（R2）
- `window_mismatch_s = csi_window_s − human_window_s`
- CSI 没跑的录像 → **不出行**，并在 stderr 打一行 `CSI 未覆盖：<录像名>`。
  **⚠ 已修正：7 段录像现在全部有 CSI 结果**（旧版说 `抑郁8-10`、`正常5+抑郁1-3` 没跑），
  所以正常情况下这条分支一行都不该打印。**如果打印了，说明目录不全，在 PR 里说清楚。**
- 人工 `status != 'accepted'` 的记录 → 出行但 `human_immobility_s` 留空。
  这正好是 4 条空杯位记录（`FST-抑郁8-10-ch4`，`window_s=None`、`declared_empty=true`）。
  **CSI 对这个孔位报的是 `Immobile = 466.88 s` / 窗口 467.24 s。**
  这一行必须出，且 `delta_csi_minus_human_s` 留空——
  **不许用 0 兜底、不许把它当成一个 466.88 的有效读数参与任何统计。**

同时打一份 markdown 摘要到 stdout：每个 `(scorer_id, batch_date)` 一行，
列 `场数 / 平均 |CSI − 人工| / 偏差均值(CSI − 人工)`。
**不许有任何"通过/不通过"字样。**

## 6. 测试夹具（只许这 **5** 个文件进 git）

拷到 `tests/fixtures/csi_fst/`：

| 文件 | 大小 | 用途 |
|---|---|---|
| `正常1-4（1）.xlsx` | 10 KB | 事件表 + **有** Statistics 块的金标准 |
| `正常5+抑郁1-3（2）.xlsx` | 12 KB | **无** Statistics 块 + `Climb` 非零（7.76 s）——新增 |
| `抑郁8-10（4）空鼠.xlsx` | 9 KB | **空杯位** + 文件名带后缀，两个坑一次盖住——新增 |
| `10mg 2周.SET` | 1611 B | `.SET` 布局金标准 |
| `正常1-4.CLB` | 308 B | `.CLB` 转储金标准 |

合计约 33 KB。**`.BMP`（每个约 370 KB）一律不进 git**（R6）。
`Bin导出数据.xlsx`（20 孔位，12.9 KB）和 `28只鼠bin导出数据.xlsx`（28 孔位，14.6 KB）
**也不进 git**；Bin 相关的测试用手写的小 xlsx 或直接跳过（在 PR 里说清楚哪种）。
`match_bin_to_tanks` 的真数据验证用 `28只鼠bin导出数据.xlsx` 跑一遍、
把结果贴在 PR 正文里即可，**不要把它拷进仓库**。

## 7. 金标准数字（照抄，不许改）

### 7.1 `parse_tank_xlsx('正常1-4（1）.xlsx')`

| 项 | 期望 |
|---|---|
| 事件行数 | 31 |
| `ranges_s['Immobile']` | 21.76 |
| `ranges_s['Swim']` | 9.52 |
| `ranges_s['Escape']` | 363.40 |
| `window_s` | 394.68 |
| `statistics['Frames/Time']['Immobile']` | 207.36 |
| `statistics['Ranges']['Immobile']` | 21.76 |
| `statistics['Average of Frames and Ranges']['Immobile']` | 114.56 |
| `statistics['Ranges']['Climb']` | 0.0 |
| 第 1 条事件 | `from_s=0, to_s=0, length_s=0.28, event='Swim'` |
| 第 2 条事件 | `from_s=0, to_s=30, length_s=30.2, event='Escape'` |
| `parse_time_label("1'39\"")` | 99.0 |

### 7.2 `parse_set('10mg 2周.SET')`

| 键 | 期望值 |
|---|---|
| `n_tanks` | 4 |
| `tank_triples` | `[(200,80,70), (200,70,60), (200,80,80), (200,60,60)]` |
| `unknown_off_56` | 18 |
| `frame_padding` | 10 |
| `bkgd_gen_thresh` | 5000 |
| `only_change_bg_above_water` | 1 |
| `high_cutoff` / `low_cutoff` / `learning_memory` | ≈0.2 / ≈0.01 / ≈0.95（容差 1e-6） |
| `struggle_esc_thresh` | ≈0.11（容差 1e-6） |
| `float_immobile_thresh` | ≈0.09（容差 1e-6） |
| `motion_ints` | `[15, 10, 15, 2.0, 5, 15, 15, 10, 20, 5, 10, 20, 5]` |
| `bool_block` | `bytes([0,1,1,1,0,0,0,0,0,0,0,1,0,0,0])` |
| `unknown_off_159` / `163` / `167` / `171` / `175` | 1 / 1 / 1 / 0 / 0 |

再加一条测试：`sorted(motion_ints) == sorted([20,15,10,5,5,15,10,15,2,20,15,10,5])`
（多重集与面板一致），并且**不许**断言逐项对应关系。

> **四个孔位的三元组不一样**，但这**不**代表参数没冻住（旧版对此存疑，现已澄清）：
> 变的只有 Above/Under Water Contrast（80/70、70/60、80/80、60/60），
> `Animal Size Threshold` 四孔位都是 200。对比度是成像/分割参数，按杯子反光和水位单独调
> 是合理的；行为阈值（0.11 / 0.09 和那 13 个数）是全局的、只有一套。
> 而且**这批 28 只鼠全部用这一份 `.SET`**。
> **仍然不要在代码里下结论**，只如实解析。

### 7.3 全 **28** 孔位不变量（用真目录跑，测试里可标成需要外部数据、缺了就 skip）

- 孔位总数 28，录像 7 段，每段 4 个
- 每段录像 4 个孔位的 `window_s` 极差 ≤ 0.8 s
- 有 `Statistics` 块的孔位数 = 20；缺块的 8 个全部属于 `抑郁8-10` 和 `正常5+抑郁1-3`
- `PassDive` 只在 `抑郁4-7（1）` 出现，合计 6.08 s
- **`Climb` 只在 2 个孔位非零**：`正常5+抑郁1-3（2）` = 7.76 s、`正常5+抑郁1-3（4）` = 12.68 s
  （**⚠ 已修正**：旧版写「`Climb` 全为 0.0」，错的）

新增 8 个孔位的金标准（`ranges_s`，单位秒）：

| 孔位 | 事件数 | `window_s` | Immobile | Swim | Escape | Climb |
|---|---|---|---|---|---|---|
| 抑郁8-10（1） | 25 | 467.52 | 83.24 | 0.80 | 383.48 | 0 |
| 抑郁8-10（2） | 57 | 467.52 | 153.08 | 6.44 | 308.00 | 0 |
| 抑郁8-10（3） | 47 | 467.24 | 238.72 | 0.36 | 228.16 | 0 |
| **抑郁8-10（4）** | 2 | 467.24 | **466.88** | 0.36 | 0.00 | 0 |
| 正常5+抑郁1-3（1） | 28 | 365.52 | 48.60 | 8.56 | 308.36 | 0 |
| 正常5+抑郁1-3（2） | 91 | 365.52 | 47.08 | 21.16 | 289.52 | **7.76** |
| 正常5+抑郁1-3（3） | 48 | 365.52 | 37.72 | 2.24 | 325.56 | 0 |
| 正常5+抑郁1-3（4） | 92 | 365.52 | 13.36 | 52.88 | 286.60 | **12.68** |

`抑郁8-10（4）` 是**空杯位**（没有动物）。CSI 把它报成 `Immobile` 占窗口 **99.92%**。
**这必须写成一条测试**（`immobile_ranges_s / window_s > 0.99`），
作为"CSI 没有空孔位检测"的回归留证。**不许在本 PR 里加任何空杯判定逻辑**——
那是 G10 的事，架构师定。

### 7.4 人工侧（调用现成模块，用来确认连接没错位）

`load_audit_json` 扫 `data/human_scores/incoming/timer_audit_FST_*.json` 后：

| 项 | 期望 |
|---|---|
| 记录总数 | **92** |
| `status == 'accepted'` | **88** |
| `status == 'unscoreable'` | 4（全是空杯位 `FST-抑郁8-10-ch4`） |
| `naive_inflation_s > 0.5` 的记录数 | **41** |
| `holds_unsorted` 为真的记录数 | **42** |
| 徐乐彤 09-10 `FST-正常1-4-ch3` 的 `immobility_s` | 200.72 |
| 陈璇 09-10 `FST-正常1-4-ch3` 的 `immobility_s` | 215.75 |

> **⚠ 已修正**：旧版是 77 / 73 / 4 / 28 / 29，那是 PR #76 时的数。
> PR #78（`4209f24`）补进了徐乐彤 09-09 的三份导出（15 条记录），现在是上表这一版。
> `naive_inflation_s > 0.5` 和 `holds_unsorted` **不是同一批记录**（41 ≠ 42 且不互相包含）。
> 如果你跑出来不是这几个数，**照实写你跑出来的、并说明你的 `incoming/` 是哪个 commit**，
> 不许往这张表上凑。

徐乐彤的 28 场已经补齐，接力链闭合（每份的 `prior_done` / `cumulative`）：
`09-08 023656Z` 8/0/8 → `09-09 060639Z` 5/8/13 → `09-09 072636Z` 5/13/18 →
`09-09 085128Z` 5/18/23 → `09-10 final` 5/23/28，28 个 `trial_id` 唯一无缺口。
**`timer_audit_FST_徐乐彤_2026-09-08_partial8of28_023656Z(1).json` 是字节级重复上传
（md5 `870f6a21edac5bfc832a418eada90a76`），已故意不入库**，不要去找它。

## 8. 不在本 PR 范围内（碰了就是越界）

- 真值最终取谁（主评人 / 平均 / 裁决）—— 架构师定
- 任何一致性门、验收门 —— 架构师定
- 判断 CSI 的哪个参数该调、是否拿 CSI 做拟合目标 —— 架构师定
- `Climb` 只在 2 个孔位触发的归因 —— 架构师定
- 空杯位被 CSI 报成 99.92% 不动，这件事怎么进 G10 空杯对照门 —— 架构师定
- `.SET` 里那 13 个 Motion 数的逐项定序 —— 需要第二份参数不同的 `.SET`，本 PR 只存有序 list
- 孔位↔通道映射在别的录像上是否成立 —— 本 PR 只写成一个常量 + 证据注释。
  **注**：28 孔位齐了之后，`window_s` 在每段录像内 4 孔位一致，
  对映射**没有**提供新的区分力（4 个孔位窗口相同，分不出谁是谁），
  所以证据强度和旧版一样，仍只有 `正常1-4` 的排序一条。不许升级这个结论的措辞。
- TST 侧的 CSI 对照（`Section Size` 是多数票时间箱，量纲不同）—— 另案

## 9. 分支 / 提交 / PR

```bash
cd ~/Work/depression抑郁绝望/depressionplex
git checkout main && git pull --ff-only
git checkout -b feat/dp094-csi-fst-import

# 写 §3 那几个文件；夹具按 §6 只拷 5 个

python3 run_tests.py          # 必须全绿。当前基线：通过 252 失败 0
git add depressionplex/csi/ depressionplex/cli/csi_fst_compare.py \
        tests/test_csi_fst_import.py tests/fixtures/csi_fst/ run_tests.py
# 不许 git add -A
git commit -m "feat(DP-094): 导入 CSI FST 事件表与参数文件，出人工对照"
git push -u origin feat/dp094-csi-fst-import
gh pr create --title "feat(DP-094): 导入 CSI FST 事件表与参数文件，出人工对照" --body-file /tmp/pr_dp094.md
```

PR 正文必须有：

1. `python3 run_tests.py` 最后一行（通过数 / 失败数），以及新增了几个测试
2. `git diff --name-status main` 完整输出（确认没有 `.BMP`、没有动 `human_agreement.py`）
3. §7.4 那几个数字你实测的值 + 你用的是哪一版 `incoming/`
4. §7.2 `parse_set` 那张表你实测的全部键值
5. §7.3 那 8 个新孔位的实测值，以及空杯位那条测试的输出
6. `csi_fst_compare` 跑一遍的 markdown 摘要原样贴上来
7. `match_bin_to_tanks` 对 `28只鼠bin导出数据.xlsx` 的匹配结果（28 ↔ 28 是否成立）
8. 任何和本规格对不上的地方，以及你怎么处理的

**不要自己合并。**

## 10. 参数与格式的完整实录

Settings 面板 7 页的逐项实录、`.SET` 全字节表、导出格式细节，
都在 `docs/备忘_CSI_FST参数与格式_2026-09-10.md`。
**那份是参考资料，本规格才是契约**；两边冲突时以本规格为准，并在 PR 里指出来。
