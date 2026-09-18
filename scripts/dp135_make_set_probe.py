#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-135：造 B1 的最小差分探针 `.SET`，并落一份可核对的来源记录。

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

1. 值在这里钉死（`PROBE_VALUES`），且**只许探未收口的那 4 对**（改一对已收口的没有意义）；
2. 产物旁边强制落一份 provenance JSON：模板哈希、产物哈希、改了哪几个字节、
   读回来的值、以及 `csi_read_back_verified: false`；
3. 写完当场做**语义**复核（不只是字节复核）：Motion 只在探的那两个下标上变、
   其余 11 个数与 8 个标量字段与孔位三元组一字不动。

**边界**（与 DP-104 / 架构 §3.3 / Spec B SPEC:166-167 对齐）：

- 产物**不进仓库、不进 `data/`、不进任何验收路径**。写路径落在仓库工作树里 ⇒ 拒绝。
- **不执行 CSI 原程序、不加载 DT。** 产物要拿到那台装了 DepressionSuite 的 Windows 机器上，
  由人打开面板看读数：作业单 `docs/作业单_DP-135_SET探针与Motion归属收口.md`。
- **只改，不造**：模板必须是客户自己的 `.SET`，其余字节逐位保留
  （`set_write.write_set` 的规矩，它自己会验一遍并对不上就删半成品）。

用法：

    /tmp/dpx311/bin/python scripts/dp135_make_set_probe.py \\
        --template "~/Work/depression抑郁绝望/9月10日集中标注/CSI分析强迫游泳数据_对照参数/正常1-4对照.SET" \\
        --out ~/Work/csi_probe/正常1-4对照_PROBE_minlen_20_40.SET

模板 sha256 应为 `fd9596e5a588e15c…`（见身份清单 `csi_out:csi_out_ctrl:正常1-4对照.SET`）；
对不上就说明拿错了文件，脚本会照 DP-133 的清单点名。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
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
#: 两个值必须**不等**，且都要落在面板常见值域内（面板两列实测是 20/15/10/5）。
#: 20 是面板上本来就有的值 ⇒ 哪一侧显示 20、哪一侧显示 40，肉眼一次就能分清。
PROBE_VALUES = (20, 40)

MANIFEST = ROOT / "docs" / "共用输入身份清单_v1.csv"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def material_id_of(sha: str) -> str | None:
    """按哈希在 DP-133 身份清单里点名这份模板（清单不在就返回 None，不猜）。"""
    if not MANIFEST.exists():
        return None
    with open(MANIFEST, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["sha256"] == sha:
                return r["material_id"]
    return None


def parse_pair(s: str) -> tuple[int, int]:
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


def check_outside_repo(out: Path) -> Path:
    out = out.expanduser().resolve()
    try:
        out.relative_to(ROOT)
    except ValueError:
        return out
    raise SystemExit(
        f"拒绝把探针写进仓库工作树：{out}\n"
        "DP-104：set_write 的产物不得进入验收路径。写到仓库外，例如 "
        "~/Work/csi_probe/。")


def semantic_diff(template: Path, product: Path, pair: tuple[int, int],
                  values: tuple[int, int]) -> dict:
    """写完当场复核语义：只许那一对变，其余一律逐字相同。

    `write_set` 自己已经验了「读回来的值 == 写进去的值」和「改动字节 ⊆ 预期偏移」；
    这里补的是它不管的两件事：① 预期之外的**字段**有没有被动（标量、孔位、布尔块）；
    ② 被探的两个 int32 槽**各自至少有一个字节真的变了**（一个都没变说明写空了），
       且两个槽之外**一个字节都没变**（多了说明模板被别的东西碰过）。

    注意判据不是「恰好 8 个字节变」：`15 → 20 / 40` 在小端下只动每个 int32 的最低字节
    （实测差 2 个字节：`rel+71`、`rel+75`）。按 8 字节判会把正确的探针判成坏的 ——
    这个错本脚本自己先犯过一次，2026-09-18 实测抓到，故改成按槽判。
    """
    a, b = parse_set(template), parse_set(product)
    diff = {}
    for k in SET_SCALAR_FIELDS:
        if a[k] != b[k]:
            diff[k] = (a[k], b[k])
    for k in ("n_tanks", "base", "tank_triples", "bool_block"):
        if a[k] != b[k]:
            diff[k] = (a[k], b[k])
    ma, mb = a["motion_ints"], b["motion_ints"]
    changed_idx = [i for i in range(len(ma)) if ma[i] != mb[i]]
    if diff or changed_idx != list(pair):
        raise SystemExit(
            f"探针语义不对，产物已留下但**不要用**：\n"
            f"  Motion 变了的下标 = {changed_idx}，应为 {list(pair)}\n"
            f"  其他被动过的字段 = {diff or '（无）'}")
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
    untouched = [i for i in pair
                 if all(raw_t[o] == raw_p[o] for o in slots[i])]
    if untouched:
        raise SystemExit(f"这几个槽一个字节都没变，等于没写进去：idx{untouched}")
    return {
        "motion_idx": list(pair),
        "field_hints": [MOTION_FIELD_HINTS[i] for i in pair],
        "values_written": list(values),
        "values_read_back": [mb[i] for i in pair],
        "bytes_changed": len(actual),
        "changed_offsets": actual,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--template", required=True,
                    help="客户自己的 .SET（只改不造，其余字节逐位保留）")
    ap.add_argument("--out", required=True, help="探针输出路径（必须在仓库外）")
    ap.add_argument("--pair", type=parse_pair, default=PROBE_IDX_PAIR,
                    help=f"探哪一对下标，默认 {PROBE_IDX_PAIR}（MinLengthThresh）")
    ap.add_argument("--values", type=parse_values, default=PROBE_VALUES,
                    help=f"两个不等的整数，默认 {PROBE_VALUES}")
    args = ap.parse_args()

    template = Path(args.template).expanduser()
    if not template.is_file():
        raise SystemExit(f"模板不存在：{template}")
    if template.suffix.upper() != ".SET":
        raise SystemExit(f"模板必须是 .SET，收到 {template.name}")
    out = check_outside_repo(Path(args.out))
    out.parent.mkdir(parents=True, exist_ok=True)

    tpl_sha = sha256_file(template)
    summary = write_set(template, out,
                        motion_by_index={args.pair[0]: args.values[0],
                                         args.pair[1]: args.values[1]})
    try:
        detail = semantic_diff(template, out, args.pair, args.values)
    except BaseException:
        out.unlink(missing_ok=True)   # 不留一份没复核过的探针在外面
        raise
    out_sha = sha256_file(out)

    prov = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": "scripts/dp135_make_set_probe.py",
        "purpose": "收口 MOTION_PAIR_UNRESOLVED_IDX 的高低归属：让 CSI 面板上"
                   "同一对字段的两侧显示不同的数",
        "template": {
            "path": str(template),
            "sha256": tpl_sha,
            "bytes": template.stat().st_size,
            "material_id_in_dp133": material_id_of(tpl_sha),
        },
        "product": {
            "path": str(out),
            "sha256": out_sha,
            "bytes": out.stat().st_size,
        },
        "probe": detail,
        "write_set_summary": summary,
        # 这两行不是格式，是口径：谁把它们改成 True / 删掉，就等于宣称
        # 「CSI 已经读回过这份参数」——那需要那台 Windows 机器上的实测。
        "csi_read_back_verified": False,
        "must_not_enter_acceptance_paths": True,
    }
    prov_path = out.with_suffix(out.suffix + ".provenance.json")
    prov_path.write_text(json.dumps(prov, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")

    print(f"探针已写出：{out}")
    print(f"  模板      {template.name}  sha256={tpl_sha[:16]}…  "
          f"身份清单里是 {prov['template']['material_id_in_dp133'] or '（不在清单里，先登记）'}")
    print(f"  产物      sha256={out_sha[:16]}…  改动 {detail['bytes_changed']} 字节 "
          f"（偏移 {detail['changed_offsets']}）")
    print(f"  探的是    idx{args.pair} = {list(args.values)}  "
          f"（{detail['field_hints'][0].split('（')[0]} 的两侧）")
    print(f"  读回自检  {detail['values_read_back']}（我们自己的 parser，不是 CSI）")
    print(f"  来源记录  {prov_path}")
    print()
    print("下一步（要人做，本脚本不做）：")
    print("  1. 把这两份文件拷到那台装了 DepressionSuite 的 Windows 机器；")
    print("  2. 按 docs/作业单_DP-135_SET探针与Motion归属收口.md 打开面板，")
    print(f"     看 Motion Setting 里 Min Length 那一行**哪一侧显示 {args.values[0]}**；")
    print("  3. 把面板截图 + CSI 另存回来的 .SET 一起交回来；")
    print("  4. 交回来之前，csi_read_back_verified 必须保持 False，产物不得进验收路径。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
