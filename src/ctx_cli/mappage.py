"""The one page `ctx map` serves: inline HTML, CSS and JS, no network
requests beyond the command's own /api endpoints (the venue wifi is unknown).

Sized for a projector: designed at 1280x720, type 18px and up, the two
numbers that matter at 44px, light and dark (follows the system; `t` or the
button flips it). Every string from the data is set with textContent."""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ctx map</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {
  --bg: #fbfaf7; --panel: #ffffff; --ink: #14181f; --muted: #5a6270; --line: #d9d6cc;
  --current: #0b5fd6; --old: #aeb4bf; --hit: #d9480f; --drop: #e8a317; --weak: #7a3fc4;
  --miss: #c92a2a; --good: #1b7f3b; --link: #8f9bb0;
}
:root[data-theme="dark"] {
  --bg: #0e1116; --panel: #171b22; --ink: #f2f4f8; --muted: #a3acb9; --line: #2c333f;
  --current: #5aa2ff; --old: #4a5260; --hit: #ff8a4c; --drop: #f4c04a; --weak: #b794f4;
  --miss: #ff6b6b; --good: #5ad38a; --link: #5d6a80;
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; background: var(--bg); color: var(--ink);
  font: 18px/1.25 -apple-system, "Segoe UI", system-ui, sans-serif; }
body { display: grid; grid-template-rows: auto auto 1fr auto; height: 100vh; overflow: hidden; }
header { display: flex; align-items: baseline; gap: 18px; padding: 8px 16px 0; }
header h1 { margin: 0; font-size: 28px; }
header .sub { color: var(--muted); font-size: 18px; flex: 1; }
button { font: inherit; background: var(--panel); color: var(--ink); border: 1px solid var(--line);
  border-radius: 6px; padding: 2px 10px; cursor: pointer; }
form { display: flex; gap: 10px; padding: 8px 16px; }
input[type=text] { flex: 1; font: inherit; font-size: 24px; padding: 6px 12px; color: var(--ink);
  background: var(--panel); border: 2px solid var(--line); border-radius: 8px; }
input[type=text]:focus { outline: none; border-color: var(--current); }
form button { font-size: 24px; padding: 6px 22px; background: var(--current); color: var(--bg);
  border-color: var(--current); font-weight: 600; }
main { display: grid; grid-template-columns: 1fr 420px; gap: 12px; padding: 0 16px; min-height: 0; }
#mapwrap { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; min-height: 0; }
#map { width: 100%; height: 100%; display: block; }
#side { display: flex; flex-direction: column; gap: 8px; min-height: 0; overflow: hidden; }
#numbers { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.num { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 6px 12px; }
.num b { display: block; font-size: 44px; line-height: 1.05; }
.num span { color: var(--muted); font-size: 18px; }
#ctxnum b { color: var(--good); }
#grepnum b { color: var(--hit); }
#verdict { font-size: 22px; font-weight: 600; min-height: 28px; }
#verdict.miss { color: var(--miss); }
#hits { overflow: auto; min-height: 0; flex: 1; }
.hit { padding: 4px 0; border-top: 1px solid var(--line); font-size: 18px; }
.hit .t { font-weight: 600; }
.hit .id { color: var(--muted); font-size: 16px; }
.hit.kept .t { color: var(--hit); }
.hit.dropped .t { color: var(--drop); }
.hit.weak .t { color: var(--weak); }
.tag { font-size: 14px; text-transform: uppercase; letter-spacing: .05em; margin-right: 6px; }
#strip { padding: 4px 16px 8px; }
#strip .cap { color: var(--muted); font-size: 16px; display: flex; gap: 16px; }
#strip svg { width: 100%; height: 86px; display: block; }
.rigname { font-size: 20px; font-weight: 700; fill: var(--ink); }
.corpname { font-size: 16px; fill: var(--muted); }
.dot { fill: var(--current); }
.dot.old { fill: var(--old); }
.link { stroke: var(--link); stroke-width: 1.4; opacity: .8; }
.link.oldlink { stroke-dasharray: 3 2; opacity: .6; }
body.q .dot:not(.kept):not(.dropped):not(.weak) { opacity: .28; }
body.q .link { opacity: .25; }
.dot.kept { fill: var(--hit); }
.dot.dropped { fill: var(--drop); }
.dot.weak { fill: var(--weak); }
.ring { fill: none; stroke-width: 3; }
.ring.kept { stroke: var(--hit); } .ring.dropped { stroke: var(--drop); stroke-dasharray: 3 2; }
.ring.weak { stroke: var(--weak); stroke-dasharray: 2 2; }
#examples { position: fixed; top: 112px; left: 16px; width: 760px; background: var(--panel);
  border: 2px solid var(--current); border-radius: 10px; padding: 8px 14px; z-index: 5; display: none;
  box-shadow: 0 8px 30px rgba(0,0,0,.35); }
#examples.open { display: block; }
#examples h2 { margin: 0 0 4px; font-size: 20px; color: var(--muted); font-weight: 600; }
#examples button { display: block; width: 100%; text-align: left; border: 0; border-top: 1px solid var(--line);
  border-radius: 0; padding: 7px 4px; }
#examples button:hover, #examples button:focus { background: var(--bg); outline: none; }
#examples .eq { font-size: 24px; font-weight: 600; }
#examples .en { display: block; color: var(--muted); font-size: 18px; }
#error { color: var(--miss); font-size: 20px; font-weight: 600; }
#perrig { color: var(--muted); font-size: 15px; }
.legend { color: var(--muted); font-size: 16px; }
.legend i { display: inline-block; width: 12px; height: 12px; border-radius: 6px; margin: 0 4px 0 12px;
  vertical-align: -1px; }
</style>
</head>
<body>
<header>
  <h1>ctx map</h1>
  <div class="sub" id="sub">loading…</div>
  <div class="legend"><i style="background:var(--current)"></i>current<i style="background:var(--old)"></i>superseded<i style="background:var(--hit)"></i>returned<i style="background:var(--drop)"></i>dropped<i style="background:var(--weak)"></i>weak lead</div>
  <button id="help" type="button" title="example queries (?)">?</button>
  <button id="theme" type="button" title="light / dark (t)">light / dark</button>
</header>
<form id="qform">
  <input type="text" id="q" placeholder="ask the map: where did we decide…" autocomplete="off" autofocus>
  <button type="submit">ask</button>
</form>
<div id="examples" role="dialog" aria-label="example queries"></div>
<main>
  <div id="mapwrap"><svg id="map" xmlns="http://www.w3.org/2000/svg"></svg></div>
  <div id="side">
    <div id="numbers">
      <div class="num" id="ctxnum"><b>–</b><span>tokens ctx returned</span></div>
      <div class="num" id="grepnum"><b>–</b><span id="grepcap">tokens a grep reads</span></div>
    </div>
    <div id="verdict"></div>
    <div id="error"></div>
    <div id="hits"></div>
    <div id="perrig"></div>
  </div>
</main>
<section id="strip">
  <div class="cap"><span id="stripcap">logged queries per day</span><span id="misscap"></span></div>
  <svg id="stripsvg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1248 86" preserveAspectRatio="none"></svg>
</section>
<script>
"use strict";
const NS = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);
let MAP = null, HIST = null;
const dotEls = new Map();   // "rig|id" -> circle

function el(tag, attrs, text) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (text !== undefined) e.textContent = text;
  return e;
}

/* theme: system by default, `t` flips and remembers */
function setTheme(t) { document.documentElement.dataset.theme = t; try { localStorage.setItem("ctxmap", t); } catch (e) {} }
(function () {
  let t = null; try { t = localStorage.getItem("ctxmap"); } catch (e) {}
  const forced = new URLSearchParams(location.search).get("theme");
  if (forced === "light" || forced === "dark") t = forced;
  if (!t) t = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  setTheme(t);
})();
function flipTheme() { setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"); }
$("theme").onclick = flipTheme;
document.addEventListener("keydown", (e) => {
  if (e.key === "t" && document.activeElement !== $("q")) flipTheme();
});

/* pack n dots into a w x h box with the largest pitch that fits */
function pack(n, w, h) {
  for (let p = 18; p >= 3; p -= 0.5) {
    const cols = Math.max(1, Math.floor(w / p));
    if (Math.ceil(n / cols) * p <= h) return { p, cols };
  }
  return { p: 3, cols: Math.max(1, Math.floor(w / 3)) };
}

/* "~/gt/research/crew/ivan" -> "research"; "~/gt" -> "gt" */
function shortName(label) {
  const parts = label.split("/"), i = parts.indexOf("crew");
  return i > 0 ? parts[i - 1] : parts[parts.length - 1];
}

function drawMap() {
  const svg = $("map"), W = 900, H = 500;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.replaceChildren(); dotEls.clear();
  const rigs = MAP.rigs;
  const total = rigs.reduce((s, r) => s + r.corpora.reduce((a, c) => a + c.docs.length, 0), 0) || 1;
  const gap = 16, usable = W - gap * (rigs.length + 1);
  let x = gap;
  rigs.forEach((rig) => {
    const n = rig.corpora.reduce((a, c) => a + c.docs.length, 0);
    const rw = Math.max(120, usable * (n / total));
    svg.appendChild(el("text", { x: x, y: 24, class: "rigname" },
      shortName(rig.label) + " · " + n));
    if (rig.error) svg.appendChild(el("text", { x: x, y: 50, class: "corpname" }, rig.error));
    const cs = rig.corpora.filter((c) => c.docs.length);
    const avail = H - 40 - 22 * cs.length;
    let y = 36;
    cs.forEach((c) => {
      const bh = Math.max(34, avail * (c.docs.length / (n || 1)));
      svg.appendChild(el("text", { x: x, y: y + 14, class: "corpname" }, c.name + " " + c.docs.length));
      const top = y + 22, { p, cols } = pack(c.docs.length, rw, bh);
      const pos = new Map();
      c.docs.forEach((d, i) => pos.set(d.id, [x + (i % cols) * p + p / 2, top + Math.floor(i / cols) * p + p / 2]));
      const oldIds = new Set(c.links.map((l) => l.to));
      c.links.forEach((l) => {
        const a = pos.get(l.from), b = pos.get(l.to);
        if (a && b) svg.appendChild(el("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1],
          class: "link" + (oldIds.has(l.from) ? " oldlink" : "") }));
      });
      c.docs.forEach((d) => {
        const [cx, cy] = pos.get(d.id);
        const dot = el("circle", { cx: cx, cy: cy, r: Math.max(1.8, p * 0.36),
          class: "dot" + (d.current ? "" : " old") });
        dot.appendChild(el("title", {}, d.id + " — " + d.title));
        svg.appendChild(dot);
        dotEls.set(rig.label + "|" + d.id, dot);
      });
      y = top + Math.ceil(c.docs.length / cols) * p + 8;
    });
    x += rw + gap;
  });
  const docs = rigs.reduce((s, r) => s + r.corpora.reduce((a, c) => a + c.docs.length, 0), 0);
  const stale = rigs.filter((r) => r.stale).length;
  $("sub").textContent = rigs.length + " rigs · " + docs + " documents" +
    (stale ? " · " + stale + " stale" : "");
}

function clearMarks() {
  document.body.classList.remove("q");
  document.querySelectorAll(".ring").forEach((r) => r.remove());
  dotEls.forEach((d) => d.classList.remove("kept", "dropped", "weak"));
}
function mark(rigLabel, hits, cls) {
  hits.forEach((h) => {
    const d = dotEls.get(rigLabel + "|" + h.id);
    if (!d) return;
    d.classList.add(cls);
    const r = el("circle", { cx: d.getAttribute("cx"), cy: d.getAttribute("cy"),
      r: Number(d.getAttribute("r")) + 4, class: "ring " + cls });
    d.parentNode.appendChild(r);
  });
}

function fmt(n) { return n.toLocaleString("en-US"); }

function showResult(res) {
  clearMarks();
  document.body.classList.add("q");
  res.rigs.forEach((r) => {
    mark(r.label, r.dropped, "dropped");
    mark(r.label, r.weak, "weak");
    mark(r.label, r.kept, "kept");
  });
  $("error").textContent = "";
  $("ctxnum").firstChild.textContent = fmt(res.ctx_tokens);
  $("grepnum").firstChild.textContent = fmt(res.grep_tokens);
  $("grepcap").textContent = "tokens a grep reads · " + fmt(res.grep_files) + " files match";
  const v = $("verdict");
  const kept = res.hits.filter((h) => h.kind === "kept").length;
  v.className = res.miss ? "miss" : "";
  v.textContent = res.miss ? "MISS — ctx has nothing for this; write the note"
    : kept + " returned" + (res.ctx_tokens && res.grep_tokens ? " · " +
      Math.max(1, Math.round(res.grep_tokens / res.ctx_tokens)) + "× less than grep" : "");
  const box = $("hits"); box.replaceChildren();
  const where = (rigs) => rigs.map(shortName).join(" + ");
  const tags = { kept: ["kept", "hit"], dropped: ["dropped", "dropped"], weak: ["weak", "weak lead"] };
  res.hits.forEach((h) => {
    const row = document.createElement("div"); row.className = "hit " + tags[h.kind][0];
    const t = document.createElement("div"); t.className = "t";
    const g = document.createElement("span"); g.className = "tag"; g.textContent = tags[h.kind][1];
    t.append(g, h.title || h.id);
    const id = document.createElement("div"); id.className = "id";
    id.textContent = h.id + " · " + where(h.rigs);
    row.append(t, id); box.appendChild(row);
  });
  const per = res.rigs.map((r) => shortName(r.label) + ": ctx " + fmt(r.ctx_tokens) + " tokens").join("  ·  ");
  $("perrig").textContent = "per rig (shared docs count in each) — " + per +
    (res.grep_unreadable ? "  ·  " + res.grep_unreadable + " unreadable file(s) skipped" : "");
}

function showError(msg) {
  clearMarks();
  $("verdict").textContent = ""; $("hits").replaceChildren(); $("perrig").textContent = "";
  $("ctxnum").firstChild.textContent = "–"; $("grepnum").firstChild.textContent = "–";
  $("error").textContent = "Could not run that query: " + msg;
}

async function ask(q) {
  if (!q.trim()) return;
  try {
    const resp = await fetch("/api/query?q=" + encodeURIComponent(q));
    const res = await resp.json();
    if (!resp.ok || res.error) { showError(res.error || ("HTTP " + resp.status)); return; }
    showResult(res);
  } catch (e) {
    showError(String(e));
  }
}

/* the "?" panel: curated queries, one click to run, nothing typed on stage */
async function loadExamples() {
  let list = [];
  try { list = (await (await fetch("/api/examples")).json()).examples || []; } catch (e) {}
  const box = $("examples");
  const h = document.createElement("h2"); h.textContent = "Try one (click to run)";
  box.replaceChildren(h);
  list.forEach((ex) => {
    const b = document.createElement("button"); b.type = "button";
    const q = document.createElement("span"); q.className = "eq"; q.textContent = ex.q;
    const n = document.createElement("span"); n.className = "en"; n.textContent = ex.note;
    b.append(q, n);
    b.onclick = () => { closeExamples(); $("q").value = ex.q; ask(ex.q); };
    box.appendChild(b);
  });
}
function closeExamples() { $("examples").classList.remove("open"); $("q").focus(); }
function toggleExamples() {
  const box = $("examples");
  if (box.classList.toggle("open")) { const b = box.querySelector("button"); if (b) b.focus(); }
}
$("help").onclick = toggleExamples;
document.addEventListener("keydown", (e) => {
  const inBox = document.activeElement === $("q");
  if (e.key === "?" && (!inBox || !$("q").value)) { e.preventDefault(); toggleExamples(); }
  else if (e.key === "Escape") closeExamples();
});
$("qform").onsubmit = (e) => { e.preventDefault(); ask($("q").value); };

/* the logged-history strip: queries per day, misses in red on top */
function drawStrip() {
  const svg = $("stripsvg"); svg.replaceChildren();
  const day = (ts) => ts.slice(0, 10);
  const qs = {}, ms = {};
  HIST.queries.forEach((r) => { qs[day(r.ts)] = (qs[day(r.ts)] || 0) + 1; });
  HIST.misses.forEach((r) => { ms[day(r.ts)] = (ms[day(r.ts)] || 0) + 1; });
  const days = Object.keys(qs).concat(Object.keys(ms)).sort().filter((d, i, a) => a.indexOf(d) === i);
  $("misscap").textContent = HIST.queries.length + " queries · " + HIST.misses.length +
    " misses (red) — a miss you can see is a note you can write";
  if (!days.length) return;
  const first = new Date(days[0]), last = new Date(days[days.length - 1]);
  const span = Math.round((last - first) / 864e5) + 1;
  const max = Math.max(...days.map((d) => qs[d] || 0), 1);
  const bw = 1248 / span;
  days.forEach((d) => {
    const i = Math.round((new Date(d) - first) / 864e5);
    const h = ((qs[d] || 0) / max) * 70, mh = Math.min(h, ((ms[d] || 0) / max) * 70);
    const bar = el("rect", { x: i * bw + 1, y: 80 - h, width: Math.max(1, bw - 2), height: h, fill: "var(--current)", opacity: .75 });
    bar.appendChild(el("title", {}, d + ": " + (qs[d] || 0) + " queries, " + (ms[d] || 0) + " misses"));
    svg.appendChild(bar);
    if (mh) svg.appendChild(el("rect", { x: i * bw + 1, y: 80 - mh, width: Math.max(1, bw - 2), height: mh, fill: "var(--miss)" }));
  });
  svg.appendChild(el("line", { x1: 0, y1: 80.5, x2: 1248, y2: 80.5, stroke: "var(--line)" }));
  $("stripcap").textContent = "logged queries per day · " + days[0] + " to " + days[days.length - 1];
}

(async function () {
  [MAP, HIST] = await Promise.all([fetch("/api/map").then((r) => r.json()), fetch("/api/history").then((r) => r.json())]);
  drawMap(); drawStrip(); loadExamples();
  const p = new URLSearchParams(location.search).get("q");
  if (p) { $("q").value = p; ask(p); }
})();
</script>
</body>
</html>
"""
