/* 秒表工具 v1.5 双范式的离线自测：只测两件真会出错的事——
 * ① 清单解析（assay 缺列/留空/大小写/非法值/混排/FST- 前缀防撞）；
 * ② 导出 CSV 的列与「没问到就留空」。
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
  + " ASSAYS, applyAssay, markDeclaredEmpty, nextTrial, mobileAccumulator };")(mod);
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

console.log(fails ? "\n" + fails + " 条不通过" : "\n全部通过");
process.exit(fails ? 1 : 0);
