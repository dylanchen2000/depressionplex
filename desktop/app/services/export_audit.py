"""审计包渲染器（B6，DP-110）。

审计包内容（派工单 §1）：
- 4 个引擎产出文件（csv / timeline_csv / run_json / report_txt）
- experiment.json
- 采集自检 JSON（若有）
- 声明.txt（从 Report.declaration 取，与 xlsx/PDF 同源）
- MANIFEST.json（每个成员的 sha256、大小；取不到的字段写 null）

约束：
- 纯标准库（zipfile + hashlib + json），沙箱可验。
- MANIFEST 只有一个序列化器（json.dumps，在本文件里）。
- 取不到的字段写 null，不许悄悄少装（缺哪个在 MANIFEST 里标 null + 原因）。
- 渲染层只摆不算：无 +/-/*/÷/round()。
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

from desktop.app.models.report import Report


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest_entry(
    name: str,
    data: bytes | None,
    missing_reason: str | None = None,
) -> dict[str, Any]:
    """构造 MANIFEST.json 的一个条目。取不到的字段写 null。"""
    if data is None:
        return {
            "name": name,
            "sha256": None,
            "size": None,
            "missing_reason": missing_reason or "文件不存在或取不到",
        }
    return {
        "name": name,
        "sha256": _sha256(data),
        "size": len(data),
        "missing_reason": None,
    }


def render_audit_zip(report: Report, out_path: Path) -> None:
    """把审计包写到 out_path（zip 格式）。

    Args:
        report: 内容层 Report 对象（含引擎产出路径）
        out_path: 输出路径（.zip）
    """
    # 声明文本（与 xlsx/PDF 同源，唯一来源）
    declaration_bytes = report.declaration.encode("utf-8")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    manifest_entries: list[dict[str, Any]] = []

    # 先收集所有要写入的文件
    members: list[tuple[str, bytes]] = []

    # 1. 声明.txt（固定包含）
    members.append(("声明.txt", declaration_bytes))
    manifest_entries.append(_manifest_entry("声明.txt", declaration_bytes))

    # 2. 四个引擎产出文件
    engine_file_map = {
        "csv":          "csv",
        "timeline_csv": "timeline_csv",
        "run_json":     "run_json",
        "report_txt":   "report_txt",
    }
    for key, label in engine_file_map.items():
        path = report.engine_output_paths.get(key)
        if path is not None and Path(path).exists():
            data = Path(path).read_bytes()
            arc_name = Path(path).name
            members.append((arc_name, data))
            manifest_entries.append(_manifest_entry(arc_name, data))
        else:
            # 取不到的字段写 null + 原因，不许悄悄少装
            arc_name = f"[{key}]"
            reason = (
                f"路径未提供" if path is None
                else f"文件不存在：{path}"
            )
            manifest_entries.append(_manifest_entry(arc_name, None, reason))

    # 3. experiment.json（从 engine_output_paths 的父目录找）
    exp_json_path: Path | None = None
    if report.engine_output_paths:
        # 取任意一个路径的父目录
        any_path = next(iter(report.engine_output_paths.values()))
        candidate = Path(any_path).parent / "experiment.json"
        if candidate.exists():
            exp_json_path = candidate

    if exp_json_path is not None:
        data = exp_json_path.read_bytes()
        members.append(("experiment.json", data))
        manifest_entries.append(_manifest_entry("experiment.json", data))
    else:
        manifest_entries.append(
            _manifest_entry("experiment.json", None, "未找到 experiment.json")
        )

    # 4. 采集自检 JSON（若有）
    if report.self_test_json_path is not None and report.self_test_json_path.exists():
        data = report.self_test_json_path.read_bytes()
        arc_name = report.self_test_json_path.name
        members.append((arc_name, data))
        manifest_entries.append(_manifest_entry(arc_name, data))
    else:
        manifest_entries.append(
            _manifest_entry(
                "[self_test.json]",
                None,
                "本机未做采集自检" if report.self_test_json_path is None
                else f"自检 JSON 不存在：{report.self_test_json_path}",
            )
        )

    # 5. 生成 MANIFEST.json（只有一个序列化器，就在这里）
    # 先占位计算自身 sha256，再统一序列化
    placeholder_entry = _manifest_entry("MANIFEST.json", b"")
    all_entries = [*manifest_entries, placeholder_entry]
    final_manifest = json.dumps(
        {"version": "1", "entries": all_entries},
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    # 用最终字节覆盖占位符（重新计算 sha256）
    real_entry = _manifest_entry("MANIFEST.json", final_manifest)
    all_entries[-1] = real_entry
    final_manifest = json.dumps(
        {"version": "1", "entries": all_entries},
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")

    # 6. 写 zip
    with zipfile.ZipFile(str(out_path), "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arc_name, data in members:
            zf.writestr(arc_name, data)
        zf.writestr("MANIFEST.json", final_manifest)
