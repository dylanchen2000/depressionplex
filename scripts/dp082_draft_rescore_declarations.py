#!/usr/bin/env python3
"""DP-082：为一个数据目录里的重复键**起草**重评声明（只打印，不落盘）。

    python3 scripts/dp082_draft_rescore_declarations.py [数据目录] > /tmp/decl.csv

为什么只打印：声明说的是「这一键的哪一条读数算真值」，那是科学判断。脚本能做的
只是把数据摆清楚——谁和谁撞了、两条读数差多少、哪一条先落盘。落盘由人 review
之后 commit（`data/human_scores/incoming/rescore_declarations.csv`）。

三种情况脚本**不出声明、直接非零退出**，因为它们都需要人先定口径：

- 一个键三条以上读数
- 两条读数的 `scored_at` 一样（分不出首评，不许按文件名猜）
- 读数完全相同（那是「同源副本」，入库时自动取一条，不需要声明）→ 只记账不报错

退出码：0 = 起草成功；2 = 遇到需要人先定口径的形状。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from depressionplex.human_agreement import (  # noqa: E402
    DECL_COLUMNS, RESCORE_DECL_NAME, _reading_signature, load_audit_json,
    load_salvaged_csv, reading_key,
)

#: 归因口径。**不是猜的**：28 个重复键全部满足「跨会话、新 seed、两次
#: `scored_at` 不同日」，且没有任何一份导出的 `delivered` 自身有重复
#: ⇒ 是计时工具换会话后把评过的场次又派了一遍，不是评分员有意重评。
ATTRIBUTION = "工具跨会话重派（新seed下又派到已评场次；非有意重评）"


def collect(data_dir: Path):
    rows = []
    for p in sorted(data_dir.glob("timer_audit_*.json")):
        rows.extend(load_audit_json(p, on_reject="mark")[1])
    for p in sorted(data_dir.glob("human_scores_*SALVAGED*.csv")):
        rows.extend(load_salvaged_csv(p, on_reject="mark"))
    groups: dict[tuple[str, str], list] = {}
    for r in rows:
        groups.setdefault(reading_key(r), []).append(r)
    return groups


def main(argv: list[str]) -> int:
    data_dir = Path(argv[1] if len(argv) > 1 else "data/human_scores/incoming")
    groups = collect(data_dir)
    dupes = {k: g for k, g in groups.items() if len(g) > 1}

    drafts, same_source, blocked = [], [], []
    for k, g in sorted(dupes.items()):
        if len({_reading_signature(r) for r in g}) == 1:
            same_source.append(k)
            continue
        if len(g) > 2:
            blocked.append((k, f"{len(g)} 条读数，没定过口径"))
            continue
        a, b = sorted(g, key=lambda r: (str(r.scored_at or ""), str(r.source_file or "")))
        if str(a.scored_at or "") == str(b.scored_at or ""):
            blocked.append((k, f"两条 scored_at 都是 {a.scored_at!r}，分不出首评"))
            continue
        drafts.append((k, a, b))

    out = sys.stdout
    print(f"# DP-082 重评声明（起草于 {data_dir}，由 "
          f"scripts/dp082_draft_rescore_declarations.py 生成，人工 review 后 commit）", file=out)
    print(f"# 口径：同一 (scorer_id, trial_id) 有两条读数且读数不同 ⇒ 必须在此声明，"
          f"否则入库停机（DP-124）。", file=out)
    print(f"# 首评 = 先落盘的那一条（复评时评分员已记得上一次按了多久，晚的那条被素材污染），"
          f"进真值表；复评另存，只用于同一评分员的组内重测一致性。", file=out)
    print(f"# 读数完全相同的重复不在此列（入库自动取一条并记「同源副本」）："
          f"{len(same_source)} 个键。", file=out)
    print(",".join(DECL_COLUMNS), file=out)
    for (sc, tid), a, b in drafts:
        print(f"# {sc}/{tid}: union {a.mobile_union_s} → {b.mobile_union_s}"
              f"（差 {abs((a.mobile_union_s or 0) - (b.mobile_union_s or 0)):.2f} s）"
              f" order {a.presentation_order}→{b.presentation_order}"
              f" seed {a.seed}→{b.seed}", file=out)
        print(",".join([sc, tid, a.source_file, b.source_file, ATTRIBUTION]), file=out)

    print(f"# 共 {len(drafts)} 条声明；同源副本 {len(same_source)} 个键；"
          f"需人工先定口径 {len(blocked)} 个键。", file=out)
    for k, why in blocked:
        print(f"需人工先定口径：{k[0]}/{k[1]} —— {why}", file=sys.stderr)
    if blocked:
        print(f"有 {len(blocked)} 个键脚本不敢起草 ⇒ 退出码 2（{RESCORE_DECL_NAME} "
              f"不许含猜出来的行）", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
