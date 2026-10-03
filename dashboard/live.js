/* Below One live board: SSE-driven graph, freeze rings, sourced counters. */
"use strict";

const POISON = "#ff5d5d", CLEAN = "#4ecf8d", DIM = "#39434e";
const params = new URLSearchParams(location.search);

if (params.get("mode") === "split") {
  document.body.dataset.mode = "split";
  const link = document.getElementById("mode-link");
  link.href = "/";
  link.textContent = "live view";
}

const cy = cytoscape({
  container: document.getElementById("graph"),
  style: [
    { selector: "node", style: {
      "background-color": CLEAN, label: "data(id)",
      color: "#e8edf2", "font-size": 11, width: 26, height: 26 } },
    { selector: "node.frozen", style: {
      "background-color": POISON,
      "border-width": 3, "border-color": POISON } },
    { selector: "node.infected", style: { "background-color": POISON } },
    { selector: "edge", style: {
      width: 1, "line-color": DIM, "curve-style": "bezier",
      "target-arrow-shape": "triangle", "arrow-color": DIM } },
  ],
  layout: { name: "grid", fit: true, padding: 40 },
});
// The one authored moment: a frozen agent pulses its trace ring.
function pulse(node) {
  node.animate({ style: { "border-width": 8 } },
    { duration: 200, easing: "ease-out" })
    .animate({ style: { "border-width": 3 } },
      { duration: 400, easing: "ease-out" });
}

function applySnapshot(snap) {
  document.getElementById("synthetic-badge").classList.toggle("on", !!snap.synthetic);
  const els = Object.keys(snap.agents).map((id) => ({
    group: "nodes", data: { id }, classes: snap.agents[id] === "clean" ? "" : snap.agents[id],
  }));
  cy.elements().remove();
  cy.add(els.length ? els : [{ group: "nodes", data: { id: "—" } }]);
  cy.layout({ name: "grid", fit: true, padding: 40 }).run();
  renderCounters(snap.counters);
}

function renderCounters(rows) {
  const el = document.getElementById("counters");
  el.innerHTML = "";
  for (const r of rows) {
    const div = document.createElement("div");
    div.className = "row";
    const v = r.value === null || r.value === undefined ? "—" : r.value;
    div.innerHTML = `<span>${r.label}</span><span class="v">${
      typeof v === "number" ? +v.toFixed(4) : v}</span> ` +
      `<span class="src">${r.source}</span>`;
    el.appendChild(div);
  }
}

function tick(ev) {
  const t = document.getElementById("ticker");
  const div = document.createElement("div");
  const who = ev.agent_id ?? "—";
  div.className = ev.kind === "freeze" ? "freeze"
    : ev.kind === "infection" ? "infection"
    : ev.kind === "release" || ev.kind === "steer" ? "release" : "";
  div.innerHTML = `<b>${who}</b> ${ev.kind} @ ${fmt(ev.elapsed)}`;
  t.prepend(div);
  if (ev.kind === "freeze") {
    const n = cy.getElementById(ev.agent_id);
    if (n.nonempty()) { n.addClass("frozen"); pulse(n); }
  }
  if (ev.kind === "infection") {
    const n = cy.getElementById(ev.agent_id);
    if (n.nonempty()) n.addClass("infected");
  }
}

const fmt = (s) => (typeof s === "number" ? s.toFixed(1) : "?") + "s";

// Shared relative clock: scrub + animation in live and split panels.
const scrub = document.getElementById("scrub");
const clock = document.getElementById("clock");
let maxElapsed = 1;
function setClock(t) {
  clock.textContent = `t = ${fmt(t)} (relative)`;
  scrub.value = Math.round((t / maxElapsed) * 1000);
}
scrub.addEventListener("input", () => {
  const t = (scrub.value / 1000) * maxElapsed;
  clock.textContent = `t = ${fmt(t)} (relative)`;
  document.dispatchEvent(new CustomEvent("clock", { detail: t }));
});

async function runLive(run) {
  const res = await fetch(`/events?run=${encodeURIComponent(run)}`);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  const events = [];
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
      const data = chunk.split("\n").filter((l) => l.startsWith("data: "))
        .map((l) => l.slice(6)).join("");
      if (!data) continue;
      if (data === "[done]") continue;
      try {
        const obj = JSON.parse(data);
        if (obj.synthetic !== undefined || obj.counters) {
          document.getElementById("run-name").textContent =
            `${run}${obj.synthetic ? " (fixture)" : ""}`;
          applySnapshot(obj);
        } else { events.push(obj); tick(obj); }
      } catch { /* skip malformed */ }
    }
  }
  if (events.length) {
    maxElapsed = Math.max(...events.map((e) => e.elapsed || 0), 1);
    replayTimeline(events);
  }
}

// Step events along the shared clock; the scrub re-derives state.
function replayTimeline(events) {
  const byT = [...events].sort((a, b) => (a.elapsed || 0) - (b.elapsed || 0));
  let i = 0;
  document.addEventListener("clock", (e) => {
    const t = e.detail;
    while (i < byT.length && (byT[i].elapsed || 0) <= t) { tick(byT[i]); i++; }
    while (i > 0 && (byT[i - 1].elapsed || 0) > t) { i--; }
  });
  const dur = Math.min(maxElapsed * 100, 30000);
  const t0 = performance.now();
  (function step(now) {
    const t = ((now - t0) / dur) * maxElapsed;
    setClock(t);
    if (t < maxElapsed) requestAnimationFrame(step);
  })(t0);
}

// In split mode the race panels own the shared clock (split.js); no default
// live run is streamed.
if (params.get("mode") !== "split") {
  runLive(params.get("run") || "fixture-outbreak");
}
