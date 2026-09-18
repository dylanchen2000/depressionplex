#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-135 守卫：`.SET` 探针生成器（`scripts/dp135_make_set_probe.py`）。

探针是 B 线收口 `MOTION_PAIR_UNRESOLVED_IDX` 的唯一仪器：把某一对同类型 Motion 字段
的两侧写成**不等**的值，拿去 CSI 面板上看哪一侧显示哪个数，一次定死归属。
所以这里守的是三件一旦失效整轮收口就作废的事：

1. **只动该动的字节**：被探的两个 int32 槽各自真的变了，槽外一个字节都没变，
   标量字段 / 孔位三元组 / 布尔块 / 长度全部逐字保留。
   （判据不是「恰好 8 字节变」：15→20/40 在小端下只动最低字节，实测差 2 个字节。）
2. **产物不许留在验收路径上**：写进仓库工作树 ⇒ 拒绝；来源记录里
   `csi_read_back_verified` 恒为 `false`（只有那台 Windows 机器上的实测能把它变成 true）。
3. **不许造出没有信息量的探针**：两个值相等、或探一对已收口的下标 ⇒ 拒绝。
   现有 3 份互不相同的 `.SET` 之所以解不开归属，正是因为每一对的两侧都相等。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "dp135_make_set_probe.py"
TEMPLATE = ROOT / "tests/fixtures/csi_fst/10mg 2周.SET"

sys.path.insert(0, str(ROOT))
from depressionplex.csi.fst_import import (          # noqa: E402
    MOTION_PAIR_UNRESOLVED_IDX,
    SET_MOTION_REL,
    parse_set,
)
from depressionplex.csi.set_write import SET_SCALAR_FIELDS  # noqa: E402


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=str(ROOT))


def test_probe_changes_only_the_probed_pair() -> None:
    """端到端跑一次：产物只在那一对上不同，其余逐字保留，来源记录齐全。"""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "probe.SET"
        r = _run("--template", str(TEMPLATE), "--out", str(out))
        assert r.returncode == 0, r.stdout + r.stderr
        assert out.is_file()

        before, after = parse_set(TEMPLATE), parse_set(out)
        assert after["motion_ints"][5] == 20 and after["motion_ints"][6] == 40
        for i in range(13):
            if i not in (5, 6):
                assert after["motion_ints"][i] == before["motion_ints"][i], i
        for k in SET_SCALAR_FIELDS:
            assert after[k] == before[k], k
        for k in ("n_tanks", "base", "tank_triples", "bool_block"):
            assert after[k] == before[k], k

        raw_t, raw_p = TEMPLATE.read_bytes(), out.read_bytes()
        assert len(raw_t) == len(raw_p)
        base = before["base"]
        slots = {i: [base + SET_MOTION_REL + 4 * i + k for k in range(4)]
                 for i in (5, 6)}
        changed = [i for i in range(len(raw_t)) if raw_t[i] != raw_p[i]]
        assert changed and set(changed) <= set(sum(slots.values(), [])), changed
        # 小端下 15→20 / 15→40 只动最低字节：实测就是 2 个字节，不是 8 个
        assert changed == [slots[5][0], slots[6][0]], changed

        prov = json.loads((Path(str(out) + ".provenance.json"))
                          .read_text(encoding="utf-8"))
        assert prov["csi_read_back_verified"] is False
        assert prov["must_not_enter_acceptance_paths"] is True
        assert prov["probe"]["values_read_back"] == [20, 40]
        assert prov["write_set_summary"]["csi_read_back_verified"] is False
        # 模板按哈希在 DP-133 身份清单里点得到名（清单不在时为 null，不猜）
        assert prov["template"]["sha256"] == _sha(TEMPLATE)
        assert prov["product"]["bytes"] == len(raw_p)


def test_refuses_to_write_inside_the_repo() -> None:
    """DP-104：探针产物不得进入验收路径 ⇒ 落在仓库工作树里就拒绝，且不落文件。"""
    out = ROOT / "tmp_dp135_should_never_exist.SET"
    try:
        r = _run("--template", str(TEMPLATE), "--out", str(out))
        assert r.returncode != 0
        assert "DP-104" in (r.stdout + r.stderr)
        assert not out.exists()
    finally:
        out.unlink(missing_ok=True)


def test_refuses_equal_values_and_resolved_pairs() -> None:
    """两侧相等 ⇒ 面板显示一样的数 ⇒ 归属照样定不下来；探已收口的下标 ⇒ 白跑。"""
    with tempfile.TemporaryDirectory() as d:
        r = _run("--template", str(TEMPLATE), "--out", str(Path(d) / "a.SET"),
                 "--values", "20,20")
        assert r.returncode != 0 and "没有信息量" in (r.stdout + r.stderr)
        assert not Path(d, "a.SET").exists()

        free = next(i for i in range(13)
                    if all(i not in p for p in MOTION_PAIR_UNRESOLVED_IDX))
        r = _run("--template", str(TEMPLATE), "--out", str(Path(d) / "b.SET"),
                 "--pair", f"{free},{free + 1}" if free + 1 < 13 else f"{free - 1},{free}")
        assert r.returncode != 0 and "MOTION_PAIR_UNRESOLVED_IDX" in (r.stdout + r.stderr)
        assert not Path(d, "b.SET").exists()


def test_refuses_to_overwrite_and_reports_bad_template() -> None:
    """不覆盖已有产物（客户的参数文件被静默盖掉 = 抹掉一批实验的口径）；模板不存在要大声失败。"""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "probe.SET"
        assert _run("--template", str(TEMPLATE), "--out", str(out)).returncode == 0
        first = out.read_bytes()
        r = _run("--template", str(TEMPLATE), "--out", str(out))
        assert r.returncode != 0 and "已存在" in (r.stdout + r.stderr)
        assert out.read_bytes() == first        # 第二次没碰它

        r = _run("--template", str(Path(d) / "不存在.SET"), "--out", str(Path(d) / "c.SET"))
        assert r.returncode != 0 and "模板不存在" in (r.stdout + r.stderr)

        txt = Path(d) / "假.SET"
        txt.write_bytes(b"FSS3" + b"\x00" * 40)
        r = _run("--template", str(txt), "--out", str(Path(d) / "d.SET"))
        assert r.returncode != 0                # 读不动的模板不许往里写
        assert not Path(d, "d.SET").exists()


def _sha(p: Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()
