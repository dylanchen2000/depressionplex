#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-135：把 CSI 面板的**读回证据**记成一份新记录（永不改写生成记录）。

**为什么单独一支脚本**：`dp135_make_set_probe.py` 生成的 `*.provenance.json` 是
「我们造了什么」的记录，它的 `csi_read_back_verified` 恒为 `false`。谁把它改成
`true`，就等于在**生成记录**里宣称「CSI 已经读回过」——那是把两件事混成一件，
而且改一次就把原始记录毁了。所以读回证据另存一份 `*.readback.json`，
按 **sha256 指回原探针**（R113-05）。

**读回完成至少要绑住五件事**，缺任何一件本脚本都拒绝出记录：

1. 原 EXE 的身份与版本（哈希 + 面板/关于页上能看到的版本号）；
2. 探针 `.SET` 的 sha256（必须与生成记录里的 `product.sha256` 一致，否则指不回原探针）；
3. 操作人与观测时间、在哪台机器上；
4. 面板截图，且能看到**两个分组的真实标题**；
5. CSI 用 Save As 另存回来的 `.SET` 的 sha256，与其中被探两个下标的读数。

**为什么截图和另存文件两个都要**（返修单原话的意思）：

- 截图**不是**另存回来的文件：截图证明「人在面板上看到了什么」，
  但截图可以是任何一个参数文件的画面，甚至可以是别的项目的。
- 一份文件里同时出现那两个探针值，**也不**证明两列语义：文件只说明值被读到了，
  说明不了「哪个值显示在哪个分组里」。
- **两个合起来**才是证据：截图给出「`Struggle/Escape/Climb Settings` 组显示 22」，
  另存文件给出「idx8 的字节就是 22」，两者对上，归属才真的定死。

**边界**：本脚本只**记录**，不改 `MOTION_FIELD_HINTS`、不改
`MOTION_PAIR_UNRESOLVED_IDX`、不改 `set_write` 里那个恒为 false 的字段。
它输出判读结论与建议改哪几行，由人在**另一个** PR 里改（一次探针只收一个 bit）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from depressionplex.csi.fst_import import parse_set          # noqa: E402

SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_out(raw: str) -> Path:
    """读回记录同样不许落进仓库工作树（DP-104：产物不进验收路径）。"""
    p = Path(raw).expanduser().resolve()
    try:
        p.relative_to(ROOT)
    except ValueError:
        return p
    raise SystemExit(
        f"拒绝把读回记录写进仓库工作树：{p}\n"
        "读回记录是证据，证据走台账/附件登记，不混进代码验收路径。")


def parse_group_reading(s: str) -> tuple[str, int]:
    """`"Struggle/Escape/Climb Settings=22"` → (组标题, 读数)。"""
    if "=" not in s:
        raise argparse.ArgumentTypeError(
            f"--group-reading 要写成 `<面板分组真实标题>=<读数>`，收到 {s!r}")
    title, _, val = s.rpartition("=")
    try:
        return title.strip(), int(val.strip())
    except ValueError:
        raise argparse.ArgumentTypeError(f"--group-reading 的读数不是整数：{s!r}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--probe", required=True,
                    help="我们生成的探针 .SET（旁边必须有同名 .provenance.json）")
    ap.add_argument("--csi-set", required=True,
                    help="CSI 用 Save As 另存回来的 .SET（**不是**截图，不是我们的探针）")
    ap.add_argument("--screenshot", action="append", required=True,
                    help="面板截图路径，可多次给；必须能看到两个分组的真实标题")
    ap.add_argument("--exe-sha256",
                    help="原 EXE 的 sha256（64 位十六进制）。取不到就给 "
                         "--exe-identity-unknown-reason，不许留空当作已知")
    ap.add_argument("--exe-identity-unknown-reason",
                    help="为什么拿不到 EXE 身份；给了这个就必须没给 --exe-sha256")
    ap.add_argument("--exe-version", required=True,
                    help="面板/关于页上看到的程序版本号原文；看不到就写 "
                         "`not_visible_in_panel`（会被如实记成未确认）")
    ap.add_argument("--operator", required=True, help="谁在那台机器上操作的")
    ap.add_argument("--machine", required=True, help="哪台机器（能对上授权范围的说法）")
    ap.add_argument("--observed-at", required=True,
                    help="观测时间（ISO 8601，带时区最好，例如 2026-09-20T14:05+08:00）")
    ap.add_argument("--group-reading", action="append", required=True,
                    type=parse_group_reading,
                    help="`<面板分组真实标题>=<该组里那一行显示的数>`，**给两条**")
    ap.add_argument("--authorization", required=True,
                    help="这次动态观测依据的是哪一条许可（例如「客户自有机器上由客户操作，"
                         "方案 A」或某次明确的 scope 扩展）。**返修单本身不是授权**")
    ap.add_argument("--out", help="读回记录输出路径，默认 <probe>.readback.json")
    ap.add_argument("--allow-identical-to-probe", action="store_true",
                    help="另存回来的文件与我们的探针逐字节相同时仍出记录（会被标成"
                         "**弱证据**：字节相同说明不了 CSI 做过什么，很可能只是拷回来了）")
    args = ap.parse_args(argv)

    probe = Path(args.probe).expanduser().resolve()
    if not probe.is_file():
        raise SystemExit(f"探针不存在：{probe}")
    prov_path = Path(str(probe) + ".provenance.json")
    if not prov_path.is_file():
        raise SystemExit(
            f"探针旁边没有生成记录：{prov_path}\n"
            "读回记录必须按 sha256 指回原探针；没有生成记录就无从指回，拒绝出记录。")
    prov = json.loads(prov_path.read_text(encoding="utf-8"))
    probe_sha = sha256_file(probe)
    recorded = (prov.get("product") or {}).get("sha256")
    if recorded != probe_sha:
        raise SystemExit(
            f"这份 .SET 与生成记录里的产物哈希不符，指不回原探针：\n"
            f"  生成记录 {recorded}\n  实际文件 {probe_sha}\n"
            "拿错文件会让整轮收口归到别的探针上。")
    if prov.get("csi_read_back_verified") is not False:
        raise SystemExit(
            "生成记录里的 csi_read_back_verified 不是 false —— 生成记录**永不**被改写，"
            "谁改了它就先查清楚是谁改的，别在这上面继续叠记录。")

    detail = prov.get("probe") or {}
    pair = tuple(detail.get("motion_idx") or ())
    values = list(detail.get("values_written") or [])
    if len(pair) != 2 or len(values) != 2:
        raise SystemExit(f"生成记录里 pair/values 不完整：pair={pair} values={values}")
    g_high = detail.get("panel_group_high_convention")
    g_low = detail.get("panel_group_low_convention")
    row_label = detail.get("panel_row_label")

    # ---- 五件事之一：EXE 身份与版本 ------------------------------------- #
    if bool(args.exe_sha256) == bool(args.exe_identity_unknown_reason):
        raise SystemExit(
            "--exe-sha256 与 --exe-identity-unknown-reason **必须给且只给一个**："
            "身份要么报得出哈希，要么如实写明为什么报不出，不许留空当作已知。")
    if args.exe_sha256 and not SHA_RE.match(args.exe_sha256.strip().lower()):
        raise SystemExit(f"--exe-sha256 不是 64 位十六进制：{args.exe_sha256!r}")
    exe_version_confirmed = args.exe_version.strip().lower() not in (
        "", "not_visible_in_panel", "unknown")

    # ---- 五件事之四：截图（可多张，逐张记哈希）--------------------------- #
    shots = []
    for s in args.screenshot:
        p = Path(s).expanduser().resolve()
        if not p.is_file():
            raise SystemExit(f"截图不存在：{p}")
        shots.append({"path": str(p), "bytes": p.stat().st_size,
                      "sha256": sha256_file(p)})
    if not shots:
        raise SystemExit("至少要有一张面板截图（截图与另存文件**两个都要**，缺一不可）")

    # ---- 五件事之五：CSI 另存回来的文件与读数 --------------------------- #
    csi_set = Path(args.csi_set).expanduser().resolve()
    if not csi_set.is_file():
        raise SystemExit(f"CSI 另存回来的 .SET 不存在：{csi_set}")
    if csi_set.resolve() == probe:
        raise SystemExit(
            "--csi-set 指的就是我们的探针文件本身。要的是 **CSI 用 Save As 另存回来的**"
            "那一份；拿探针当读回证据等于自己给自己作证。")
    csi_sha = sha256_file(csi_set)
    identical = csi_sha == probe_sha
    if identical and not args.allow_identical_to_probe:
        raise SystemExit(
            "CSI 另存回来的文件与我们的探针**逐字节相同**。\n"
            "这通常意味着文件只是被拷回来了、CSI 并没有重新写过它 —— 那就**没有**"
            "「CSI 自己读回了这份参数」的证据。\n"
            "确认过确实是 CSI 另存的结果，就加 --allow-identical-to-probe，"
            "记录里会把它标成弱证据。")
    try:
        csi_parsed = parse_set(csi_set)
    except Exception as exc:                      # CsiParseError 等一律当「读不动」
        raise SystemExit(
            f"CSI 另存回来的文件用我们的 parse_set 读不动：{exc}\n"
            "**一律停下**，不许改标签、不许猜读数；先把这份文件与探针逐字节差分，"
            "看 CSI 改了哪几个字节（作业单第五节第三行就是这个分支）。")
    readings = {i: csi_parsed["motion_ints"][i] for i in pair}
    if sorted(readings.values()) != sorted(values):
        raise SystemExit(
            f"另存文件里被探两个下标的读数 {readings} 与探针写进去的 {values} 不符。\n"
            "**一律停下**：可能是 CSI 另有取整，也可能另存的不是这份参数。"
            "不许改标签，先把两份文件逐字节差分。")

    # ---- 面板读数：必须按**真实分组标题**给，且两条 ---------------------- #
    got = dict(args.group_reading)
    if len(args.group_reading) != 2 or len(got) != 2:
        raise SystemExit(
            f"--group-reading 要给**两条不同的分组标题**，收到 {args.group_reading}")
    unknown_titles = sorted(set(got) - {g_high, g_low})
    if unknown_titles:
        raise SystemExit(
            f"分组标题 {unknown_titles} 不是这一对在面板上的真实标题。\n"
            f"  期望：{g_high!r} 与 {g_low!r}（行是 {row_label!r}）\n"
            "按**真实标题**记，不要按左右位置记 —— 左右会随窗口宽度变，"
            "而且「哪个组对应低 idx」正是这次要测的东西。")
    if sorted(got.values()) != sorted(values):
        raise SystemExit(
            f"面板上读到的两个数 {sorted(got.values())} 与探针值 {sorted(values)} 不符。"
            "两组显示同一个数、或都不是探针值 ⇒ 一律停下，不许改标签。")
    if got[g_high] == got[g_low]:
        raise SystemExit("两个分组显示同一个数，这一趟没有判别力，出不了结论。")

    # ---- 结论：低 idx 落在哪个分组 -------------------------------------- #
    low_idx, high_idx = pair            # pair 已按升序登记
    if got[g_high] == readings[low_idx]:
        conclusion = "convention_holds"
        meaning = (f"idx{low_idx} 显示在 `{g_high}` 组 ⇒「低 idx = high/Escape 侧」"
                   "的约定推断**成立**")
        suggested = [
            f"MOTION_FIELD_HINTS[{low_idx}] / [{high_idx}] 去掉「约定推断」字样",
            f"MOTION_PAIR_UNRESOLVED_IDX 删掉 {pair}",
        ]
    else:
        conclusion = "convention_reversed"
        meaning = (f"idx{low_idx} 显示在 `{g_low}` 组 ⇒ 归属**反了**，"
                   f"idx{low_idx} 是 low/Immobile 侧")
        suggested = [
            f"交换 MOTION_FIELD_HINTS[{low_idx}] 与 [{high_idx}] 的 high/low 标签",
            f"MOTION_PAIR_UNRESOLVED_IDX 删掉 {pair}",
        ]

    record = {
        "record_kind": "csi_readback",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "recorder": "scripts/dp135_record_readback.py",
        "generation_record": {
            "path": str(prov_path),
            "sha256": sha256_file(prov_path),
            "immutable": True,
            "note": "生成记录不被本脚本改写；本文件是**新增**的读回记录",
        },
        "evidence": {
            "exe": {
                "sha256": (args.exe_sha256.strip().lower() if args.exe_sha256 else None),
                "identity_unknown_reason": args.exe_identity_unknown_reason,
                "version_text": args.exe_version,
                "version_confirmed": exe_version_confirmed,
            },
            "probe": {"path": str(probe), "sha256": probe_sha,
                      "matches_generation_record": True},
            "operator": args.operator,
            "machine": args.machine,
            "observed_at": args.observed_at,
            "authorization_basis": args.authorization,
            "screenshots": shots,
            "csi_saved_set": {
                "path": str(csi_set),
                "sha256": csi_sha,
                "bytes": csi_set.stat().st_size,
                "byte_identical_to_probe": identical,
                "parsed_by_us": True,
                "readings_by_idx": {str(k): v for k, v in readings.items()},
            },
            "panel_readings_by_group_title": {k: v for k, v in sorted(got.items())},
            "panel_row_label": row_label,
        },
        "conclusion": {
            "resolves_pair": list(pair),
            "field_type": detail.get("field_type"),
            "result": conclusion,
            "meaning": meaning,
            "suggested_code_changes": suggested,
            "only_this_pair": True,
            "other_pairs_still_convention": sorted(
                list(p) for p in ((5, 6), (7, 10), (8, 11), (9, 12)) if tuple(p) != pair),
        },
        # 只有五件事齐了才写 true；而这句话的权威在**本文件**，不在生成记录里。
        "csi_read_back_verified": True,
        "evidence_strength": "weak_identical_bytes" if identical else "screenshot_plus_saved_file",
        "must_not_enter_acceptance_paths": True,
    }

    out = resolve_out(args.out or (str(probe) + ".readback.json"))
    if out.exists() or out.is_symlink():
        raise SystemExit(
            f"读回记录已存在，不覆盖：{out}\n"
            "同一份探针的第二次观测要另起一个名字（例如加日期），"
            "两份都留着 —— 覆盖就等于抹掉一次观测。")
    blob = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    json.loads(blob.decode("utf-8"))            # 发布前先确认自己是合法 JSON
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_bytes(blob)
    try:
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)             # 只清理本次创建的临时文件
        raise

    print(f"读回记录已写出：{out}")
    print(f"  探针        sha256={probe_sha[:16]}…（与生成记录一致）")
    print(f"  CSI 另存件  sha256={csi_sha[:16]}…"
          f"{'  **与探针逐字节相同 ⇒ 弱证据**' if identical else ''}")
    print(f"  面板读数    " + "；".join(f"`{k}` 组 = {v}" for k, v in sorted(got.items())))
    print(f"  文件读数    " + "；".join(f"idx{k} = {v}" for k, v in sorted(readings.items())))
    print(f"  结论        {conclusion} —— {meaning}")
    print(f"  证据强度    {record['evidence_strength']}")
    if not exe_version_confirmed:
        print(f"  注意        程序版本号未确认（{args.exe_version}），如实记在记录里")
    print()
    print("下一步（要人做，本脚本不做）：")
    for s in record["conclusion"]["suggested_code_changes"]:
        print(f"  - {s}")
    print(f"  - 另外 3 对仍是约定推断：{record['conclusion']['other_pairs_still_convention']}")
    print("  - 改动走**另一个** PR，并把这条读回记录登记进台账；生成记录不动")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
