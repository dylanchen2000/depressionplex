#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-135：造 B1 的最小差分探针 `.SET`，连来源记录与一页作业单一起交付。

**要收口的是哪一条**：`depressionplex/csi/fst_import.py` 的 `MOTION_PAIR_UNRESOLVED_IDX`
记着 4 对同类型 Motion 字段（min length / noise / merge / bin）的「挣扎侧 vs 放弃侧」
归属仍是**约定推断**，不是字节级证明。同一处注释早就写清了收口条件：

    收口只需一份把任一个参数的挣扎侧与放弃侧填成不同值的 .SET
    （例如 Min Length 挣扎 20 / 放弃 40）

而 2026-09-18 在 DP-133 的身份清单上量到的事实是：手头**3 份互不相同的 `.SET`**
（`10mg 2周.SET` / `正常1-4对照.SET` / `正常1-4对照更改.SET`）里，那 4 对的两侧
**全部相等**（15/15、15/15、18/18；(7,10)(8,11)(9,12) 同样相等）。
⇒ 归属**不可能**从现有材料解开。不是「还没做」，是「材料里没有这个信息」。
这个脚本就是把那份缺的材料造出来 —— 造在仓库外，交给人拿到 CSI 面板上读。

**为什么是脚本而不是一句 `write_set(...)`**：探针那两个值一旦被人随手改，
「面板上显示 20 的那一侧」就不再对应任何确定的下标，整轮收口作废。所以：

1. 每一对都有**记录的默认值**（`PAIR_PANEL_MAP[...]["recorded_default"]`，按已观察到
   的模板值挑的、两侧都与原值不同），也可以 `--values` **显式覆盖**；但**只许探未收口的那 4 对**，
   两个值相等直接拒绝（相等就没有信息量）。默认值是**这一批模板**的默认值，
   不是「已对所有字段、所有 CSI 版本验证过」的参数。
2. 产物旁边强制落一份 provenance JSON：模板哈希、产物哈希、改了哪几个字节、
   读回来的值、面板上的**真实行标题与真实分组标题**、以及 `csi_read_back_verified: false`。
3. 写完当场做**语义**复核（不只是字节复核）：Motion 只在探的那两个下标上变、
   其余 11 个数与 8 个标量字段与孔位三元组一字不动。
4. **SET 与来源记录是成对交付的**：两个目标路径先一起预检、再一起暂存校验、
   最后一起发布；任一步失败就把本次发布的文件撤回，**不留没有来源记录的探针在外面**。

**关于「成对保存」的诚实说明**：两次 `os.replace` **不是跨文件原子事务** ——
进程在两次 rename 之间被杀，磁盘上就会只剩前一份。所以默认模式发布完会再跑一次
`verify_pair_delivered` 复核，并且 `--package-dir` 提供**真正原子的交付单元**：
整个目录先建好、再**一次 rename** 落地（目录 rename 在 POSIX 上是原子的）。
交给人拿去 Windows 的，建议一律用 `--package-dir`。

**边界**（与 DP-104 / 架构 §3.3 / Spec B SPEC:166-167 对齐）：

- 产物**不进仓库、不进 `data/`、不进任何验收路径**。写路径落在仓库工作树里 ⇒ 拒绝。
- 产物**不许落在模板旁边**：客户的真实参数目录里多出一份探针，
  下一个人可能把它当成正式配置加载。⇒ 与模板同目录（或模板的祖先目录）一律拒绝。
- 产物**不许覆盖身份清单里已登记的任何素材**（按 realpath 比）。
- **不执行 CSI 原程序、不加载 DT。** 产物要拿到那台装了 DepressionSuite 的 Windows 机器上，
  由人打开面板看读数：作业单 `docs/作业单_DP-135_SET探针与Motion归属收口.md`，
  `--package-dir` 里还会附一页**随参数变化**的作业单（Noise / Merge / Bin 的探针
  不会叫去看 Min Length）。
- **只改，不造**：模板必须是客户自己的 `.SET`，其余字节逐位保留
  （`set_write.write_set` 的规矩，它自己会验一遍并对不上就删半成品）。

用法（推荐：整包交付）：

    /tmp/dpx311/bin/python scripts/dp135_make_set_probe.py \\
        --template "~/…/CSI分析强迫游泳数据_对照参数/正常1-4对照.SET" \\
        --package-dir ~/Work/csi_probe/正常1-4对照_minlen_probe \\
        --expect-template-sha256 fd9596e5a588e15c…

用法（只出两个文件，向后兼容）：

    … --template … --out ~/Work/csi_probe/正常1-4对照_PROBE_minlen_idx5-20_idx6-40.SET

**读回记录是另一支脚本**：`scripts/dp135_record_readback.py`。本脚本**永不**改写
生成记录（R113-05）；读回证据另存一份 `.readback.json`，按 sha256 指回原探针。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from depressionplex.csi.fst_import import (          # noqa: E402
    MOTION_FIELD_HINTS,
    MOTION_PAIR_UNRESOLVED_IDX,
    SET_MOTION_REL,
    parse_set,
)
from depressionplex.csi.set_write import (           # noqa: E402
    SET_SCALAR_FIELDS,
    write_set,
)

#: 探针默认打在 MinLengthThresh 那一对（idx5/idx6）：`fst_import.py` 注释里点名的例子。
PROBE_IDX_PAIR = (5, 6)
#: 默认值必须**不等**，且两侧都要与模板原值不同（否则面板上有一侧看不出变化）。
#: 20 是面板上本来就有的值 ⇒ 哪一侧显示 20、哪一侧显示 40，肉眼一次就能分清。
PROBE_VALUES = (20, 40)

MANIFEST = ROOT / "docs" / "共用输入身份清单_v1.csv"

#: 文件名主干里用的短名。
FIELD_SLUG = {(5, 6): "minlen", (7, 10): "noise", (8, 11): "merge", (9, 12): "bin"}

#: **每一对**的面板事实与默认值（R113-03：操作提示必须随参数变化）。
#:
#: - `panel_row_label` / `panel_group_*` 是 2026-09-10 那 7 页面板截图上的**真实标题**
#:   （左组 `Struggle/Escape/Climb Settings`，右组 `Float/Immobile Settings`）。
#:   **两个组里这四行的行标题完全一样**，所以作业单一律按**组标题**记，
#:   不按左右位置记（R113-03）—— 而且「哪个组对应低 idx」正是本探针要测的东西，
#:   所以 `panel_group_*` 只是**约定推断**下的标注，不是已证事实。
#: - `unit` 里 `unit_verified=False` 的就是面板上**没标单位**的字段，不许当已知。
#: - `recorded_default` 是**按已观察到的模板值挑的**（`observed_template_values`），
#:   两侧都与原值不同。它不是「对所有字段、所有 CSI 版本验证过」的参数。
PAIR_PANEL_MAP: dict[tuple[int, int], dict] = {
    (5, 6): {
        "field_type": "MinLengthThresh",
        "panel_row_label": "Min Length Thresh",
        "panel_group_high_convention": "Struggle/Escape/Climb Settings",
        "panel_group_low_convention": "Float/Immobile Settings",
        "unit": "面板未标注单位；备忘 §7.4 假设是帧（15 帧 @25 fps = 0.6 s），该假设未验证",
        "unit_verified": False,
        "observed_template_values": (15, 15),
        "recorded_default": (20, 40),
    },
    (7, 10): {
        "field_type": "NoiseThreshFrames",
        "panel_row_label": "Noise Thresh(Frames)",
        "panel_group_high_convention": "Struggle/Escape/Climb Settings",
        "panel_group_low_convention": "Float/Immobile Settings",
        "unit": "帧（面板行标题自带 `(Frames)`）",
        "unit_verified": True,
        "observed_template_values": (10, 10),
        "recorded_default": (12, 25),
    },
    (8, 11): {
        "field_type": "MergeBoutsLimit",
        "panel_row_label": "Merge Bouts Limit",
        "panel_group_high_convention": "Struggle/Escape/Climb Settings",
        "panel_group_low_convention": "Float/Immobile Settings",
        # 二轮复核 R2-113：不许因字段名里有 Bouts 就当段数。面板行标题没带
        # 单位；既有逆向证据反而按**帧**解释（全量验证报告 2026-09-10 §3.3：
        # 「逆向已确认 min/noise/merge 在后处理器中以帧数使用」⇒ 20 @25 fps
        # ≈ 0.80 s；备忘 §7.4 同口径）。名字不是单位证据 ⇒ 证据冲突、
        # 运行未验证，降为待确认；不加大实验、不改冻结参数。
        "unit": ("面板未标注单位。证据冲突待确认：字段名 Bouts 暗示段数，"
                 "但逆向报告（全量验证 §3.3）确认 min/noise/merge 在后处理器"
                 "中以帧数使用（20 @25 fps ≈ 0.80 s）；运行级验证未完成前"
                 "两种读法都不许当已证实"),
        "unit_verified": False,
        "observed_template_values": (20, 20),
        "recorded_default": (22, 35),
    },
    (9, 12): {
        "field_type": "BinSizeSeconds",
        "panel_row_label": "Bin Size (seconds)",
        "panel_group_high_convention": "Struggle/Escape/Climb Settings",
        "panel_group_low_convention": "Float/Immobile Settings",
        "unit": "秒（面板行标题自带 `(seconds)`）",
        "unit_verified": True,
        "observed_template_values": (5, 5),
        "recorded_default": (6, 11),
    },
}

#: 读回记录**至少**要绑住的五件事（R113-05）。写进 provenance 是为了让下一个人
#: 不用回来读返修单也知道「拿到截图」不等于「读回完成」。
READBACK_REQUIRED_EVIDENCE = [
    "原 EXE 的身份与版本（哈希 + 面板/关于页上能看到的版本号）",
    "探针 .SET 的 sha256（与本次生成记录一致）",
    "操作人与观测时间（谁在哪台机器上、什么时候看的）",
    "面板截图，且**能看到两个分组的真实标题**（Struggle/Escape/Climb Settings 与 Float/Immobile Settings）",
    "CSI 用 Save As 另存回来的 .SET 的 sha256 与其中被探两个下标的读数",
]
READBACK_CAVEATS = [
    "截图 ≠ 另存回来的文件；两者都在才算读回证据。",
    "另存回来的文件里出现那两个探针值 ≠ 证明了两列语义；必须同时有面板上"
    "**哪个组**显示哪个数（文件只证明值被读到了，截图才证明值落在哪一列）。",
    "生成记录（*.provenance.json）永不被改写；读回证据另存 *.readback.json，按 sha256 指回原探针。",
]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# 模板身份（R113-04：字节身份 / 来源位置别名 / 期望模板，三件事分开）
# --------------------------------------------------------------------------- #

def lookup_template(sha: str, *, manifest: Path = MANIFEST) -> dict:
    """按哈希在 DP-133 身份清单里查这份模板，**返回全部命中行**。

    三件事分开，不混成一句「模板已核对」：

    1. **字节身份** = sha256（本函数只按它匹配）；
    2. **来源位置/别名** = 命中行的 `path` 列表 —— 同一份字节在清单里可能有多行
       （多处副本、多个登记名），**多个别名不是多份不同内容**；
    3. **期望模板** = 调用方另外指定的 `--expect-template-sha256`，在
       `verify_expected_template` 里严格比对。

    清单不存在时**不猜**：`manifest_present=False`，命中为空。这只说明
    「在本次核对的材料中没查到这份字节的登记」，不说明这份模板有问题。
    """
    out = {
        "sha256": sha,
        "manifest_present": manifest.exists(),
        "manifest_path": str(manifest),
        "matches": [],
        "distinct_material_ids": [],
        "distinct_paths": [],
    }
    if not out["manifest_present"]:
        return out
    with open(manifest, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("sha256") != sha:
                continue
            out["matches"].append({
                "material_id": r.get("material_id"),
                "path": r.get("path"),
                "assay": r.get("assay"),
                "role": r.get("role"),
                "bytes": r.get("bytes"),
                "usage_group": r.get("usage_group"),
                "evidence_status": r.get("evidence_status"),
            })
    out["distinct_material_ids"] = sorted({m["material_id"] for m in out["matches"]
                                           if m["material_id"]})
    out["distinct_paths"] = sorted({m["path"] for m in out["matches"] if m["path"]})
    return out


def verify_expected_template(actual_sha: str, expected: str | None) -> None:
    """严格模式：调用方点名了期望模板，哈希对不上就停（前缀比对也认）。"""
    if not expected:
        return
    exp = expected.strip().lower()
    act = actual_sha.strip().lower()
    if exp == act or (len(exp) >= 16 and act.startswith(exp)):
        return
    raise SystemExit(
        f"模板哈希与 --expect-template-sha256 不符，拒绝写探针：\n"
        f"  期望 {exp}\n  实际 {act}\n"
        "拿错模板等于把探针打在另一批实验的参数上，读数回来也归不了属。")


def registered_paths(manifest: Path = MANIFEST) -> set[str]:
    """清单里登记过的素材路径（realpath 化），用来拒绝覆盖已登记素材。"""
    if not manifest.exists():
        return set()
    got = set()
    with open(manifest, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            p = (r.get("path") or "").strip()
            if not p:
                continue
            got.add(str(Path(p).expanduser().resolve()))
    return got


# --------------------------------------------------------------------------- #
# 参数解析与绑定（R113-02：反向 pair、默认值与原值相同）
# --------------------------------------------------------------------------- #

def parse_pair(s: str) -> tuple[int, int]:
    """收下 `5,6` **也**收下 `6,5`，原样返回；规范化在 `bind_pair` 里做。

    以前这里接受反序、`semantic_diff` 又按升序比，于是「先接受后拒绝」，
    报错还指向产物而不是命令行（R113-02）。现在接受与比较用的是同一个规范化。
    """
    try:
        a, b = (int(x) for x in s.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--pair 要写成 `5,6`，收到 {s!r}")
    if (a, b) not in MOTION_PAIR_UNRESOLVED_IDX and \
       (b, a) not in MOTION_PAIR_UNRESOLVED_IDX:
        raise argparse.ArgumentTypeError(
            f"({a},{b}) 不在 MOTION_PAIR_UNRESOLVED_IDX={MOTION_PAIR_UNRESOLVED_IDX} 里。"
            "已收口的那一对不用再探；探一个说不出归属的下标等于白跑一趟 CSI。")
    return (a, b)


def parse_values(s: str) -> tuple[int, int]:
    try:
        a, b = (int(x) for x in s.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--values 要写成 `20,40`，收到 {s!r}")
    if a == b:
        raise argparse.ArgumentTypeError(
            f"两个值相等（{a}）就没有信息量：面板两侧显示一样的数，归属照样定不下来。"
            "这正是现有 3 份 .SET 全部卡住的原因。")
    return (a, b)


def canonical_pair(as_written: tuple[int, int]) -> tuple[int, int]:
    """归一到 `MOTION_PAIR_UNRESOLVED_IDX` 里登记的顺序（升序）。"""
    return as_written if as_written in MOTION_PAIR_UNRESOLVED_IDX \
        else tuple(reversed(as_written))


def bind_pair(pair_as_written: tuple[int, int],
              values_as_written: tuple[int, int] | None,
              originals: dict[int, int]) -> dict:
    """把「哪一对 + 哪两个值」绑成一张不会错位的表。

    **绝不单独排序下标**：下标与值是成对移动的（R113-02）。
    `--pair 6,5 --values 40,20` 的意思是 idx6=40 / idx5=20，
    规范化后是 pair=(5,6)、values=(20,40) —— 绑定关系一字未变。

    `--values` 没给时用**这一对的记录默认值**，且默认值按**规范化后**的顺序取
    （把 `--pair` 反着写不该把默认值也反过来）。
    """
    canonical = canonical_pair(pair_as_written)
    reversed_input = canonical != pair_as_written
    if values_as_written is None:
        values = PAIR_PANEL_MAP[canonical]["recorded_default"]
        values_explicit = False
    else:
        # 按用户写的顺序绑到用户写的下标上，再一起搬到规范顺序
        per_idx = dict(zip(pair_as_written, values_as_written))
        values = (per_idx[canonical[0]], per_idx[canonical[1]])
        values_explicit = True
    if values[0] == values[1]:
        raise SystemExit(
            f"两个值相等（{values[0]}）就没有信息量：面板两侧显示一样的数，"
            "归属照样定不下来。这正是现有 3 份 .SET 全部卡住的原因。")
    already = [i for i, v in zip(canonical, values) if originals.get(i) == v]
    if len(already) == len(canonical):
        raise SystemExit(
            f"两个值都与模板原值相同（{list(values)}），产物会与模板逐字节一致，"
            "等于没有探针。换一个值。")
    return {
        "pair": canonical,
        "values": values,
        "values_explicit": values_explicit,
        "reversed_input": reversed_input,
        "binding": {str(canonical[0]): values[0], str(canonical[1]): values[1]},
        "originals": {str(canonical[0]): originals.get(canonical[0]),
                      str(canonical[1]): originals.get(canonical[1])},
        "sides_already_at_target": already,
        "panel": PAIR_PANEL_MAP[canonical],
    }


# --------------------------------------------------------------------------- #
# 输出保护与成对发布（R113-01）
# --------------------------------------------------------------------------- #

def resolve_target(raw: str | Path) -> Path:
    """展开 `~`、解析软链接后返回 realpath。路径本身合不合法与写不写无关。"""
    return Path(raw).expanduser().resolve()


def check_output_paths(out_paths: dict[str, Path], template: Path,
                       manifest: Path = MANIFEST) -> None:
    """四类禁止落点，**在写任何东西之前**判完。

    1. 仓库工作树内（DP-104：产物不得进验收路径）；
    2. 模板所在目录或模板的祖先目录（探针混进客户真实参数目录 = 下一个人可能加载它）；
    3. 身份清单里已登记的素材路径（不许悄悄覆盖一份登记过的材料）；
    4. 已存在的目标（默认不覆盖；成对预检见 `precheck_pair`）。
    """
    tpl = template.expanduser().resolve()
    tpl_dir = tpl.parent
    registered = registered_paths(manifest)
    for kind, p in out_paths.items():
        try:
            p.relative_to(ROOT)
            raise SystemExit(
                f"拒绝把探针{kind}写进仓库工作树：{p}\n"
                "DP-104：set_write 的产物不得进入验收路径。写到仓库外，例如 "
                "~/Work/csi_probe/。")
        except ValueError:
            pass
        if p == tpl or p.parent == tpl_dir or tpl_dir in p.parents or p in tpl.parents:
            raise SystemExit(
                f"拒绝把探针{kind}写在模板旁边：{p}\n"
                f"  模板 {tpl}\n"
                "客户的真实参数目录里多出一份探针，下一个人可能把它当正式配置加载。"
                "换一个目录（或用 --package-dir 建一个新目录当交付单元）。")
        if str(p) in registered:
            raise SystemExit(
                f"拒绝覆盖身份清单里已登记的素材：{p}\n"
                "清单里那一行是这批实验的既有证据，探针不是它的替代品。")


def precheck_pair(targets: dict[str, Path]) -> None:
    """**所有**目标一起预检：任一个已存在就整体拒绝（R113-01）。

    只查 SET 不查来源记录，就会出现「SET 写出来了、记录因为撞名失败」的半包；
    而半包比什么都没有更危险 —— 它看起来像一份可用的探针。
    """
    clash = {k: str(p) for k, p in targets.items() if p.exists() or p.is_symlink()}
    if clash:
        raise SystemExit(
            "以下目标已存在，默认不覆盖（**成对**预检，一个撞名就整体拒绝）：\n"
            + "\n".join(f"  {k}: {v}" for k, v in sorted(clash.items()))
            + "\n换文件名/换目录，或自己先把旧文件移走。")


def stage_pair(payload: dict[str, tuple[str, bytes]], staging: Path) -> dict[str, Path]:
    """把待发布的文件先写进同一个暂存目录（发布前它们一个都不在目标位置）。

    `payload` 是 `{逻辑名: (最终文件名, 字节)}`。**暂存时就用最终文件名** ——
    `--package-dir` 模式下暂存目录本身会被 rename 成交付目录，用逻辑名当文件名
    就会交出一包叫 `set` / `provenance` 的文件（这个错 2026-09-19 实测犯过一次，
    被 `verify_pair_delivered` 抓成半包）。
    """
    staged = {}
    for key, (name, blob) in payload.items():
        p = staging / name
        p.write_bytes(blob)
        staged[key] = p
    return staged


def publish_pair(staged: dict[str, Path], targets: dict[str, Path],
                 order: tuple[str, ...], *, replace=os.replace) -> list[Path]:
    """按 `order` 把暂存文件搬到目标；任一步失败就撤回**本次**已发布的文件。

    撤回只动 `published` 里记着的路径 —— 那些路径刚刚才被 `precheck_pair`
    确认过不存在，所以撤掉的一定是本次创建的，不会碰到别人的旧文件。

    **这不是跨文件原子事务**，两次 rename 之间被杀就会留下前一份。
    要真正原子的交付单元用 `--package-dir`（整目录一次 rename）。
    """
    published: list[Path] = []
    try:
        for k in order:
            if k not in staged:
                continue
            replace(staged[k], targets[k])
            published.append(targets[k])
    except BaseException:
        for p in reversed(published):
            p.unlink(missing_ok=True)
        raise
    return published


def verify_pair_delivered(targets: dict[str, Path]) -> None:
    """发布后复核：**要么全在，要么一个都不在**。半包不许留在盘上。"""
    missing = sorted(k for k, p in targets.items() if not p.is_file())
    present = sorted(k for k, p in targets.items() if p.is_file())
    if missing and present:
        raise SystemExit(
            "**半包**：以下文件在、以下文件不在，这一份**不可交付**，请整份删掉重造：\n"
            f"  在：{present}\n  缺：{missing}\n"
            "没有来源记录的探针不许交给任何人 —— 读数回来也归不了属。")
    if missing:
        raise SystemExit(f"发布失败，目标一个都没留下：缺 {missing}")


def validate_staged(staged: dict[str, Path], prov: dict) -> None:
    """发布**之前**把暂存的每一份都读回来验一遍（R113-01：stage → validate → publish）。

    验的是「交给人的一包里有没有废文件」，而不是重复 `write_set` 已经做过的字节核对：

    - 来源记录必须是能解析的 JSON，且 `csi_read_back_verified` 仍为 False、
      `product.sha256` 与暂存 SET 的实际字节一致（哈希对不上 = 包里两份文件讲的
      不是同一个故事，读数回来会归错属）；
    - 校验和文件必须逐行对得上包内文件的实际哈希；
    - 一页作业单必须**点名实际探的那一对**：出现别的字段名（例如探 Noise 却写着
      Min Length）就拒绝发布 —— 这正是 R113-03 要挡的那类错。
    """
    def _read(k: str) -> bytes:
        p = staged[k]
        if not p.is_file() or p.stat().st_size == 0:
            raise SystemExit(f"暂存的 {k} 是空的或不存在：{p}")
        return p.read_bytes()

    # SHA256SUMS 里写的是**最终文件名**，staged 的键是逻辑名 ⇒ 按文件名再建一张表。
    by_name = {p.name: k for k, p in staged.items()}

    back = json.loads(_read("provenance").decode("utf-8"))
    if back.get("csi_read_back_verified") is not False:
        raise SystemExit("来源记录里 csi_read_back_verified 不是 false，拒绝发布")
    if back.get("must_not_enter_acceptance_paths") is not True:
        raise SystemExit("来源记录里 must_not_enter_acceptance_paths 不是 true，拒绝发布")
    want_sha = back.get("product", {}).get("sha256")
    got_sha = hashlib.sha256(_read("set")).hexdigest()
    if want_sha != got_sha:
        raise SystemExit(
            f"来源记录里的产物哈希 {want_sha} 与暂存 SET 的实际哈希 {got_sha} 不符，"
            "拒绝发布这一包")
    if back.get("probe", {}).get("values_written") != \
            back.get("probe", {}).get("values_read_back"):
        raise SystemExit("来源记录里写进去的值与读回来的值不一致，拒绝发布")

    if "checksums" in staged:
        rows = [ln for ln in _read("checksums").decode("utf-8").splitlines() if ln.strip()]
        listed = set()
        for ln in rows:
            sha, sep, rest = ln.partition("  ")
            if not sep or len(sha.strip()) != 64:
                raise SystemExit(
                    f"SHA256SUMS 有一行不是「<64 位哈希><两空格><文件名>」：{ln[:40]!r}\n"
                    "（一份每行一个字符的校验和文件曾经从这里溜出去过，故按格式硬判。）")
            name = rest.split("  #")[0].strip()
            if name not in by_name:
                continue                    # 只登记包内文件；多出来的名字跳过
            listed.add(name)
            if hashlib.sha256(_read(by_name[name])).hexdigest() != sha.strip():
                raise SystemExit(f"SHA256SUMS 与 {name} 的实际哈希不符，拒绝发布")
        # 包里的每一份（除校验和文件自己）都必须被点到名，否则少一份也没人发现。
        want = {p.name for k, p in staged.items() if k != "checksums"}
        if want - listed:
            raise SystemExit(
                f"SHA256SUMS 漏登记了包内文件 {sorted(want - listed)}，拒绝发布")

    if "workorder" in staged:
        wo = _read("workorder").decode("utf-8")
        row = back["probe"]["panel_row_label"]
        if row not in wo:
            raise SystemExit(f"一页作业单里没有实际要看的行标题 {row!r}，拒绝发布")
        others = sorted({v["panel_row_label"] for v in PAIR_PANEL_MAP.values()} - {row})
        leaked = [o for o in others if o in wo]
        if leaked:
            raise SystemExit(
                f"一页作业单里出现了不属于这一对的字段名 {leaked}（本对是 {row!r}）："
                "操作提示必须随参数变化，拒绝发布")
        for v in back["probe"]["values_written"]:
            if str(v) not in wo:
                raise SystemExit(f"一页作业单里没有实际探针值 {v}，拒绝发布")


def semantic_diff(template: Path, product: Path, binding: dict) -> dict:
    """写完当场复核语义：只许那一对变，其余一律逐字相同。

    `write_set` 自己已经验了「读回来的值 == 写进去的值」和「改动字节 ⊆ 预期偏移」；
    这里补的是它不管的两件事：① 预期之外的**字段**有没有被动（标量、孔位、布尔块）；
    ② 改动**全部落在**被探的两个 int32 槽里，且**至少有一个字节真的变了**。

    **判据不是「恰好 8 字节变」**：`15 → 20 / 40` 在小端下只动每个 int32 的最低字节
    （实测差 2 个字节：`rel+71`、`rel+75`）。按 8 字节判会把正确的探针判成坏的 ——
    这个错本脚本自己先犯过一次，2026-09-18 实测抓到，故改成按槽判。

    **也不再要求两侧都改动字节**（R113-02）：模板里 `Merge Bouts Limit` 两侧都是 20，
    探 `--pair 8,11 --values 22,35` 时两侧都会变；但若有人显式写 `--values 20,35`，
    idx8 那一侧一个字节都不动 —— **这仍然是有判别力的探针**（面板上两列显示 20 和 35，
    谁是谁看得清）。所以真正的要求是：**两个目标值不同**（`bind_pair` 已保证）、
    读回来就是这两个值、改动全在槽内。哪一侧本来就在目标值上，如实记进
    `sides_already_at_target`，不藏。
    """
    pair = binding["pair"]
    values = binding["values"]
    a, b = parse_set(template), parse_set(product)
    diff = {}
    for k in SET_SCALAR_FIELDS:
        if a[k] != b[k]:
            diff[k] = (a[k], b[k])
    for k in ("n_tanks", "base", "tank_triples", "bool_block"):
        if a[k] != b[k]:
            diff[k] = (a[k], b[k])
    ma, mb = a["motion_ints"], b["motion_ints"]
    changed_idx = sorted(i for i in range(len(ma)) if ma[i] != mb[i])
    if diff:
        raise SystemExit(
            f"探针语义不对，产物已撤回、**不要用**：预期之外的字段被动过 = {diff}")
    if not set(changed_idx) <= set(pair):
        raise SystemExit(
            f"探针语义不对，产物已撤回、**不要用**：\n"
            f"  Motion 变了的下标 = {changed_idx}，只允许 {list(pair)}")
    if [mb[i] for i in pair] != list(values):
        raise SystemExit(f"读回来的值 {[mb[i] for i in pair]} != 写进去的 {list(values)}")

    raw_t, raw_p = template.read_bytes(), product.read_bytes()
    if len(raw_t) != len(raw_p):
        raise SystemExit(f"产物长度 {len(raw_p)} != 模板 {len(raw_t)}：探针不许改结构")
    base = a["base"]
    slots = {i: [base + SET_MOTION_REL + 4 * i + d for d in range(4)] for i in pair}
    allowed = {o for offs in slots.values() for o in offs}
    actual = sorted(i for i in range(len(raw_t)) if raw_t[i] != raw_p[i])
    outside = [i for i in actual if i not in allowed]
    if outside:
        raise SystemExit(f"探针改到了槽外的字节：{outside[:16]}（允许的范围是 {sorted(allowed)}）")
    if not actual:
        raise SystemExit(
            "产物与模板逐字节相同，等于没有探针（两个值都已经是模板原值？）")
    return {
        "motion_idx": list(pair),
        "field_hints": [MOTION_FIELD_HINTS[i] for i in pair],
        "field_type": binding["panel"]["field_type"],
        "panel_row_label": binding["panel"]["panel_row_label"],
        "panel_group_high_convention": binding["panel"]["panel_group_high_convention"],
        "panel_group_low_convention": binding["panel"]["panel_group_low_convention"],
        "unit": binding["panel"]["unit"],
        "unit_verified": binding["panel"]["unit_verified"],
        "values_written": list(values),
        "values_read_back": [mb[i] for i in pair],
        "original_values": [binding["originals"][str(i)] for i in pair],
        "sides_already_at_target": list(binding["sides_already_at_target"]),
        "values_explicit": binding["values_explicit"],
        "bytes_changed": len(actual),
        "changed_offsets": actual,
    }


# --------------------------------------------------------------------------- #
# 作业单（R113-03：提示必须随参数变化）
# --------------------------------------------------------------------------- #

def probe_stem(template: Path, binding: dict) -> str:
    a, b = binding["pair"]
    va, vb = binding["values"]
    return (f"{template.stem}_PROBE_{FIELD_SLUG[binding['pair']]}"
            f"_idx{a}-{va}_idx{b}-{vb}")


def build_work_order(template: Path, binding: dict, files: dict[str, Path],
                     hashes: dict[str, str]) -> str:
    """一页作业单，**内容跟着实际探的那一对走**。

    探 Noise 的包不会叫去看 Min Length；探 Bin 的包会写「Bin Size (seconds)」和秒。
    高低两侧一律按面板上的**真实分组标题**记，不按左右位置记 —— 因为
    「哪个组对应低 idx」正是这一趟要测的东西。
    """
    p = binding["panel"]
    a, b = binding["pair"]
    va, vb = binding["values"]
    hi, lo = p["panel_group_high_convention"], p["panel_group_low_convention"]
    already = binding["sides_already_at_target"]
    already_note = (
        f"> **注意**：idx{already} 那一侧本来就已经是目标值，字节不会变。"
        "这**不影响判别** —— 面板上两列仍然显示两个不同的数。\n\n" if already else "")
    return f"""# 作业单（一页） · DP-135 · 探 `{p['field_type']}`（idx{a} / idx{b}）

**要在面板上找的行**：`{p['panel_row_label']}`
**单位**：{p['unit']}
**这份探针写进去的两个值**：idx{a} = **{va}**，idx{b} = **{vb}**
（{'调用方显式指定' if binding['values_explicit'] else '脚本记录的默认值'}；
模板原值是 idx{a}={binding['originals'][str(a)]}、idx{b}={binding['originals'][str(b)]}）

{already_note}## 要拷过去的文件

| 文件 | sha256 |
|---|---|
| `{files['set'].name}` | `{hashes['set']}` |
| `{files['provenance'].name}` | `{hashes['provenance']}` |

模板是客户自己的 `{template.name}`（sha256 `{hashes['template']}`）。
除 idx{a}/idx{b} 两个 int32 外**逐位保留**。

## 6 步（约 5 分钟）

1. 把上面两个文件拷到那台装了 DepressionSuite 的 Windows 机器
   （U 盘/内网都行，**不要**经过任何云端分析服务）。
2. 打开 **ForcedSwimScan**（FST 那个，不是 TailSuspScan），进 **Settings**，
   在 **关于/标题栏**记下**程序版本号**（截图里必须能看到，或另写一行）。
3. 在 **Motion Setting** 页把参数文件**加载**成 `{files['set'].name}`
   （面板上一般是 Load / Open `.SET`）。
4. 找 `{p['panel_row_label']}` 那一行。它在两个分组里**各出现一次**：
   - `{hi}` 组里显示 **多少**？→ 记下来
   - `{lo}` 组里显示 **多少**？→ 记下来

   **按分组的真实标题记，不要按左右位置记**（窗口一拉宽/一换分辨率，左右就不可靠了；
   而且「哪个组对应低 idx」正是我们要测的东西，用左右记等于把答案假设进去了）。
   两个值应该是 **{va}** 和 **{vb}**（顺序未知，这正是要读的）。
   **截图整页**，要能看到两个分组的标题栏。
5. 用面板的 **Save As / 另存**把当前参数**存成一个新文件**
   （**不要**覆盖原来的 `{template.name}`），文件名建议
   `{template.stem}_READBACK_{FIELD_SLUG[binding['pair']]}.SET`。
6. 把**截图**、**另存出来的 `.SET`**、**程序版本号**、**操作人与时间**一起交回来。

> 只到第 5 步就够了。**不要**用它跑任何视频、不要导出任何结果 —— 这一轮只要
> 「CSI 自己把这份参数读回来」的证据，跑分析是另一张单（DP-126）。

## 判读（交回来之后我们做）

| 观察到 | 结论 | 改动 |
|---|---|---|
| `{hi}` 组显示 **{va}**，`{lo}` 组显示 **{vb}** | idx{a} = `{hi}` 侧 ⇒ **约定推断成立** | `MOTION_FIELD_HINTS` 去掉 idx{a}/idx{b} 的「约定推断」字样；`MOTION_PAIR_UNRESOLVED_IDX` 删掉 `{binding['pair']}` |
| `{hi}` 组显示 **{vb}**，`{lo}` 组显示 **{va}** | 归属**反了** | 交换 idx{a}/idx{b} 的标签，同样删掉 `{binding['pair']}` |
| 两组显示同一个数 / 都不是 {va}、{vb} | CSI 没按我们写的读，或面板另有取整 | **一律停下**，不许改标签；把另存的 `.SET` 与探针逐字节差分 |

**只更新这一对的映射**，另外 3 对仍是约定推断 —— 一次探针只收一个 bit。

## 读回完成需要绑住的五件事（缺一件就不算完成）

{chr(10).join(f'{i}. {e}' for i, e in enumerate(READBACK_REQUIRED_EVIDENCE, 1))}

{chr(10).join('- ' + c for c in READBACK_CAVEATS)}

## 禁止事项

- **不许 agent 执行原 EXE、不许加载/执行 DT**（架构 §3.3、Spec B SPEC:167）。
  「启动原 EXE 做动态观测」是**当前 scope 未覆盖**（Spec B:36），不是永久禁止；
  但**本作业单不是授权**，要另开明确的 scope。
- **不许伪造截图或「成功」**：没有就是没有，状态写 `blocked_environment`。
- 探针产物**不进仓库、不进 `data/`、不进任何验收路径**（DP-104）。
- **不改冻结口径**：TST θ_mob / ASSAY_WINDOWS / CSV 字段 / 历史数据 / 验收门与本单无关。
- 探针**不许**用来跑分析出数，也**不许**当成客户参数文件的替代品。
"""


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--template", required=True,
                    help="客户自己的 .SET（只改不造，其余字节逐位保留）")
    dst = ap.add_mutually_exclusive_group(required=True)
    dst.add_argument("--out", help="探针输出路径（必须在仓库外、且不在模板旁边）；"
                                   "会同时写出同名 .provenance.json")
    dst.add_argument("--package-dir",
                     help="**推荐**：把 SET + 来源记录 + SHA256SUMS + 一页作业单"
                          "作为一个新目录一次 rename 落地（真正的原子交付单元）")
    ap.add_argument("--pair", type=parse_pair, default=PROBE_IDX_PAIR,
                    help=f"探哪一对下标，默认 {PROBE_IDX_PAIR}（MinLengthThresh）。"
                         "反序写法（如 6,5）也认，会连值一起归一，绑定不错位")
    ap.add_argument("--values", type=parse_values, default=None,
                    help="两个不等的整数。不给就用**这一对**记录的默认值："
                         + "；".join(
                             f"idx{k[0]}/{k[1]}（{v['field_type']}）"
                             f"={v['recorded_default'][0]}/{v['recorded_default'][1]}"
                             for k, v in sorted(PAIR_PANEL_MAP.items()))
                         + "。这些默认值是**按已观察到的模板值挑的**，"
                           "不是「已对所有字段、所有 CSI 版本验证过」的参数")
    ap.add_argument("--expect-template-sha256",
                    help="严格模式：点名期望的模板哈希（可只给前 16 位），不符即停")
    ap.add_argument("--require-registered", action="store_true",
                    help="模板必须能在 DP-133 身份清单里按哈希点到名，否则拒绝")
    ap.add_argument("--manifest", default=str(MANIFEST),
                    help="DP-133 身份清单路径（默认仓库里那一份）。清单不在**不猜**："
                         "模板照样能用，但来源记录里会显式标成「未登记」。")
    args = ap.parse_args(argv)
    manifest = Path(args.manifest).expanduser()

    template = Path(args.template).expanduser()
    if not template.is_file():
        raise SystemExit(f"模板不存在：{template}")
    if template.suffix.upper() != ".SET":
        raise SystemExit(f"模板必须是 .SET，收到 {template.name}")

    tpl_sha = sha256_file(template)
    verify_expected_template(tpl_sha, args.expect_template_sha256)
    reg = lookup_template(tpl_sha, manifest=manifest)
    if args.require_registered and not reg["matches"]:
        raise SystemExit(
            f"模板 {template.name}（sha256 {tpl_sha[:16]}…）在身份清单里点不到名，"
            "而 --require-registered 要求点到名。\n"
            f"清单：{reg['manifest_path']}（present={reg['manifest_present']}）\n"
            "先在 DP-133 清单里登记这份字节，或去掉 --require-registered 接受"
            "「未登记模板」的显式标记。")

    parsed_tpl = parse_set(template)
    originals = {i: parsed_tpl["motion_ints"][i] for i in range(len(parsed_tpl["motion_ints"]))}
    binding = bind_pair(args.pair, args.values, originals)
    a, b = binding["pair"]
    va, vb = binding["values"]

    stem = probe_stem(template, binding)
    if args.package_dir:
        pkg = resolve_target(args.package_dir)
        files = {
            "set": pkg / f"{stem}.SET",
            "provenance": pkg / f"{stem}.SET.provenance.json",
            "checksums": pkg / "SHA256SUMS.txt",
            "workorder": pkg / f"作业单_一页_{stem}.md",
        }
        package_mode = True
    else:
        set_path = resolve_target(args.out)
        files = {
            "set": set_path,
            "provenance": Path(str(set_path) + ".provenance.json"),
        }
        pkg = None
        package_mode = False
    order = tuple(k for k in ("set", "provenance", "checksums", "workorder") if k in files)

    # ---- 所有路径检查都在写任何东西之前 ---------------------------------- #
    check_output_paths(files, template, manifest)
    if package_mode:
        if pkg.exists() or pkg.is_symlink():
            raise SystemExit(f"交付目录已存在，默认不覆盖：{pkg}\n换一个新目录名。")
        if pkg.parent.exists() and not pkg.parent.is_dir():
            raise SystemExit(f"交付目录的父路径不是目录：{pkg.parent}")
    else:
        precheck_pair(files)

    # ---- 生成内容（此时还没有任何东西落在目标位置）---------------------- #
    prov = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": "scripts/dp135_make_set_probe.py",
        "purpose": "收口 MOTION_PAIR_UNRESOLVED_IDX 的高低归属：让 CSI 面板上"
                   "同一对字段的两侧显示不同的数",
        "template": {
            "path": str(template),
            "sha256": tpl_sha,
            "bytes": template.stat().st_size,
            # 三件事分开（R113-04）：字节身份 / 清单里的来源别名（可能多行，
            # 多个别名不是多份不同内容）/ 期望模板。命中为空只说明
            # 「在本次核对的材料中没查到登记」，不说明模板有问题。
            "byte_identity_sha256": tpl_sha,
            "registered_in_dp133": bool(reg["matches"]),
            "dp133_manifest_present": reg["manifest_present"],
            "dp133_manifest_used": str(manifest),
            "dp133_material_ids": reg["distinct_material_ids"],
            "dp133_source_aliases": reg["distinct_paths"],
            "dp133_matches": reg["matches"],
            "expected_sha256_given": args.expect_template_sha256,
            "expected_sha256_matched": bool(args.expect_template_sha256),
            "material_id_in_dp133": (reg["distinct_material_ids"][0]
                                     if len(reg["distinct_material_ids"]) == 1 else None),
        },
        "product": {},          # 发布后回填（哈希要对最终落盘的字节算）
        "probe": {},            # semantic_diff 之后回填
        "write_set_summary": {},
        "readback": {
            "recorder": "scripts/dp135_record_readback.py",
            "required_evidence": READBACK_REQUIRED_EVIDENCE,
            "caveats": READBACK_CAVEATS,
            "generation_record_is_immutable": True,
        },
        # 这两行不是格式，是口径：谁把它们改成 True / 删掉，就等于宣称
        # 「CSI 已经读回过这份参数」——那需要那台 Windows 机器上的实测。
        # 本脚本**永不**改写这份记录；读回证据另存 *.readback.json。
        "csi_read_back_verified": False,
        "must_not_enter_acceptance_paths": True,
    }
    if not reg["matches"]:
        prov["template"]["unregistered_template_acknowledged"] = True

    # 暂存目录必须与目标**同一个文件系统**，否则 os.replace 会跨设备失败。
    stage_parent = (pkg if package_mode else files["set"]).parent
    try:
        stage_parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # 建不出父目录时给一句人话，不要甩一个 PermissionError 回溯给人看：
        # 这时候**还什么都没写**，所以不存在需要清理的半成品。
        raise SystemExit(f"建不出输出目录 {stage_parent}：{exc}\n（尚未写出任何文件）")
    staging = Path(tempfile.mkdtemp(prefix=".dp135_stage_", dir=str(stage_parent)))
    try:
        set_tmp = staging / files["set"].name
        summary = write_set(template, set_tmp,
                            motion_by_index={a: va, b: vb})
        detail = semantic_diff(template, set_tmp, binding)
        set_blob = set_tmp.read_bytes()

        prov["probe"] = detail
        prov["write_set_summary"] = summary
        prov["product"] = {
            "path": str(files["set"]),
            "sha256": hashlib.sha256(set_blob).hexdigest(),
            "bytes": len(set_blob),
            "filename": files["set"].name,
        }
        prov_blob = (json.dumps(prov, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

        payload = {"set": (files["set"].name, set_blob),
                   "provenance": (files["provenance"].name, prov_blob)}
        if package_mode:
            hashes = {
                "template": tpl_sha,
                "set": prov["product"]["sha256"],
                "provenance": hashlib.sha256(prov_blob).hexdigest(),
            }
            wo = build_work_order(template, binding, files, hashes)
            wo_blob = wo.encode("utf-8")
            hashes["workorder"] = hashlib.sha256(wo_blob).hexdigest()
            # 注意：`"\n".join(一个字符串)` 会按**字符**拆开 —— 这个错 2026-09-19
            # 实测犯过一次，产出了一份每行一个字符的 SHA256SUMS。传列表。
            # 校验和文件**不含自己的哈希**（`sha256sum` 的惯例），但必须点到包内
            # 其余每一份的名 —— 漏一份就被 `validate_staged` 拦下、整包不发布
            # （2026-09-19 实测：漏了作业单那一份，包被撤回，没留半包在盘上）。
            # 只列**包内**的文件，这样 `shasum -a 256 -c SHA256SUMS.txt` 是干净通过的。
            # 曾经在这里附一行「模板 … # 不在本包内」，结果 shasum 把整行当文件名，
            # 人照着跑一次就吃一个 FAILED —— 校验和文件里不该有需要人绕开的行。
            # 模板的身份已经在一页作业单与来源记录里各写了一遍，不会丢。
            sums = "\n".join([
                f"{hashes['set']}  {files['set'].name}",
                f"{hashes['provenance']}  {files['provenance'].name}",
                f"{hashes['workorder']}  {files['workorder'].name}",
            ]) + "\n"
            payload["checksums"] = (files["checksums"].name, sums.encode("utf-8"))
            payload["workorder"] = (files["workorder"].name, wo_blob)

        staged = stage_pair(payload, staging)
        validate_staged(staged, prov)          # 发布之前把每一份都读回来验一遍

        # ---- 发布 -------------------------------------------------------- #
        if package_mode:
            # 暂存目录本身就是交付单元：一次 rename 落地，**真正原子**。
            os.replace(staging, pkg)
            staging = None                    # 已被 rename 走，finally 里不要再删
            # mkdtemp 建的是 0700；这一包是要拷给别人拿去 Windows 的交付物，
            # 目录权限收成只有自己能进会让「拷走」这一步莫名其妙地失败。
            os.chmod(pkg, 0o755)
            try:
                verify_pair_delivered(files)
            except BaseException:
                # 整包是本次创建的 ⇒ 复核不过就整包撤掉，不留一个看着像成品
                # 却缺东西的目录在外面（半包比什么都没有更危险）。
                shutil.rmtree(pkg, ignore_errors=True)
                raise
        else:
            publish_pair(staged, files, order)
            verify_pair_delivered(files)
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    out_sha = prov["product"]["sha256"]
    print(f"探针已写出：{files['set']}")
    if package_mode:
        print(f"  交付单元  {pkg}（整目录一次 rename 落地）")
    print(f"  模板      {template.name}  sha256={tpl_sha[:16]}…  "
          f"身份清单里是 "
          f"{'、'.join(prov['template']['dp133_material_ids']) or '（未登记：本次核对的材料里没查到这份字节的登记）'}")
    print(f"  产物      sha256={out_sha[:16]}…  改动 {detail['bytes_changed']} 字节 "
          f"（偏移 {detail['changed_offsets']}）")
    print(f"  探的是    idx{binding['pair']} = {list(binding['values'])}  "
          f"（面板行 `{detail['panel_row_label']}`，字段 {detail['field_type']}）")
    if binding["reversed_input"]:
        print(f"  注意      --pair 写的是反序，已连值一起归一到 idx{a}={va} / idx{b}={vb}")
    if detail["sides_already_at_target"]:
        print(f"  注意      idx{detail['sides_already_at_target']} 本来就等于目标值，"
              "那一侧字节没变；两个目标值仍然不同，**判别力不受影响**")
    if not detail["unit_verified"]:
        print(f"  单位      {detail['unit']}")
    print(f"  读回自检  {detail['values_read_back']}（我们自己的 parser，不是 CSI）")
    print(f"  来源记录  {files['provenance']}")
    if package_mode:
        print(f"  校验和    {files['checksums']}")
        print(f"  一页作业单 {files['workorder']}")
    print()
    print("下一步（要人做，本脚本不做）：")
    print("  1. 把这一包拷到那台装了 DepressionSuite 的 Windows 机器；")
    if package_mode:
        print(f"  2. 照着包里的《作业单_一页_{stem}.md》做 6 步；")
    else:
        print("  2. 照着 docs/作业单_DP-135_SET探针与Motion归属收口.md 做 6 步；")
    print(f"     在 Motion Setting 页找 `{detail['panel_row_label']}` 那一行，")
    print(f"     **按分组真实标题**记下 `{detail['panel_group_high_convention']}` 与 "
          f"`{detail['panel_group_low_convention']}` 各显示多少；")
    print(f"     这份探针写的是 idx{a}={va} / idx{b}={vb}（单位：{detail['unit']}）；")
    print("  3. 记下**程序版本号、操作人、时间**，截图要能看到两个分组的标题；")
    print("  4. 用 Save As 另存一份新 .SET（不要覆盖原配置、不要跑正式动物分析），")
    print("     把截图与另存的文件一起交回来；")
    print("  5. 交回来之前，csi_read_back_verified 必须保持 False，产物不得进验收路径；")
    print("  6. 读回记录用 scripts/dp135_record_readback.py 另存一份 *.readback.json，")
    print("     **本脚本生成的 provenance 永不被改写**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
