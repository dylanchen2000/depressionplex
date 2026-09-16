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

架构师四条裁决落地后，本模块还多钉两件事：
- 裁决 1：种子只管会话内（多 seed 不再停机），停机只剩三条、一条都不许降级成
  警告；被推翻的旧文案（含本单首版自己写的那句「从链条恢复」）逐字禁止回潮；
- 裁决 3：逐条 `first_scored_in` + 顶层 `first_scored_unresolved` 两个键名，
  以及「条数不等 ⇒ 拒绝导出」这道自检必须在第一个 download 之前。
- 裁决 2：入库侧一行未改（口径归 DP-130），所以
  test_ingest_truth_still_reads_holds_not_holds_counted 现在**应当是绿的**；
  它哪天变红，说明有人动了入库侧口径，那要有裁决、且要连这条测试一起改。
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
    """导出审计 JSON 必须带 DP-128 的契约字段，tool_version 钉死 v1.7。

    逐字段断言「键名: 值来源」都在 exportSnapshot 的对象字面量里——
    少一个键，链式恢复/重评声明/首次声明核对就会在入库侧静默失灵。

    裁决 3 追加的两个键名也在这一列：`first_scored_unresolved` 是顶层的
    「首评定不下来」清单，`first_scored_in` 是逐条记录级的首评文件名。
    入库侧（DP-130）要按这两个键名读，改名等于把裁决 3 作废。
    """
    body = _export_body()
    for key in ("cumulative_done", "claimed_first_session", "rescore",
                "rescore_of", "prior_files", "cumulative_done_count",
                "prior_done", "delivered_order",
                "first_scored_unresolved",      # 裁决 3：顶层
                "first_scored_in",              # 裁决 3：逐条记录级
                "seed"):                        # 裁决 1：本次会话的新种子也要落盘
        assert re.search(r"\b%s\s*:" % re.escape(key), body), \
            "导出对象缺契约字段 %s（DP-128 §1／裁决 3）——键名是线上契约，不许改名/漏写" % key
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


def test_export_selfcheck_stops_on_first_scored_disagreement() -> None:
    """裁决 3 的自检：`first_scored_unresolved` 的条数必须等于 `first_scored_in`
    为 null 的记录数，不等 ⇒ alert + return，一个字节都不落盘。

    与 §1.3 那道同一套处置、同一个位置要求（必须在第一个 download 之前）。
    少了这道，顶层清单与逐条字段就会各说各话，入库侧（DP-130）无从判断该信谁。
    """
    body = _export_body()
    assert "firstScoredUnresolved.length !== nullCount" in body, \
        "裁决 3 的导出前自检被删了——顶层清单与逐条 first_scored_in 打架时不许继续导出"
    i_check = body.index("firstScoredUnresolved.length !== nullCount")
    i_first_download = body.index("download(")
    assert i_check < i_first_download, \
        "裁决 3 的自检必须发生在第一个 download 之前，否则打架的文件已经落盘了"
    assert "return;" in body[i_check:i_first_download], \
        "自检失败后必须 return（拒绝导出），不许只告警继续落盘"
    # null 是「定不了」的唯一写法：不许拿空字符串或猜一个文件名顶替（那样顶层
    # 清单就永远是空的，自检也就永远绿——守卫自己变成假的）
    assert "=== null" in body[i_check - 400:i_check], \
        "first_scored_in 为 null 的记录数必须按 null 数出来，不许换判据"


# ---------------------------------------------------------------- 裁决 1：种子只管会话内


def test_seed_is_session_local_not_restored_from_chain() -> None:
    """裁决 1：多 seed 不再停机，续评/重评当场新生成种子。

    盯三处结构，任一处回潮就等于把被推翻的首版实现又装回去：
    - 全文件不许再出现 `seeds.size`（首版靠它判「链里两个种子 ⇒ 拒开」）；
    - onStart 的续评分支必须调 newSessionSeed()；
    - 首次会话仍用界面字段里工具预填的那个（可改、必须是非负整数）。
    """
    text = _html_text()
    assert "seeds.size" not in text, \
        "seeds.size 又出现了——裁决 1 已把「链里种子数」从停机条件里删掉，" \
        "种子只管会话内，多 seed 不影响去重"
    m = re.search(r"function onStart\(\) \{(.*?)\n\}\n", text, re.S)
    assert m, "找不到 onStart"
    body = m.group(1)
    assert "newSessionSeed()" in body, \
        "onStart 不再新生成会话种子——续评/重评必须当场生成，不许从链条恢复"
    assert "function newSessionSeed()" in text, \
        "newSessionSeed 被删了：种子生成的唯一入口没了"
    # 界面上不许挂一个其实不会被用到的种子数字（续评时字段清空、只读）
    assert '$("seed").value = "";' in text, \
        "续评时种子字段没清空——屏幕上挂着一个不会被使用的数字，等于又在暗示" \
        "「顺序跨会话固定」这件做不到的事"


def test_three_stops_are_stops_not_warnings() -> None:
    """裁决 1 只留三条停机，且**一条都不许降级成警告**：
    ① 并集里出现名册外的 trial_id；② len(cumulative_done) != cumulative_done_count；
    ③ 同一评分员同一范式两个会话都声称 claimed_first_session: true。

    三条都必须走 errs → onStart 里 `if (prior.errs.length) { setupErr; return; }`
    这条唯一通道（不是 console.warn、不是 chainStat 里的一行提醒）。
    """
    text = _html_text()
    # ①：records / prior_done / cumulative_done 三个来源都要对名册查
    for frag in ("：记录里的 ", "：prior_done 里的 ", "：cumulative_done 里的 "):
        assert frag in text and "不在本清单里" in text, \
            "停机条件①缺了来源 %r——名册外的 trial_id 必须三个来源都查" % frag
    # ②：字段打架
    assert "cumulative_done 有 " in text and "这份导出不可信" in text, \
        "停机条件②被删了：cumulative_done 与 cumulative_done_count 打架必须拒收"
    # ③：两份 claimed_first_session
    assert "都声称是第一次会话" in text and "不许开始" in text, \
        "停机条件③被删了：同一评分员同一范式两个会话都声称第一次必须拒开"
    # 唯一通道：errs 非空 ⇒ 红字 + return，没有「继续但提醒」这种分支
    m = re.search(r"function onStart\(\) \{(.*?)\n\}\n", text, re.S)
    assert m, "找不到 onStart"
    body = m.group(1)
    i = body.index("if (prior.errs.length)")
    tail = body[i:i + 400]
    assert "return;" in tail, "prior.errs 非空时必须 return（停机），不许降级成警告"
    assert "console.warn" not in body, "onStart 里不许用 console.warn 顶替停机"
    # 停机与「显示行」分开：chainStat 只是显示，不承担关卡职责——反过来说，
    # 关卡也不许搬进显示里（搬进去就等于「刷新一下就绕过了」）
    stat = re.search(r"function updateChainStat\(\) \{(.*?)\n\}\n", text, re.S)
    assert stat, "找不到 updateChainStat"
    for frag in ("errs.push", "alert(", "confirm("):
        assert frag not in stat.group(1), \
            "updateChainStat 里出现了 %r——它是显示行，不是关卡；三条停机必须留在 " \
            "readPriorDone / exportSnapshot 里，谁也不许降级成提醒" % frag


# ---------------------------------------------------------------- §4(3)：复制种子按钮已删


def test_copy_seed_button_is_gone() -> None:
    """§2-A：「复制种子」按钮必须不存在——手抄种子会让两个人跑到同一个顺序上。
    跨评分员共享种子按架构师实测是 **4 个**（204129075 / 446237714 / 711819040 /
    942674390，全是张咸明 + 徐乐彤成对），不是台账原先写的 3 次——所以这个按钮
    更该删，文案里也不许再引用那个错的计数。id、按钮文本、事件挂接三处都钉。"""
    text = _html_text()
    assert "copySeed" not in text, \
        "copySeed 又出现了——「复制种子」按钮是 DP-128 §2-A 明令删除的"
    assert not re.search(r">\s*复制种子\s*<", text), \
        "「复制种子」按钮文本又出现了（DP-128 §2-A）"


# ---------------------------------------------------------------- §4(4)：文案逐字钉


def test_old_false_promise_copy_is_gone() -> None:
    """§1.5 + 裁决 1：假承诺/甩锅提示必须从整个文件里消失（含注释——注释里的
    原句会被下一次「顺手恢复」抄回去）。

    裁决 1 追加的那几条禁的是**本单首版自己写进去的**文案：多 seed 停机、
    「种子从链条恢复」、「只选最新一份就够」，以及 DP-081 台账里那个错的
    共享种子计数。它们都已被实测推翻，留在文件里就是下一次抄回去的种子。
    """
    text = _html_text()
    banned = ("不会把评过的重新发一遍",   # 旧 103–104 行：工具保证不了的打包票
              "漏了会重评",               # 旧 112 行：把结构缺陷的责任推给评分员
              "漏一份就会重评",           # 旧 batchDone：同一句的变体
              # ---- 裁决 1 推翻的（首版实现 + v1.6 旧文案）----
              "这几份导出不是同一个顺序，不许混在一起续评",  # 首版双种子停机文案
              "选最新一份就能接上全部历史",                  # 实测漏 0 / 8 / 4 场
              "续评时种子从「已评进度」里恢复",               # 种子不再从链条恢复
              "缺 seed，无法从链条恢复顺序",                  # 缺 seed 不再拒开
              "已真实发生 3 次",        # 共享种子实测是 4 个，不是台账写的 3 次
              )
    for phrase in banned:
        assert phrase not in text, \
            "被禁的旧文案又出现了：%r——能靠结构挡住的事，不许靠提示语挡（DP-128 §1.5／裁决 1）" % phrase


def test_global_order_promise_survives_only_as_a_retraction() -> None:
    """裁决 1 的原话：旧版「顺序按种子全局固定」从来没成立过（6 次会话 6 个
    seed），「别用另一句同样做不到的承诺替换它」。

    所以这句话在文件里只允许以**被撤回的原话**形式出现一次（changelog 里
    「旧版那句『…』从来没成立过」），界面上一句活的承诺都不许留。
    """
    text = _html_text()
    retraction = "旧版那句「顺序、呈现号按种子全局固定」从来没成立过"
    assert text.count(retraction) == 1, \
        "changelog 里那句撤回记录应当恰好出现一次，实际 %d 次" % text.count(retraction)
    assert text.count("按种子全局固定") == text.count(retraction), \
        "「按种子全局固定」又被当成活的承诺写回去了——它从来没成立过，" \
        "撤回记录之外一处都不许有"


def test_new_truthful_copy_is_pinned() -> None:
    """§1.5 + 裁决 1／裁决 3：新文案逐字钉死。这些句子是评分员唯一能看到的
    规则说明，改一个字都可能把「结构保证」又变回「口头承诺」。

    界面上那句关于顺序的话按**去掉空白后的全文相等**来钉：源码为了行宽会折行，
    钉折行位置没意义，钉评分员真看到的那一句才有意义。
    """
    text = _html_text()
    phrases = (
        "选进「已评进度」的都不会重发",           # §1.5：工具真能保证的那句
        "续评必须选文件，不选就不许开始",          # §1.5：结构规则的原话
        "claimed_first_session",                 # §1.2：声明落进文件的字段名
        "两个说同一件事的字段打架",                # §1.3：自检停机的理由原话
        # ---- 裁决 1：必须选全部历史导出 + 当场把恢复/剩余摆给人看 ----
        "全部选上，不是只选最新一份",
        "已选 ", "已恢复 ", "cumulative_done_count 最大的是",
        # ---- 裁决 1：三条停机的文案 ----
        "不在本清单里", "都声称是第一次会话",
        # ---- 裁决 3：两个键名与自检的理由 ----
        "first_scored_unresolved", "first_scored_in",
    )
    missing = [p for p in phrases if p not in text]
    assert not missing, "v1.7 必须逐字在场的文案缺失：%s" % missing

    m = re.search(r"关于顺序，工具能做到的只有这一句：<b>(.*?)</b>", text, re.S)
    assert m, "界面上「工具能做到的只有这一句」那段不见了——裁决 1 要求把真话写在那儿"
    assert re.sub(r"\s+", "", m.group(1)) == \
        "顺序在本次会话内由种子固定，跨会话不保证；跨会话靠已评清单去重，不靠顺序", \
        "关于顺序的那句话被改了：裁决 1 逐字给的就是这一句，" \
        "不许换成另一句同样做不到的承诺（实际：%r）" % m.group(1)


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
        # 裁决 3：顶层「首评定不下来」清单（与 records[].first_scored_in 为 null
        # 的条数必须相等，工具导出前自检；这里如实给空表）
        "first_scored_unresolved": [],
        "scorer_id": "测试员", "seed": 4242,
        "delivered_order": ["v-ch1", "v-ch2"],
        "tool_version": "v1.7", "exported_at": "2026-09-15T08:00:00.000Z",
        "records": records,
    }
    doc.update(extra)
    return doc


def _v17_record(trial_id, order, holds, rewatch_s, counted=None, first_scored_in=None):
    """v1.7 的记录形状。`holds` 是原始按键区间（语义同 v1.4–v1.6），
    `holds_counted` 是实际计入 mobile_seconds 的那些（重看段已在前沿处修剪）；
    `first_scored_in` 是裁决 3 加的逐条字段：这一条的首评在哪份导出里，
    定不下来就是 null（并且必须同时出现在顶层 first_scored_unresolved 里）。"""
    return {
        "trial_id": trial_id, "assay": "TST",
        "mobile_seconds": round(sum(b - a for a, b in (holds if counted is None else counted)), 2),
        "window_s": 360.0, "declared_empty": False,
        "tail_climbing": False, "wall_support_still": None,
        "unscoreable": False, "note": "", "playback_rate": 1,
        "holds": holds,
        "holds_counted": holds if counted is None else counted,   # v1.7 新记录字段（§2-B/C）
        "rewatch_s": rewatch_s,          # v1.7 新记录字段（§2-C）
        "first_scored_in": first_scored_in,   # v1.7 新记录字段（裁决 3）
        "presentation_order": order, "scored_at": "2026-09-15",
    }


def test_ingest_eats_v17_export_shape() -> None:
    """带 cumulative_done / rescore / rewatch_s 的 v1.7 导出必须原样穿过
    build_table：新字段被安全忽略（工具只如实记账，哪遍算真值是入库侧口径），
    读数照旧从 holds 并集重算。断言具体值，不断言「不报错」。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        own = "timer_audit_TST_测试员_2026-09-15_final_080000Z.json"
        recs = [_v17_record("v-ch1", 1, [[0.0, 10.25], [10.25, 12.75]], 0.0,
                            first_scored_in=own),
                _v17_record("v-ch2", 2, [[5.0, 20.0], [0.0, 10.0]], 3.4,
                            first_scored_in=own)]
        p = tmp / own
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
        # 新字段不许渗进行模型：它们不在 TrialRow 上，也不在重算表列里。
        # 裁决 3 的两个键也在这一列——它们是给入库侧**另开一单**（DP-130）用的
        # 线索，本单一行不改入库侧，所以不许顺手把它们接进真值行模型。
        field_names = {f.name for f in dataclasses.fields(TrialRow)}
        for alien in ("cumulative_done", "claimed_first_session", "rescore",
                      "rescore_of", "prior_files", "rewatch_s", "holds_counted",
                      "first_scored_in", "first_scored_unresolved"):
            assert alien not in field_names, \
                "%s 渗进了 TrialRow——工具只如实记账，入库侧口径不许被导出形状牵着走" % alien


def test_ingest_truth_still_reads_holds_not_holds_counted() -> None:
    """v1.7 起一条记录里有两个区间数组，**入库侧真值读的仍是 `holds`**（DP-128 一行
    未改入库侧，§5 禁令）。这条把现状钉住：重看过的场次两个数组不同，真值取哪个是
    DP-082/DP-124 的口径——要改必须由架构师裁决并**改这条测试**，不许静默漂过去。

    **裁决 2（架构师，2026-09-16）**：真值该读 `holds_counted`，`holds` 保留为原始
    按键轨迹；两份都落盘是对的，恒等式 union(holds_counted) == mobile_seconds 也是
    对的。但**入库侧的口径改动归 DP-130 另开一单，本单一行都不许改**——v1.4–v1.6
    的历史导出没有 holds_counted，不许回填、不许重算（41/92 条含重看按键、最差
    218.15 s 另账），per_key_excess_wallclock_s 变负也不是 bug。

    所以这条测试现在是**绿的、并且应当保持绿**：它钉的是「本单没碰入库侧」这个
    事实。DP-130 真去改口径时，这条会红——那是有意的，届时必须连这条测试一起改，
    并把改动写在那一单的交付报告里，不许悄悄漂过去。

    夹具是一场重看过的试次：原始按键 [30,40] 与 [10,35]（并集 30 s），
    实际计入的只有 [30,40]（10 s，重看段的按键按 §2-C 不计入在动）。
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        own = "timer_audit_TST_测试员_2026-09-15_final_090000Z.json"
        rec = _v17_record("v-ch9", 1, [[30.0, 40.0], [10.0, 35.0]], 25.0,
                          counted=[[30.0, 40.0]], first_scored_in=own)
        assert rec["mobile_seconds"] == 10.0        # 工具自己算的（重看不计入）
        p = tmp / own
        p.write_text(json.dumps(_v17_doc([rec], cumulative_done=["v-ch9"],
                                        cumulative_done_count=1), ensure_ascii=False),
                     encoding="utf-8")
        res = build_table(tmp)
        assert res.n_trials == 1
        row = res.rows[0]
        # 现状：真值并集来自 holds（原始按键），所以这一场读出 30.0 而不是 10.0
        assert row.mobile_union_s == 30.0, \
            "入库真值不再等于 union(holds)——这是 DP-082/DP-124 的口径，改它要有裁决"
        assert row.mobile_seconds_DISCARDED == 10.0   # 工具那份数照旧被丢弃但留着对账
        assert row.n_hold_segments == 2               # 段数也按 holds 数
        for alien in ("rewatch_s", "holds_counted",
                      "first_scored_in", "first_scored_unresolved"):
            assert alien not in {f.name for f in dataclasses.fields(TrialRow)}, \
                "%s 渗进了 TrialRow——v1.7 的新记账字段不许改入库侧的行模型" % alien
        assert res.crosscheck_mismatches == []        # 本 tmp 目录没有配套 CSV


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
