"""标注工具盲法硬约束测试（评审 2026-08-25，工具级约束）。

锚定偏差：预填机器输出会让标注员倾向接受眼前答案 ⇒ κ 虚高 ⇒ 验收真值
被污染。故 --from-events 仅训练池；κ 只在全盲文件上算。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from depressionplex.cli import annotate as A

SEED = {"bouts": [{"start": 10, "end": 40,
                   "primitives": {"hind_active": True}, "axis_orient": "down"}]}


def _seed_file(tmp: Path) -> Path:
    p = tmp / "seed.json"
    p.write_text(json.dumps(SEED))
    return p


def test_from_events_rejected_outside_train_pool() -> None:
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        seed = _seed_file(tmp)
        try:
            A.main(["init", str(tmp / "v.json"), "--trial", "v1", "--assay", "TST",
                    "--annotator", "ann1", "--n-frames", "100",
                    "--from-events", str(seed)])
        except SystemExit as e:
            assert "train" in str(e)
        else:
            raise AssertionError("验证池预填必须被硬约束拦下")


def test_from_events_allowed_in_train_pool() -> None:
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        seed = _seed_file(tmp)
        out = tmp / "train.json"
        rc = A.main(["init", str(out), "--trial", "v1", "--assay", "TST",
                     "--annotator", "ann1", "--n-frames", "100",
                     "--pool", "train", "--from-events", str(seed)])
        assert rc == 0
        doc = json.loads(out.read_text())
        assert doc["prefill"] is True and doc["pool"] == "train"


def test_agree_rejects_prefilled_files() -> None:
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        seed = _seed_file(tmp)
        a = tmp / "a.json"
        b = tmp / "b.json"
        A.main(["init", str(a), "--trial", "v1", "--assay", "TST",
                "--annotator", "ann1", "--n-frames", "100",
                "--pool", "train", "--from-events", str(seed)])
        A.main(["init", str(b), "--trial", "v1", "--assay", "TST",
                "--annotator", "ann2", "--n-frames", "100"])
        try:
            A.main(["agree", str(a), str(b)])
        except SystemExit as e:
            assert "盲" in str(e)
        else:
            raise AssertionError("含预填的文件不得算 κ")


def test_agree_ok_on_blind_files() -> None:
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        a = tmp / "a.json"
        b = tmp / "b.json"
        for name, path in (("ann1", a), ("ann2", b)):
            rc = A.main(["init", str(path), "--trial", "v1", "--assay", "TST",
                         "--annotator", name, "--n-frames", "100"])
            assert rc == 0
            doc = json.loads(path.read_text())
            assert doc["prefill"] is False
            doc["bouts"] = SEED["bouts"]
            path.write_text(json.dumps(doc))
        rc = A.main(["agree", str(a), str(b)])
        assert rc == 0
