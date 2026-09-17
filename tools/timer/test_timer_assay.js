/* 秒表工具（v1.5 双范式 + v1.7 DP-128）的离线自测：只测真会出错的事——
 * ① 清单解析（assay 缺列/留空/大小写/非法值/混排/FST- 前缀防撞）；
 * ② 导出 CSV 的列与「没问到就留空」；
 * ③ DP-077 三处静默丢数据缺陷的回归；
 * ④ v1.7（DP-128）：种子只管本次会话、跨会话靠已评清单并集去重 + 三条停机
 *    （§1.1，按裁决 1 改判：多 seed 不停机）、首次会话显式声明（§1.2）、
 *    cumulative_done + 导出前自检（§1.3）、重评只派指定场次（§1.4）+ 逐条
 *    first_scored_in / 顶层 first_scored_unresolved + 导出前自检（裁决 3）、
 *    DP-086 按键膨胀的结构性修复与重看记账（§2 B/C）；
 * ⑤ DP-131 裁决①：本次选到 0 场视频是一道「不许开始」——0 场拦住（含存档在场时
 *    关优先于确认框、存档一个字节不抹）、nHave ≥ 1 不许被误伤、名册全评完仍照旧
 *    直接补出最终文件。契约侧另有源码级钉子（tests/test_timer_tool_contract.py）。
 * 用最小 DOM 桩把单文件工具的 <script> 原样跑起来，不复制一行被测逻辑。
 * 跑法（由 tests/test_timer_tool.py 代跑，也可手动）：
 *   node tools/timer/test_timer_assay.js <timer.html> <FST清单.csv>
 */
"use strict";
const fs = require("fs");
const path = process.argv[2] || __dirname + "/DepressionPlex_stopwatch_timer_v1.html";
const manifestPath = process.argv[3]
  || __dirname + "/../../data/human_scores/manifests/FST全程_视频清单_给评分员.csv";
const html = fs.readFileSync(path, "utf-8");
const js = html.match(/<script>\n([\s\S]*)<\/script>/)[1];

/* ---- 最小 DOM 桩：只提供被测路径真正碰到的东西 ---- */
const nodes = new Map();
function el(id) {
  if (!nodes.has(id)) nodes.set(id, {
    id, textContent: "", value: "", checked: false, hidden: false, innerHTML: "",
    files: [], style: {}, classList: { toggle() {}, add() {}, remove() {} },
    focus() {}, appendChild() {}, onclick: null, play() {}, pause() {},
    click() {}, remove() {}, href: "", download: "",   // 供 download() 用的 <a> 桩
  });
  return nodes.get(id);
}
global.document = {
  getElementById: el,
  addEventListener() {},
  createElement: () => el("_tmp" + Math.random()),
  body: { appendChild() {}, removeChild() {} },
};
global.localStorage = { _m: {}, getItem(k) { return this._m[k] ?? null; },
  setItem(k, v) { this._m[k] = v; }, removeItem(k) { delete this._m[k]; } };
Object.defineProperty(global.localStorage, "length", { get() { return 0; } });
/* node 24 起 navigator 是只读 getter ⇒ 用 defineProperty 覆盖 */
Object.defineProperty(global, "navigator", { value: { clipboard: { writeText() {} } },
                                             configurable: true, writable: true });
global.URL = { createObjectURL: () => "blob:x" };
global.Blob = class {};
global.alert = (m) => { throw new Error("alert: " + m); };

const mod = { exports: {} };
new Function("module", js + "\nmodule.exports = { parseManifest, csvFromDone, state, q1Fields,"
  + " ASSAYS, applyAssay, markDeclaredEmpty, nextTrial, mobileAccumulator, exportSnapshot, readPriorDone, doneTidSet, nDone, remainingTrials, rebuildQueue, onStart,"
  + " loadTrial, onPriorChange, buildRescorePick, chainInfo, claimConflictErrs, buildFirstScoredIn,"
  + " resolveFirstScoredIn, updateChainStat, onManifestChange, newSessionSeed, saveProgress,"
  + " onResume };")(mod);
const T = mod.exports;

let fails = 0;
/* 自测自己汇总的条数。为什么要印它：tests/test_timer_tool.py 拿「印出来的
 * pass 行数」跟这个数对账（自洽），再对下限常量 NODE_SELFTEST_MIN 把关
 * （防整体退化）。只印「全部通过」四个字的话，自测退化成 3 条也照样绿——
 * DP-131 的 M43 已经量过：CI 三个 job 对这类错一律是绿的。 */
let passes = 0;
function ok(name, fn) {
  try { fn(); passes++; console.log("  pass  " + name); }
  catch (e) { fails++; console.log("  FAIL  " + name + " → " + (e.message || e)); }
}
function eq(a, b, msg) {
  const sa = JSON.stringify(a), sb = JSON.stringify(b);
  if (sa !== sb) throw new Error((msg || "") + " 期望 " + sb + " 实得 " + sa);
}
async function okA(name, fn) {
  try { await fn(); passes++; console.log("  pass  " + name); }
  catch (e) { fails++; console.log("  FAIL  " + name + " → " + (e.message || e)); }
}
function throws(fn, frag) {
  let got = null;
  try { fn(); } catch (e) { got = String(e.message || e); }
  if (got === null) throw new Error("本该报错却通过了");
  if (frag && !got.includes(frag)) throw new Error("报错文案不对：" + got);
}

const H2 = "trial_id,video_filename\r\n";
const H3 = "trial_id,video_filename,assay\r\n";

console.log("清单解析：");
ok("旧清单（无 assay 列）默认 TST —— 向后兼容", () => {
  const r = T.parseManifest(H2 + "10mg_2周-ch1,a.mp4\r\n10mg_2周-ch2,b.mp4\r\n");
  eq(r.map(x => x.assay), ["TST", "TST"]);
});
ok("assay 留空 = TST", () => {
  eq(T.parseManifest(H3 + "10mg_2周-ch1,a.mp4,\r\n")[0].assay, "TST");
});
ok("assay 小写/带空格照收", () => {
  eq(T.parseManifest(H3 + "FST-正常1-4-ch2,v.mp4, fst \r\n")[0].assay, "FST");
});
ok("FST 多个杯子共用同一个 video_filename（本来就没切片）", () => {
  const r = T.parseManifest(H3 + "FST-正常1-4-ch1,v.mp4,FST\r\nFST-正常1-4-ch2,v.mp4,FST\r\n");
  eq(r.length, 2); eq(r[0].filename === r[1].filename, true);
});
ok("非法 assay 拒收", () => {
  throws(() => T.parseManifest(H3 + "x-ch1,a.mp4,OFT\r\n"), "assay 只能是 TST 或 FST");
});
ok("混排两个范式拒收（一份清单一个范式）", () => {
  throws(() => T.parseManifest(H3 + "10mg_2周-ch1,a.mp4,TST\r\nFST-正常1-4-ch2,v.mp4,FST\r\n"),
         "一份清单只能一个范式");
});
ok("FST 少了 FST- 前缀拒收（防 `10mg 2周` 与 `10mg_2周` 撞名）", () => {
  throws(() => T.parseManifest(H3 + "10mg 2周-ch1,v.mp4,FST\r\n"), "必须写成 FST-");
});
ok("TST 却带了 FST- 前缀拒收", () => {
  throws(() => T.parseManifest(H3 + "FST-正常1-4-ch2,v.mp4,TST\r\n"), "带 FST- 前缀但 assay 不是 FST");
});
ok("空清单拒收", () => { throws(() => T.parseManifest(H2), "没有任何试次"); });
ok("缺列头照旧拒收", () => {
  throws(() => T.parseManifest("trial_id,assay\r\nx-ch1,TST\r\n"), "清单缺少列头");
});

console.log("导出：");
function exportOnce(assay, checked, win) {
  T.state.scorer = "R1"; T.state.assay = assay; T.applyAssay();
  T.state.done = [Object.assign({ trial_id: "t-ch1", assay, mobile_seconds: 12.34,
                                  window_s: win, unscoreable: false, note: "",
                                  presentation_order: 1, scored_at: "2026-09-07" },
                                T.q1Fields(checked))];
  const csv = T.csvFromDone().replace(/^﻿/, "").trim().split("\r\n");
  return { head: csv[0].split(","), row: csv[1].split(",") };
}
ok("列头是约定的 11 列", () => {
  eq(exportOnce("TST", true, 360).head,
     ["scorer_id", "trial_id", "assay", "mobile_seconds", "window_s",
      "tail_climbing", "wall_support_still", "unscoreable", "note",
      "scored_at", "presentation_order"]);
});
ok("TST 行：tail_climbing 有值，wall_support_still 留空", () => {
  const r = exportOnce("TST", true, 360).row;
  eq([r[2], r[4], r[5], r[6]], ["TST", "360.00", "true", ""]);
});
ok("FST 行：wall_support_still 有值，tail_climbing 留空（不写 false）", () => {
  const r = exportOnce("FST", true, 467.56).row;
  eq([r[2], r[4], r[5], r[6]], ["FST", "467.56", "", "true"]);
});
ok("FST 答「没有」也只落在自己那列", () => {
  const r = exportOnce("FST", false, 362.2).row;
  eq([r[5], r[6]], ["", "false"]);
});
ok("window_s 缺失（老存档续评）留空而不是 0", () => {
  T.state.scorer = "R1"; T.state.assay = "TST";
  T.state.done = [{ trial_id: "t-ch1", mobile_seconds: 1, unscoreable: false, note: "",
                    tail_climbing: false, presentation_order: 1, scored_at: "2026-09-07" }];
  const r = T.csvFromDone().replace(/^﻿/, "").trim().split("\r\n")[1].split(",");
  eq([r[4], r[6]], ["", ""]);
});

console.log("清单声明的空位：");
ok("declared_empty 缺列 / 留空都是 false", () => {
  eq(T.parseManifest(H2 + "x-ch1,a.mp4\r\n")[0].declared_empty, false);
  eq(T.parseManifest("trial_id,video_filename,declared_empty\r\nx-ch1,a.mp4,\r\n")[0].declared_empty,
     false);
});
ok("declared_empty 认 true/1/yes，非法值拒收", () => {
  const H = "trial_id,video_filename,declared_empty\r\n";
  eq(T.parseManifest(H + "x-ch1,a.mp4,TRUE\r\n")[0].declared_empty, true);
  eq(T.parseManifest(H + "x-ch1,a.mp4,1\r\n")[0].declared_empty, true);
  throws(() => T.parseManifest(H + "x-ch1,a.mp4,maybe\r\n"), "declared_empty 只能是");
});
ok("没声明的试次按不了跳过（评分员不能自行跳过）", () => {
  T.state.declaredEmpty = false; T.state.started = false;
  el("quiz").hidden = true; el("q1Row").hidden = false;
  T.markDeclaredEmpty();
  eq(el("quiz").hidden, true, "居然被跳过了：");
});
ok("已开播的试次也按不了跳过", () => {
  T.state.declaredEmpty = true; T.state.started = true;
  el("quiz").hidden = true;
  T.markDeclaredEmpty();
  eq(el("quiz").hidden, true, "开播后仍可跳过：");
});
ok("声明过的空位：跳过后落一行 unscoreable，秒数/窗口/两个问句列全空", () => {
  T.state.assay = "FST"; T.applyAssay();
  T.state.declaredEmpty = true; T.state.started = false;
  T.state.windowS = 467.56;              // 故意先塞一个值，验证跳过会清掉
  el("quiz").hidden = true; el("q1Row").hidden = false;
  el("qUnsc").checked = false; el("qNote").value = "";
  T.markDeclaredEmpty();
  eq([el("quiz").hidden, el("q1Row").hidden, el("qUnsc").checked], [false, true, true]);
  eq(el("qNote").value, "全程无鼠（清单声明）");
  // 走一遍真正的落盘路径
  T.state.scorer = "R1"; T.state.done = []; T.state.acc = T.mobileAccumulator();
  T.state.rate = 1; T.state.redoTrialId = null;
  T.state.queue = [{ trial_id: "FST-抑郁8-10-ch4", pos: 20, declared_empty: true, file: {} }];
  T.state.fullOrder = T.state.queue; T.state.qidx = 0;
  T.nextTrial();
  const r = T.csvFromDone().replace(/^﻿/, "").trim().split("\r\n")[1].split(",");
  //        mobile_seconds  window_s  tail_climbing  wall_support_still  unscoreable
  eq([r[3], r[4], r[5], r[6], r[7]], ["", "", "", "", "true"]);
  eq(T.state.done[0].declared_empty, true);
});

console.log("真实清单：");
ok("data/human_scores/manifests/FST全程_视频清单_给评分员.csv 能被工具解析", () => {
  const rows = T.parseManifest(fs.readFileSync(manifestPath, "utf-8"));
  eq(rows.length, 28, "行数：");
  eq(new Set(rows.map(r => r.assay)).size, 1);
  eq(new Set(rows.map(r => r.filename)).size, 7, "录像数：");
  const empty = rows.filter(r => r.declared_empty).map(r => r.trial_id);
  eq(empty, ["FST-抑郁8-10-ch4"], "声明的空位：");
  // 每段录像 4 个杯位，ch 号连续 1..4，不重不漏
  const byVid = new Map();
  rows.forEach(r => byVid.set(r.filename, (byVid.get(r.filename) || []).concat(
    [Number(r.trial_id.match(/-ch(\d+)$/)[1])])));
  byVid.forEach((chs, v) => eq(chs.sort(), [1, 2, 3, 4], v + " 的杯位："));
});

/* ================= DP-077：三处会静默丢数据的缺陷 ================= */
/* 捕获 download() 落的文件名 */
const dl = [];
document.createElement = () => ({
  href: "", style: {},
  set download(v) { dl.push(v); }, get download() { return ""; },
  click() {}, remove() {}, appendChild() {},
});
const RealDate = Date;
function clockAt(iso) {
  global.Date = class extends RealDate {
    constructor(...a) { return a.length ? new RealDate(...a) : new RealDate(iso); }
    static now() { return new RealDate(iso).getTime(); }
  };
}
function realClock() { global.Date = RealDate; }
function mkDone(tid, pos) {
  return { trial_id: tid, assay: T.state.assay, mobile_seconds: 1.0, window_s: 360,
           unscoreable: false, note: "", tail_climbing: false,
           presentation_order: pos, scored_at: "2026-09-07" };
}
function threeTrials() {
  T.state.scorer = "R1"; T.state.assay = "TST"; T.applyAssay();
  T.state.fullOrder = [{ trial_id: "a-ch1", pos: 1, file: {} },
                       { trial_id: "b-ch1", pos: 2, file: {} },
                       { trial_id: "c-ch1", pos: 3, file: {} }];
  T.state.priorDone = []; T.state.done = []; T.state.qidx = 0;
  /* v1.7 会话形态字段一并复位——state 是跨测试共享的单例，漏一个就会串场 */
  T.state.claimedFirst = false; T.state.rescore = false; T.state.rescoreOf = [];
  T.state.priorFileNames = []; T.state.chainDone = [];
  /* 裁决 1/3 新增的会话状态也一并复位（chainSeed 已随裁决 1 删除：种子不再从
   * 链里恢复）——漏一个，上一条测试的链条就会串到下一条 */
  T.state.chainMaxCount = null; T.state.manifestTids = null;
  T.state.firstScoredIn = null;
  T.state.rewatchS = 0; T.state.redoTrialId = null; T.state.started = false;
  /* 裁决 D：两道确认门合成一道 confirmOnce；它跨测试残留的话，下一条测试第一次
   * 点「开始评分」就会当成已确认直接开评（假绿）。state 是共享单例，一并复位。 */
  T.state.confirmOnce = null;
  el("rescoreChk").checked = false;
  el("kindFirst").checked = false; el("kindLost").checked = false;
  el("seed").readOnly = false;
}
/* 把审计 JSON 包成 onStart 认得的「文件」桩 */
function auditFile(name, doc) {
  return { name, text: async () => JSON.stringify(doc) };
}
function auditDoc(over) {
  return Object.assign({
    format: "depressionplex.stopwatch-audit.v1", assay: "TST", scorer_id: "R1",
    partial: true, done_count: 1, total_trials: 3, records: [{ trial_id: "a-ch1" }],
    /* 真实导出 v1.4 起就带 seed；裁决 1 之后它只作诊断用（顺序不再从链里恢复），
     * 所以链条文件缺 seed 也照样收——见下面「缺 seed 不再拒开」那条 */
    seed: 7,
  }, over || {});
}

console.log("DP-077 缺陷①（导出文件名重名会静默覆盖）：");
ok("partial 文件名带秒级导出时刻，且与 exported_at 一致", () => {
  threeTrials(); T.state.done = [mkDone("a-ch1", 1)];
  clockAt("2026-09-07T05:32:01.004Z");
  dl.length = 0;
  let audit = null;
  const realBlob = global.Blob;
  global.Blob = class { constructor(parts) { audit = parts[0]; } };
  T.exportSnapshot(false);
  global.Blob = realBlob; realClock();
  eq(dl.length, 2, "一次导出应落两个文件：");
  const re = /^human_scores_TST_R1_2026-09-07_partial1of3_053201Z\.csv$/;
  if (!re.test(dl[0])) throw new Error("CSV 名不合式：" + dl[0]);
  if (dl[1] !== "timer_audit_TST_R1_2026-09-07_partial1of3_053201Z.json") {
    throw new Error("审计名不合式：" + dl[1]);
  }
  eq(JSON.parse(audit).exported_at, "2026-09-07T05:32:01.004Z", "exported_at：");
});
ok("同一天两次导出落成不同文件名（原来两次都叫 partial4of27）", () => {
  threeTrials(); T.state.done = [mkDone("a-ch1", 1)];
  dl.length = 0;
  clockAt("2026-09-07T05:32:01.000Z"); T.exportSnapshot(false);
  clockAt("2026-09-07T06:48:01.000Z"); T.exportSnapshot(false);
  realClock();
  eq(dl.length, 4);
  if (dl[0] === dl[2] || dl[1] === dl[3]) throw new Error("仍然重名：" + dl[0]);
});
ok("最终导出带 _final 与时刻，不带 partial", () => {
  threeTrials();
  T.state.done = [mkDone("a-ch1", 1), mkDone("b-ch1", 2), mkDone("c-ch1", 3)];
  dl.length = 0; clockAt("2026-09-07T09:00:00.000Z"); T.exportSnapshot(true); realClock();
  if (!/_final_090000Z\.csv$/.test(dl[0]) || dl[0].includes("partial")) {
    throw new Error("final 名不合式：" + dl[0]);
  }
});
ok("审计 JSON：done_count 仍是本会话条数，累计另立字段", () => {
  threeTrials();
  T.state.priorDone = ["a-ch1", "b-ch1"];
  T.state.done = [mkDone("c-ch1", 3)];
  let audit = null;
  const realBlob = global.Blob;
  global.Blob = class { constructor(parts) { audit = parts[0]; } };
  dl.length = 0; T.exportSnapshot(false);
  global.Blob = realBlob;
  const d = JSON.parse(audit);
  eq([d.done_count, d.cumulative_done_count, d.total_trials], [1, 3, 3]);
  eq(d.prior_done, ["a-ch1", "b-ch1"]);
  eq(d.cumulative_done, ["a-ch1", "b-ch1", "c-ch1"]);   // v1.7-③：与计数说的是同一件事
  eq(d.tool_version, "v1.7");
});

console.log("DP-077 缺陷③（进度只存 localStorage，换机/误点就丢）：");
ok("已评 = 本会话 ∪ 之前导出，剩余按此算", () => {
  threeTrials();
  T.state.priorDone = ["a-ch1"]; T.state.done = [mkDone("b-ch1", 2)];
  eq(T.nDone(), 2); eq(T.remainingTrials(), ["c-ch1"]);
});
ok("rebuildQueue 跳过之前评过的，不会重发", () => {
  threeTrials();
  T.state.priorDone = ["a-ch1", "c-ch1"];
  T.rebuildQueue();
  eq(T.state.queue.map(m => m.trial_id), ["b-ch1"]);
});

/* ============ DP-128 §2-B（DP-086 按键膨胀的结构性修复） ============ */
console.log("DP-086 按键膨胀（在动秒数 = 按住区间的并集，timeupdate 粒度不参与）：");
ok("首看按键：value 恰等于 [按下, 松开]，不多一个 timeupdate 粒度", () => {
  const acc = T.mobileAccumulator();
  acc.startHold(10.25, 10.25);      // frontier=按下点：首看
  acc.endHold(12.75);
  eq(acc.value, 2.5);
  eq(acc.holds, [[10.25, 12.75]]);
  eq(acc.counted, [[10.25, 12.75]]);          // 没重看 ⇒ 两份账逐位相同
  /* 旧版这里是 2.5 + 平均 0.115 s 的膨胀：按下后第一次 timeupdate 会把
   * [上次更新, 按下] 那段也算进去。v1.7 只在 endHold 结算，膨胀无从发生。 */
});
ok("多段按住取并集：重叠不翻倍，审计逐次全记", () => {
  const acc = T.mobileAccumulator();
  acc.startHold(10, 10); acc.endHold(20);     // 首看 [10,20]
  acc.startHold(15, 20); acc.endHold(25);     // 从已看区 15 按住播过前沿到 25
  eq(acc.value, 15);                          // 并集 [10,25]
  eq(acc.holds, [[10, 20], [15, 25]]);
  eq(acc.counted, [[10, 25]]);                // 计入区间已合并
});
ok("重看段的按键一律不计入在动（前沿以下不计数），审计 holds 仍全记", () => {
  const acc = T.mobileAccumulator();
  acc.startHold(30, 30); acc.endHold(40);     // 首看到 40
  acc.startHold(10, 40); acc.endHold(35);     // 重看 10→35：整段在前沿以下
  eq(acc.value, 10);                          // 只有 [30,40]
  eq(acc.holds, [[30, 40], [10, 35]]);        // 反应延迟分析一个字节不丢
  eq(acc.counted, [[30, 40]]);                // 重看那段不在计入区间里
});
ok("跨越前沿的按住：只计前沿之后的部分", () => {
  const acc = T.mobileAccumulator();
  acc.startHold(95, 100); acc.endHold(120);   // 按下时前沿 100
  eq(acc.value, 20);                          // 计入 [100,120]
  eq(acc.holds, [[95, 120]]);
  eq(acc.counted, [[100, 120]]);
});
/* 两个数组各说各的真话：holds 是原始按键（一个字节不丢），holds_counted 是
 * 实际计入秒数的那些。恒等式 union(holds_counted) == mobile_seconds 对每一场都
 * 成立，入库侧不必猜工具算了什么；真值取哪一份是 DP-082/DP-124 的口径。 */
ok("holds_counted 与秒数恒等；重看场次 holds 比 counted 多出的就是不计入的部分", () => {
  const acc = T.mobileAccumulator();
  acc.startHold(2.5, 2.5);  acc.endHold(7.25);   // 首看 [2.5,7.25] ⇒ 计 4.75
  acc.startHold(1, 7.25);   acc.endHold(6);      // 重看 [1,6] 全在前沿下 ⇒ 计 0
  acc.startHold(6.5, 7.25); acc.endHold(9.75);   // 跨前沿 ⇒ 只计 [7.25,9.75]
  eq(acc.holds, [[2.5, 7.25], [1, 6], [6.5, 9.75]]);   // 原始三段全记
  eq(acc.bursts, 3);
  eq(acc.counted, [[2.5, 9.75]]);                        // 相邻 ⇒ 合并成一段
  eq(acc.value, 7.25);                                   // 9.75 − 2.5
  const u = acc.counted.reduce((s, x) => s + (x[1] - x[0]), 0);
  eq(Math.round(u * 100) / 100, acc.value);              // 恒等式本身
});

/* ============ DP-128 §2-C（重看记账） ============ */
console.log("重看记账（rewatch_s）：");
ok("timeupdate：前沿以下的正向播放累积 rewatch_s，前沿推进不累积", () => {
  threeTrials();
  T.state.queue = [{ trial_id: "a-ch1", pos: 1, file: {}, declared_empty: false }];
  T.state.qidx = 0;
  T.loadTrial();                              // 重置 rewatchS 并挂 ontimeupdate
  eq(T.state.rewatchS, 0);
  const v = el("vid");
  v.duration = 360;
  v.currentTime = 10;   v.ontimeupdate();     // 首帧：只立 lastTime/maxT
  v.currentTime = 10.4; v.ontimeupdate();     // 前沿推进：不记重看
  v.currentTime = 10;   v.ontimeupdate();     // ←键回看（负向）：不记
  v.currentTime = 10.2; v.ontimeupdate();     // 已看区内正向：rewatch += 0.2
  eq(Math.round(T.state.rewatchS * 100) / 100, 0.2);
  eq(T.state.maxT, 10.4);
});
ok("nextTrial 把 rewatch_s 落进记录（导出里这一场重看过多少秒有据可查）", () => {
  threeTrials();
  T.state.acc = T.mobileAccumulator();
  T.state.rate = 1; T.state.windowS = 360; T.state.rewatchS = 12.3456;
  T.state.queue = [{ trial_id: "a-ch1", pos: 1, file: {}, declared_empty: false }];
  T.state.qidx = 0;
  el("qUnsc").checked = false; el("qTail").checked = true; el("qNote").value = "";
  el("q1Row").hidden = false;
  T.nextTrial();
  eq(T.state.done[0].rewatch_s, 12.35);
  eq(T.state.done[0].mobile_seconds, 0);      // 没按过键 = 0 秒，重看不计入在动
  eq(T.state.done[0].holds, []);              // 两份区间数组都落进记录
  eq(T.state.done[0].holds_counted, []);
});
ok("记录里 holds 与 holds_counted 分开落盘：重看场次两者不同、且 counted 的并集 == mobile_seconds", () => {
  threeTrials();
  const acc = T.mobileAccumulator();
  acc.startHold(30, 30); acc.endHold(40);     // 首看 [30,40] ⇒ 计 10
  acc.startHold(10, 40); acc.endHold(35);     // 重看 [10,35] ⇒ 计 0
  T.state.acc = acc;
  T.state.rate = 1; T.state.windowS = 360; T.state.rewatchS = 25;
  T.state.queue = [{ trial_id: "a-ch1", pos: 1, file: {}, declared_empty: false }];
  T.state.qidx = 0;
  el("qUnsc").checked = false; el("qTail").checked = false; el("qNote").value = "";
  el("q1Row").hidden = false;
  T.nextTrial();
  const d = T.state.done[0];
  eq(d.holds, [[30, 40], [10, 35]]);          // 原始按键：重看那段也在，一个字节不丢
  eq(d.holds_counted, [[30, 40]]);            // 计入秒数的：重看那段不在
  eq(d.mobile_seconds, 10);
  eq(d.rewatch_s, 25);
  const u = d.holds_counted.reduce((s, x) => s + (x[1] - x[0]), 0);
  eq(Math.round(u * 100) / 100, d.mobile_seconds);
});

/* ============ DP-128 §1.3（cumulative_done + 导出前自检） ============ */
console.log("v1.7 导出形状与自检：");
function captureAudit(fn) {
  let audit = null;
  const realBlob = global.Blob;
  global.Blob = class { constructor(parts) { audit = parts[0]; } };
  try { fn(); } finally { global.Blob = realBlob; }
  return JSON.parse(audit);
}
ok("续评导出：v1.7 新字段全在场且说的是真话", () => {
  threeTrials();
  T.state.priorDone = ["a-ch1"]; T.state.done = [mkDone("b-ch1", 2)];
  T.state.priorFileNames = ["timer_audit_TST_R1_p1.json"];
  const d = captureAudit(() => { dl.length = 0; T.exportSnapshot(false); });
  eq(d.tool_version, "v1.7");
  eq(d.cumulative_done, ["a-ch1", "b-ch1"]);
  eq(d.cumulative_done_count, 2);
  eq(d.claimed_first_session, false);         // 续评会话不是第一次
  eq(d.rescore, false); eq(d.rescore_of, []);
  eq(d.prior_files, ["timer_audit_TST_R1_p1.json"]);
});
ok("首次会话导出：claimed_first_session=true 落在文件里（屏幕上的确认不算确认）", () => {
  threeTrials();
  T.state.claimedFirst = true; T.state.done = [mkDone("a-ch1", 1)];
  const d = captureAudit(() => { dl.length = 0; T.exportSnapshot(false); });
  eq(d.claimed_first_session, true);
  eq(d.cumulative_done, ["a-ch1"]);
  eq(d.prior_files, []); eq(d.rescore, false); eq(d.rescore_of, []);
});
ok("重评导出：rescore / rescore_of / prior_files（本次选中的历史导出）如实记账", () => {
  threeTrials();
  T.state.rescore = true; T.state.rescoreOf = ["a-ch1"];
  T.state.priorDone = ["a-ch1", "b-ch1"];
  T.state.priorFileNames = ["timer_audit_TST_R1_p1.json", "timer_audit_TST_R1_p2.json"];
  T.state.done = [mkDone("a-ch1", 1)];
  const d = captureAudit(() => { dl.length = 0; T.exportSnapshot(false); });
  eq(d.rescore, true); eq(d.rescore_of, ["a-ch1"]);
  eq(d.prior_files, ["timer_audit_TST_R1_p1.json", "timer_audit_TST_R1_p2.json"]);
  eq(d.claimed_first_session, false);
});
ok("自检：cumulative_done 与 cumulative_done_count 打架 ⇒ 拒绝导出，一个字节不落", () => {
  threeTrials();
  T.state.priorDone = ["a-ch1", "z-ch9"];     // z-ch9 不在清单：集合 3 条 vs nDone 2
  T.state.done = [mkDone("b-ch1", 2)];
  dl.length = 0;
  throws(() => T.exportSnapshot(false), "打架");   // node 桩里 alert 抛错；浏览器里弹窗后 return
  eq(dl.length, 0);
});

/* ============ DP-128 §1.4（重评只派指定场次） ============ */
console.log("重评派单（rescore）：");
ok("rebuildQueue：rescore=true 只派指定场次，评过没指定的也不派", () => {
  threeTrials();
  T.state.fullOrder[0].file = {}; T.state.fullOrder[1].file = {}; T.state.fullOrder[2].file = {};
  T.state.priorDone = ["a-ch1", "b-ch1"];
  T.state.rescore = true; T.state.rescoreOf = ["a-ch1"];
  T.rebuildQueue();
  eq(T.state.queue.map(m => m.trial_id), ["a-ch1"]);
});
ok("rebuildQueue：不勾重评 ⇒ 已评场次一场都不派", () => {
  threeTrials();
  T.state.fullOrder[0].file = {}; T.state.fullOrder[1].file = {}; T.state.fullOrder[2].file = {};
  T.state.priorDone = ["a-ch1", "b-ch1"];
  T.state.rescore = false;
  T.rebuildQueue();
  eq(T.state.queue.map(m => m.trial_id), ["c-ch1"]);
});

const TIDS = new Set(["a-ch1", "b-ch1", "c-ch1"]);
async function main() {
  await okA("导入进度：评分员对不上 ⇒ 拒收并报出（不能把别人的进度并进来）", async () => {
    const r = await T.readPriorDone([auditFile("x.json", auditDoc({ scorer_id: "R2" }))],
                                    "R1", "TST", TIDS);
    eq(r.done.size, 0);
    if (!r.errs.join("").includes("R2")) throw new Error("没报出评分员不符：" + r.errs);
  });
  await okA("导入进度：范式对不上 ⇒ 拒收", async () => {
    const r = await T.readPriorDone([auditFile("x.json", auditDoc({ assay: "FST" }))],
                                    "R1", "TST", TIDS);
    eq(r.done.size, 0);
    if (!r.errs.join("").includes("FST")) throw new Error("没报出范式不符：" + r.errs);
  });
  await okA("导入进度：记录里的试次不在本清单 ⇒ 拒收", async () => {
    const r = await T.readPriorDone(
      [auditFile("x.json", auditDoc({ records: [{ trial_id: "z-ch9" }] }))], "R1", "TST", TIDS);
    eq(r.done.size, 0);
    if (!r.errs.join("").includes("z-ch9")) throw new Error("没报出越界试次：" + r.errs);
  });
  await okA("导入进度：不是本工具的文件 / 不是 JSON ⇒ 拒收", async () => {
    const bad = { name: "y.json", text: async () => "{不是json" };
    const r = await T.readPriorDone([bad, auditFile("z.json", auditDoc({ format: "别的" }))],
                                    "R1", "TST", TIDS);
    eq(r.done.size, 0); eq(r.errs.length, 2);
  });
  await okA("导入进度：多份取并集，并顺着 prior_done 链条接上", async () => {
    const r = await T.readPriorDone([
      auditFile("1.json", auditDoc({ records: [{ trial_id: "a-ch1" }] })),
      auditFile("2.json", auditDoc({ records: [{ trial_id: "b-ch1" }],
                                     prior_done: ["a-ch1"] })),
    ], "R1", "TST", TIDS);
    eq(r.errs, []);
    eq(Array.from(r.done).sort(), ["a-ch1", "b-ch1"]);
  });
  await okA("导入进度：cumulative_done 与计数打架 ⇒ 拒收（不许挑一个信）", async () => {
    const r = await T.readPriorDone([auditFile("x.json", auditDoc({
      cumulative_done: ["a-ch1"], cumulative_done_count: 2 }))], "R1", "TST", TIDS);
    eq(r.done.size, 0);
    if (!r.errs.join("").includes("打架")) throw new Error("没报出字段打架：" + r.errs);
  });
  await okA("导入进度：cumulative_done 让链断一环也接得上（只选最新一份就够）", async () => {
    const r = await T.readPriorDone([auditFile("latest.json", auditDoc({
      records: [{ trial_id: "c-ch1" }], prior_done: ["b-ch1"],
      cumulative_done: ["a-ch1", "b-ch1", "c-ch1"], cumulative_done_count: 3 }))],
      "R1", "TST", TIDS);
    eq(r.errs, []);
    eq(Array.from(r.done).sort(), ["a-ch1", "b-ch1", "c-ch1"]);
    eq(Array.from(r.seeds), [7]);
    eq(r.names, ["latest.json"]);
  });

  /* ---- v1.7（DP-128）：会话形态的三套 onStart 关卡 ---- */
  const MAN3 = "trial_id,video_filename\r\na-ch1,a.mp4\r\nb-ch1,b.mp4\r\nc-ch1,c.mp4\r\n";
  const tick = () => new Promise(r => setTimeout(r, 0));
  function setupStart(opts) {
    threeTrials();                       // 复位共享 state（含 v1.7 会话形态字段）
    localStorage.removeItem("dpst:v2:R1");
    el("scorerId").value = "R1";
    el("seed").value = opts.seedValue === undefined ? "7" : opts.seedValue;
    el("manifestFile").files = [{ name: "m.csv", text: async () => MAN3 }];
    el("videoFiles").files = (opts.videos || ["a.mp4", "b.mp4", "c.mp4"]).map(n => ({ name: n }));
    el("priorFiles").files = opts.priors || [];
    el("kindFirst").checked = !!opts.kindFirst;
    el("kindLost").checked = !!opts.kindLost;
    el("rescoreChk").checked = !!opts.rescore;
    T.state.confirmOnce = null;
    /* 新纪律（M11 假绿那一课）：DOM 桩跨测试复用，先把红字现场写成非默认值。
     * 否则下面那些 eq(setupErr, "") 分不清「这次开评没报错」与「本来就是空的」，
     * 删掉 beginSession 里清空红字那一行的变异就会假绿。 */
    el("setupErr").textContent = "【桩】上一条测试留下的红字";
  }

  console.log("DP-128 §1.1（裁决 1：种子只管会话内，跨会话靠已评清单并集去重）：");
  await okA("选进「已评进度」⇒ 声明框收起、重评列表出现，种子字段清空只读（不再从链里恢复）", async () => {
    /* 先把字段填上一个显眼的数：DOM 桩跨测试复用，不填的话「清空了」与「本来就是
     * 空的」分不开，M11 变异（删掉清空那一行）就会假绿。 */
    el("seed").value = "999"; el("seed").readOnly = false;
    el("firstBox").hidden = false;                 // 该收起的，先摆成「露着」
    el("rescoreBox").hidden = true;                // 该露出的，先摆成「收着」
    el("rescorePick").hidden = true; el("rescorePick").innerHTML = "【桩】";
    el("setupErr").textContent = "【桩】上一条测试留下的红字";
    el("priorFiles").files = [auditFile("p1.json", auditDoc({ seed: 4242,
      records: [{ trial_id: "a-ch1" }], cumulative_done: ["a-ch1"], cumulative_done_count: 1 }))];
    await T.onPriorChange();
    eq(el("seed").value, "");           // 屏幕上不许挂一个其实不会被用到的数字
    eq(el("seed").readOnly, true);
    eq(el("firstBox").hidden, true);
    eq(el("rescoreBox").hidden, false); eq(el("rescorePick").hidden, false);
    if (el("rescorePick").innerHTML === "【桩】") {
      throw new Error("重评勾选列表没重建（还是桩留下的内容）");
    }
    eq(T.state.chainDone, ["a-ch1"]); eq(T.state.chainMaxCount, 1);
    eq(el("setupErr").textContent, "");
  });
  await okA("链里两个种子 ⇒ 不再停机：照常解析、照常并集去重，一句红字都没有", async () => {
    el("priorFiles").files = [auditFile("p1.json", auditDoc({ seed: 1, records: [{ trial_id: "a-ch1" }] })),
                              auditFile("p2.json", auditDoc({ seed: 2, records: [{ trial_id: "b-ch1" }] }))];
    el("setupErr").textContent = "【桩】上一条测试留下的红字";
    el("firstBox").hidden = false; el("seed").readOnly = false;
    await T.onPriorChange();
    eq(el("setupErr").textContent, "");
    eq(T.state.chainDone, ["a-ch1", "b-ch1"]);
    eq(el("firstBox").hidden, true); eq(el("seed").readOnly, true);
  });
  await okA("清空「已评进度」⇒ 首次声明框回来，种子字段重新填好并可改", async () => {
    el("priorFiles").files = [];
    /* 先把「该恢复的」摆成收起、「该收起的」摆成露出：这一条测的全是清理与复位，
     * 现场不弄脏就验不到任何一件事 */
    el("firstBox").hidden = true; el("seed").readOnly = true; el("seed").value = "";
    el("rescoreBox").hidden = false;
    el("chainStat").hidden = false; el("chainStat").textContent = "【桩】上一条测试留下的进度行";
    await T.onPriorChange();
    eq(el("firstBox").hidden, false); eq(el("seed").readOnly, false);
    if (!/^\d+$/.test(el("seed").value)) {
      throw new Error("清空后没重新填一个可用种子（点开始只会得到「种子必须是非负整数」）："
        + el("seed").value);
    }
    eq(el("rescoreBox").hidden, true);
    eq(el("chainStat").hidden, true); eq(el("chainStat").textContent, "");
  });
  await okA("chainStat：把选中份数、名册、cumulative_done_count 摆在一起显示「已恢复 N 场、剩余 M 场」", async () => {
    el("manifestFile").files = [{ name: "m.csv", text: async () => MAN3 }];
    await T.onManifestChange();
    el("priorFiles").files = [auditFile("p1.json", auditDoc({ seed: 1,
      records: [{ trial_id: "a-ch1" }], cumulative_done: ["a-ch1"], cumulative_done_count: 1 }))];
    el("chainStat").hidden = true; el("chainStat").textContent = "";
    await T.onPriorChange();
    const s = el("chainStat").textContent;
    if (!s.includes("已选 1 份导出")) throw new Error("没报选中份数：" + s);
    if (!s.includes("清单共 3 场")) throw new Error("没对名册：" + s);
    if (!s.includes("已恢复 1 场、剩余 2 场")) throw new Error("已恢复/剩余不对：" + s);
    if (!s.includes("cumulative_done_count 最大的是 1")) throw new Error("没对照 cumulative_done_count：" + s);
    if (!s.includes("全部选上，不是只选最新一份")) throw new Error("没明说必须全选历史导出：" + s);
    eq(el("chainStat").hidden, false);
  });
  await okA("chainStat：还没选清单 ⇒ 只报已恢复几场，并明说剩余要等清单", async () => {
    el("manifestFile").files = [];
    await T.onManifestChange();
    el("priorFiles").files = [auditFile("p1.json", auditDoc({ seed: 1,
      records: [{ trial_id: "a-ch1" }] }))];
    await T.onPriorChange();
    const s = el("chainStat").textContent;
    if (!s.includes("已恢复 1 场")) throw new Error("没报已恢复：" + s);
    if (!s.includes("还没选清单 CSV")) throw new Error("没说清剩余为什么算不出来：" + s);
  });
  await okA("续评 onStart：种子当场新生成（不是链里那个），剩余 = 名册 − 已评并集，导出写的就是新种子", async () => {
    setupStart({ seedValue: "",
      priors: [auditFile("p.json", auditDoc({ seed: 4242, records: [{ trial_id: "a-ch1" }] }))] });
    T.onStart(); await tick();
    eq(el("setupErr").textContent, "");
    if (T.state.seed === 4242) throw new Error("续评仍沿用链里的种子——裁决 1 要的是当场新生成");
    if (!Number.isInteger(T.state.seed) || T.state.seed < 0 || T.state.seed >= 1e9) {
      throw new Error("新种子越界：" + T.state.seed);
    }
    eq(el("seed").value, String(T.state.seed));   // 屏幕上显示的就是这次真用的那个
    eq(el("seed").readOnly, true);
    eq(T.state.claimedFirst, false); eq(T.state.rescore, false);
    eq(T.state.priorFileNames, ["p.json"]);
    eq(T.state.queue.map(m => m.trial_id).sort(), ["b-ch1", "c-ch1"]);
    const d = captureAudit(() => { dl.length = 0; T.exportSnapshot(false); });
    eq(d.seed, T.state.seed);
  });
  await okA("续评 onStart：链里两个种子 ⇒ 照常开评，两份的已评并集都算进去", async () => {
    setupStart({ seedValue: "",
      priors: [auditFile("p1.json", auditDoc({ seed: 7, records: [{ trial_id: "a-ch1" }] })),
               auditFile("p2.json", auditDoc({ seed: 99, records: [{ trial_id: "b-ch1" }] }))] });
    T.onStart(); await tick();
    eq(el("setupErr").textContent, "");
    eq(T.state.priorDone, ["a-ch1", "b-ch1"]);
    eq(T.state.queue.map(m => m.trial_id), ["c-ch1"]);
    if (T.state.seed === 7 || T.state.seed === 99) {
      throw new Error("沿用了链里的种子：" + T.state.seed);
    }
  });
  await okA("链条文件缺 seed ⇒ 不再拒开（种子只管本次会话），records 与 cumulative_done 照收", async () => {
    const doc = auditDoc({ records: [{ trial_id: "a-ch1" }],
                           cumulative_done: ["a-ch1", "b-ch1"], cumulative_done_count: 2 });
    delete doc.seed;
    setupStart({ seedValue: "", priors: [auditFile("p.json", doc)] });
    T.onStart(); await tick();
    eq(el("setupErr").textContent, "");
    eq(T.state.priorDone, ["a-ch1", "b-ch1"]);
    eq(T.state.queue.map(m => m.trial_id), ["c-ch1"]);
  });

  console.log("DP-128 裁决 1 的三条停机（一条都不许降级成警告）：");
  await okA("停机①：并集里出现名册外的 trial_id ⇒ 拒开；records / prior_done / cumulative_done 三个来源都查", async () => {
    const cases = [
      ["records", { records: [{ trial_id: "z-ch9" }] }],
      ["prior_done", { records: [], prior_done: ["z-ch9"] }],
      ["cumulative_done", { records: [], cumulative_done: ["z-ch9"], cumulative_done_count: 1 }],
    ];
    for (const [key, over] of cases) {
      const r = await T.readPriorDone([auditFile("x.json", auditDoc(over))], "R1", "TST", TIDS);
      if (!r.errs.join("").includes("z-ch9")) {
        throw new Error(key + " 里的越界试次没报出来：" + r.errs);
      }
      eq(r.done.size, 0, key + "：越界试次还留在 done 里");
      /* 同一件事在 onStart 上也得停下来，不能只在函数返回值里红 */
      setupStart({ seedValue: "", priors: [auditFile("x.json", auditDoc(over))] });
      T.onStart(); await tick();
      if (!el("setupErr").textContent.includes("z-ch9")) {
        throw new Error(key + " 越界但 onStart 没停机：" + el("setupErr").textContent);
      }
      eq(T.state.priorFileNames, [], key + "：停机了却还是开了评");
    }
  });
  await okA("停机③：同一评分员同一范式两个会话都声称 claimed_first_session ⇒ 拒开，不开评", async () => {
    setupStart({ seedValue: "",
      priors: [auditFile("p1.json", auditDoc({ seed: 1, claimed_first_session: true,
                                               records: [{ trial_id: "a-ch1" }] })),
               auditFile("p2.json", auditDoc({ seed: 2, claimed_first_session: true,
                                               records: [{ trial_id: "b-ch1" }] }))] });
    T.onStart(); await tick();
    if (!el("setupErr").textContent.includes("都声称是第一次会话")) {
      throw new Error("没有按裁决 1 停机③：" + el("setupErr").textContent);
    }
    if (!el("setupErr").textContent.includes("不许开始")) {
      throw new Error("停机文案没说清后果：" + el("setupErr").textContent);
    }
    eq(T.state.priorFileNames, []);       // 没走到赋值 = 没开评
  });
  await okA("停机③不误伤：同一次会话分批落下的 partial + final（同一个种子）不算两份声明", async () => {
    setupStart({ seedValue: "",
      priors: [auditFile("p_partial.json", auditDoc({ seed: 5, claimed_first_session: true,
                                                      records: [{ trial_id: "a-ch1" }] })),
               auditFile("p_final.json", auditDoc({ seed: 5, claimed_first_session: true,
                                                    records: [{ trial_id: "b-ch1" }] }))] });
    T.onStart(); await tick();
    eq(el("setupErr").textContent, "");
    eq(T.state.priorDone, ["a-ch1", "b-ch1"]);
  });
  await okA("停机③不误伤：别的评分员声称第一次与本链无关（先被评分员那道关挡掉）", async () => {
    const r = await T.readPriorDone([
      auditFile("p1.json", auditDoc({ seed: 1, scorer_id: "R1", claimed_first_session: true })),
      auditFile("p2.json", auditDoc({ seed: 2, scorer_id: "R2", claimed_first_session: true })),
    ], "R1", "TST", TIDS);
    if (!r.errs.join("").includes("R2")) throw new Error("别人的进度没被挡：" + r.errs);
    if (r.errs.join("").includes("都声称是第一次会话")) {
      throw new Error("把别的评分员的第一次声明算到本链头上：" + r.errs);
    }
  });
  ok("claimConflictErrs：同种子=同一会话只算一次；两个种子才算两个；范式不同各是各的链", () => {
    eq(T.claimConflictErrs([{ name: "a", scorer: "R1", assay: "TST", seed: "5" },
                            { name: "b", scorer: "R1", assay: "TST", seed: "5" }]), []);
    const e = T.claimConflictErrs([{ name: "a", scorer: "R1", assay: "TST", seed: "5" },
                                   { name: "b", scorer: "R1", assay: "TST", seed: "6" }]);
    eq(e.length, 1);
    if (!e[0].includes("R1") || !e[0].includes("TST")) throw new Error("没报出是谁哪个范式：" + e[0]);
    if (!e[0].includes("a") || !e[0].includes("b")) throw new Error("没报出是哪两份：" + e[0]);
    eq(T.claimConflictErrs([{ name: "a", scorer: "R1", assay: "TST", seed: "5" },
                            { name: "b", scorer: "R1", assay: "FST", seed: "6" }]), []);
    /* 评分员编号里带空格也不许撞车（不用字符串拼 key 的原因） */
    eq(T.claimConflictErrs([{ name: "a", scorer: "张 咸明", assay: "TST", seed: "5" },
                            { name: "b", scorer: "张", assay: "咸明 TST", seed: "5" }]), []);
  });

  console.log("DP-128 §1.2（首次会话必须显式声明，声明落进导出）：");
  await okA("没选文件又没声明 ⇒ 不许开始", async () => {
    setupStart({});
    T.onStart();
    if (!el("setupErr").textContent.includes("首次会话声明")) {
      throw new Error("没拦：" + el("setupErr").textContent);
    }
  });
  await okA("声明「续评但找不到文件」⇒ 不许开始", async () => {
    setupStart({ kindLost: true });
    T.onStart();
    if (!el("setupErr").textContent.includes("不许开始")) {
      throw new Error("没拦：" + el("setupErr").textContent);
    }
  });
  await okA("既选文件又声明第一次 ⇒ 矛盾，不许开始", async () => {
    setupStart({ kindFirst: true, priors: [auditFile("p.json", auditDoc())] });
    T.onStart();
    if (!el("setupErr").textContent.includes("矛盾")) {
      throw new Error("没拦：" + el("setupErr").textContent);
    }
  });
  await okA("勾了首次声明 ⇒ 开始，导出里 claimed_first_session=true", async () => {
    setupStart({ kindFirst: true });
    T.onStart(); await tick();
    eq(el("setupErr").textContent, "");
    eq(T.state.claimedFirst, true); eq(T.state.seed, 7);
    const d = captureAudit(() => { dl.length = 0; T.exportSnapshot(false); });
    eq(d.claimed_first_session, true);
    eq(d.seed, 7);
  });

  console.log("DP-128 §1.4（重评：只派指定场次，新种子自盲重派）：");
  await okA("勾重评并指定场次 ⇒ 只派指定的，新种子不沿用旧链", async () => {
    setupStart({ seedValue: "", rescore: true,
      priors: [auditFile("p1.json", auditDoc({ seed: 7,
        records: [{ trial_id: "a-ch1" }, { trial_id: "b-ch1" }] }))] });
    T.state.rescoreOf = ["a-ch1"];
    T.onStart(); await tick();
    eq(el("setupErr").textContent, "");
    eq(T.state.rescore, true);
    eq(T.state.queue.map(m => m.trial_id), ["a-ch1"]);
    eq(T.state.priorDone, ["a-ch1", "b-ch1"]);
    if (T.state.seed === 7) throw new Error("重评会话仍沿用旧链种子——自盲重派要用新种子");
    if (!Number.isInteger(T.state.seed) || T.state.seed < 0 || T.state.seed >= 1e9) {
      throw new Error("新种子越界：" + T.state.seed);
    }
  });
  await okA("勾了重评却没指定场次 ⇒ 不许开始", async () => {
    setupStart({ seedValue: "", rescore: true,
      priors: [auditFile("p1.json", auditDoc({ seed: 7, records: [{ trial_id: "a-ch1" }] }))] });
    T.state.rescoreOf = [];
    T.onStart(); await tick();
    if (!el("setupErr").textContent.includes("没指定任何场次")) {
      throw new Error("没拦：" + el("setupErr").textContent);
    }
  });
  await okA("指定了没评过的场次去重评 ⇒ 不许开始", async () => {
    setupStart({ seedValue: "", rescore: true,
      priors: [auditFile("p1.json", auditDoc({ seed: 7, records: [{ trial_id: "a-ch1" }] }))] });
    T.state.rescoreOf = ["c-ch1"];
    T.onStart(); await tick();
    if (!el("setupErr").textContent.includes("不在链条的已评清单里")) {
      throw new Error("没拦：" + el("setupErr").textContent);
    }
  });
  await okA("重评场次本次没选到视频 ⇒ 不许开始", async () => {
    setupStart({ seedValue: "", rescore: true, videos: ["b.mp4"],
      priors: [auditFile("p1.json", auditDoc({ seed: 7,
        records: [{ trial_id: "a-ch1" }, { trial_id: "b-ch1" }] }))] });
    T.state.rescoreOf = ["a-ch1"];
    T.onStart(); await tick();
    if (!el("setupErr").textContent.includes("没选到视频")) {
      throw new Error("没拦：" + el("setupErr").textContent);
    }
  });

  console.log("DP-128 裁决 3（逐条 first_scored_in + 顶层 first_scored_unresolved）：");
  await okA("重评导出：每条记录指得回首评所在那份文件名，unresolved 为空；prior_files 原名不动", async () => {
    setupStart({ seedValue: "", rescore: true,
      priors: [auditFile("first_A.json", auditDoc({ seed: 1, records: [{ trial_id: "a-ch1" }] })),
               auditFile("first_B.json", auditDoc({ seed: 2, records: [{ trial_id: "b-ch1" }],
                 cumulative_done: ["a-ch1", "b-ch1"], cumulative_done_count: 2 }))] });
    T.state.rescoreOf = ["a-ch1", "b-ch1"];
    T.onStart(); await tick();
    eq(el("setupErr").textContent, "");
    eq(T.state.firstScoredIn.get("a-ch1"), "first_A.json");
    eq(T.state.firstScoredIn.get("b-ch1"), "first_B.json");
    T.state.done = [mkDone("a-ch1", 1), mkDone("b-ch1", 2)];
    const d = captureAudit(() => { dl.length = 0; T.exportSnapshot(false); });
    eq(d.records.map(r => r.first_scored_in), ["first_A.json", "first_B.json"]);
    eq(d.first_scored_unresolved, []);
    eq(d.prior_files, ["first_A.json", "first_B.json"]);   // 会话级，键名与语义都保留
    eq(d.rescore, true); eq(d.rescore_of, ["a-ch1", "b-ch1"]);
    eq(d.records.length, 2, "记录条数：");
  });
  await okA("首评定不了（两份文件的 records 都有这一场）⇒ 如实写 null 并列进 unresolved，照样导出、不拦人", async () => {
    setupStart({ seedValue: "", rescore: true,
      priors: [auditFile("p1.json", auditDoc({ seed: 1, records: [{ trial_id: "a-ch1" }] })),
               auditFile("p2.json", auditDoc({ seed: 2, records: [{ trial_id: "a-ch1" }] }))] });
    T.state.rescoreOf = ["a-ch1"];
    T.onStart(); await tick();
    eq(el("setupErr").textContent, "");       // 定不了首评不是拒开的理由
    eq(T.state.firstScoredIn.get("a-ch1"), null);
    T.state.done = [mkDone("a-ch1", 1)];
    const d = captureAudit(() => { dl.length = 0; T.exportSnapshot(false); });
    eq(d.records.map(r => r.first_scored_in), [null]);
    eq(d.first_scored_unresolved, ["a-ch1"]);
    eq(dl.length, 2, "定不了首评就不导出了——裁决 3 要求不拦人：");
  });
  await okA("首评那份没选上（这一场只在 cumulative_done 里露面）⇒ null + unresolved，不拦人", async () => {
    setupStart({ seedValue: "", rescore: true,
      priors: [auditFile("latest.json", auditDoc({ seed: 3, records: [{ trial_id: "b-ch1" }],
        cumulative_done: ["a-ch1", "b-ch1"], cumulative_done_count: 2 }))] });
    T.state.rescoreOf = ["a-ch1"];
    T.onStart(); await tick();
    eq(el("setupErr").textContent, "");
    T.state.done = [mkDone("a-ch1", 1)];
    const d = captureAudit(() => { dl.length = 0; T.exportSnapshot(false); });
    eq(d.records.map(r => r.first_scored_in), [null]);
    eq(d.first_scored_unresolved, ["a-ch1"]);
    eq(d.prior_files, ["latest.json"], "会话级 prior_files 不受影响：");
  });
  await okA("非重评会话：first_scored_in = 本导出自己的文件名（这一条的首评就是这一份）", async () => {
    setupStart({ kindFirst: true });
    T.onStart(); await tick();
    eq(el("setupErr").textContent, "");
    T.state.done = [mkDone("a-ch1", T.state.fullOrder.find(m => m.trial_id === "a-ch1").pos)];
    dl.length = 0; clockAt("2026-09-08T02:03:04.500Z");
    const d = captureAudit(() => { T.exportSnapshot(false); });
    realClock();
    eq(dl.length, 2, "一次导出两个文件：");
    eq(d.records.map(r => r.first_scored_in), [dl[1]]);
    eq(d.first_scored_unresolved, []);
    if (!dl[1].startsWith("timer_audit_TST_R1_")) throw new Error("审计名不合式：" + dl[1]);
  });
  ok("resolveFirstScoredIn：没有链条信息（旧存档）⇒ null，不猜一个文件名", () => {
    threeTrials();
    T.state.firstScoredIn = null;
    eq(T.resolveFirstScoredIn("a-ch1", "own.json"), null);
    T.state.firstScoredIn = new Map();
    eq(T.resolveFirstScoredIn("a-ch1", "own.json"), "own.json");
    T.state.firstScoredIn = new Map([["a-ch1", "p1.json"], ["b-ch1", null]]);
    eq(T.resolveFirstScoredIn("a-ch1", "own.json"), "p1.json");
    eq(T.resolveFirstScoredIn("b-ch1", "own.json"), null);
    eq(T.resolveFirstScoredIn("c-ch1", "own.json"), "own.json");
  });
  ok("buildFirstScoredIn：首评候选恰好一份才写它；零份或两份以上一律 null（裁决 C）", () => {
    const m = T.buildFirstScoredIn({
      done: new Set(["a-ch1", "b-ch1", "c-ch1", "d-ch1"]),
      firstCand: new Map([["a-ch1", ["f1.json"]], ["b-ch1", ["f1.json", "f2.json"]],
                          ["d-ch1", ["f1.json", "f2.json", "f3.json"]]]),
    });
    eq(m.get("a-ch1"), "f1.json");   // 恰好一份候选 ⇒ 写它
    eq(m.get("b-ch1"), null);        // 两份候选：说不清哪份是首评，不许挑一份
    eq(m.get("c-ch1"), null);        // 零份候选：只在 prior_done / cumulative_done 里露面
    eq(m.get("d-ch1"), null);        // 三份候选：同样不许挑
    eq(m.size, 4, "链条认得的每一场都要有个交代：");
  });
  ok("chainInfo：把这一场列进自己 rescore_of 的那一份不算首评候选（裁决 C 排除规则）", () => {
    /* f1 是首评那一份；f2 是重评会话（rescore_of 列了 a-ch1），两份的 records 都有
     * a-ch1。f2 被排除 ⇒ 候选恰好剩 f1 ⇒ first_scored_in 指得回真正的首评。 */
    const f2 = { name: "f2.json", doc: { seed: 2, rescore: true, rescore_of: ["a-ch1"],
                                         records: [{ trial_id: "a-ch1" }],
                                         prior_done: ["a-ch1"],
                                         cumulative_done: ["a-ch1"],
                                         cumulative_done_count: 1 } };
    const info = T.chainInfo([
      { name: "f1.json", doc: { seed: 1, records: [{ trial_id: "a-ch1" }],
                                cumulative_done: ["a-ch1"], cumulative_done_count: 1 } },
      f2,
    ]);
    eq(info.errs, []);
    eq(info.firstCand.get("a-ch1"), ["f1.json"], "重评那一份必须被排除：");
    eq(T.buildFirstScoredIn(info).get("a-ch1"), "f1.json");
    /* 反过来（裁决 C 要防的那个「说错」）：漏选首评那份、只剩重评那一份 ⇒ 候选为零
     * ⇒ null，不许自信地写上重评那一份的名字 */
    const only = T.chainInfo([f2]);
    eq(only.firstCand.has("a-ch1"), false, "只剩重评那一份时不该有任何候选：");
    eq(T.buildFirstScoredIn(only).get("a-ch1"), null);
  });
  ok("chainInfo：legacy（v1.4–v1.6，没有 rescore_of）两份都收过这一场 ⇒ 两候选 ⇒ null", () => {
    /* 历史导出没有 rescore 字段，一场评两遍在它们眼里长得一样 ⇒ 「不知道」才是真话 */
    const info = T.chainInfo([
      { name: "old1.json", doc: { seed: 11, records: [{ trial_id: "a-ch1" }] } },
      { name: "old2.json", doc: { seed: 22, records: [{ trial_id: "a-ch1" }] } },
    ]);
    eq(info.firstCand.get("a-ch1"), ["old1.json", "old2.json"]);
    eq(T.buildFirstScoredIn(info).get("a-ch1"), null);
  });
  ok("chainInfo：同一份文件里这一场出现两次也只算一个候选（不去重会假报「两份」）", () => {
    const info = T.chainInfo([
      { name: "f1.json", doc: { seed: 1, records: [{ trial_id: "a-ch1" },
                                                   { trial_id: "a-ch1" }] } },
    ]);
    eq(info.firstCand.get("a-ch1"), ["f1.json"]);
    eq(T.buildFirstScoredIn(info).get("a-ch1"), "f1.json");
  });
  ok("分批会话的 partial + final 同选 ⇒ 两个候选 ⇒ 首评如实写 null（裁决 C 的直接后果）", () => {
    /* 同一次会话分批落下两份：final 的 records 是 partial 的超集，所以 partial 里那些
     * 场次有两份候选（同一个种子、同一次会话）。裁决 C 明说「两份以上 ⇒ null，不许挑
     * 一份」，工具就照办：宁可说不知道。这条把后果钉在明处，将来若要按会话归并候选，
     * 必须有新裁决并改这条测试。 */
    const info = T.chainInfo([
      { name: "s_partial.json", doc: { seed: 5, records: [{ trial_id: "a-ch1" }],
                                       prior_done: [], cumulative_done: ["a-ch1"],
                                       cumulative_done_count: 1 } },
      { name: "s_final.json", doc: { seed: 5, records: [{ trial_id: "a-ch1" },
                                                        { trial_id: "b-ch1" }],
                                     prior_done: ["a-ch1"],
                                     cumulative_done: ["a-ch1", "b-ch1"],
                                     cumulative_done_count: 2 } },
    ]);
    eq(info.errs, [], "同一个种子的 partial + final 不该触发任何停机：");
    eq(info.firstCand.get("a-ch1"), ["s_partial.json", "s_final.json"]);
    eq(info.firstCand.get("b-ch1"), ["s_final.json"], "只在 final 里出现的那场仍然定得下来：");
    const m = T.buildFirstScoredIn(info);
    eq(m.get("a-ch1"), null);
    eq(m.get("b-ch1"), "s_final.json");
  });
  ok("saveProgress：首评解析表存成二元组表（Map 进不了 JSON），恢复回来还是同一张表", () => {
    threeTrials();
    T.state.scorer = "R1";
    T.state.firstScoredIn = new Map([["a-ch1", "first_A.json"], ["b-ch1", null]]);
    T.saveProgress();
    const raw = JSON.parse(localStorage.getItem("dpst:v2:R1"));
    eq(raw.firstScoredIn, [["a-ch1", "first_A.json"], ["b-ch1", null]]);
    T.state.firstScoredIn = null;
    T.saveProgress();
    eq(JSON.parse(localStorage.getItem("dpst:v2:R1")).firstScoredIn, []);
  });
  await okA("存档续评：firstScoredIn 跟着 localStorage 往返，重评导出仍指得回首评文件", async () => {
    threeTrials();
    el("scorerId").value = "R1"; el("seed").value = "7";
    el("manifestFile").files = [{ name: "m.csv", text: async () => MAN3 }];
    el("videoFiles").files = [{ name: "a.mp4" }, { name: "b.mp4" }, { name: "c.mp4" }];
    localStorage.setItem("dpst:v2:R1", JSON.stringify({
      seed: 7, order: ["a-ch1", "b-ch1", "c-ch1"], assay: "TST",
      done: [mkDone("a-ch1", 1)], priorDone: ["a-ch1"],
      claimedFirst: false, rescore: true, rescoreOf: ["a-ch1"],
      priorFileNames: ["first_A.json"], firstScoredIn: [["a-ch1", "first_A.json"]], ts: 1 }));
    T.onResume(); await tick();
    eq(T.state.firstScoredIn.get("a-ch1"), "first_A.json");
    const d = captureAudit(() => { dl.length = 0; T.exportSnapshot(false); });
    eq(d.records.map(r => r.first_scored_in), ["first_A.json"]);
    eq(d.first_scored_unresolved, []);
    eq(d.prior_files, ["first_A.json"]);
  });
  ok("自检：first_scored_unresolved 与 first_scored_in 为 null 的条数打架 ⇒ 拒绝导出，一个字节不落", () => {
    threeTrials();
    /* 同一场落了两条记录：顶层清单去重后 1 条、记录里 null 有 2 条 ⇒ 两个字段说的
     * 不是一回事。工具的账自己都对不上时不许导出（与 §1.3 同一套处置）。 */
    T.state.firstScoredIn = new Map([["a-ch1", null]]);
    T.state.priorDone = [];
    T.state.done = [mkDone("a-ch1", 1), mkDone("a-ch1", 1)];
    dl.length = 0;
    throws(() => T.exportSnapshot(false), "first_scored_unresolved");
    eq(dl.length, 0, "自检没过却已经落了文件：");
  });

  console.log("DP-077 缺陷②（「开始评分」静默抹掉存档进度）：");
  await okA("有存档时第一次点「开始评分」只警告、不抹；再点一次才抹", async () => {
    const MAN = "trial_id,video_filename\r\na-ch1,a.mp4\r\nb-ch1,b.mp4\r\nc-ch1,c.mp4\r\n";
    threeTrials();                       // 复位会话形态：这条测的是续评+存档确认，不是重评
    el("scorerId").value = "R1"; el("seed").value = "7";
    el("manifestFile").files = [{ name: "m.csv", text: async () => MAN }];
    el("videoFiles").files = [{ name: "a.mp4" }, { name: "b.mp4" }, { name: "c.mp4" }];
    /* 三场都已在之前导出里 ⇒ 不走"没安排"确认，直接撞上抹进度那道关 */
    el("priorFiles").files = [auditFile("p.json", auditDoc({
      records: [{ trial_id: "a-ch1" }, { trial_id: "b-ch1" }, { trial_id: "c-ch1" }] }))];
    localStorage.setItem("dpst:v2:R1", JSON.stringify({
      seed: 7, order: ["a-ch1", "b-ch1", "c-ch1"], assay: "TST",
      done: [mkDone("a-ch1", 1), mkDone("b-ch1", 2)], ts: 1 }));
    T.state.confirmOnce = null;
    el("setupErr").textContent = "【桩】上一条测试留下的红字";
    el("resumeBox").hidden = true;       // 该露出的先摆成收着
    el("setup").hidden = false;

    T.onStart();
    await new Promise(r => setTimeout(r, 0));
    if (localStorage.getItem("dpst:v2:R1") === null) {
      throw new Error("第一次点就把存档抹了——这正是 2026-09-07 丢进度的原因");
    }
    if (!el("setupErr").textContent.includes("已评 2 场")) {
      throw new Error("没告诉评分员存着几场：" + el("setupErr").textContent);
    }
    if (el("resumeBox").hidden) throw new Error("没把「继续上次未完成的评分」露出来");
    /* 这一场只有存档一件事（三场都在链条里 ⇒ 没有「没排满」），框里就不许冒出
     * 第二条，也不许说「上面 2 件事」——合并只减少点击，不许凭空多报一件事。 */
    const box1 = el("setupErr").textContent;
    if (box1.includes("① 本批没排满")) throw new Error("没有缺场却报了「本批没排满」：" + box1);
    if (box1.includes("上面 2 件事")) throw new Error("只有一件事却说「上面 2 件事」：" + box1);
    if (!box1.includes("② 本机有存档")) throw new Error("存档那条没逐字列出：" + box1);

    T.onStart();                       // 明确确认后才允许重开
    await new Promise(r => setTimeout(r, 0));
    eq(localStorage.getItem("dpst:v2:R1"), null, "确认后仍没重开：");
  });

  console.log("裁决 D（DP-128 §9-(3)：两道「再点一次」合成一个框，点击减半、内容一条不少）：");
  await okA("没排满 + 有存档 ⇒ 两条内容在同一个框里一次说清，两次点击就开评（原来要四次）", async () => {
    /* 连跑 4 轮：裁决 1 之后每点一次「开始评分」都当场新生成种子（Math.random），
     * 洗牌顺序轮轮不同。确认指纹要是掺了顺序，第二轮起就只能靠随机撞上同一个
     * 顺序才开得了评（3 场名册 1/6，12 场名册等于永远开不了）——多跑几轮才钉得住。 */
    for (let round = 1; round <= 4; round++) {
      threeTrials();
      localStorage.removeItem("dpst:v2:R1");
      el("scorerId").value = "R1"; el("seed").value = "7";
      el("manifestFile").files = [{ name: "m.csv", text: async () => MAN3 }];
      el("videoFiles").files = [{ name: "a.mp4" }];       // 只选到一场视频
      el("priorFiles").files = [auditFile("p.json", auditDoc({
        records: [{ trial_id: "b-ch1" }] }))];             // 之前评过一场 ⇒ c-ch1 没安排
      el("kindFirst").checked = false; el("kindLost").checked = false;
      localStorage.setItem("dpst:v2:R1", JSON.stringify({
        seed: 7, order: ["a-ch1", "b-ch1", "c-ch1"], assay: "TST",
        done: [mkDone("a-ch1", 1), mkDone("b-ch1", 2)], ts: 1 }));
      /* 先把现场弄脏（新纪律）：DOM 桩与 state 都跨测试复用，不写入非默认值，
       * 「这次清掉了」和「本来就是空的」分不开。 */
      T.state.confirmOnce = "上一条测试留下的指纹";
      el("setupErr").textContent = "上一条测试留下的红字";
      el("resumeBox").hidden = true; el("setup").hidden = false; el("scoring").hidden = true;

      T.onStart(); await tick();
      const box = el("setupErr").textContent;
      if (round === 1) {
        if (!box.includes("① 本批没排满")) throw new Error("没列第一条（本批没排满）：" + box);
        if (!box.includes("② 本机有存档")) throw new Error("没列第二条（本机有存档）：" + box);
        /* 第一条的四件事（少一件评分员就没法判断这次点下去会发生什么） */
        for (const s of ["清单共 3 场", "之前已评 1 场", "本次选到 1 场视频", "还有 1 场没安排",
                         "再点一次「开始评分」= 本批只评这 1 场", "c-ch1"]) {
          if (!box.includes(s)) throw new Error("第一条缺了「" + s + "」：" + box);
        }
        /* 第二条的三件事 */
        for (const s of ["本机存着「R1」已评 2 场的进度", "继续上次未完成的评分",
                         "再点一次「开始评分」= 放弃这 2 场存档"]) {
          if (!box.includes(s)) throw new Error("第二条缺了「" + s + "」：" + box);
        }
        if (!box.includes("上面 2 件事一次说清：再点一次「开始评分」= 两件都照办。")) {
          throw new Error("没把「一次点击=两件都照办」说清：" + box);
        }
        eq(el("resumeBox").hidden, false, "没把「继续上次未完成的评分」露出来：");
        /* 指纹逐字钉住：四段全是排序后的集合，一段都不许来自本次洗牌顺序 */
        eq(T.state.confirmOnce, "a-ch1|b-ch1|c-ch1#b-ch1#c-ch1#2",
           "确认指纹必须与洗牌顺序无关（每点一次都换种子）：");
      }
      if (localStorage.getItem("dpst:v2:R1") === null) {
        throw new Error("第 " + round + " 轮：第一次点就把存档抹了——这正是 DP-077 缺陷②");
      }
      if (el("setup").hidden) {
        throw new Error("第 " + round + " 轮：第一次点就开评了（那两条内容等于没说）");
      }
      /* state.started 是「按下开始观看」的旗标，不是「会话已开」的旗标；这里能证明
       * 还没开评的是：设置页还在、存档没被抹、会话状态一个字段都还没写进去。 */
      eq(T.state.priorDone, [], "还没确认就把链条状态写进会话了：");

      T.onStart(); await tick();           // 第二次点 = 两件都照办
      if (!el("setup").hidden) {
        throw new Error("第 " + round + " 轮：第二次点仍没开评——「再点一次」确认不了，"
          + "评分员会被卡在设置页反复点。红字：" + el("setupErr").textContent.split("\n")[0]);
      }
      eq(el("setupErr").textContent, "", "确认后还挂着红字：");
      eq(el("scoring").hidden, false);
      eq(T.state.priorDone, ["b-ch1"], "确认后链条的已评清单才进会话状态：");
      eq(T.state.queue.map(m => m.trial_id), ["a-ch1"], "本批只派选到视频又没评过的那一场：");
      eq(el("seed").readOnly, true, "续评会话的种子是当场新生成的，界面上只读：");
      if (!/^\d+$/.test(el("seed").value)) {
        throw new Error("新生成的种子不是非负整数：" + el("seed").value);
      }
      eq(localStorage.getItem("dpst:v2:R1"), null, "确认后没重开：");
      eq(T.state.confirmOnce, null, "确认后指纹没清掉（下次情况没变也会免读直接过）：");
    }
  });

  await okA("点完一次之后情况变了 ⇒ 指纹失效，两条内容重新摆出来重读一遍", async () => {
    threeTrials();
    localStorage.removeItem("dpst:v2:R1");
    el("scorerId").value = "R1"; el("seed").value = "7";
    el("manifestFile").files = [{ name: "m.csv", text: async () => MAN3 }];
    el("videoFiles").files = [{ name: "a.mp4" }];
    el("priorFiles").files = [auditFile("p.json", auditDoc({
      records: [{ trial_id: "b-ch1" }] }))];
    localStorage.setItem("dpst:v2:R1", JSON.stringify({
      seed: 7, order: ["a-ch1", "b-ch1", "c-ch1"], assay: "TST",
      done: [mkDone("a-ch1", 1)], ts: 1 }));
    el("setupErr").textContent = "脏值"; el("setup").hidden = false;

    T.onStart(); await tick();
    if (!el("setupErr").textContent.includes("已评 1 场的进度")) {
      throw new Error("第一次的框内容不对：" + el("setupErr").textContent);
    }
    /* 存档变成 3 场：要放弃的东西不一样了，旧确认不许沿用 */
    localStorage.setItem("dpst:v2:R1", JSON.stringify({
      seed: 7, order: ["a-ch1", "b-ch1", "c-ch1"], assay: "TST",
      done: [mkDone("a-ch1", 1), mkDone("b-ch1", 2), mkDone("c-ch1", 3)], ts: 2 }));
    T.onStart(); await tick();
    const box2 = el("setupErr").textContent;
    if (!box2.includes("已评 3 场的进度")) {
      throw new Error("情况变了却沿用上一次的确认（或没把新数字摆出来）：" + box2);
    }
    eq(T.state.confirmOnce, "a-ch1|b-ch1|c-ch1#b-ch1#c-ch1#3",
       "指纹要跟着「存着几场」变，不跟着洗牌顺序变：");
    eq(el("setup").hidden, false, "情况变了还照样开评：");
    if (localStorage.getItem("dpst:v2:R1") === null) throw new Error("情况变了就把存档抹了：");
  });

  /* ---- DP-131 裁决①：N == 0 时不许开始 ----
   * 这道关打得着的形态只有一种：本次**选了**视频文件，但选到的每一场都已经在链条的
   * 已评清单里 ⇒ nHave == 0 而 missing 非空。「一个视频文件都没选」轮不到它——§1.2
   * 那句「请选择视频文件」先拦住了；「名册全都评过」也不归它——那时 missing 为空，
   * 照旧走 beginSession 的「直接补出最终文件」（最后一条测试钉的就是这条不许被误伤）。
   * 每条测试都先弄脏这道关自己会写的两个字段（setupErr、confirmOnce），否则
   * 「关清了指纹」与「本来就没有指纹」、「关写了红字」与「桩里残留的红字」分不开。
   * 复核意见 1：红字说的是评分员真能做的下一步。这道关打得着时他确实**选了**视频，
   * 只是选到的每一场都评过了，所以文案说「都已评过」并给两条真出路，不说「你没选
   * 视频」（那句照做也解决不了任何事）。期望串只写一遍（ZERO_COPY），五条测试共用，
   * 免得抄五遍抄出五种版本。
   * 复核意见 2：后半句原来点名的那颗按钮在**评分页**（`<section id="scoring" hidden>`），
   * 这道关拦住时压根没渲染，它开的下拉又只列本次会话的 state.done（此刻是空的）——
   * 红字指了一个评分员当时看不见的控件。现在改成点名设置页上真对得上的那一组，并且
   * 钉两件事：① 引号里那两个标签从 HTML 原文取真值来比（改任一侧就红）；② **可见性**
   * ——这道关触发的那一刻「重评」那一栏与下面的清单必然在屏幕上。②就是抓①这类错的：
   * 只钉标签、不钉可见性，抓不到「指了个看不见的控件」。可见性不是假设，是推出来的：
   * nHave == 0 ⇒（「请选择视频文件」那道关保证 vids 非空、「多余视频（不在清单）」那道
   * 关保证 vids 都在名册里）⇒ 有名册条目带着文件 ⇒ prior.done 非空 ⇒ priors 非空且
   * 解析无误 ⇒ onPriorChange 里 chainDone 非空 ⇒「重评」那一栏已经露了出来。第 5 条
   * 测试钉的就是这条推导唯一可能塌的那个口子（priors 为空时 nHave 还能不能等于 0）。
   * 所以两个 0 场夹具都得先走一遍 onPriorChange：评分员选进「已评进度」那一下必然
   * 触发 change，跳过它就等于拿上一条测试留下的残值当现场，钉子钉上去也是空的。 */
  const ZERO_COPY = "你选的视频对应的场次都已经评过了：本批没有要评的场次。请把还没评的那几场的视频选进来；确实要重做已评过的场次，就在上面「重评」那一栏勾「本次是重评（rescore）」，再在下面的清单里勾中要重做的场次。";
  /* 这道关的 signature 片段：整串出现时它必然出现。第 3／4 条用它验「这道关没插手」，
   * 比整串比对更严——文案被改过字、只剩这一段，也照样抓得到（原来比的是旧串前半截，
   * 换成整串就等于把这两条验松了一档，不能那么换）。 */
  const ZERO_SIG = "本批没有要评的场次";
  console.log("DP-131 裁决①（N == 0 不许开始：0 场是一道「不许开始」，不是「再点一次就评 0 场」）：");
  await okA("选到的视频全是已评过的场次 ⇒ 逐字红字拦住，不弹确认框、第二次点还是这一句", async () => {
    setupStart({ videos: ["a.mp4"], priors: [auditFile("p.json", auditDoc({
      records: [{ trial_id: "a-ch1" }] }))] });       // a 评过又选了它的视频 ⇒ nHave = 0，b/c 没安排
    /* 复核意见 2：可见性钉子要钉在**真现场**上——评分员选进「已评进度」那一下必然
     * 触发 change ⇒ onPriorChange。先弄脏（该露出的摆成收着、清单摆成桩内容），
     * 否则「它露出来了」与「它本来就露着」分不开，钉子就是空的。 */
    el("rescoreBox").hidden = true;
    el("rescorePick").hidden = true; el("rescorePick").innerHTML = "【桩】";
    await T.onPriorChange();
    /* 先弄脏：指纹写非空。onPriorChange 会把 setupErr 清空，脏值必须写在它之后 */
    el("setupErr").textContent = "【桩】上一条测试留下的红字";
    T.state.confirmOnce = "上一条测试留下的指纹";
    /* 现场摆成评分员真实看到的样子，这几条验的是「这道关没顺手改别的」 */
    el("setup").hidden = false; el("scoring").hidden = true; el("finish").hidden = true;
    dl.length = 0;

    T.onStart(); await tick();
    eq(el("setupErr").textContent, ZERO_COPY, "0 场那一道关的文案（逐字）：");
    /* 复核意见 2 第 1 条：引号里那两个标签从 HTML 原文取真值来比（照 redoBtn 那颗钉子
     * 的做法）——任一侧改了字，评分员就照着一句假话找不着那个勾。 */
    const strip = s => s.replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim();
    const legend = strip(/<fieldset id="rescoreBox"[^>]*>\s*<legend>([\s\S]*?)<\/legend>/
      .exec(html)[1]);
    const chkLabel = strip(/<input type="checkbox" id="rescoreChk">([\s\S]*?)<\/label>/
      .exec(html)[1]);
    if (!legend.startsWith("重评")) {
      throw new Error("rescoreBox 的 legend 不以「重评」开头了（界面是 " + legend + "）");
    }
    if (!chkLabel.includes("本次是重评（rescore）")) {
      throw new Error("rescoreChk 的 label 里没有「本次是重评（rescore）」了：" + chkLabel);
    }
    const quoted = ZERO_COPY.match(/「[^」]+」/g).map(s => s.slice(1, -1));
    eq(quoted, ["重评", "本次是重评（rescore）"], "红字引号里引的两个标签（逐字）：");
    if (!legend.startsWith(quoted[0]) || !chkLabel.includes(quoted[1])) {
      throw new Error("红字引的标签与界面上的不一致（照它找不着那个勾）：legend="
        + legend + " / label=" + chkLabel);
    }
    /* 复核意见 2 第 3 条：评分页那颗重做按钮，这道关的文案里 0 命中；它自己不许动 */
    const redoLabel = /<button id="redoBtn"[^>]*>([^<]+)<\/button>/.exec(html)[1];
    eq(redoLabel, "重做已完成试次…", "redoBtn 的真标签（本轮不许动它，也不许再引它）：");
    if (el("setupErr").textContent.includes(redoLabel)) {
      throw new Error("这道关的文案又点名了评分页那颗按钮——它此刻不在屏幕上，"
        + "而且它开的下拉只列本次会话的 state.done（现在是空的）："
        + el("setupErr").textContent);
    }
    /* 复核意见 2 第 2 条：**可见性**钉子。只钉标签不钉可见性，抓不到「红字指了一个
     * 看不见的控件」——复核意见 1 那颗 redoBtn 钉子正是这么漏的，别省。 */
    eq(el("rescoreBox").hidden, false,
       "这道关拦住的那一刻「重评」那一栏不在屏幕上（红字指了个看不见的控件）：");
    eq(el("rescorePick").hidden, false, "红字说的「下面的清单」不在屏幕上：");
    if (el("rescorePick").innerHTML === "【桩】") {
      throw new Error("清单那一格还是桩留下的内容——buildRescorePick 没跑过"
        + "（真跑过会先把它清空再逐场重建）");
    }
    if (!(T.state.chainDone.length > 0)) {
      throw new Error("nHave == 0 却 chainDone 为空：可见性那条推导的前提塌了");
    }
    /* 清单就是按 chainDone 逐场生成的（HTML 里 `for (const tid of state.chainDone)`，
     * 契约那边钉着这条源码耦合），所以钉住 chainDone 就等于钉住清单里有哪几场。 */
    eq(T.state.chainDone, ["a-ch1"], "清单里列的就是链条已评的那几场：");
    eq(T.state.confirmOnce, null, "0 场那次点击留下了确认指纹——那等于「再点一次就放行」：");
    eq(el("setup").hidden, false, "0 场却把设置页收起来了：");
    eq(el("scoring").hidden, true, "0 场却进了评分页：");
    eq(el("finish").hidden, true, "0 场却跳到收工页：");
    eq(dl.length, 0, "0 场却落了文件：");
    eq(T.state.priorDone, [], "还没开评就把链条状态写进会话了：");
    /* 那句假出路必须消失：原来这里照样弹确认框，写着「本批只评这 0 场」 */
    for (const s of ["① 本批没排满", "本批只评这 0 场", "再点一次「开始评分」"]) {
      if (el("setupErr").textContent.includes(s)) {
        throw new Error("0 场时还挂着假出路「" + s + "」：" + el("setupErr").textContent);
      }
    }
    /* 第二次点仍是**这一句** ⇒ 拦住的是这道关，不是 beginSession 那句兜底
     * （「本次所选视频都已评过或不在剩余清单里」——那是原来点第二次才读到的另一句话） */
    T.onStart(); await tick();
    eq(el("setupErr").textContent, ZERO_COPY,
       "第二次点没被这道关拦住（或换成了 beginSession 那句兜底）：");
    eq(el("scoring").hidden, true, "第二次点就开评了：");
    eq(el("setup").hidden, false, "第二次点把设置页收起来了：");
    eq(dl.length, 0, "第二次点落了文件：");
  });

  await okA("0 场且本机有存档 ⇒ 这道关优先于确认框：不列②、不露「继续上次」、存档一个字节都不抹", async () => {
    setupStart({ videos: ["a.mp4"], priors: [auditFile("p.json", auditDoc({
      records: [{ trial_id: "a-ch1" }] }))] });
    /* 同上一条：可见性钉子要钉在真现场上（选进「已评进度」⇒ 必然触发 onPriorChange） */
    el("rescoreBox").hidden = true;
    el("rescorePick").hidden = true; el("rescorePick").innerHTML = "【桩】";
    await T.onPriorChange();
    el("setupErr").textContent = "【桩】上一条测试留下的红字";
    /* 弄脏：真写一份存档进去——「没被抹」与「本来就没有」必须分得开（DP-077 缺陷②） */
    localStorage.setItem("dpst:v2:R1", JSON.stringify({
      seed: 7, order: ["a-ch1", "b-ch1", "c-ch1"], assay: "TST",
      done: [mkDone("a-ch1", 1)], ts: 1 }));
    T.state.confirmOnce = "上一条测试留下的指纹";
    el("resumeBox").hidden = true;      // 真实现场：「继续上次」收着；②那条会把它露出来
    el("setup").hidden = false; el("scoring").hidden = true;

    T.onStart(); await tick();
    eq(el("setupErr").textContent, ZERO_COPY,
       "有存档时这道关的文案也得是这一句（关优先于确认框）：");
    eq(el("rescoreBox").hidden, false,
       "有存档这一路也一样：这道关拦住时「重评」那一栏必须在屏幕上（红字点名的就是它）：");
    if (el("setupErr").textContent.includes("② 本机有存档")) {
      throw new Error("0 场时还把存档那条列出来了——关让位给了确认框："
        + el("setupErr").textContent);
    }
    eq(el("resumeBox").hidden, true, "0 场却把「继续上次未完成的评分」露出来了：");
    eq(T.state.confirmOnce, null, "0 场那次点击留下了确认指纹：");
    if (localStorage.getItem("dpst:v2:R1") === null) {
      throw new Error("0 场那次点击就把存档抹了——这正是 DP-077 缺陷②");
    }
    eq(el("setup").hidden, false, "0 场却开了评：");

    T.onStart(); await tick();
    eq(el("setupErr").textContent, ZERO_COPY, "第二次点没拦住：");
    if (localStorage.getItem("dpst:v2:R1") === null) throw new Error("第二次点把存档抹了：");
  });

  await okA("关不许过宽：nHave ≥ 1 且还有场次没排满 ⇒ 照旧弹确认框，N ≥ 1 的文案一个字都没动", async () => {
    setupStart({ videos: ["a.mp4"], priors: [auditFile("p.json", auditDoc({
      records: [{ trial_id: "b-ch1" }] }))] });       // a 选到视频又没评过 ⇒ nHave = 1；c 没安排
    T.state.confirmOnce = "上一条测试留下的指纹";
    el("setup").hidden = false; el("scoring").hidden = true;
    dl.length = 0;

    T.onStart(); await tick();
    const box = el("setupErr").textContent;
    if (!box.includes("① 本批没排满")) {
      throw new Error("nHave = 1 时确认框没了（新关过宽，把能评的批次也拦了）：" + box);
    }
    for (const s of ["清单共 3 场", "之前已评 1 场", "本次选到 1 场视频", "还有 1 场没安排",
                     "再点一次「开始评分」= 本批只评这 1 场", "c-ch1"]) {
      if (!box.includes(s)) throw new Error("N ≥ 1 的文案缺了「" + s + "」：" + box);
    }
    if (box.includes(ZERO_SIG)) {
      throw new Error("nHave = 1 也被 0 场那道关拦了：" + box);
    }
    eq(T.state.confirmOnce, "a-ch1|b-ch1|c-ch1#b-ch1#c-ch1#0",
       "确认指纹（没存档 ⇒ 末段是 0），且与洗牌顺序无关：");
    eq(el("setup").hidden, false, "第一次点就开评了：");
    eq(dl.length, 0, "第一次点就落文件了：");

    T.onStart(); await tick();                       // 第二次点 = 确认
    eq(el("setupErr").textContent, "", "确认后还挂着红字：");
    eq(el("setup").hidden, true, "第二次点没开评：");
    eq(el("scoring").hidden, false, "第二次点没进评分页：");
    eq(T.state.queue.map(m => m.trial_id), ["a-ch1"], "本批只派选到视频又没评过的那一场：");
    eq(T.state.priorDone, ["b-ch1"], "确认后链条的已评清单才进会话状态：");
    eq(T.state.confirmOnce, null, "确认后指纹没清掉：");
  });

  await okA("关不许误伤：名册全都评过（missing 空、nHave 0）⇒ 照旧直接补出最终文件", async () => {
    setupStart({ videos: ["a.mp4"], priors: [auditFile("p.json", auditDoc({
      records: [{ trial_id: "a-ch1" }, { trial_id: "b-ch1" }, { trial_id: "c-ch1" }],
      done_count: 3 }))] });                          // 三场全评过 ⇒ missing 为空，不进那道关
    T.state.confirmOnce = "上一条测试留下的指纹";
    el("setup").hidden = false; el("scoring").hidden = true;
    el("finish").hidden = true;                       // 弄脏：收工页先摆成收着
    el("finishMsg").textContent = "【桩】上一条测试留下的收工话";
    dl.length = 0;

    T.onStart(); await tick();
    if (el("setupErr").textContent.includes(ZERO_SIG)) {
      throw new Error("名册全评完那条路被 0 场那道关拦了——评分员再也补不出最终文件："
        + el("setupErr").textContent);
    }
    eq(el("setup").hidden, true, "全部已评完 ⇒ 本该直接收工，设置页却还露着：");
    eq(el("finish").hidden, false, "没走 finishSession（最终文件补不出来）：");
    eq(dl.length, 2, "补出的最终文件该是 CSV + 审计两份：");
    if (!dl.every(n => n.includes("_final_"))) {
      throw new Error("落盘的不是最终文件：" + dl.join(" | "));
    }
    if (!el("finishMsg").textContent.includes("全部 3/3 个试次已评分")) {
      throw new Error("收工那句话不对（还是桩里的残留）：" + el("finishMsg").textContent);
    }
    eq(T.state.confirmOnce, null, "这条路上指纹该清空：");
  });

  await okA("复核意见 2 的可达性：priors 为空 ⇒ 这道关压根打不着（名册外的视频被「多余视频」那道关先接住）", async () => {
    /* 能让 nHave == 0 而 chainDone 为空的路只有两条：一个视频都没选（被 §1.2
     * 「请选择视频文件」那道关先接住，另一道关），或选到的视频都不在名册里。
     * 后者在这里钉死：它被 onStart 里「多余视频（不在清单）」那道关接住，所以红字
     * 不是这道关那句，「重评」那一栏也没被冒出来——可见性那条推导没有别的口子。 */
    setupStart({ videos: ["zzz_不在名册.mp4"], priors: [], kindFirst: true });
    /* 弄脏成「露着」再走真的 onPriorChange：priors 为空 ⇒ 它必然把这一栏收回。
     * 不先摆成露着，下面那条「收着」就分不清是真代码收的还是桩里本来就是。 */
    el("rescoreBox").hidden = false; el("rescorePick").hidden = false;
    await T.onPriorChange();
    el("setupErr").textContent = "【桩】上一条测试留下的红字";
    T.state.confirmOnce = "上一条测试留下的指纹";
    el("setup").hidden = false; el("scoring").hidden = true;
    dl.length = 0;

    T.onStart(); await tick();
    const box = el("setupErr").textContent;
    if (!box.includes("多余视频（不在清单）")) {
      throw new Error("名册外的视频没被那道关接住（可达性推导的前提就塌了）：" + box);
    }
    if (box.includes(ZERO_SIG)) {
      throw new Error("priors 为空却触发了 0 场那道关——那它的红字会指一个不在屏幕上的控件："
        + box);
    }
    eq(el("rescoreBox").hidden, true,
       "priors 为空时「重评」那一栏必然收着：这条路上 0 场那道关要真打得着，红字就又在指一个看不见的控件：");
    eq(el("scoring").hidden, true, "却开了评：");
    eq(el("setup").hidden, false, "却把设置页收起来了：");
    eq(dl.length, 0, "却落了文件：");
  });

  console.log("\n自测汇总：通过 " + passes + " 条，不通过 " + fails + " 条");
  console.log(fails ? fails + " 条不通过" : "全部通过");
  process.exit(fails ? 1 : 0);
}
main();
