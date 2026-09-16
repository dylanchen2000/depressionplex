/* 秒表工具（v1.5 双范式 + v1.7 DP-128）的离线自测：只测真会出错的事——
 * ① 清单解析（assay 缺列/留空/大小写/非法值/混排/FST- 前缀防撞）；
 * ② 导出 CSV 的列与「没问到就留空」；
 * ③ DP-077 三处静默丢数据缺陷的回归；
 * ④ v1.7（DP-128）：种子从链条恢复且必须唯一（§1.1）、首次会话显式声明
 *    （§1.2）、cumulative_done + 导出前自检（§1.3）、重评只派指定场次
 *    （§1.4）、DP-086 按键膨胀的结构性修复与重看记账（§2 B/C）。
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
  + " loadTrial, onPriorChange, buildRescorePick, chainInfo };")(mod);
const T = mod.exports;

let fails = 0;
function ok(name, fn) {
  try { fn(); console.log("  pass  " + name); }
  catch (e) { fails++; console.log("  FAIL  " + name + " → " + (e.message || e)); }
}
function eq(a, b, msg) {
  const sa = JSON.stringify(a), sb = JSON.stringify(b);
  if (sa !== sb) throw new Error((msg || "") + " 期望 " + sb + " 实得 " + sa);
}
async function okA(name, fn) {
  try { await fn(); console.log("  pass  " + name); }
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
  T.state.priorFileNames = []; T.state.chainSeed = null; T.state.chainDone = [];
  T.state.rewatchS = 0; T.state.redoTrialId = null; T.state.started = false;
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
    /* v1.7 起链条必须带 seed（§1.1：顺序从链条恢复）；真实导出 v1.4 起就带 */
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
  /* 旧版这里是 2.5 + 平均 0.115 s 的膨胀：按下后第一次 timeupdate 会把
   * [上次更新, 按下] 那段也算进去。v1.7 只在 endHold 结算，膨胀无从发生。 */
});
ok("多段按住取并集：重叠不翻倍，审计逐次全记", () => {
  const acc = T.mobileAccumulator();
  acc.startHold(10, 10); acc.endHold(20);     // 首看 [10,20]
  acc.startHold(15, 20); acc.endHold(25);     // 从已看区 15 按住播过前沿到 25
  eq(acc.value, 15);                          // 并集 [10,25]
  eq(acc.holds, [[10, 20], [15, 25]]);
});
ok("重看段的按键一律不计入在动（前沿以下不计数），审计 holds 仍全记", () => {
  const acc = T.mobileAccumulator();
  acc.startHold(30, 30); acc.endHold(40);     // 首看到 40
  acc.startHold(10, 40); acc.endHold(35);     // 重看 10→35：整段在前沿以下
  eq(acc.value, 10);                          // 只有 [30,40]
  eq(acc.holds, [[30, 40], [10, 35]]);        // 反应延迟分析一个字节不丢
});
ok("跨越前沿的按住：只计前沿之后的部分", () => {
  const acc = T.mobileAccumulator();
  acc.startHold(95, 100); acc.endHold(120);   // 按下时前沿 100
  eq(acc.value, 20);                          // 计入 [100,120]
  eq(acc.holds, [[95, 120]]);
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
ok("重评导出：rescore / rescore_of / prior_files（首评所在文件）如实记账", () => {
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
    T.state.confirmBatch = null; T.state.confirmWipe = null;
    el("setupErr").textContent = "";
  }

  console.log("DP-128 §1.1（种子从链条恢复；两个种子停机）：");
  await okA("选进「已评进度」当场恢复种子（只读显示），首次声明框收起、重评列表出现", async () => {
    el("priorFiles").files = [auditFile("p1.json", auditDoc({ seed: 4242,
      records: [{ trial_id: "a-ch1" }], cumulative_done: ["a-ch1"], cumulative_done_count: 1 }))];
    await T.onPriorChange();
    eq(el("seed").value, "4242"); eq(el("seed").readOnly, true);
    eq(el("firstBox").hidden, true);
    eq(el("rescoreBox").hidden, false); eq(el("rescorePick").hidden, false);
    eq(T.state.chainDone, ["a-ch1"]); eq(T.state.chainSeed, 4242);
    eq(el("setupErr").textContent, "");
  });
  await okA("链里两个种子 ⇒ 当场红字，文案逐字是裁决那句", async () => {
    el("priorFiles").files = [auditFile("p1.json", auditDoc({ seed: 1, records: [] })),
                              auditFile("p2.json", auditDoc({ seed: 2, records: [] }))];
    await T.onPriorChange();
    if (!el("setupErr").textContent.includes("这几份导出不是同一个顺序，不许混在一起续评")) {
      throw new Error("停机文案不对：" + el("setupErr").textContent);
    }
    eq(el("seed").value, ""); eq(el("seed").readOnly, true);
    eq(T.state.chainSeed, null);
  });
  await okA("清空「已评进度」⇒ 首次声明框回来，种子字段恢复可填", async () => {
    el("priorFiles").files = [];
    await T.onPriorChange();
    eq(el("firstBox").hidden, false); eq(el("seed").readOnly, false);
    eq(el("rescoreBox").hidden, true);
  });
  await okA("续评 onStart：种子来自链条而不是界面字段，恢复成只读", async () => {
    setupStart({ seedValue: "",
      priors: [auditFile("p.json", auditDoc({ seed: 4242, records: [{ trial_id: "a-ch1" }] }))] });
    T.onStart(); await tick();
    eq(el("setupErr").textContent, "");
    eq(T.state.seed, 4242);
    eq(el("seed").value, "4242"); eq(el("seed").readOnly, true);
    eq(T.state.claimedFirst, false); eq(T.state.rescore, false);
    eq(T.state.priorFileNames, ["p.json"]);
    eq(T.state.queue.map(m => m.trial_id).sort(), ["b-ch1", "c-ch1"]);
  });
  await okA("续评 onStart：链里两个种子 ⇒ 停机，不开评", async () => {
    setupStart({ seedValue: "",
      priors: [auditFile("p1.json", auditDoc({ seed: 7, records: [{ trial_id: "a-ch1" }] })),
               auditFile("p2.json", auditDoc({ seed: 99, records: [{ trial_id: "b-ch1" }] }))] });
    T.onStart(); await tick();
    if (!el("setupErr").textContent.includes("这几份导出不是同一个顺序，不许混在一起续评")) {
      throw new Error("没有按裁决停机：" + el("setupErr").textContent);
    }
    eq(T.state.priorFileNames, []);       // 没走到赋值 = 没开评
  });
  await okA("链条文件缺 seed ⇒ 拒开（无法从链条恢复顺序）", async () => {
    const doc = auditDoc({ records: [{ trial_id: "a-ch1" }] }); delete doc.seed;
    setupStart({ seedValue: "", priors: [auditFile("p.json", doc)] });
    T.onStart(); await tick();
    if (!el("setupErr").textContent.includes("缺 seed")) {
      throw new Error("没报缺 seed：" + el("setupErr").textContent);
    }
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
    T.state.confirmWipe = null; T.state.confirmBatch = null;
    el("setupErr").textContent = "";

    T.onStart();
    await new Promise(r => setTimeout(r, 0));
    if (localStorage.getItem("dpst:v2:R1") === null) {
      throw new Error("第一次点就把存档抹了——这正是 2026-09-07 丢进度的原因");
    }
    if (!el("setupErr").textContent.includes("已评 2 场")) {
      throw new Error("没告诉评分员存着几场：" + el("setupErr").textContent);
    }
    if (el("resumeBox").hidden) throw new Error("没把「继续上次未完成的评分」露出来");

    T.onStart();                       // 明确确认后才允许重开
    await new Promise(r => setTimeout(r, 0));
    eq(localStorage.getItem("dpst:v2:R1"), null, "确认后仍没重开：");
  });

  console.log(fails ? "\n" + fails + " 条不通过" : "\n全部通过");
  process.exit(fails ? 1 : 0);
}
main();
