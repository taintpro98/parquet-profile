"""Dựng report.html: một trang tự chứa (dữ liệu nhúng sẵn), mở thẳng bằng trình duyệt.

`make summary` gọi write_html() với mọi kết quả đọc được trong results/. Phần nào chưa có kết quả
thì trang để trống phần đó.
"""

import json
from datetime import datetime

POD_RAM_MB = 4096  # giới hạn RAM của pod, vẽ thành vạch đỏ trên biểu đồ RAM


def write_html(path, entries, oracle, machine):
    data = {
        "entries": entries,
        "oracle": oracle,
        "machine": machine,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "podRamMb": POD_RAM_MB,
    }
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    path.write_text(TEMPLATE.replace("__DATA__", payload), encoding="utf-8")


TEMPLATE = r"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Archive Bench Report</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Sans+Condensed:wght@600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: một cột đọc; mỗi câu hỏi = câu trả lời viết sẵn + biểu đồ cặp Java/Go theo từng kiểu lưu. */
:root {
  --bg: #f6f7f8; --panel: #ffffff; --ink: #15181c; --ink-2: #4a5059; --ink-3: #737a85;
  --rule: #dfe3e8; --grid: #eceff2; --java: #2a78d6; --go: #eb6834; --limit: #b3261e;
  --oracle: #5f6670; --win-bg: #e6f2ec; --win-ink: #17603b; --warn-bg: #fdecea; --warn-ink: #9b1c14;
  --tab-bg: #e8ebef;
  --font-display: "IBM Plex Sans Condensed", "Arial Narrow", sans-serif;
  --font-body: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-data: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #121417; --panel: #1a1d21; --ink: #eef0f2; --ink-2: #b9bfc7; --ink-3: #8a919b;
    --rule: #2c3138; --grid: #23272d; --java: #3987e5; --go: #d95926; --limit: #ff8a80;
    --oracle: #a3a9b2; --win-bg: #16301f; --win-ink: #8fd8ad; --warn-bg: #3a1714; --warn-ink: #ffb4ab;
    --tab-bg: #23272d; color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #121417; --panel: #1a1d21; --ink: #eef0f2; --ink-2: #b9bfc7; --ink-3: #8a919b;
  --rule: #2c3138; --grid: #23272d; --java: #3987e5; --go: #d95926; --limit: #ff8a80;
  --oracle: #a3a9b2; --win-bg: #16301f; --win-ink: #8fd8ad; --warn-bg: #3a1714; --warn-ink: #ffb4ab;
  --tab-bg: #23272d; color-scheme: dark;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.6 var(--font-body);
  padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px));
  padding-block: 32px 64px;
}
main { max-width: 900px; margin: 0 auto; display: grid; gap: 36px; }
h1, h2 { font-family: var(--font-display); text-wrap: balance; margin: 0; }
h1 { font-size: 32px; line-height: 1.15; }
h2 { font-size: 22px; line-height: 1.25; }
h3 { font-size: 14px; margin: 0; font-weight: 600; }
p { margin: 0; max-width: 70ch; }
code { font: 0.9em var(--font-data); }
.eyebrow { font: 500 12px/1.4 var(--font-data); letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-3); }
.sub { color: var(--ink-2); }
header { display: grid; gap: 10px; }
.meta { display: flex; flex-wrap: wrap; gap: 4px 18px; font: 13px/1.5 var(--font-data); color: var(--ink-2); }

.controls { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 24px; position: sticky;
  top: env(safe-area-inset-top, 0px); z-index: 5; background: var(--bg); padding-block: 10px; border-bottom: 1px solid var(--rule); }
body::before { content: ""; position: fixed; inset: 0 0 auto; height: env(safe-area-inset-top, 0px); z-index: 10; background: var(--bg); }
.tabs { display: inline-flex; background: var(--tab-bg); border-radius: 8px; padding: 3px; gap: 3px; }
.tabs button { font: 500 14px var(--font-body); border: 0; background: transparent; color: var(--ink-2);
  padding: 6px 14px; border-radius: 6px; cursor: pointer; }
.tabs button[aria-pressed="true"] { background: var(--panel); color: var(--ink); box-shadow: 0 1px 2px rgba(0,0,0,.12); }
.tabs button:focus-visible { outline: 2px solid var(--java); outline-offset: 1px; }
.legend { display: flex; gap: 16px; font-size: 13px; color: var(--ink-2); }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; vertical-align: -1px; }
.legend .line { width: 14px; height: 0; border-top: 2px dashed var(--limit); border-radius: 0; vertical-align: 3px; }
.legend .oline { width: 14px; height: 0; border-top: 2px dashed var(--oracle); border-radius: 0; vertical-align: 3px; }

.answers { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
.answer { background: var(--panel); border: 1px solid var(--rule); border-radius: 8px; padding: 14px 16px; display: grid; gap: 4px; align-content: start; }
.answer .q { font-size: 13px; color: var(--ink-3); }
.answer .a { font: 600 18px/1.3 var(--font-display); }
.answer .d { font-size: 13px; color: var(--ink-2); }
.warn { background: var(--warn-bg); color: var(--warn-ink); border-radius: 8px; padding: 12px 16px; }

section { display: grid; gap: 14px; }
.charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }
figure { margin: 0; background: var(--panel); border: 1px solid var(--rule); border-radius: 8px; padding: 14px 16px 10px; min-width: 0; position: relative; }
figcaption { display: grid; gap: 2px; margin-bottom: 8px; }
figcaption span { font: 12px var(--font-data); color: var(--ink-3); }
svg { display: block; width: 100%; height: auto; overflow: visible; }
svg text { font-family: var(--font-data); font-size: 11px; fill: var(--ink-2); }
svg text.fmt { font-family: var(--font-body); font-size: 12px; font-weight: 600; fill: var(--ink); }
svg text.variant { font-size: 10px; fill: var(--ink-3); }
svg text.val { fill: var(--ink); }
svg text.ref { font-size: 10px; }
svg .bar:hover, svg .bar:focus { opacity: 0.8; outline: none; }
.tip { position: absolute; pointer-events: none; background: var(--panel); border: 1px solid var(--rule); border-radius: 6px;
  padding: 6px 9px; font: 12px/1.45 var(--font-data); color: var(--ink); box-shadow: 0 2px 8px rgba(0,0,0,.15); white-space: nowrap; z-index: 3; }
.tip span { color: var(--ink-3); }
.empty { color: var(--ink-3); font-size: 13px; }
.key { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--grid); font-size: 12px; color: var(--ink-2); }
.key i { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }
.key i.dash { width: 14px; height: 0; border-radius: 0; border-top: 2px dashed; vertical-align: 3px; }

.table-wrap { overflow-x: auto; border: 1px solid var(--rule); border-radius: 8px; background: var(--panel); }
table { border-collapse: collapse; width: 100%; font: 13px/1.4 var(--font-data); font-variant-numeric: tabular-nums; }
th, td { padding: 7px 12px; text-align: right; border-bottom: 1px solid var(--rule); white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
th { color: var(--ink-3); font-weight: 500; font-size: 12px; position: sticky; top: 0; background: var(--panel); }
tr.group td { color: var(--ink-3); font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; background: var(--bg); text-align: left; }
tr:last-child td { border-bottom: 0; }
td.best { font-weight: 600; color: var(--win-ink); }
td.worst { font-weight: 600; color: var(--limit); }
.over { color: var(--limit); font-size: 11px; font-weight: 600; }
td.bad { color: var(--limit); }
details summary { cursor: pointer; color: var(--ink-2); font-size: 14px; }
ul { margin: 0; padding-left: 20px; display: grid; gap: 6px; max-width: 72ch; }
@media (max-width: 480px) { h1 { font-size: 26px; } }
</style>
</head>
<body>
<main>
  <header>
    <div class="eyebrow">Benchmark lưu trữ lạnh</div>
    <h1>Lưu một ngày giao dịch thế nào để tra key + timestamp nhanh, tốn ít RAM và ít ổ đĩa?</h1>
    <p class="sub">Mỗi phương án dump một ngày từ Oracle ra một file, rồi tra bản ghi đầy đủ theo <code>REFERENCE</code> + <code>CREATED_AT</code>. Mọi con số đo từ ngoài tiến trình (gồm cả khởi động JVM/DuckDB). Thời gian là trung vị các lần chạy, RAM là đỉnh lớn nhất.</p>
    <div class="meta" id="meta"></div>
  </header>

  <div class="controls">
    <span class="sub" id="tabs-label">Bộ dữ liệu:</span>
    <div class="tabs" id="tabs" role="group" aria-label="Bộ dữ liệu"></div>
    <div class="legend" aria-hidden="true">
      <span><i style="background:var(--java)"></i>Java</span>
      <span><i style="background:var(--go)"></i>Go</span>
    </div>
  </div>

  <section>
    <h2 id="dataset-title"></h2>
    <p class="sub" id="dataset-desc"></p>
    <div class="answers" id="answers"></div>
    <div id="warnings"></div>
  </section>

  <section id="find-section">
    <div>
      <div class="eyebrow">Tra cứu</div>
      <h2>Tra cứu nhanh tới đâu và tốn bao nhiêu RAM?</h2>
    </div>
    <div id="find-charts" style="display:grid;gap:14px"></div>
  </section>

  <section>
    <div>
      <div class="eyebrow">Dung lượng</div>
      <h2>File archive lớn bao nhiêu so với dữ liệu trong Oracle?</h2>
      <p class="sub">Vạch xám là dung lượng cùng số dòng đó khi nằm trong Oracle: chỉ bảng, và bảng cộng index (khoá chính + <code>(REFERENCE, CREATED_AT)</code>). Thanh ngắn hơn vạch là archive nhỏ hơn Oracle.</p>
    </div>
    <div class="charts" id="size-charts"></div>
  </section>

  <section>
    <div>
      <div class="eyebrow">Ghi archive</div>
      <h2>Dump một ngày mất bao lâu và cần bao nhiêu RAM?</h2>
    </div>
    <div class="charts" id="dump-charts"></div>
  </section>

  <section>
    <div>
      <div class="eyebrow">So hai bộ dữ liệu</div>
      <h2>Dữ liệu lặp nhiều (status) nén tốt hơn ngẫu nhiên bao nhiêu?</h2>
      <p class="sub">Dung lượng mỗi 1 triệu dòng, để hai bộ so được với nhau dù số dòng đã dump khác nhau.</p>
    </div>
    <div class="charts" id="compare-charts"></div>
  </section>

  <section>
    <h2>Toàn bộ số đo</h2>
    <div class="table-wrap"><table id="table"></table></div>
    <p class="sub">Khớp Oracle: số bản ghi trả về giống từng cột với Oracle trên tổng số key tra (– là số đo cũ, chưa kiểm). Trong mỗi nhóm, mỗi cột: <span style="color:var(--win-ink);font-weight:600">xanh lá = tốt nhất</span>, <span style="color:var(--limit);font-weight:600">đỏ = tệ nhất</span>. <span class="over">⚠ vượt pod</span> = RAM vượt giới hạn pod. Wall = thời gian thực, CPU = tổng thời gian CPU của mọi luồng, File = dung lượng archive.</p>
    <details><summary>Cấu hình đã dùng cho từng lần đo</summary><ul id="settings"></ul></details>
  </section>
</main>

<script id="bench-data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById("bench-data").textContent);
// 5 phương án đang xét trước; arrow-zstd, rowblock-zstd (tự chế, số cũ) xếp cuối.
const FORMAT_ORDER = ["parquet-duckdb", "parquet-lookup", "rocksdb", "orc", "avro", "arrow-zstd", "rowblock-zstd"];
const LANGS = [["java", "Java", "var(--java)"], ["go", "Go", "var(--go)"]];
const SCENARIOS = [
  ["1", "Tra 1 key", "1 key ngẫu nhiên từ Oracle."],
  ["1000", "Tra 1000 key", "1000 key ngẫu nhiên từ Oracle."],
  ["unknown", "Tra 1 key không tồn tại", "Phải trả rỗng."],
];
const DATASET_TEXT = {
  random: ["Bộ random: mọi cột text ngẫu nhiên", "Trường hợp xấu nhất cho nén: hầu như không có giá trị lặp."],
  status: ["Bộ status: 20% số cột là trạng thái lặp nhiều", "14/70 cột chỉ có 2–20 giá trị, lệch về vài giá trị phổ biến, gần dữ liệu giao dịch thật hơn."],
  status30: ["Bộ status30 (số cũ): 30% số cột là trạng thái", "Bộ dữ liệu đợt đo trước, 21/70 cột trạng thái. Giữ để tham khảo, không đo lại."],
};
const NS = "http://www.w3.org/2000/svg";
const nf = (v, d) => v.toLocaleString("vi-VN", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtSec = v => nf(v, v < 1 ? 2 : v < 10 ? 1 : 0) + " s";
const fmtMb = v => v >= 10240 ? nf(v / 1024, 1) + " GB" : nf(v, 0) + " MB";
const FMT = { wall: fmtSec, cpu: fmtSec, ram: fmtMb, file: fmtMb };

// Mỗi dòng kết quả: dataset = phần đầu nhãn, variant = phần còn lại (cấu hình thử thêm).
D.entries.forEach(e => {
  const [dataset, ...rest] = (e.label || "random").split("-");
  e.dataset = dataset; e.variant = rest.join("-");
  e.lang = e.impl.split("-")[0]; e.format = e.impl.slice(e.lang.length + 1);
  e.option = e.format + (e.variant ? " · " + e.variant : "");
});
// Bộ dữ liệu có kết quả hoặc có dung lượng Oracle đều có tab, dù chưa đo đủ.
const datasets = [...new Set([...D.entries.map(e => e.dataset), ...Object.keys(D.oracle)])].sort();
let current = datasets.includes("status") ? "status" : datasets[0];
if (!datasets.length) {
  document.querySelector("main").insertAdjacentHTML("beforeend",
    '<p class="warn">Chưa có kết quả nào trong results/. Chạy make seed, make dump và make find rồi chạy lại make summary.</p>');
}

function el(tag, attrs, text) {
  const n = document.createElementNS(NS, tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (text !== undefined) n.textContent = text;
  return n;
}
function niceMax(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}
function optionsOf(list) {
  const opts = [...new Set(list.map(e => e.option))];
  const rank = o => { const i = FORMAT_ORDER.indexOf(o.split(" · ")[0]); return i < 0 ? 99 : i; };
  return opts.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

// Biểu đồ thanh ngang. Mỗi nhóm là một phương án: tên phương án ở trên, bên dưới là thanh Java và
// thanh Go, mỗi thanh có nhãn ngôn ngữ ngay bên trái nên không cần nhìn chú thích màu.
// refs: vạch tham chiếu (giới hạn RAM pod, dung lượng Oracle), mỗi vạch có nhãn chữ.
function barChart(fig, list, metric, refs) {
  const fmt = FMT[metric];
  const opts = optionsOf(list);
  if (!opts.length) { fig.append(Object.assign(document.createElement("p"), { className: "empty", textContent: "Chưa có số đo." })); return; }
  const value = (o, l) => { const e = list.find(x => x.option === o && x.lang === l); return e ? e[metric] : null; };
  const vals = opts.flatMap(o => LANGS.map(([l]) => value(o, l))).filter(v => v != null);
  const max = niceMax(Math.max(...vals, ...refs.map(r => r.value)));
  const W = 360, labelW = 44, valueW = 70, barH = 14, gap = 3, headH = 17, groupGap = 12, top = refs.length ? 18 : 2, axisH = 20;
  const groupH = headH + 2 * barH + gap;
  const plotW = W - labelW - valueW;
  const H = top + opts.length * groupH + (opts.length - 1) * groupGap + axisH;
  const x = v => labelW + (v / max) * plotW;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": fig.querySelector("h3").textContent });
  const bottom = H - axisH;
  for (let i = 0; i <= 4; i++) {
    const v = max / 4 * i, gx = x(v);
    svg.append(el("line", { x1: gx, x2: gx, y1: top, y2: bottom, stroke: "var(--grid)", "stroke-width": 1 }));
    svg.append(el("text", { x: gx, y: H - 4, "text-anchor": i === 0 ? "start" : i === 4 ? "end" : "middle" }, fmt(+v.toPrecision(3))));
  }
  const tip = document.createElement("div"); tip.className = "tip"; tip.hidden = true; fig.append(tip);
  opts.forEach((o, gi) => {
    const gy = top + gi * (groupH + groupGap);
    svg.append(el("text", { class: "fmt", x: 0, y: gy + 12 }, o));
    LANGS.forEach(([l, name, color], li) => {
      const v = value(o, l);
      const y = gy + headH + li * (barH + gap);
      svg.append(el("rect", { x: 0, y: y + 3, width: 8, height: 8, rx: 2, fill: color }));
      svg.append(el("text", { x: 12, y: y + barH - 3 }, name));
      if (v == null) { svg.append(el("text", { x: labelW + 4, y: y + barH - 3 }, "chưa đo")); return; }
      const w = Math.max(x(v) - labelW, 2), r = Math.min(4, w / 2);
      const bar = el("path", { class: "bar", tabindex: 0, fill: color,
        d: `M${labelW},${y} h${w - r} a${r},${r} 0 0 1 ${r},${r} v${barH - 2 * r} a${r},${r} 0 0 1 ${-r},${r} h${-(w - r)} z` });
      const show = () => {
        tip.innerHTML = `<b>${name} · ${o}</b><br><span>${fig.querySelector("h3").textContent}:</span> ${fmt(v)}`;
        tip.hidden = false;
        const f = fig.getBoundingClientRect(), b = bar.getBoundingClientRect();
        tip.style.left = Math.max(4, Math.min(b.right - f.left + 6, f.width - tip.offsetWidth - 4)) + "px";
        tip.style.top = (b.top - f.top - tip.offsetHeight - 4) + "px";
      };
      bar.addEventListener("mouseenter", show); bar.addEventListener("focus", show);
      bar.addEventListener("mouseleave", () => tip.hidden = true); bar.addEventListener("blur", () => tip.hidden = true);
      svg.append(bar);
      svg.append(el("text", { class: "val", x: labelW + w + 5, y: y + barH - 3 }, fmt(v)));
    });
  });
  // Vạch tham chiếu; nhãn của vạch quá gần vạch trước thì gộp vào nhãn trước để khỏi đè nhau.
  let lastX = -Infinity, lastText = null;
  refs.forEach(r => {
    const rx = x(r.value);
    svg.append(el("line", { x1: rx, x2: rx, y1: top - 4, y2: bottom, stroke: r.color, "stroke-width": 1.5, "stroke-dasharray": "4 3" }));
    if (lastText && rx - lastX < 60) { lastText.textContent += " / " + r.label; return; }
    lastText = el("text", { class: "ref", x: rx, y: top - 7, "text-anchor": rx > W - 90 ? "end" : "middle", style: `fill:${r.color}` }, r.label);
    svg.append(lastText); lastX = rx;
  });
  fig.append(svg);
  // Chú thích riêng của biểu đồ, viết bằng chữ.
  const notes = [`<span><i style="background:var(--java)"></i>Java</span>`, `<span><i style="background:var(--go)"></i>Go</span>`];
  refs.forEach(r => notes.push(`<span><i class="dash" style="border-color:${r.color}"></i>${r.explain}</span>`));
  fig.insertAdjacentHTML("beforeend", `<div class="key">${notes.join("")}</div>`);
}
function figure(parent, title, note) {
  const f = document.createElement("figure");
  f.innerHTML = `<figcaption><h3>${title}</h3><span>${note}</span></figcaption>`;
  parent.append(f);
  return f;
}
// Biểu đồ tra cứu dùng số đo cache nóng (mặc định của make find); file -cold.json được bỏ qua ở đây.
const POD_REF = { value: D.podRamMb, label: "giới hạn pod", color: "var(--limit)",
  explain: `vạch đỏ: giới hạn RAM của pod (${D.podRamMb / 1024} GiB); thanh vượt vạch là không chạy được trong pod` };
const oracleRefs = o => o ? [
  { value: o.table, label: "Oracle", color: "var(--oracle)", explain: `vạch xám trái: cùng số dòng trong bảng Oracle (${fmtMb(o.table)})` },
  { value: o.withIndex, label: "+ index", color: "var(--oracle)", explain: `vạch xám phải: bảng Oracle cộng index (${fmtMb(o.withIndex)})` },
] : [];
const latestFor = (dataset, op, scenario) => D.entries.filter(e => e.dataset === dataset && e.op === op
  && (op === "dump" || (e.scenario === scenario && e.cache !== "cold")));
const label = e => `${e.lang === "java" ? "Java" : "Go"} ${e.option}`;
const best = (list, m) => list.filter(e => e[m] != null).sort((a, b) => a[m] - b[m])[0];

function oracleFor(dataset, rows) {
  const o = D.oracle[dataset];
  if (!o || !rows) return null;
  const scale = rows / o.rows;
  return { table: o.table_mb * scale, withIndex: (o.table_mb + o.index_mb) * scale };
}

function render() {
  document.querySelectorAll("#tabs button").forEach(b => b.setAttribute("aria-pressed", b.dataset.ds === current));
  const [title, desc] = DATASET_TEXT[current] || [`Bộ ${current}`, ""];
  document.getElementById("dataset-title").textContent = title;
  document.getElementById("dataset-desc").textContent = desc;

  const dumps = latestFor(current, "dump");
  const many = latestFor(current, "find", "1000");
  const one = latestFor(current, "find", "1");
  const answers = document.getElementById("answers");
  answers.innerHTML = "";
  const card = (q, e, m, extra) => {
    if (!e) return;
    answers.insertAdjacentHTML("beforeend", `<div class="answer"><div class="q">${q}</div><div class="a">${label(e)}</div><div class="d">${FMT[m](e[m])}${extra ? " · " + extra : ""}</div></div>`);
  };
  const fastMany = best(many, "wall"), slowMany = many.slice().sort((a, b) => b.wall - a.wall)[0];
  card("Tra 1000 key nhanh nhất", fastMany, "wall", fastMany && slowMany ? `chậm nhất ${FMT.wall(slowMany.wall)}` : "");
  card("Tra 1 key nhanh nhất", best(one, "wall"), "wall");
  const small = best(dumps, "file"), big = dumps.slice().sort((a, b) => b.file - a.file)[0];
  const o = small && oracleFor(current, small.rows);
  card("File nhỏ nhất", small, "file", o ? `${nf(o.table / small.file, 1)} lần nhỏ hơn bảng Oracle` : (big ? `lớn nhất ${FMT.file(big.file)}` : ""));
  card("Dump tốn ít RAM nhất", best(dumps, "ram"), "ram");

  const over = D.entries.filter(e => e.dataset === current && e.ram > D.podRamMb);
  const wrong = D.entries.filter(e => e.dataset === current && !e.ok);
  const warn = document.getElementById("warnings");
  warn.innerHTML = "";
  if (over.length) warn.insertAdjacentHTML("beforeend", `<p class="warn">Vượt giới hạn RAM pod ${fmtMb(D.podRamMb)}: ${over.map(e => `${label(e)} (${e.op === "dump" ? "dump" : "tra " + e.scenario}, ${fmtMb(e.ram)})`).join("; ")}.</p>`);
  SCENARIOS.forEach(([sc, name]) => {
    const ids = [...new Set(latestFor(current, "find", sc).map(e => e.sample).filter(Boolean))];
    if (ids.length > 1) warn.insertAdjacentHTML("beforeend", `<p class="warn">${name}: các phương án được đo bằng ${ids.length} bộ key khác nhau (${ids.join(", ")}), so sánh chưa công bằng. Chạy lại make find sau cùng một make sample.</p>`);
  });
  if (wrong.length) warn.insertAdjacentHTML("beforeend", `<p class="warn">Kết quả sai: ${wrong.map(e => `${label(e)} ${e.op} ${e.scenario}`).join("; ")}. Xem output.log trong results/runs/.</p>`);

  const findBox = document.getElementById("find-charts");
  findBox.innerHTML = "";
  SCENARIOS.forEach(([sc, name, note]) => {
    const list = latestFor(current, "find", sc);
    if (!list.length) return;
    const row = document.createElement("div"); row.className = "charts";
    barChart(figure(row, `${name} · thời gian`, `${note} Giây, thấp hơn là tốt.`), list, "wall", []);
    barChart(figure(row, `${name} · RAM đỉnh`, "MB, thấp hơn là tốt."), list, "ram", [POD_REF]);
    findBox.append(row);
  });
  if (!findBox.children.length) findBox.innerHTML = '<p class="empty">Chưa có kết quả tra cứu cho bộ này. Chạy make find.</p>';

  const sizeBox = document.getElementById("size-charts");
  sizeBox.innerHTML = "";
  const rows = dumps.length ? Math.max(...dumps.map(e => e.rows || 0)) : 0;
  const ora = oracleFor(current, rows);
  const refs = oracleRefs(ora);
  barChart(figure(sizeBox, "Dung lượng file archive", `${rows ? nf(rows, 0) + " dòng" : ""}${ora ? ` · Oracle: bảng ${fmtMb(ora.table)}, bảng + index ${fmtMb(ora.withIndex)}` : ""}. Thấp hơn là tốt.`), dumps, "file", refs);

  const dumpBox = document.getElementById("dump-charts");
  dumpBox.innerHTML = "";
  barChart(figure(dumpBox, "Thời gian dump", "Giây. Phần lớn là đọc Oracle."), dumps, "wall", []);
  barChart(figure(dumpBox, "RAM đỉnh khi dump", "MB, thấp hơn là tốt."), dumps, "ram", [POD_REF]);

  renderTable();
}

// So hai bộ dữ liệu: dung lượng mỗi 1M dòng, mỗi bộ một biểu đồ cùng thang.
function renderCompare() {
  const box = document.getElementById("compare-charts");
  const per = e => ({ ...e, file: e.file / (e.rows / 1e6) });
  const all = datasets.map(ds => latestFor(ds, "dump").filter(e => e.file && e.rows).map(per));
  const oraPerM = ds => oracleFor(ds, 1e6);
  datasets.forEach((ds, i) => {
    const o = oraPerM(ds);
    const refs = oracleRefs(o);
    barChart(figure(box, `Bộ ${ds} · mỗi 1M dòng`, o ? `Oracle: bảng ${fmtMb(o.table)}, bảng + index ${fmtMb(o.withIndex)}` : "Chưa có dung lượng Oracle."), all[i], "file", refs);
  });
}

function renderTable() {
  const cols = [["wall", "Wall"], ["cpu", "CPU"], ["ram", "RAM đỉnh"], ["file", "File"], ["runs", "Lần"]];
  const table = document.getElementById("table");
  table.innerHTML = "<thead><tr><th>Phương án</th>" + cols.map(c => `<th>${c[1]}</th>`).join("") + "<th>Khớp Oracle</th><th>Kết quả</th></tr></thead>";
  const body = document.createElement("tbody");
  const groups = [["dump", null, "Dump"], ...SCENARIOS.map(([sc, name]) => ["find", sc, name])];
  groups.forEach(([op, sc, name]) => {
    const list = latestFor(current, op, sc);
    if (!list.length) return;
    body.insertAdjacentHTML("beforeend", `<tr class="group"><td colspan="${cols.length + 3}">${name}</td></tr>`);
    // Mỗi cột: nhỏ nhất là tốt nhất (xanh lá), lớn nhất là tệ nhất (đỏ).
    const b = {}, w = {};
    cols.forEach(([m]) => {
      const vs = list.map(e => e[m]).filter(v => v != null);
      b[m] = Math.min(...vs); w[m] = Math.max(...vs);
    });
    const order = optionsOf(list);
    list.slice().sort((a, x) => order.indexOf(a.option) - order.indexOf(x.option) || (a.lang === "java" ? -1 : 1)).forEach(e => {
      const cells = cols.map(([m]) => {
        const v = e[m];
        if (v == null) return "<td>–</td>";
        if (m === "runs") return `<td>${v}</td>`;
        const cls = list.length < 2 || b[m] === w[m] ? "" : v === b[m] ? "best" : v === w[m] ? "worst" : "";
        const over = m === "ram" && v > D.podRamMb ? ` <span class="over" title="vượt giới hạn RAM pod">⚠ vượt pod</span>` : "";
        return `<td class="${cls}">${FMT[m](v)}${over}</td>`;
      }).join("");
      const result = e.op === "dump" ? `${nf(e.rows, 0)} dòng` : `${e.found} tìm thấy`;
      // Số bản ghi trả về khớp từng cột với Oracle; "–" là kết quả đo trước khi có bước kiểm này.
      const integrity = e.checked == null ? "<td>–</td>"
        : `<td class="${e.matched === e.checked ? "best" : "worst"}">${nf(e.matched ?? 0, 0)}/${nf(e.checked, 0)}</td>`;
      body.insertAdjacentHTML("beforeend", `<tr><td>${label(e)}</td>${cells}${integrity}<td class="${e.ok ? "" : "bad"}">${e.ok ? result : "SAI · " + result}</td></tr>`);
    });
  });
  table.append(body);
  const settings = document.getElementById("settings");
  settings.innerHTML = D.entries.filter(e => e.dataset === current && e.settings)
    .map(e => `<li><code>${label(e)} · ${e.op}${e.scenario ? " " + e.scenario : ""}</code>: <code>${e.settings}</code> (${e.date})</li>`).join("");
}

document.getElementById("meta").innerHTML = [
  `Cập nhật ${D.updated}`, D.machine,
  ...datasets.map(ds => D.oracle[ds] ? `Oracle ${ds}: ${nf(D.oracle[ds].rows, 0)} dòng = ${fmtMb(D.oracle[ds].table_mb)} + index ${fmtMb(D.oracle[ds].index_mb)}` : ""),
].filter(Boolean).map(t => `<span>${t}</span>`).join("");
const tabs = document.getElementById("tabs");
datasets.forEach(ds => {
  const b = document.createElement("button");
  b.type = "button"; b.dataset.ds = ds; b.textContent = ds;
  b.addEventListener("click", () => { current = ds; render(); });
  tabs.append(b);
});
if (datasets.length < 2) { tabs.hidden = true; document.getElementById("tabs-label").hidden = true; }
if (datasets.length) { render(); renderCompare(); }
</script>
</body>
</html>
"""
