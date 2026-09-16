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
- DP-131 裁决①：本次选到 0 场视频是一道「不许开始」（逐字文案 + 关在源码里的
  位置 + 那句假出路的字面全文件禁留，含注释）；裁决②：§1.2 三道关与 §1.4 派题
  口径的**源码级**钉子——那道钉子只证明关还在源码里，不证明行为，行为归 node
  （为什么源码级也要钉：看守链太长，理由见该测试自己的 docstring）。
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


# ------------------------------------------------- 第二轮裁决（撤回 + 裁决 B/C/D）


def _on_start_body() -> str:
    """抠出 onStart 函数体：裁决 D 的那一个确认框就在这里面。"""
    text = _html_text()
    m = re.search(r"function onStart\(\) \{(.*?)\n\}\n", text, re.S)
    assert m, "找不到 onStart——「开始评分」的关卡全在这个函数里"
    return m.group(1)


def _body_of(fname: str, args: str) -> str:
    text = _html_text()
    m = re.search(r"function %s\(%s\) \{(.*?)\n\}\n" % (fname, args), text, re.S)
    assert m, "找不到 %s——它被改名/挪走等于契约断线" % fname
    return m.group(1)


def test_ruling_b_prior_files_copy_is_pinned() -> None:
    """裁决 B：重评勾选框那句提示必须说真话。

    `prior_files` 是**本次选中的历史导出**（会话级、原名不动），不是旧文案说的
    「首评所在文件」；首评在哪一份是记录级 `first_scored_in` 的事，而且它可能是
    null。旧文案把两个键混成一句，评分员照着它会把 prior_files 当成首评归因读。
    按去掉空白与 <code> 标签后的全文相等来钉：源码为了行宽会折行，钉折行位置
    没意义，钉评分员真看到的那一句才有意义（与「关于顺序」那句同一套钉法）。
    """
    text = _html_text()
    assert "（首评所在文件）" not in text, (
        "裁决 B 明令改掉的旧文案又出现了：prior_files 不是「首评所在文件」，"
        "首评归因是记录级 first_scored_in（定不了就写 null）")
    m = re.search(r'id="rescoreChk">(.*?)</label>', text, re.S)
    assert m, "找不到重评勾选框（id=rescoreChk）的提示——裁决 B 要钉的就是这一句"
    plain = re.sub(r"\s+", "", re.sub(r"</?code>", "", m.group(1)))
    assert plain == (
        "本次是重评（rescore）：只重派下面勾中的已评场次，并用新种子自盲重派。"
        "导出会记rescore/rescore_of/prior_files（本次选中的历史导出），"
        "逐条见first_scored_in"), (
        "重评勾选框的文案被改了（实际：%r）——裁决 B 逐字给的就是"
        "「prior_files（本次选中的历史导出）+ 逐条见 first_scored_in」" % plain)


def test_ruling_c_first_scored_in_is_three_branch_not_a_guess() -> None:
    """裁决 C：`first_scored_in` 宁可说不知道，不许说错。

    规则三分支，与 DP-127 的配对同形：候选 = 选中的导出里 records 含这一场、
    且该导出没把这一场列进 rescore_of 的；恰好一份 ⇒ 写它；零份或两份以上 ⇒
    null 并进 first_scored_unresolved，**不许挑一份**。指错的文件名会被直接
    写进 rescore_declarations.csv 的归因，比 null 危险得多。

    行为由 node 自测钉（chainInfo／buildFirstScoredIn 五条 + 导出端到端），
    这里钉**契约文本**：三分支的形状、rescore_of 排除必须在收候选之前、
    同一份文件里重复出现只记一次（不去重就会假报「两份」而误写 null）。
    """
    build = _body_of("buildFirstScoredIn", "info")
    assert "cand.length === 1 ? cand[0] : null" in build, (
        "裁决 C 的三分支核心不见了：恰好一份才写它，零份或两份以上一律 null。"
        "实际 buildFirstScoredIn 函数体：%r" % build)
    assert build.count("cand[0]") == 1, (
        "buildFirstScoredIn 里 cand[0] 出现了 %d 次——多出来的一处十有八九是"
        "「挑一份」的兜底（cand[0] || null 之类），裁决 C 明令不许挑" % build.count("cand[0]"))

    chain = _body_of("chainInfo", "items")
    assert "const rof = Array.isArray(d.rescore_of) ? d.rescore_of : [];" in chain, (
        "裁决 C 的排除规则不见了：读该导出自己的 rescore_of（缺字段的 v1.4–v1.6 "
        "当成空表，于是两份候选 ⇒ null，正是裁决要的效果）")
    assert "if (rof.includes(r.trial_id)) continue;" in chain, (
        "裁决 C 的排除规则不见了：把这一场列进自己 rescore_of 的那一份是重评，"
        "不算首评候选")
    assert chain.index("if (rof.includes(r.trial_id)) continue;") < chain.index("firstCand.set"), (
        "rescore_of 的排除必须发生在收候选**之前**——放到后面就等于先收再删，"
        "同一份文件里那条 continue 挡不住候选被建出来")
    assert "if (!l.includes(name)) l.push(name);" in chain, (
        "同一份文件里这一场出现两次只记一个候选的去重不见了：少了它，一份文件的"
        "重复记录会被当成「两份候选」，把定得下来的首评误写成 null")
    assert "firstCand.set(r.trial_id, [])" in chain, (
        "首评候选必须是**每场一个列表**（三分支要靠「几份」判断）："
        "存成单个文件名就没法表达「两份以上」")

    resolve = _body_of("resolveFirstScoredIn", "tid, ownName")
    assert "if (!m.has(tid)) return ownName;" in resolve, (
        "链条里没有这一场（本次是它的第一遍）⇒ 首评就是本导出文件自己；"
        "这一支被改了：%r" % resolve)
    assert "return v === null ? null : String(v);" in resolve, (
        "定不下来就得如实写 null（同时列进顶层 first_scored_unresolved），"
        "不许改成本文件名或随便挑一份：%r" % resolve)


def test_ruling_d_one_confirmation_box_lists_both_notes() -> None:
    """裁决 D：「分批没排满」与「本机有存档」两道二次确认合并成**一个框**。

    四连点的真实后果是盲点头，那比多点一次更糟；但合并只是少点几次，
    **两条内容一件都不许少说**，两段必须在同一个框里逐条列出。

    这里还钉一条实测出来的回归：裁决 1 之后每点一次「开始评分」都当场新生成
    种子（Math.random），洗牌顺序每次都变；确认指纹里掺了顺序的话「再点一次」
    就永远确认不了——合并前的 HEAD 上，3 场名册的续评要点到第 5 次才开评
    （靠随机撞上同一个顺序），名册一长就等于开不了评。指纹只许认「要确认的事」
    本身：名册、已评、缺哪几场、存着几场，全部排序后取集合。
    """
    text = _html_text()
    for gone in ("confirmBatch", "confirmWipe"):
        assert gone not in text, (
            "%s 又出现了：裁决 D 把两道二次确认合并成一道 confirmOnce，"
            "旧旗标留着就是下次「顺手恢复」成四连点的种子" % gone)
    body = _on_start_body()
    assert body.count("if (state.confirmOnce !== fp)") == 1, (
        "「开始评分」应当只有**一道**二次确认门（裁决 D），实际 %d 道"
        % body.count("if (state.confirmOnce !== fp)"))
    for note in ("① 本批没排满：", "② 本机有存档："):
        assert note in body, "确认框里少了 %r——裁决 D 要求两条内容都逐条列出" % note
    # 光钉字面还不够：字还在、条件被改成 if (false) 的话文本守卫看不见那是死分支
    # （变异 M18 正是这么逃过第一版的）。所以「哪一支条件下说哪一句」一并钉住。
    assert re.search(r'if \(missing\.length\) \{\s*const nHave = .*?\s*notes\.push\("① 本批没排满：',
                     body, re.S), (
        "「本批没排满」那一条必须在 `if (missing.length)` 这一支里说出来——"
        "条件被抽掉（if (false)）就等于少说一件事，裁决 D 明令不许")
    assert re.search(r'if \(nSaved\) \{\s*\$\("resumeBox"\)\.hidden = false;\s*'
                     r'notes\.push\("② 本机有存档：', body), (
        "「本机有存档」那一条必须在 `if (nSaved)` 这一支里说出来，并同时把"
        "「继续上次未完成的评分」露出来——条件被抽掉就等于少说一件事（裁决 D）")
    assert 'notes.join("\\n\\n")' in body, (
        "两条内容必须写进**同一个** setupErr（notes.join），分别写两次等于"
        "后一条把前一条冲掉，评分员只看得见最后那件")
    assert "件事一次说清：再点一次「开始评分」= 两件都照办。" in body, (
        "没说清「一次点击 = 两件都照办」，评分员会以为还要再点一次")
    fp = re.search(r"const fp = (\[.*?\]\.join\(\"#\"\));", body, re.S)
    assert fp, "找不到确认指纹的表达式——裁决 D 的「情况变了就得重读」全靠它"
    norm = re.sub(r"\s+", "", fp.group(1))
    assert norm == (
        '[full.map(m=>m.trial_id).sort().join("|"),priorDone.join("|"),'
        'missing.map(m=>m.trial_id).sort().join("|"),nSaved].join("#")'), (
        "确认指纹被改了（实际：%s）——四段必须全是排序后的集合：种子每点一次"
        "都新生成，指纹里掺进洗牌顺序的话「再点一次」永远确认不了" % norm)
    assert 'full.map(m => m.trial_id).join("|")' not in text, (
        "顺序相关的旧指纹又回来了：那正是「续评 + 本批没排满时点多少次都开不了评」"
        "的成因（实测 HEAD 上要点到第 5 次）")


# ------------------------------------------------- DP-131（裁决①／裁决②）


def test_dp131_zero_selected_is_a_stop_not_a_confirmation() -> None:
    """DP-131 裁决①：本次选到 **0 场**视频 ⇒ 一道「不许开始」，不是「再点一次就评 0 场」。

    原来 0 场照样弹确认框，把「本批要评几场」写成 0 还劝人再点一次；评分员点第二次
    才被 beginSession 用**另一句**话（「本次所选视频都已评过或不在剩余清单里」）拦住。
    那不是措辞问题，是缺一道关：先读一段假出路，再被告知走不通。现在 0 场就是不许
    开始，文案按**复核意见 1** 逐字钉死。原来那句说的是一个不可达的状态：真的一个
    文件都没选，会被 §1.2 那道关先接住，这道关打得着的只有「选了视频、但选到的每一
    场都已评过」，而旧文案在说「你没选视频」——评分员照它再选一遍同样的文件，解决
    不了任何事（旧串的字面在这里也不写：写下来就成了下次「顺手恢复」的种子）。
    N ≥ 1 的现有文案是真话，一个字不许动。

    三件事一起钉，少一件这道关就是假的：
    ① 条件 `if (!nHave)` 必须与那句文案、`state.confirmOnce = null`、`return;` 绑在
       一起——换成 `if (false)`、或只写红字不 return（降级成提醒），都等于放行；
    ② 这道关必须在 `if (missing.length)` 那一支里、且在 `notes.push("① 本批没排满：")`
       **之前**——挪到后面就等于先把假出路说完再拦；
    ③ 那句假出路的字面（「本批只评这 0 场」）在整个文件里一处都不许留，含注释：
       注释里的原句会被下一次「顺手恢复」抄回去（与 test_old_false_promise_copy_is_gone
       同一条纪律）；活着的「本批只评这 …场」只能有一处，就是 N ≥ 1 那条路；
    ④ 复核意见 1 的两条边界：新串在整个文件里**恰好 1 处**、旧串连前半截也
       **0 命中**（含注释）；§1.2 那道「一个视频文件都没选」的关
       （`if (!vids.length) err.push("请选择视频文件");`）一个字不许动、也不许跟这道
       合并——那是另一道关，合并就等于把两种不同的现场说成一句话。
       另钉「文案不许另起说法」（**复核意见 2**）：红字引号里那两个标签必须与界面上
       的真值逐字相同——`rescoreBox` 的 legend 以「重评」开头、`rescoreChk` 的 label
       含「本次是重评（rescore）」，改任一侧就红；评分页那颗重做按钮（`redoBtn`）在这
       道关的文案里 **0 命中**，它自己与 `openRedoPanel`／`redoSel` 一个字不许动（复核
       意见 1 那版红字点名的就是它，而它 hidden 在 `<section id="scoring">` 里、这道关
       拦住时压根没渲染，它开的下拉只列本次会话的 state.done，此刻是空的）。
    ⑤ **可见性**（复核意见 2 第 2 条，抓的就是「红字指了一个看不见的控件」这类错）：
       「重评」那一栏必须由 `state.chainDone` 驱动露出、清单必须由 `buildRescorePick`
       按 `state.chainDone` 逐场生成——nHave == 0 蕴含 chainDone 非空，蕴含这一栏在
       屏幕上。只钉标签不钉可见性，抓不到这类错（复核意见 1 那颗钉子正是这么漏的）。

    行为归 node（test_timer_assay.js 里 DP-131 那四条：0 场拦住、0 场 + 存档时关优先、
    nHave ≥ 1 不许被误伤、名册全评完仍直接补出最终文件）；这里钉契约文本。
    """
    text = _html_text()
    body = _on_start_body()
    zero_copy = ("你选的视频对应的场次都已经评过了：本批没有要评的场次。"
                 "请把还没评的那几场的视频选进来；"
                 "确实要重做已评过的场次，就在上面「重评」那一栏勾「本次是重评（rescore）」，"
                 "再在下面的清单里勾中要重做的场次。")
    assert re.search(
        r'if \(!nHave\) \{\s*\$\("setupErr"\)\.textContent = "%s";\s*'
        r'state\.confirmOnce = null; return;\s*\}' % re.escape(zero_copy), body), (
        "DP-131 裁决①那道关不在了：`if (!nHave)` 必须与复核意见 1 给的逐字文案、"
        "清空确认指纹、`return;` 绑在一起（放行/降级成提醒/文案改字都要红）。"
        "实际 onStart 里 missing 那一支：%r"
        % body[body.index("if (missing.length) {"):body.index("if (missing.length) {") + 900])
    assert text.count(zero_copy) == 1, (
        "那道关的文案应当逐字出现**恰好一次**，实际 %d 次" % text.count(zero_copy))
    assert "本次没有选到任何视频" not in text, (
        "旧文案（复核意见 1 判定它说的是不可达状态）又出现了：连前半截都不许留，"
        "含注释——注释里的原句会被下一次「顺手恢复」抄回去")

    i_missing = body.index("if (missing.length) {")
    i_gate = body.index("if (!nHave) {")
    i_note = body.index('notes.push("① 本批没排满：')
    assert i_missing < i_gate < i_note, (
        "那道关的位置不对（missing 支起点 %d、关 %d、①那条 %d）：必须在 "
        "`if (missing.length)` 里面、且在说出「本批没排满」之前——顺序反了就等于"
        "先给假出路再拦" % (i_missing, i_gate, i_note))

    assert "本批只评这 0 场" not in text, (
        "那句假出路又出现了（含注释也算）：裁决①明说「本批只评这 0 场」随之消失——"
        "0 场不许开始，就没有「再点一次就评 0 场」这回事")
    assert body.count('本批只评这 " + nHave + " 场') == 1, (
        "N ≥ 1 的现有文案是真话，一个字不许动：「本批只评这 …场」在 onStart 里"
        "应当恰好一处，实际 %d 处" % body.count('本批只评这 " + nHave + " 场'))
    assert text.count("本批只评这") == 1, (
        "全文件「本批只评这」应当只剩 N ≥ 1 那一处（0 场那处已随裁决①消失），"
        "实际 %d 处" % text.count("本批只评这"))

    # §1.2「一个视频文件都没选」是**另一道关**：文案一个字不许动，也不许跟这道合并
    assert text.count('if (!vids.length) err.push("请选择视频文件");') == 1, (
        "§1.2 那道关被动了：`if (!vids.length) err.push(\"请选择视频文件\");` 应当"
        "逐字恰好一处，实际 %d 处——它管的是「真没选文件」，与这道关管的「选到的都"
        "评过了」是两种现场，合并就等于把两种现场说成一句话"
        % text.count('if (!vids.length) err.push("请选择视频文件");'))

    # 复核意见 2 第 1 条：红字引号里那两个标签，必须与界面上的真值逐字相同（改任一侧就红）
    legend = re.search(r'<fieldset id="rescoreBox"[^>]*>\s*<legend>(.*?)</legend>', text, re.S)
    assert legend, (
        "界面上找不到 rescoreBox 那个 legend 了：红字里引的「重评」成了空指")
    legend_txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", legend.group(1))).strip()
    chk = re.search(r'<input type="checkbox" id="rescoreChk">(.*?)</label>', text, re.S)
    assert chk, (
        "界面上找不到 rescoreChk 那个 label 了：红字里引的那个勾成了空指")
    chk_txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", chk.group(1))).strip()
    assert legend_txt.startswith("重评"), (
        "rescoreBox 的 legend 不以「重评」开头了（界面是 %r）：红字点名的是这一栏，栏改了名"
        "而文案没跟着改，评分员就照着一句假话找不着它" % legend_txt)
    assert "本次是重评（rescore）" in chk_txt, (
        "rescoreChk 的 label 里没有「本次是重评（rescore）」了（界面是 %r）：红字引的就是它，"
        "label 改了字而文案没跟着改就是红" % chk_txt)
    quoted = re.findall(r"「([^」]+)」", zero_copy)
    assert quoted == ["重评", "本次是重评（rescore）"], (
        "红字引号里引的应当恰好是界面上这两个标签（复核意见 2 逐字给的），实际 %r" % (quoted,))
    assert legend_txt.startswith(quoted[0]) and quoted[1] in chk_txt, (
        "红字引的标签与界面上的不一致（照它找不着那个勾）：legend=%r / label=%r"
        % (legend_txt, chk_txt))

    # 复核意见 2 第 3 条：评分页那颗重做按钮，这道关的文案里 0 命中；它自己一个字不许动
    # 取「这道关那一段」（注释 + if 块）。`state.confirmOnce = null; return;` 全文有 7 处，
    # 必须从注释锚点往后找第一个——不往后找的话这段会切成空串（首个 return 在锚点之前），
    # 下面那条断言就成了空转的假绿，所以顺手钉住它不许切空。
    i_dp131 = text.index("/* DP-131（裁决①")
    gate_block = text[i_dp131:text.index("state.confirmOnce = null; return;", i_dp131)]
    assert zero_copy in gate_block and len(gate_block) > 200, (
        "取「这道关那一段」的锚点失效了（切出来 %d 字节）：那会让下面几条断言空转成假绿，"
        "先修锚点再看结论" % len(gate_block))
    assert "重做已完成试次" not in gate_block, (
        "这道关（含它上面那段注释）又点名了评分页那颗重做按钮：那颗按钮 hidden 在 "
        "`<section id=\"scoring\">` 里，这道关拦住时压根没渲染，它开的下拉只列**本次会话**的 "
        "state.done（此刻是空的）——红字指了一个评分员当时看不见的控件，注释里的原句同样"
        "会被下一次「顺手恢复」抄回去")
    for pin in ('<button id="redoBtn" type="button">重做已完成试次…</button>',
                '$("redoBtn").onclick = openRedoPanel;',
                "function openRedoPanel() {",
                '<select id="redoSel">'):
        assert text.count(pin) == 1, (
            "评分页那颗重做按钮相关的这一处被动了（本单不许动它，它是另一条路的入口）："
            "%r 应当恰好一处，实际 %d 处" % (pin, text.count(pin)))

    # 复核意见 2 第 2 条：可见性钉子的源码侧——「重评」那一栏由 chainDone 驱动露出，
    # 且露出的同时按 chainDone 逐场重建清单（nHave == 0 ⇒ chainDone 非空 ⇒ 它在屏幕上）
    assert re.search(r'if \(state\.chainDone\.length\) \{\s*'
                     r'\$\("rescoreBox"\)\.hidden = false;\s*buildRescorePick\(\);\s*\}',
                     text), (
        "「重评」那一栏的可见性不再由 state.chainDone 驱动了：0 场那道关的红字点名了这一栏，"
        "这一栏要是不必然在屏幕上，红字就又在指一个看不见的控件（复核意见 2 抓的就是这类错）")
    assert re.search(r"function buildRescorePick\(\) \{[\s\S]{0,200}?"
                     r"for \(const tid of state\.chainDone\)", text), (
        "重评清单不再按 state.chainDone 逐场生成了：红字说的「再在下面的清单里勾中要重做的"
        "场次」就没有对应的东西——那句话又成了假话")

    # beginSession 那句兜底还在——但它从此只是兜底，不再是 0 场时评分员读到的第一句话
    begin = _body_of("beginSession", "")
    assert "本次所选视频都已评过或不在剩余清单里" in begin, (
        "beginSession 的兜底被删了：0 场那道关是**加**一道，不是把兜底换掉")


def test_dp131_source_level_pins_for_session_shape_gates() -> None:
    """DP-131 裁决②：§1.2 三道关 + §1.4 派题口径，在契约侧补源码级钉子。

    **这道钉子只证明那道关还在源码里，不证明行为；行为归 node**
    （tools/timer/test_timer_assay.js，套件里由 tests/test_timer_tool.py 一条代跑）。

    为什么源码级也要钉：看守链太长。node 那 89 条全靠 test_timer_tool 这一条代理
    测试代跑——代理不可用时（node 不在 PATH、CI 换镜像、超时被当成跳过），那两道关
    就没人看了，而它们正是「谎报第一次」与「不勾重评却把已评场次重新派一遍」的
    **唯一防线**。DP-128 交付前自查的变异 M25／M26 实测过这件事：把 §1.2 的矛盾关
    与入口条件放行，契约侧 **0 红**，只有 node 红。这条钉子就是把那一环补上。
    """
    body = _on_start_body()

    # ---- §1.2：会话形态三道关，全部经 err → 唯一通道，不许各自写红字、不许降级 ----
    assert body.count("if (!priors.length) {") == 2, (
        "§1.2 的入口条件 `if (!priors.length)`（另一处是种子那一支）应当恰好两处，"
        "实际 %d 处——M26 那种换成 if (false) 的放行会改变这个数"
        % body.count("if (!priors.length) {"))
    i_entry = body.index("if (!priors.length) {")
    i_channel = body.index("if (err.length)")
    assert i_entry < i_channel, (
        "§1.2 的入口条件不在 err 通道之前了（入口 %d、通道 %d）：三道关成了死分支"
        % (i_entry, i_channel))
    region = body[i_entry:i_channel]
    for cond, frag in (
        (r'if \(\$\("kindLost"\)\.checked\) \{',
         "你声明了「不是第一次、但找不到之前导出的文件」——不许开始。"),
        (r'else if \(!\$\("kindFirst"\)\.checked\) \{',
         "续评必须选文件，不选就不许开始"),
        (r'else if \(\$\("kindFirst"\)\.checked \|\| \$\("kindLost"\)\.checked\) \{',
         "既选了「已评进度」文件、又勾了首次会话声明——两者矛盾"),
    ):
        m = re.search(cond, region)
        assert m, (
            "§1.2 的条件 %r 不在源码里了（M25／M26 那种放行就是把它换成 if (false)）："
            "这道关是「谎报第一次」的唯一防线，源码里没有就等于没人看守" % cond)
        tail = region[m.end():m.end() + 500]
        assert "err.push(" in tail, (
            "§1.2 的 %r 这一支不再往 err 里推——不推进通道就等于不拦" % cond)
        assert frag in tail, (
            "§1.2 的 %r 这一支的文案缺了 %r：文案是产品行为的一部分，"
            "「不为空」不算验收，「说的是真话」才算" % (cond, frag))
    assert body.count("if (err.length)") == 1, (
        "§1.2 只能有**一条**通道（err 汇总后一次说出），实际 %d 条"
        % body.count("if (err.length)"))
    assert re.search(r'if \(err\.length\) \{ \$\("setupErr"\)\.textContent = '
                     r'err\.join\("\\n"\); return; \}', body), (
        "§1.2 的唯一通道被改了：err 非空 ⇒ 红字（err.join）+ return，"
        "少掉 return 就是把「不许开始」降级成「提醒一句照样开始」")

    # ---- §1.4：重评那一支的入口、三道关，与非重评的派题口径 ----
    assert '} else if ($("rescoreChk").checked) {' in body, (
        "§1.4 的入口条件 `else if ($(\"rescoreChk\").checked)` 不在源码里了："
        "重评那一支整体变成死分支，三道关一起失效")
    # 三道关各把「条件 → 逐字文案 → return」绑在一起钉。只钉「文案后面有 return」
    # 是不够的：条件被换成 if (false) 时文案与 return 都还在原地，那种弱 pin 看不见
    # （本轮设计变异 M34 时发现并当场补强，与裁决 D 那条钉子补条件耦合同一个道理）。
    for cond, frag in (
        (r'if \(!rescoreOf\.length\) \{', "勾了「重评」却没指定任何场次"),
        (r'if \(notDone\.length\) \{', "指定重评的场次不在链条的已评清单里"),
        (r'if \(noVid\.length\) \{', "指定重评的场次本次没选到视频"),
    ):
        m = re.search(cond, body)
        assert m, (
            "§1.4 的条件 %r 不在源码里了：把它换成 if (false) 就是放行——"
            "重评会话会静默变成续评，或把没选到视频的场次当成重评派出去" % cond)
        tail = body[m.end():m.end() + 400]
        assert frag in tail, (
            "§1.4 的 %r 这一支的文案缺了 %r（条件还在、话不说了，等于悄悄改口径）"
            % (cond, frag))
        assert "return;" in tail, (
            "§1.4 的 %r 后面没有 return——停机被降级成提醒（报错停机是产品行为，"
            "绝不放宽）" % cond)
    # 两道关的判据本身也得钉：条件为真靠的是这两个 filter，掏空它们等于放行
    assert "const notDone = rescoreOf.filter(t => !prior.done.has(t));" in body, (
        "§1.4 第二道关的判据被改了：「指定重评的场次不在链条的已评清单里」必须是"
        "拿 rescoreOf 去查 prior.done，掏空成 const notDone = [] 就是放行")
    assert "const noVid = rescoreOf.filter(t => !haveFile.has(t));" in body, (
        "§1.4 第三道关的判据被改了：「本次没选到视频」必须是拿 rescoreOf 去查"
        "本次真选到的视频，掏空就是放行")
    queue = _body_of("rebuildQueue", "")
    assert "state.queue = state.fullOrder.filter(m => m.file && !doneTids.has(m.trial_id));" in queue, (
        "§1.4／裁决 1 的派题口径被改了：非重评会话只派「选到视频又没评过」的场次。"
        "去掉 !doneTids.has(m.trial_id) 就是把评过的场次重新派一遍——那正是 DP-077 "
        "缺陷②实测两位评分员各被重发 4 场的成因。实际 rebuildQueue：%r" % queue)
    assert "state.queue = state.fullOrder.filter(m => m.file && pick.has(m.trial_id));" in queue, (
        "重评会话只派被指定场次这一支被改了（v1.7-④）：%r" % queue)
    assert "const doneTids = doneTidSet();" in queue, (
        "「已评」必须取 doneTidSet()（本次会话 ∪ 链条并集），"
        "只数本次会话就会把之前评过的重新派一遍：%r" % queue)
    assert queue.index("pick.has(m.trial_id)") < queue.index("!doneTids.has(m.trial_id)"), (
        "两支的归属变了：`state.rescore` 那一支（pick）必须在前，"
        "否则重评会话会走非重评的口径、把没勾的场次也派出去")
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
