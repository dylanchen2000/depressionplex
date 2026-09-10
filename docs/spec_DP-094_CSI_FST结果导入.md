# 开发规格 DP-094：CSI DepressionScan（FST）结果导入 + 人工对照

> 交给 agent 做。**本规格里的口径、字段名、金标准数字都是契约**，不许改、不许"优化"、不许自行加门槛。
> 任何一处对不上，**在 PR 正文里写清楚然后停下**，不要猜着往下写。

## 0. 背景（三句话）

CSI 已经跑完 7 段 FST 录像里的 **5 段**，导出了每孔位事件时间线（`.xlsx`）、
参数文件（`.SET`）、标定文件（`.CLB`）和一份汇总（`Bin导出数据.xlsx`）。
本模块把这些读进来，做成能和人工真值并排比的一张表。

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

每份 `.xlsx` 底部有一个 `Statistics` 块，长这样（`正常1-4（1）.xlsx`）：

```
Statistics                      | Escape | Immobile | Climb
Frames/Time:                    | 323.36 | 207.36   | 0.0
Ranges:                         | 363.40 |  21.76   | 0.0
Average of Frames and Ranges:   | 343.36 | 114.56   | 0.0
```

已在全部 20 个孔位上实测确认三件事：

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

   **所以 `Ranges` 是对时间的一个划分，和人工的"窗口 − 并集"同一个量纲。**
3. `Frames/Time` 有场次达到整段窗口（`正常1-4（3）` = 394.04 = 全窗口），
   显然是"逐帧判定为不动的帧数"，没过最小段长/合段逻辑，与人工不可比。

`Climb` 这一列在 **20 个孔位全部为 0.0** —— CSI 这次的参数下攀爬类从未触发。
这件事要在 §5 的报告里作为一行事实输出，**不要下结论**（是参数问题还是它把攀爬并进 Escape，未定）。

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
CSI_EVENTS = ("Immobile", "Swim", "Escape", "PassDive")

#: CSI 孔位号 → 我们的 chamber 号。证据：正常1-4 的 4 个孔位 Ranges-Immobile
#: 依次为 21.76 / 104.36 / 208.72 / 23.24，人工（陈璇 09-10）依次为
#: 43.62 / 109.29 / 215.75 / 37.90，大小顺序与量级都对齐，故取恒等映射。
#: 这是**只有一段录像支持的假设**，别处不许再假定，需要更多录像验证。
CSI_TANK_TO_CHAMBER = {1: 1, 2: 2, 3: 3, 4: 4}

#: CSI 文件名 → 清单里的录像名。CSI 那边 "20mg 1周" 中间有空格，清单里没有。
RECORDING_ALIASES = {"20mg 1周": "20mg1周"}

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
    window_s: float                   # = sum(ranges_s.values())

def parse_tank_xlsx(path) -> CsiTank:
    """解析一个孔位的事件表。

    行结构：第 1 行表头 `From Time | To Time | Length | Event`；
    之后是事件行；**遇到第 0 列为 `Statistics` 的那一行就停止收事件**，
    该行第 1..3 列是事件名表头，往下每行第 0 列是度量名。

    自检（不通过就抛 CsiParseError）：
      - 每个 length_s 是 0.04 的整数倍（容差 1e-6）
      - `ranges_s[e]` 与 `statistics['Ranges'][e]` 相等（容差 0.02），对所有出现的 e
      - `statistics['Average of Frames and Ranges'][e]`
        == (statistics['Ranges'][e] + statistics['Frames/Time'][e]) / 2（容差 0.02）
      - 出现的事件名都在 CSI_EVENTS 里
    """

def immobile_ranges_s(tank: CsiTank) -> float:
    """CSI 侧唯一可用的不动时长。见 R1/R2。"""

def parse_bin_xlsx(path) -> list[dict]:
    """解析 Bin导出数据.xlsx。表头：
    `Trial ID | Tank ID | Events | Bouts1 | Total Bouts | Duration1(s) | Total Duration`，
    共 20 个 trial。返回每 (trial_id, tank_id, event) 一条 dict。

    **Bin 里的 Trial ID 是 1..20 的序号，不带录像名**，所以：
    """

def match_bin_to_tanks(bin_rows, tanks: Sequence[CsiTank]) -> dict[int, CsiTank]:
    """把 Bin 的 Trial ID 对上录像/孔位——**靠时长匹配，不靠顺序猜**。

    对每个 Bin trial，取它各事件的 Total Duration 向量，和每个 CsiTank 的
    ranges_s 向量比；全部事件都在 0.02 s 内相等才算命中。
    要求：命中恰好一对一（20 ↔ 20）；不是一对一就抛 CsiParseError 并把
    冲突的 trial 号打出来。
    """
```

### 4.3 `.SET` 与 `.CLB`

```python
SET_MAGIC = b"FSS3"

def parse_set(path) -> dict:
    """解析 CSI 的 FST 参数文件。已在 `10mg 2周.SET`（1611 字节）上验证的布局：

    | 偏移 | 类型 | 含义 | 依据 |
    |---|---|---|---|
    | 0  | 4s      | magic `FSS3`            | 实测 |
    | 4  | int32   | 孔位数（= 4）           | 实测，与 4 个 xlsx 一致 |
    | 8+12i | int32×3 | 第 i 个孔位的三元组（i=0..n-1） | 实测，**含义未知** |
    | 56 | int32   | Above/Under Water Contrast（= 18） | 手册默认值 18 ✓ |
    | 60 | int32   | Frame Padding Size（= 10）        | 手册默认值 10 ✓ |
    | 64 | int32   | 未知（= 5000）          | —— |
    | 68 | int32   | 未知（= 1）             | —— |
    | 72 | float32 | High-Side Cutoff（= 0.2）  | 手册默认 20% ✓ |
    | 76 | float32 | Low-Side Cutoff（= 0.01）  | 手册默认 1% ✓ |
    | 80 | float32 | Learning Memory Factor（= 0.95） | 手册默认 95% ✓ |
    | 84.. | —     | 四个约 276 字节的块，布尔/颜色/每孔位显示设置 | 未解 |

    返回 dict：已确认的用上表的名字（`n_tanks`/`contrast`/`frame_padding`/
    `high_cutoff`/`low_cutoff`/`learning_memory`/`tank_triples`），
    其余一律 `unknown_off_64` 这种键名。**见 R3，不许起名。**

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
- CSI 没跑的录像（`抑郁8-10`、`正常5+抑郁1-3`）→ **不出行**，并在 stderr 打一行
  `CSI 未覆盖：<录像名>`
- 人工 `status != 'accepted'` 的记录 → 出行但 `human_immobility_s` 留空

同时打一份 markdown 摘要到 stdout：每个 `(scorer_id, batch_date)` 一行，
列 `场数 / 平均 |CSI − 人工| / 偏差均值(CSI − 人工)`。
**不许有任何"通过/不通过"字样。**

## 6. 测试夹具（只许这 3 个文件进 git）

拷到 `tests/fixtures/csi_fst/`：

| 文件 | 大小 | 用途 |
|---|---|---|
| `正常1-4（1）.xlsx` | 10 KB | 事件表 + Statistics 金标准 |
| `10mg 2周.SET` | 1611 B | `.SET` 布局金标准 |
| `正常1-4.CLB` | 308 B | `.CLB` 转储金标准 |

`.BMP`（每个约 370 KB）和 `Bin导出数据.xlsx` **不进 git**；
Bin 相关的测试用手写的小 xlsx 或直接跳过（在 PR 里说清楚哪种）。

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

`n_tanks=4`、`contrast=18`、`frame_padding=10`、
`high_cutoff≈0.2`、`low_cutoff≈0.01`、`learning_memory≈0.95`（float 容差 1e-6），
`tank_triples == [(200,80,70), (200,70,60), (200,80,80), (200,60,60)]`。

> 顺带一个要在 PR 正文里点出来的事实：**四个孔位的三元组不一样**。
> 要么是操作者按孔位单独调过阈值（那就破了"参数冻结"），要么是 CSI 自己按孔位推的。
> **不要在代码里下结论**，只把它如实解析出来。

### 7.3 全 20 孔位不变量（用真目录跑，测试里可标成需要外部数据、缺了就 skip）

- 每段录像 4 个孔位的 `window_s` 极差 ≤ 0.8 s
- `Climb` 的 `Ranges` 全为 0.0
- `PassDive` 只在 `抑郁4-7（1）` 出现，合计 6.08 s

### 7.4 人工侧（调用现成模块，用来确认连接没错位）

`load_audit_json` 扫 `data/human_scores/incoming/timer_audit_FST_*.json` 后：

| 项 | 期望 |
|---|---|
| 记录总数 | 77 |
| `status == 'accepted'` | 73 |
| `status == 'unscoreable'` | 4 |
| `naive_inflation_s > 0.5` 的记录数 | 28 |
| `holds_unsorted` 为真的记录数 | 29 |
| 徐乐彤 09-10 `FST-正常1-4-ch3` 的 `immobility_s` | 200.72 |
| 陈璇 09-10 `FST-正常1-4-ch3` 的 `immobility_s` | 215.75 |

> 77 / 73 / 4 / 28 / 29 这几个数是在 **PR #76 合并之后**（含 09-10 两份）跑出来的。
> 如果你手上的 `incoming/` 还没有 09-10 那两份，这几个数都会小一些（少 13 条记录）。
> **那就照实写你跑出来的，并在 PR 里说明是哪一版数据**，不许凑成 77。

## 8. 不在本 PR 范围内（碰了就是越界）

- 真值最终取谁（主评人 / 平均 / 裁决）—— 架构师定
- 任何一致性门、验收门 —— 架构师定
- 判断 CSI 的哪个参数该调、是否拿 CSI 做拟合目标 —— 架构师定
- `Climb` 全 0 的归因 —— 架构师定
- 孔位↔通道映射在别的录像上是否成立 —— 需要更多 CSI 运行结果，本 PR 只写成一个常量 + 证据注释
- TST 侧的 CSI 对照（`Section Size` 是多数票时间箱，量纲不同）—— 另案

## 9. 分支 / 提交 / PR

```bash
cd ~/Work/depression抑郁绝望/depressionplex
git checkout main && git pull --ff-only
git checkout -b feat/dp094-csi-fst-import

# 写 §3 那几个文件；夹具按 §6 只拷 3 个

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
4. §7.2 那个"四孔位三元组不一样"的实测结果
5. `csi_fst_compare` 跑一遍的 markdown 摘要原样贴上来
6. 任何和本规格对不上的地方，以及你怎么处理的

**不要自己合并。**
