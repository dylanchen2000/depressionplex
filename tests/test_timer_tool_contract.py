# -*- coding: utf-8 -*-
"""DP-128（派工单 B13）：秒表工具 v1.7 的**契约守卫**。

被测对象是单文件 HTML 工具本体与入库侧的接缝。node 自测
（tools/timer/test_timer_assay.js，由 test_timer_tool.py 代跑）盯行为；
这里盯的是**契约文本**：导出对象里必须有哪些字段、版本钉在哪、哪些文案
必须存在/必须消失、入库侧必须吃得下新形状、真值数据回归数字不许漂。

为什么用「HTML 文本 + 入库端到端」两层而不是只跑 node：
- 导出字段名是工具与入库侧（human_agreement.load_audit_json）之间的线上契约，
  改一个键名就会静默断掉 cumulative_done 的链式恢复（DP-128 §1.3 的伤口）；
- 文案是产品行为的一部分（§1.5：界面文案不许承诺工具做不到的事），
  「不为空」不算验收，「说的是真话」才算——所以逐字钉。
"""
from __future__ import annotations

import dataclasses
import json
import re
import tempfile
from pathlib import Path

from depressionplex.human_agreement import (
    KEY_FIELDS,
    READING_FIELDS,
    SESSION_TRACE_FIELDS,
    TrialRow,
    build_table,
    load_audit_json,
)

REPO = Path(__file__).resolve().parents[1]
HTML = REPO / "tools" / "timer" / "DepressionPlex_stopwatch_timer_v1.html"
INCOMING = REPO / "data" / "human_scores" / "incoming"


def _html_text() -> str:
    assert HTML.exists(), "缺秒表工具文件：%s" % HTML
    return HTML.read_text(encoding="utf-8")


def _export_body() -> str:
    """抠出 exportSnapshot 函数体——守卫必须盯**真实产品路径**（导出对象本身），
    不是全文件撒网：注释里提一句字段名不算实现了它。"""
    text = _html_text()
    m = re.search(r"function exportSnapshot\(final\) \{(.*?)\n\}\n", text, re.S)
    assert m, "找不到 exportSnapshot——导出函数被改名/挪走等于契约断线"
    return m.group(1)


# ---------------------------------------------------------------- §4(2)：导出对象契约


def test_export_object_pins_v17_contract_fields() -> None:
    """导出审计 JSON 必须带 DP-128 的五个契约字段，tool_version 钉死 v1.7。

    逐字段断言「键名: 值来源」都在 exportSnapshot 的对象字面量里——
    少一个键，链式恢复/重评声明/首次声明核对就会在入库侧静默失灵。
    """
    body = _export_body()
    for key in ("cumulative_done", "claimed_first_session", "rescore",
                "rescore_of", "prior_files", "cumulative_done_count",
                "prior_done", "delivered_order"):
        assert re.search(r"\b%s\s*:" % re.escape(key), body), \
            "导出对象缺契约字段 %s（DP-128 §1）——键名是线上契约，不许改名/漏写" % key
    m = re.search(r'tool_version\s*:\s*"([^"]+)"', body)
    assert m, "导出对象里找不到 tool_version"
    assert m.group(1) == "v1.7", \
        "tool_version 必须是 \"v1.7\"，实际 %r——版本号是入库侧分层的依据" % m.group(1)


def test_export_selfcheck_stops_on_field_disagreement() -> None:
    """§1.3 的停机必须写在导出路径上：cumulative_done 与 cumulative_done_count
    打架 ⇒ alert + return（一个字节都不落盘）。守卫盯住这两句都在。"""
    body = _export_body()
    assert "cumulativeDone.length !== nDone()" in body, \
        "导出前自检被删了——两个说同一件事的字段打架时，不许挑一个信（DP-128 §1.3）"
    # 自检失败必须在任何 download 之前 return：alert 到 return 之间不许夹 download
    i_check = body.index("cumulativeDone.length !== nDone()")
    i_first_download = body.index("download(")
    assert i_check < i_first_download, \
        "自检必须发生在第一个 download 之前，否则打架的文件已经落盘了"
    assert "return;" in body[i_check:i_first_download], \
        "自检失败后必须 return（拒绝导出），不许只告警继续落盘"


# ---------------------------------------------------------------- §4(3)：复制种子按钮已删


def test_copy_seed_button_is_gone() -> None:
    """§2-A：「复制种子」按钮必须不存在——手抄种子会让两个人跑到同一个顺序上
    （DP-081 记的共享种子已真实发生 3 次）。id、按钮文本、事件挂接三处都钉。"""
    text = _html_text()
    assert "copySeed" not in text, \
        "copySeed 又出现了——「复制种子」按钮是 DP-128 §2-A 明令删除的"
    assert not re.search(r">\s*复制种子\s*<", text), \
        "「复制种子」按钮文本又出现了（DP-128 §2-A）"


# ---------------------------------------------------------------- §4(4)：文案逐字钉


def test_old_false_promise_copy_is_gone() -> None:
    """§1.5：旧版两句假承诺/甩锅提示必须从整个文件里消失（含注释——注释里的
    原句会被下一次「顺手恢复」抄回去）。"""
    text = _html_text()
    banned = ("不会把评过的重新发一遍",   # 旧 103–104 行：工具保证不了的打包票
              "漏了会重评",               # 旧 112 行：把结构缺陷的责任推给评分员
              "漏一份就会重评")           # 旧 batchDone：同一句的变体
    for phrase in banned:
        assert phrase not in text, \
            "被禁的旧文案又出现了：%r——能靠结构挡住的事，不许靠提示语挡（DP-128 §1.5）" % phrase


def test_new_truthful_copy_is_pinned() -> None:
    """§1.5 + §1.1：新文案逐字钉死（样式同 test_declaration_key_phrases_are_pinned）。
    这些句子是评分员唯一能看到的规则说明，改一个字都可能把「结构保证」又变回
    「口头承诺」。"""
    text = _html_text()
    phrases = (
        "选进「已评进度」的都不会重发",           # §1.5：工具真能保证的那句
        "续评必须选文件，不选就不许开始",          # §1.5：结构规则的原话
        "这几份导出不是同一个顺序，不许混在一起续评",  # §1.1：双种子停机文案，逐字裁决
        "claimed_first_session",                 # §1.2：声明落进文件的字段名
        "两个说同一件事的字段打架",                # §1.3：自检停机的理由原话
    )
    missing = [p for p in phrases if p not in text]
    assert not missing, "v1.7 必须逐字在场的文案缺失：%s" % missing


# ---------------------------------------------------------------- §4(5)：入库侧吃得下新形状


def _v17_doc(records, **extra):
    doc = {
        "format": "depressionplex.stopwatch-audit.v1",
        "assay": "TST", "partial": False,
        "done_count": len(records), "total_trials": 2,
        "prior_done": [],
        # v1.7 新头部字段（DP-128 §1.2/§1.3/§1.4）
        "cumulative_done": ["v-ch1", "v-ch2"],
        "cumulative_done_count": 2,
        "claimed_first_session": False,
        "rescore": False, "rescore_of": [], "prior_files": [],
        "scorer_id": "测试员", "seed": 4242,
        "delivered_order": ["v-ch1", "v-ch2"],
        "tool_version": "v1.7", "exported_at": "2026-09-15T08:00:00.000Z",
        "records": records,
    }
    doc.update(extra)
    return doc


def _v17_record(trial_id, order, holds, rewatch_s):
    return {
        "trial_id": trial_id, "assay": "TST",
        "mobile_seconds": round(sum(b - a for a, b in holds), 2),
        "window_s": 360.0, "declared_empty": False,
        "tail_climbing": False, "wall_support_still": None,
        "unscoreable": False, "note": "", "playback_rate": 1,
        "holds": holds,
        "rewatch_s": rewatch_s,          # v1.7 新记录字段（§2-C）
        "presentation_order": order, "scored_at": "2026-09-15",
    }


def test_ingest_eats_v17_export_shape() -> None:
    """带 cumulative_done / rescore / rewatch_s 的 v1.7 导出必须原样穿过
    build_table：新字段被安全忽略（工具只如实记账，哪遍算真值是入库侧口径），
    读数照旧从 holds 并集重算。断言具体值，不断言「不报错」。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        recs = [_v17_record("v-ch1", 1, [[0.0, 10.25], [10.25, 12.75]], 0.0),
                _v17_record("v-ch2", 2, [[5.0, 20.0], [0.0, 10.0]], 3.4)]
        p = tmp / "timer_audit_TST_测试员_2026-09-15_final_080000Z.json"
        p.write_text(json.dumps(_v17_doc(recs), ensure_ascii=False), encoding="utf-8")
        res = build_table(tmp)
        assert res.n_trials == 2, "v1.7 导出应入库 2 条真值，实际 %d" % res.n_trials
        assert res.n_repeats == 0
        by_tid = {r.trial_id: r for r in res.rows}
        # 读数口径不变：mobile 从 holds 并集重算，工具落的 mobile_seconds 一律丢弃
        assert by_tid["v-ch1"].mobile_union_s == 12.75   # [0,10.25]∪[10.25,12.75]
        assert by_tid["v-ch2"].mobile_union_s == 20.0    # [5,20]∪[0,10] = [0,20]
        assert by_tid["v-ch2"].mobile_seconds_DISCARDED == 25.0   # 朴素和 15+10，虚高 5
        assert by_tid["v-ch1"].tool_version == "v1.7"
        assert by_tid["v-ch1"].seed == 4242
        assert res.seeds[("测试员", "TST")] == 4242
        assert res.crosscheck_mismatches == []
        # 新字段不许渗进行模型：它们不在 TrialRow 上，也不在重算表列里
        field_names = {f.name for f in dataclasses.fields(TrialRow)}
        for alien in ("cumulative_done", "claimed_first_session", "rescore",
                      "rescore_of", "prior_files", "rewatch_s"):
            assert alien not in field_names, \
                "%s 渗进了 TrialRow——工具只如实记账，入库侧口径不许被导出形状牵着走" % alien


def test_ingest_side_constants_untouched_by_dp128() -> None:
    """§5 禁令的守卫：DP-128 不许动 DP-082 的归并口径。三组字段划分与唯一键
    逐字钉死；改了这里 = 改了已发表数字的口径，必须走架构师裁决而不是顺手改。"""
    assert KEY_FIELDS == ("scorer_id", "trial_id")
    assert SESSION_TRACE_FIELDS == (
        "source", "seed", "presentation_order", "scored_at", "tool_version",
        "note", "warnings", "source_file",
    )
    assert READING_FIELDS == (
        "assay", "assay_source", "video", "chamber", "playback_rate",
        "tail_climbing", "wall_support_still", "declared_empty", "unscoreable",
        "status", "n_hold_segments", "holds_unsorted", "zero_length_segments",
        "mobile_union_s", "window_s", "window_source", "immobility_s",
        "mobile_seconds_DISCARDED", "naive_inflation_s",
        "per_key_excess_wallclock_s", "reject_reason",
    )


# ---------------------------------------------------------------- §4(6)：真值数据回归


def test_real_incoming_still_ingests_after_v17() -> None:
    """17 份真实导出（v1.4–v1.6，字段形状早于 v1.7）必须照旧入库，
    三个已发表数字一个不许漂：88 真值 / 27 已声明复评 / 1 同源副本。
    v1.7 只加了导出字段、没改 loader——这条红了说明改到了不该改的地方。"""
    audits = sorted(INCOMING.glob("timer_audit_*.json"))
    assert len(audits) == 17, "incoming 里应有 17 份审计导出，实际 %d" % len(audits)
    for p in audits:
        header, rows = load_audit_json(p)
        assert header["scorer_id"], "%s：头部缺 scorer_id" % p.name
        assert header["seed"] is not None, "%s：头部缺 seed" % p.name
        assert rows, "%s：一份记录都没有" % p.name
    res = build_table(INCOMING)
    assert res.n_trials == 88, "真值应为 88 条，实际 %d" % res.n_trials
    assert res.n_repeats == 27, "已声明复评应为 27 条，实际 %d" % res.n_repeats
    assert len(res.duplicate_notes) == 28, \
        "重复键应为 28 个（27 声明重评 + 1 同源副本），实际 %d" % len(res.duplicate_notes)
    assert sum("同源副本" in n for n in res.duplicate_notes) == 1
