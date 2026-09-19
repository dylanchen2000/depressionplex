#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DP-135 返修守卫：`.SET` 探针的**成对保存**、**参数绑定**与**读回记录**。

`tests/test_set_probe.py` 守的是「探针本身改对了没有」（只动该动的字节、不进验收
路径、不许造没有信息量的探针）。这里守的是返修单 R113-01..05 点名的另外几件事 ——
每一件都对应一个**已经真实发生过**或**差一点就交出去**的错误：

R113-01 SET 与来源记录必须**成对**保存
    只查 SET 不查来源记录，就会出现「SET 写出来了、记录撞名失败」的半包；
    半包比什么都没有更危险，因为它看起来像一份可用的探针。
    ⇒ 两个目标一起预检、一起暂存校验、一起发布；失败只撤回本次创建的文件。
    **两次 rename 不是跨文件原子事务**，所以 `--package-dir` 提供整目录一次
    rename 的交付单元；这一条也在测试里钉死，不许在文档里说成原子的。

R113-02 反向 pair 与「默认值恰好等于原值」
    `parse_pair` 收 `6,5`、`semantic_diff` 却按升序比 ⇒ 先接受后拒绝，报错还指向
    产物。模板里 Merge 两侧都是 20，`--pair 8,11 --values 20,40` 会被「两侧都得变」
    的规则拒掉，**尽管这份探针是有判别力的**。
    ⇒ 规范化时下标与值**成对移动**（绝不单独排序下标）；只要求两个目标值不同、
       读回来就是这两个值、改动全在槽内；本来就等于目标值的那一侧如实记下来。

R113-03 操作提示必须随参数变化
    探 Noise / Merge / Bin 的包里不能叫去看 Min Length；高低两侧按面板上的
    **真实分组标题**记，不按左右位置记（左右会随窗口宽度变，而且「哪个组对应低 idx」
    正是这次要测的东西）。

R113-04 模板身份是三件事，不是一句「已核对」
    字节身份（sha256）／来源位置别名（清单里可能多行，多个别名不是多份内容）／
    期望模板（`--expect-template-sha256` 严格比对）。清单不在时**不猜**。

R113-05 读回证据另存一份，**永不**改写生成记录
    截图 ≠ 另存回来的文件；一份文件里出现那两个值 ≠ 证明了两列语义；两个合起来才算。

全部用仓库夹具 `tests/fixtures/csi_fst/10mg 2周.SET`（<2 KB）与临时目录，
**不碰客户素材、不跑 CSI、不需要那 68 段视频**。沙箱里 pytest 的 site-packages 有
I/O 故障（`import pytest` 直接 ModuleNotFoundError），故本文件不依赖 pytest，
由 `run_tests.py` 直接调用每个 `test_*`。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE_SCRIPT = ROOT / "scripts" / "dp135_make_set_probe.py"
READBACK_SCRIPT = ROOT / "scripts" / "dp135_record_readback.py"
TEMPLATE = ROOT / "tests/fixtures/csi_fst/10mg 2周.SET"

sys.path.insert(0, str(ROOT))
from depressionplex.csi.fst_import import (          # noqa: E402
    MOTION_PAIR_UNRESOLVED_IDX,
    parse_set,
)
from depressionplex.csi.set_write import write_set   # noqa: E402


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


dp135 = _load(PROBE_SCRIPT, "_dp135_probe_under_test")
rb135 = _load(READBACK_SCRIPT, "_dp135_readback_under_test")

ALL_PAIRS = tuple(MOTION_PAIR_UNRESOLVED_IDX)
ROW_LABELS = {p: dp135.PAIR_PANEL_MAP[p]["panel_row_label"] for p in ALL_PAIRS}
G_HIGH = "Struggle/Escape/Climb Settings"
G_LOW = "Float/Immobile Settings"


def _run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *args],
                          capture_output=True, text=True, cwd=str(ROOT))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _make_probe(d: Path, name: str, *extra: str) -> Path:
    """在临时目录里造一份探针（`--out` 两文件模式），返回 SET 路径。"""
    out = d / name
    r = _run(PROBE_SCRIPT, "--template", str(TEMPLATE), "--out", str(out), *extra)
    assert r.returncode == 0, r.stdout + r.stderr
    return out


def _prov(out: Path) -> dict:
    return json.loads(Path(str(out) + ".provenance.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# R113-01 成对保存保护
# --------------------------------------------------------------------------- #

def test_refuses_when_only_the_provenance_exists() -> None:
    """只有来源记录在、SET 不在 ⇒ 整体拒绝，且**不写出 SET**、旧记录一个字节不变。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        out = _make_probe(d, "a.SET")
        prov = Path(str(out) + ".provenance.json")
        before = _sha(prov)
        out.unlink()                                    # 只剩来源记录
        assert not out.exists() and prov.is_file()

        r = _run(PROBE_SCRIPT, "--template", str(TEMPLATE), "--out", str(out))
        assert r.returncode != 0
        assert "已存在" in (r.stdout + r.stderr)
        assert "provenance" in (r.stdout + r.stderr)     # 点名撞的是哪一个
        assert not out.exists(), "半包：SET 被写出来了，来源记录却撞名失败"
        assert _sha(prov) == before, "旧来源记录被改写了"


def test_refuses_when_only_the_set_exists() -> None:
    """只有 SET 在、来源记录不在 ⇒ 整体拒绝，且**不写出来源记录**。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        out = _make_probe(d, "b.SET")
        prov = Path(str(out) + ".provenance.json")
        keep = _sha(out)
        prov.unlink()                                   # 只剩 SET

        r = _run(PROBE_SCRIPT, "--template", str(TEMPLATE), "--out", str(out))
        assert r.returncode != 0 and "已存在" in (r.stdout + r.stderr)
        assert not prov.exists(), "半包：SET 已在，却又补写了一份来源记录"
        assert _sha(out) == keep, "已有的 SET 被改写了"


def test_template_is_never_touched_by_a_refused_run() -> None:
    """任何一次被拒绝的运行都不许碰模板（客户的参数文件是这批实验的口径）。"""
    keep = _sha(TEMPLATE)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        out = _make_probe(d, "c.SET")
        for args in ([], ["--pair", "8,11"], ["--values", "20,20"]):
            _run(PROBE_SCRIPT, "--template", str(TEMPLATE), "--out", str(out), *args)
    assert _sha(TEMPLATE) == keep


def test_publish_second_step_failure_rolls_back_the_first() -> None:
    """发布第二步失败 ⇒ 第一步已发布的文件被撤回，**不留半包**。

    用 `publish_pair` 的 `replace` 注入点造真实失败（不是 mock 断言，是让它抛）。
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        staging = d / "stage"
        staging.mkdir()
        targets = {"set": d / "p.SET", "provenance": d / "p.SET.provenance.json"}
        staged = dp135.stage_pair(
            {"set": (targets["set"].name, b"FSS3 fake"),
             "provenance": (targets["provenance"].name, b"{}")}, staging)

        calls = []

        def flaky(src, dst):
            calls.append(str(dst))
            if len(calls) == 2:
                raise OSError("模拟第二步 rename 失败")
            os.replace(src, dst)

        try:
            dp135.publish_pair(staged, targets, ("set", "provenance"), replace=flaky)
        except OSError:
            pass
        else:
            raise AssertionError("第二步失败却没有抛出来")
        assert not targets["set"].exists(), "半包：SET 留在盘上，来源记录没有"
        assert not targets["provenance"].exists()

        # 反向：第一步就失败 ⇒ 目标一个都不该出现
        calls.clear()

        def dead(src, dst):
            raise OSError("模拟第一步就失败")

        try:
            dp135.publish_pair(staged, targets, ("set", "provenance"), replace=dead)
        except OSError:
            pass
        assert not targets["set"].exists() and not targets["provenance"].exists()


def test_publish_failure_cleans_only_what_this_run_created() -> None:
    """撤回只动本次发布的路径：旁边一份**别人的**旧文件必须原样留着。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        staging = d / "stage"
        staging.mkdir()
        neighbour = d / "别人的旧参数.SET"
        neighbour.write_bytes(b"FSS3 not mine")
        keep = _sha(neighbour)

        targets = {"set": d / "p.SET", "provenance": d / "p.SET.provenance.json"}
        staged = dp135.stage_pair(
            {"set": (targets["set"].name, b"FSS3 fake"),
             "provenance": (targets["provenance"].name, b"{}")}, staging)

        def die_on_second(src, dst):
            if str(dst).endswith(".json"):
                raise OSError("模拟失败")
            os.replace(src, dst)

        try:
            dp135.publish_pair(staged, targets, ("set", "provenance"),
                               replace=die_on_second)
        except OSError:
            pass
        assert not targets["set"].exists()
        assert _sha(neighbour) == keep, "撤回时碰了别人的文件"


def test_validate_staged_refuses_a_mismatched_pair_before_publish() -> None:
    """暂存阶段就要发现「SET 与来源记录讲的不是同一个故事」。

    这一条不是假想：2026-09-19 实测里 SHA256SUMS 曾经每行一个字符、
    又曾经漏登记作业单，都是靠发布前的这道校验拦下来的（整包被撤回，没留半包）。
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        out = _make_probe(Path(d), "v.SET")
        prov = _prov(out)
        staging = Path(d) / "stage2"
        staging.mkdir()

        good_set = out.read_bytes()
        good_prov = json.dumps(prov, ensure_ascii=False).encode("utf-8")

        def stage(set_blob: bytes, prov_blob: bytes) -> dict:
            for f in staging.iterdir():
                f.unlink()
            return dp135.stage_pair(
                {"set": ("v.SET", set_blob), "provenance": ("v.SET.provenance.json",
                                                            prov_blob)}, staging)

        dp135.validate_staged(stage(good_set, good_prov), prov)      # 基线能过

        # 来源记录里的产物哈希与 SET 实际字节不符 ⇒ 拒绝
        bad = json.loads(good_prov)
        bad["product"]["sha256"] = "0" * 64
        try:
            dp135.validate_staged(stage(good_set,
                                        json.dumps(bad).encode("utf-8")), bad)
        except SystemExit as e:
            assert "哈希" in str(e)
        else:
            raise AssertionError("哈希对不上的半包居然通过了发布前校验")

        # csi_read_back_verified 被改成 true ⇒ 拒绝（生成记录不许自己宣称读回过）
        bad2 = json.loads(good_prov)
        bad2["csi_read_back_verified"] = True
        try:
            dp135.validate_staged(stage(good_set,
                                        json.dumps(bad2).encode("utf-8")), bad2)
        except SystemExit as e:
            assert "csi_read_back_verified" in str(e)
        else:
            raise AssertionError("csi_read_back_verified=true 的生成记录通过了校验")


def test_validate_staged_catches_a_broken_checksum_file() -> None:
    """SHA256SUMS 每行一个字符 / 漏登记包内文件 ⇒ 拒绝发布。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        staging = Path(d) / "s"
        staging.mkdir()
        prov = {"csi_read_back_verified": False, "must_not_enter_acceptance_paths": True,
                "product": {"sha256": hashlib.sha256(b"SET").hexdigest()},
                "probe": {"values_written": [20, 40], "values_read_back": [20, 40],
                          "panel_row_label": "Min Length Thresh"}}
        blob = json.dumps(prov, ensure_ascii=False).encode("utf-8")
        base = {"set": ("p.SET", b"SET"), "provenance": ("p.SET.provenance.json", blob),
                "workorder": ("wo.md", "Min Length Thresh 20 40".encode("utf-8"))}

        def st(extra: dict) -> dict:
            for f in staging.iterdir():
                f.unlink()
            return dp135.stage_pair({**base, **extra}, staging)

        # 每行一个字符（`"\n".join(一个字符串)` 那个错）
        per_char = {"checksums": ("SHA256SUMS.txt", "\n".join("abc").encode("utf-8"))}
        try:
            dp135.validate_staged(st(per_char), prov)
        except SystemExit as e:
            assert "SHA256SUMS" in str(e)
        else:
            raise AssertionError("每行一个字符的校验和文件通过了校验")

        # 漏登记作业单
        partial = {"checksums": ("SHA256SUMS.txt", (
            f"{hashlib.sha256(b'SET').hexdigest()}  p.SET\n"
            f"{hashlib.sha256(blob).hexdigest()}  p.SET.provenance.json\n").encode())}
        try:
            dp135.validate_staged(st(partial), prov)
        except SystemExit as e:
            assert "漏登记" in str(e)
        else:
            raise AssertionError("漏登记包内文件的校验和通过了校验")


def test_package_dir_is_a_complete_atomic_delivery_unit() -> None:
    """`--package-dir` 交出的是**一整包**：真文件名、校验和自洽、作业单随参数变化。"""
    with tempfile.TemporaryDirectory() as d:
        pkg = Path(d) / "交付包"
        r = _run(PROBE_SCRIPT, "--template", str(TEMPLATE),
                 "--package-dir", str(pkg), "--pair", "9,12")
        assert r.returncode == 0, r.stdout + r.stderr

        names = sorted(p.name for p in pkg.iterdir())
        assert len(names) == 4, names
        assert "SHA256SUMS.txt" in names
        set_f = next(p for p in pkg.iterdir() if p.suffix == ".SET")
        prov_f = next(p for p in pkg.iterdir() if p.name.endswith(".provenance.json"))
        wo_f = next(p for p in pkg.iterdir() if p.name.startswith("作业单_一页_"))
        sums_f = pkg / "SHA256SUMS.txt"
        # 暂存时就用最终文件名（曾经交出一包叫 set/provenance 的文件）
        assert "set" not in names and "provenance" not in names, names

        # 校验和逐行自洽，且点到包内每一份的名（校验和文件自己除外）
        listed = {}
        for ln in sums_f.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            sha, _, rest = ln.partition("  ")
            listed[rest.split("  #")[0].strip()] = sha.strip()
        for p in (set_f, prov_f, wo_f):
            assert listed.get(p.name) == _sha(p), f"{p.name} 的校验和对不上"
        assert sums_f.name not in listed, "校验和文件不该登记自己的哈希"

        # 整包目录已存在 ⇒ 拒绝（不覆盖）
        r = _run(PROBE_SCRIPT, "--template", str(TEMPLATE),
                 "--package-dir", str(pkg), "--pair", "9,12")
        assert r.returncode != 0 and "已存在" in (r.stdout + r.stderr)

        prov = json.loads(prov_f.read_text(encoding="utf-8"))
        assert prov["probe"]["panel_row_label"] == "Bin Size (seconds)"
        assert prov["product"]["sha256"] == _sha(set_f)


# --------------------------------------------------------------------------- #
# R113-02 反向 pair 与默认值边界
# --------------------------------------------------------------------------- #

def test_reverse_pair_keeps_the_index_to_value_binding() -> None:
    """`--pair 11,8 --values 35,22` 必须与 `--pair 8,11 --values 22,35` 逐字节相同。

    规范化时下标与值**成对移动**；单独排序下标会把绑定错位，
    而错位之后 CSI 面板上的读数会归到相反的那一侧。
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        fwd = _make_probe(d, "fwd.SET", "--pair", "8,11", "--values", "22,35")
        rev = _make_probe(d, "rev.SET", "--pair", "11,8", "--values", "35,22")
        assert _sha(fwd) == _sha(rev), "反向写法把绑定弄错位了"

        pf, pr = _prov(fwd)["probe"], _prov(rev)["probe"]
        assert pf["motion_idx"] == pr["motion_idx"] == [8, 11]
        assert pf["values_written"] == pr["values_written"] == [22, 35]
        assert pr["values_read_back"] == [22, 35]
        assert parse_set(rev)["motion_ints"][8] == 22
        assert parse_set(rev)["motion_ints"][11] == 35


def test_reverse_pair_with_defaults_follows_canonical_order() -> None:
    """`--pair 6,5` 不给值时，用**这一对**记录的默认值，且按规范顺序取。

    把 `--pair` 反着写不该把默认值也反过来。
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        a = _make_probe(d, "a.SET", "--pair", "5,6")
        b = _make_probe(d, "b.SET", "--pair", "6,5")
        assert _sha(a) == _sha(b)
        want = list(dp135.PAIR_PANEL_MAP[(5, 6)]["recorded_default"])
        assert _prov(b)["probe"]["values_written"] == want


def test_every_pair_has_a_default_that_differs_from_the_template() -> None:
    """四对都能**直接**探，不用先猜一组值（原缺陷：`--pair 8,11` 配默认 20,40 被拒）。

    模板里这四对的两侧分别是 15/15、10/10、20/20、5/5 —— 默认值必须两侧都与原值不同，
    否则面板上有一侧看不出变化。默认值是**这一批模板**的默认值，
    不是「已对所有字段、所有 CSI 版本验证过」的参数。
    """
    originals = parse_set(TEMPLATE)["motion_ints"]
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        for n, pair in enumerate(ALL_PAIRS):
            spec = dp135.PAIR_PANEL_MAP[pair]
            va, vb = spec["recorded_default"]
            assert va != vb, pair
            for idx, v in zip(pair, (va, vb)):
                assert originals[idx] != v, f"idx{idx} 的默认值 {v} 与模板原值相同"
                assert 0 <= v < 2 ** 31

            out = _make_probe(d, f"p{n}.SET", "--pair", f"{pair[0]},{pair[1]}")
            got = _prov(out)["probe"]
            assert got["values_read_back"] == [va, vb], pair
            assert got["sides_already_at_target"] == [], pair
            assert got["panel_row_label"] == ROW_LABELS[pair]


def test_one_unchanged_side_is_still_a_discriminating_probe() -> None:
    """一侧本来就等于目标值 ⇒ 那一侧字节不变，但探针**仍然有效**。

    判据是两个目标值不同、读回来就是这两个值、改动全在槽内 —— 不是「两侧都得变」。
    """
    originals = parse_set(TEMPLATE)["motion_ints"]
    keep = originals[8]                                  # 模板里 Merge 两侧都是 20
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        out = _make_probe(d, "one.SET", "--pair", "8,11",
                          "--values", f"{keep},{keep + 15}")
        prov = _prov(out)["probe"]
        assert prov["values_read_back"] == [keep, keep + 15]
        assert prov["sides_already_at_target"] == [8], "哪一侧没变必须如实记下来"
        raw_t, raw_p = TEMPLATE.read_bytes(), out.read_bytes()
        changed = [i for i in range(len(raw_t)) if raw_t[i] != raw_p[i]]
        assert changed, "一个字节都没变就不是探针了"
        # 改动全部落在 idx11 那个槽里（idx8 那一侧不动）
        base = parse_set(TEMPLATE)["base"]
        from depressionplex.csi.fst_import import SET_MOTION_REL
        slot11 = {base + SET_MOTION_REL + 4 * 11 + k for k in range(4)}
        assert set(changed) <= slot11, changed


def test_both_values_equal_to_the_originals_is_refused() -> None:
    """两侧都已经是目标值 ⇒ 产物与模板逐字节相同，那不是探针。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        # 先造一份两侧**不等**的模板，才能触发这个分支（仓库夹具两侧都相等）
        tpl2 = Path(d) / "两侧不等.SET"
        write_set(TEMPLATE, tpl2, motion_by_index={8: 20, 11: 30})
        assert parse_set(tpl2)["motion_ints"][8] == 20
        assert parse_set(tpl2)["motion_ints"][11] == 30

        out = Path(d) / "out" / "z.SET"
        out.parent.mkdir()
        r = _run(PROBE_SCRIPT, "--template", str(tpl2), "--out", str(out),
                 "--pair", "8,11", "--values", "20,30")
        assert r.returncode != 0, "两侧都等于原值却没被拒"
        assert "逐字节" in (r.stdout + r.stderr)
        assert not out.exists()


def test_equal_values_and_resolved_pairs_are_still_refused() -> None:
    """返修不许放松原有的两条：两个值相等、探一对已收口的下标。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        r = _run(PROBE_SCRIPT, "--template", str(TEMPLATE),
                 "--out", str(Path(d) / "e.SET"), "--values", "20,20")
        assert r.returncode != 0 and "没有信息量" in (r.stdout + r.stderr)

        free = next(i for i in range(13)
                    if all(i not in p for p in MOTION_PAIR_UNRESOLVED_IDX))
        other = free + 1 if free + 1 < 13 else free - 1
        r = _run(PROBE_SCRIPT, "--template", str(TEMPLATE),
                 "--out", str(Path(d) / "f.SET"), "--pair", f"{free},{other}")
        assert r.returncode != 0
        assert "MOTION_PAIR_UNRESOLVED_IDX" in (r.stdout + r.stderr)
        assert not Path(d, "e.SET").exists() and not Path(d, "f.SET").exists()


# --------------------------------------------------------------------------- #
# R113-03 操作提示随参数变化
# --------------------------------------------------------------------------- #

def test_instructions_follow_the_actual_pair() -> None:
    """探 Noise / Merge / Bin 的包里**不许**叫去看 Min Length，反之亦然。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        for n, pair in enumerate(ALL_PAIRS):
            pkg = Path(d) / f"pkg{n}"
            r = _run(PROBE_SCRIPT, "--template", str(TEMPLATE),
                     "--package-dir", str(pkg),
                     "--pair", f"{pair[0]},{pair[1]}")
            assert r.returncode == 0, r.stdout + r.stderr
            wo = next(p for p in pkg.iterdir()
                      if p.name.startswith("作业单_一页_")).read_text(encoding="utf-8")
            mine = ROW_LABELS[pair]
            assert mine in wo, f"{pair} 的作业单里没有它自己的行标题 {mine!r}"
            for other_pair, other in ROW_LABELS.items():
                if other_pair != pair:
                    assert other not in wo, f"{pair} 的作业单里出现了 {other!r}"
            # 实际探针值必须写进去（人照着这份单子读，值不对就归错属）
            for v in dp135.PAIR_PANEL_MAP[pair]["recorded_default"]:
                assert str(v) in wo
            # stdout 的提示同样随参数变化
            assert mine in r.stdout, r.stdout
            assert dp135.PAIR_PANEL_MAP[pair]["field_type"] in r.stdout


def test_work_order_records_by_real_group_titles_not_left_right() -> None:
    """高低两侧按面板上的**真实分组标题**记，不按左右位置记。"""
    with tempfile.TemporaryDirectory() as d:
        pkg = Path(d) / "pkg"
        r = _run(PROBE_SCRIPT, "--template", str(TEMPLATE),
                 "--package-dir", str(pkg), "--pair", "7,10")
        assert r.returncode == 0, r.stdout + r.stderr
        wo = next(p for p in pkg.iterdir()
                  if p.name.startswith("作业单_一页_")).read_text(encoding="utf-8")
        assert G_HIGH in wo and G_LOW in wo
        assert "不要按左右位置记" in wo
        # 判读表也必须按组标题写，不许写成「左列显示 X」
        assert f"`{G_HIGH}` 组显示" in wo
        assert "左列显示" not in wo and "右列显示" not in wo
        # 单位如实标：面板没标单位的字段不许写成已知
        spec = dp135.PAIR_PANEL_MAP[(7, 10)]
        assert spec["unit"] in wo
        assert "面板未标注单位" in dp135.PAIR_PANEL_MAP[(5, 6)]["unit"]
        assert dp135.PAIR_PANEL_MAP[(5, 6)]["unit_verified"] is False


def test_provenance_carries_the_actual_pair_facts() -> None:
    """实际值、原值、面板真实标题、单位确认状态都要进来源记录。"""
    with tempfile.TemporaryDirectory() as d:
        out = _make_probe(Path(d), "pv.SET", "--pair", "9,12", "--values", "6,11")
        p = _prov(out)["probe"]
        assert p["motion_idx"] == [9, 12]
        assert p["values_written"] == [6, 11] == p["values_read_back"]
        assert p["original_values"] == [5, 5]
        assert p["field_type"] == "BinSizeSeconds"
        assert p["panel_row_label"] == "Bin Size (seconds)"
        assert p["panel_group_high_convention"] == G_HIGH
        assert p["panel_group_low_convention"] == G_LOW
        assert p["unit_verified"] is True and "秒" in p["unit"]
        assert p["values_explicit"] is True
        assert p["bytes_changed"] >= 1
        prov = _prov(out)
        assert prov["csi_read_back_verified"] is False
        assert prov["must_not_enter_acceptance_paths"] is True
        # 读回要求写进记录，下一个人不用回来读返修单
        assert len(prov["readback"]["required_evidence"]) == 5
        assert prov["readback"]["generation_record_is_immutable"] is True


# --------------------------------------------------------------------------- #
# R113-04 模板身份：字节 / 来源别名 / 期望模板，三件事分开
# --------------------------------------------------------------------------- #

def _fake_manifest(d: Path, rows: list[dict]) -> Path:
    import csv
    p = Path(d) / "共用输入身份清单_v1.csv"
    cols = ["material_id", "path", "assay", "role", "bytes", "sha256",
            "usage_group", "evidence_status"]
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})
    return p


def test_template_lookup_separates_identity_aliases_and_expectation() -> None:
    """同一份字节在清单里有多个别名 ⇒ 全部列出，`material_id_in_dp133` 不许挑第一行。"""
    sha = _sha(TEMPLATE)
    with tempfile.TemporaryDirectory() as d:
        m = _fake_manifest(d, [
            {"material_id": "csi_out:a:正常.SET", "path": "/x/正常.SET", "sha256": sha,
             "assay": "FST", "role": "csi_out", "bytes": TEMPLATE.stat().st_size},
            {"material_id": "csi_out:b:副本.SET", "path": "/y/副本.SET", "sha256": sha,
             "assay": "FST", "role": "csi_out", "bytes": TEMPLATE.stat().st_size},
        ])
        got = dp135.lookup_template(sha, manifest=m)
        assert got["manifest_present"] is True
        assert len(got["matches"]) == 2, "多个别名不是多份不同内容，但也不能只报第一行"
        assert got["distinct_material_ids"] == ["csi_out:a:正常.SET", "csi_out:b:副本.SET"]
        assert got["distinct_paths"] == ["/x/正常.SET", "/y/副本.SET"]

        # 清单不在 ⇒ 不猜，明确说「本次核对的材料里没查到」
        absent = dp135.lookup_template(sha, manifest=Path(d) / "没有.csv")
        assert absent["manifest_present"] is False and absent["matches"] == []


def test_ambiguous_or_missing_registration_is_marked_not_faked() -> None:
    """未登记模板允许用，但必须**显式标记**；歧义时不许冒充「已核对指定来源」。"""
    sha = _sha(TEMPLATE)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        m = _fake_manifest(d, [
            {"material_id": "a:一.SET", "path": "/x/一.SET", "sha256": sha},
            {"material_id": "b:二.SET", "path": "/y/二.SET", "sha256": sha},
        ])
        # 歧义 ⇒ material_id_in_dp133 为 None（不是挑第一行），两个 id 都在列表里
        # 用 --manifest 指过去（CLI 是子进程，进程内打补丁对它不起作用）
        out_dir = d / "out"
        out_dir.mkdir()
        out = _make_probe(out_dir, "amb.SET", "--manifest", str(m))
        t = _prov(out)["template"]
        assert t["material_id_in_dp133"] is None, "歧义时不许挑第一行当结论"
        assert t["registered_in_dp133"] is True
        assert t["dp133_material_ids"] == ["a:一.SET", "b:二.SET"]
        assert t["byte_identity_sha256"] == sha
        assert t["dp133_manifest_used"] == str(m), "用了哪一份清单也要记下来"

        # 清单不在（本分支的现实）⇒ 未登记，显式标记，不假装核对过
        out2_dir = d / "out2"
        out2_dir.mkdir()
        out2 = _make_probe(out2_dir, "unreg.SET", "--manifest", str(d / "没有.csv"))
        t2 = _prov(out2)["template"]
        assert t2["registered_in_dp133"] is False
        assert t2["dp133_manifest_present"] is False
        assert t2["unregistered_template_acknowledged"] is True
        assert t2["material_id_in_dp133"] is None


def test_expected_template_sha_is_verified_strictly() -> None:
    """点名了期望模板，哈希不符就停；前 16 位也算点名。`--require-registered` 同理。"""
    sha = _sha(TEMPLATE)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        ok = _make_probe(d, "ok.SET", "--expect-template-sha256", sha[:16])
        assert _prov(ok)["template"]["expected_sha256_matched"] is True
        assert _prov(ok)["template"]["expected_sha256_given"] == sha[:16]

        r = _run(PROBE_SCRIPT, "--template", str(TEMPLATE),
                 "--out", str(d / "bad.SET"),
                 "--expect-template-sha256", "deadbeefdeadbeef")
        assert r.returncode != 0 and "不符" in (r.stdout + r.stderr)
        assert not (d / "bad.SET").exists()

        r = _run(PROBE_SCRIPT, "--template", str(TEMPLATE),
                 "--out", str(d / "req.SET"), "--require-registered")
        assert r.returncode != 0 and "require-registered" in (r.stdout + r.stderr)
        assert not (d / "req.SET").exists()


def test_output_may_not_land_beside_or_inside_the_template_dir() -> None:
    """探针混进客户真实参数目录 = 下一个人可能把它当正式配置加载。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        cust = d / "客户参数目录"
        cust.mkdir()
        tpl = cust / "客户真实.SET"
        shutil.copy2(TEMPLATE, tpl)
        keep = _sha(tpl)

        for target in (cust / "probe.SET", cust / "子目录" / "probe.SET"):
            target.parent.mkdir(parents=True, exist_ok=True)
            r = _run(PROBE_SCRIPT, "--template", str(tpl), "--out", str(target))
            assert r.returncode != 0, target
            assert "模板旁边" in (r.stdout + r.stderr)
            assert not target.exists()
        # 模板的祖先目录也不许（那会把探针塞进素材树）
        r = _run(PROBE_SCRIPT, "--template", str(tpl), "--out", str(d / "probe.SET"))
        assert r.returncode == 0, r.stdout + r.stderr
        assert _sha(tpl) == keep


def test_symlinked_output_cannot_escape_into_the_repo() -> None:
    """软链接指向仓库内 ⇒ 按 realpath 判，照样拒绝，且仓库里不留文件。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        inside = ROOT / "tmp_dp135_symlink_escape.SET"
        link = d / "看着在仓库外.SET"
        try:
            os.symlink(inside, link)
            r = _run(PROBE_SCRIPT, "--template", str(TEMPLATE), "--out", str(link))
            assert r.returncode != 0
            assert "DP-104" in (r.stdout + r.stderr)
            assert not inside.exists(), "顺着软链接把文件写进了仓库"
        finally:
            link.unlink(missing_ok=True)
            inside.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# R113-05 读回记录：五件事齐了才出，且永不改写生成记录
# --------------------------------------------------------------------------- #

def _fake_csi_saved(probe: Path, d: Path) -> Path:
    """造一份「CSI 另存回来」的替身：读数相同、字节不同（改了个无关键）。

    真机上这一份必须来自 CSI 的 Save As；这里只是让守卫能在沙箱里跑起来，
    **不代表读回已验证** —— 台账与 PR 里仍然写 blocked_environment。
    """
    out = d / "CSI另存.SET"
    parsed = parse_set(probe)
    write_set(probe, out, scalars={"frame_padding": parsed["frame_padding"] + 1})
    assert _sha(out) != _sha(probe)
    return out


def _readback_args(probe: Path, csi_set: Path, shots: list[Path], pair, values):
    return ["--probe", str(probe), "--csi-set", str(csi_set),
            "--exe-sha256", "a" * 64, "--exe-version", "DepressionSuite 3.x",
            "--operator", "道俊", "--machine", "客户自有 Windows 机（方案 A）",
            "--observed-at", "2026-09-20T14:05+08:00",
            "--authorization", "方案 A：客户在自己机器上操作，非 agent 动态运行",
            "--group-reading", f"{G_HIGH}={values[0]}",
            "--group-reading", f"{G_LOW}={values[1]}",
            *[x for s in shots for x in ("--screenshot", str(s))]]


def test_readback_record_binds_all_five_and_leaves_the_generation_record_alone() -> None:
    """五件事齐 ⇒ 出记录、结论正确、生成记录一个字节不变。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        out = _make_probe(d, "rb.SET", "--pair", "8,11")
        prov_path = Path(str(out) + ".provenance.json")
        prov_before = _sha(prov_path)
        values = _prov(out)["probe"]["values_written"]
        csi = _fake_csi_saved(out, d)
        shot = d / "panel.png"
        shot.write_bytes(b"\x89PNG fake")

        r = _run(READBACK_SCRIPT, *_readback_args(out, csi, [shot], (8, 11), values),
                 "--out", str(d / "rb.SET.readback.json"))
        assert r.returncode == 0, r.stdout + r.stderr
        rec = json.loads((d / "rb.SET.readback.json").read_text(encoding="utf-8"))

        assert _sha(prov_path) == prov_before, "生成记录被改写了"
        assert _prov(out)["csi_read_back_verified"] is False, "生成记录不许自己变 true"
        assert rec["record_kind"] == "csi_readback"
        assert rec["csi_read_back_verified"] is True
        assert rec["generation_record"]["sha256"] == prov_before
        assert rec["generation_record"]["immutable"] is True
        e = rec["evidence"]
        assert e["exe"]["sha256"] == "a" * 64 and e["exe"]["version_confirmed"] is True
        assert e["probe"]["sha256"] == _sha(out) and e["probe"]["matches_generation_record"]
        assert e["operator"] and e["machine"] and e["observed_at"]
        assert e["authorization_basis"]
        assert e["screenshots"][0]["sha256"] == _sha(shot)
        assert e["csi_saved_set"]["sha256"] == _sha(csi)
        assert e["csi_saved_set"]["byte_identical_to_probe"] is False
        assert e["csi_saved_set"]["readings_by_idx"] == {"8": values[0], "11": values[1]}
        assert e["panel_readings_by_group_title"] == {G_HIGH: values[0], G_LOW: values[1]}
        assert rec["evidence_strength"] == "screenshot_plus_saved_file"
        c = rec["conclusion"]
        assert c["resolves_pair"] == [8, 11]
        assert c["result"] == "convention_holds"
        assert c["only_this_pair"] is True
        assert [5, 6] in c["other_pairs_still_convention"]
        assert [8, 11] not in c["other_pairs_still_convention"]


def test_readback_conclusion_flips_with_the_panel_reading() -> None:
    """同一份文件、面板读数反过来 ⇒ 结论必须是「归属反了」，不是「成立」。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        out = _make_probe(d, "rb2.SET", "--pair", "5,6")
        values = _prov(out)["probe"]["values_written"]
        csi = _fake_csi_saved(out, d)
        shot = d / "p.png"
        shot.write_bytes(b"\x89PNG fake")
        # 面板上 high 组显示的是 idx6 的值 ⇒ 约定反了
        args = _readback_args(out, csi, [shot], (5, 6), values)
        args[args.index(f"{G_HIGH}={values[0]}")] = f"{G_HIGH}={values[1]}"
        args[args.index(f"{G_LOW}={values[1]}")] = f"{G_LOW}={values[0]}"
        r = _run(READBACK_SCRIPT, *args, "--out", str(d / "rb2.readback.json"))
        assert r.returncode == 0, r.stdout + r.stderr
        rec = json.loads((d / "rb2.readback.json").read_text(encoding="utf-8"))
        assert rec["conclusion"]["result"] == "convention_reversed"
        assert "反了" in rec["conclusion"]["meaning"]


def test_readback_refuses_partial_evidence() -> None:
    """缺任何一件都不出记录：截图、另存文件、EXE 身份、分组标题、读数一致性。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        out = _make_probe(d, "rb3.SET", "--pair", "7,10")
        values = _prov(out)["probe"]["values_written"]
        csi = _fake_csi_saved(out, d)
        shot = d / "p.png"
        shot.write_bytes(b"\x89PNG fake")
        full = _readback_args(out, csi, [shot], (7, 10), values)
        target = str(d / "rb3.readback.json")

        def refuse(mutate, why: str):
            args = list(full)
            mutate(args)
            r = _run(READBACK_SCRIPT, *args, "--out", target)
            assert r.returncode != 0, f"该拒的没拒：{why}"
            assert not Path(target).exists(), f"拒绝之后还留下了记录：{why}"
            return r.stdout + r.stderr

        # ① 没有截图（argparse 层面就要求 --screenshot）
        i = full.index("--screenshot")
        msg = refuse(lambda a: (a.pop(i), a.pop(i)), "缺截图")
        assert "screenshot" in msg

        # ② 没有 CSI 另存文件
        j = full.index("--csi-set")
        msg = refuse(lambda a: (a.pop(j), a.pop(j)), "缺另存文件")
        assert "csi-set" in msg

        # ③ EXE 身份既不给哈希也不给理由
        def drop_exe(a):
            k = a.index("--exe-sha256")
            a.pop(k), a.pop(k)
        msg = refuse(drop_exe, "EXE 身份留空当已知")
        assert "只给一个" in msg or "exe-sha256" in msg

        # ④ 分组标题按「左列/右列」记，不是面板真实标题
        def bogus_titles(a):
            a[a.index(f"{G_HIGH}={values[0]}")] = f"左列={values[0]}"
            a[a.index(f"{G_LOW}={values[1]}")] = f"右列={values[1]}"
        msg = refuse(bogus_titles, "按左右位置记")
        assert "真实标题" in msg

        # ⑤ 面板读数与探针值不符
        def wrong_panel(a):
            a[a.index(f"{G_HIGH}={values[0]}")] = f"{G_HIGH}={values[0] + 100}"
        msg = refuse(wrong_panel, "面板读数不符")
        assert "停下" in msg

        # ⑥ 另存文件里的读数与探针值不符（换一份别的参数文件）
        other = _make_probe(d, "别的.SET", "--pair", "9,12")
        msg = refuse(lambda a: a.__setitem__(a.index("--csi-set") + 1, str(other)),
                     "另存文件读数不符")
        assert "停下" in msg

        # ⑦ 拿探针自己当另存文件（自己给自己作证）
        msg = refuse(lambda a: a.__setitem__(a.index("--csi-set") + 1, str(out)),
                     "csi-set 指向探针本身")
        assert "另存" in msg

        # ⑧ 生成记录不见了 ⇒ 指不回原探针
        prov = Path(str(out) + ".provenance.json")
        moved = d / "prov.bak"
        prov.rename(moved)
        try:
            r = _run(READBACK_SCRIPT, *full, "--out", target)
            assert r.returncode != 0 and "指回原探针" in (r.stdout + r.stderr)
        finally:
            moved.rename(prov)


def test_readback_marks_a_byte_identical_return_as_weak_evidence() -> None:
    """另存件与探针逐字节相同 ⇒ 默认拒绝；显式放行也必须标成弱证据。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        out = _make_probe(d, "rb4.SET", "--pair", "5,6")
        values = _prov(out)["probe"]["values_written"]
        copy = d / "拷回来的.SET"
        shutil.copy2(out, copy)
        shot = d / "p.png"
        shot.write_bytes(b"\x89PNG fake")
        args = _readback_args(out, copy, [shot], (5, 6), values)

        r = _run(READBACK_SCRIPT, *args, "--out", str(d / "x.json"))
        assert r.returncode != 0 and "逐字节相同" in (r.stdout + r.stderr)
        assert not (d / "x.json").exists()

        r = _run(READBACK_SCRIPT, *args, "--allow-identical-to-probe",
                 "--out", str(d / "x.json"))
        assert r.returncode == 0, r.stdout + r.stderr
        rec = json.loads((d / "x.json").read_text(encoding="utf-8"))
        assert rec["evidence"]["csi_saved_set"]["byte_identical_to_probe"] is True
        assert rec["evidence_strength"] == "weak_identical_bytes"


def test_readback_record_is_never_overwritten_or_put_in_the_repo() -> None:
    """第二次观测另起名字；读回记录也不许落进仓库工作树。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        out = _make_probe(d, "rb5.SET", "--pair", "9,12")
        values = _prov(out)["probe"]["values_written"]
        csi = _fake_csi_saved(out, d)
        shot = d / "p.png"
        shot.write_bytes(b"\x89PNG fake")
        args = _readback_args(out, csi, [shot], (9, 12), values)
        tgt = d / "rb5.readback.json"

        assert _run(READBACK_SCRIPT, *args, "--out", str(tgt)).returncode == 0
        first = _sha(tgt)
        r = _run(READBACK_SCRIPT, *args, "--out", str(tgt))
        assert r.returncode != 0 and "不覆盖" in (r.stdout + r.stderr)
        assert _sha(tgt) == first, "已有的读回记录被第二次观测盖掉了"

        inside = ROOT / "tmp_dp135_readback_should_never_exist.json"
        try:
            r = _run(READBACK_SCRIPT, *args, "--out", str(inside))
            assert r.returncode != 0 and "仓库工作树" in (r.stdout + r.stderr)
            assert not inside.exists()
        finally:
            inside.unlink(missing_ok=True)


def test_readback_refuses_a_probe_it_cannot_point_back_to() -> None:
    """哈希指不回生成记录 ⇒ 拒绝（拿错文件会让整轮收口归到别的探针上）。"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        out = _make_probe(d, "rb6.SET", "--pair", "5,6")
        values = _prov(out)["probe"]["values_written"]
        csi = _fake_csi_saved(out, d)
        shot = d / "p.png"
        shot.write_bytes(b"\x89PNG fake")

        tampered = d / "被换掉的.SET"
        shutil.copy2(out, tampered)
        prov = json.loads(Path(str(out) + ".provenance.json").read_text(encoding="utf-8"))
        prov["product"]["sha256"] = "0" * 64
        Path(str(tampered) + ".provenance.json").write_text(
            json.dumps(prov, ensure_ascii=False), encoding="utf-8")

        args = _readback_args(tampered, csi, [shot], (5, 6), values)
        r = _run(READBACK_SCRIPT, *args, "--out", str(d / "y.json"))
        assert r.returncode != 0 and "指不回" in (r.stdout + r.stderr)
        assert not (d / "y.json").exists()
