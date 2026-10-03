/* Below One live board: SSE-driven graph, freeze rings, sourced counters. */
"use strict";

const POISON = "#ff5d5d", CLEAN = "#4ecf8d", DIM = "#39434e", FROZEN = "#ffd27a";
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
    { selector: "node.infected", style: {
      "background-color": POISON, label: "data(id)" } },
    { selector: "node.frozen", style: {
      "background-color": "#3a3325", "border-width": 3,
      "border-color": FROZEN, color: FROZEN } },
    { selector: "edge", style: {
      width: 1.5, "line-color": DIM, "curve-style": "bezier",
      "target-arrow-shape": "triangle", "arrow-color": DIM,
      label: "data(path)", "font-size": 8, color: "#5c6b7a",
      "text-background-color": "#101418", "text-background-opacity": 1 } },
  ],
  layout: { name: "grid", fit: true, padding: 40 },
});
function refit() { cy.resize(); cy.layout({ name: "cose", fit: true, padding: 40 }).run(); }
window.addEventListener("resize", refit);
// The one authored moment: a frozen agent pulses its trace ring.
function pulse(node) {
  node.animate({ style: { "border-width": 8 } },
    { duration: 200, easing: "ease-out" })
    .animate({ style: { "border-width": 3 } },
      { duration: 400, easing: "ease-out" });
}

function normalizeSnapshot(snap) {
  // Engine (U7) shape: {agents, states, graph:{nodes,edges}, counters,...};
  // fixture/dev shape: {agents, edges, counters,...}. Both normalized to
  // {agents, edges:[{source,target,path}], counters}.
  if (snap.graph) {
    const nodeId = (n) => String(n.id ?? n).replace(/^agent:/, "");
    const agentNodes = (snap.graph.nodes || []).map(nodeId)
      .filter((id) => !String(id).startsWith("file:"));
    const edges = (snap.graph.edges || []).map((e) => ({
      source: nodeId(e.source), target: nodeId(e.target),
      path: e.path ?? "",
    })).filter((e) => agentNodes.includes(e.source)
                   && agentNodes.includes(e.target));
    const agents = snap.agents
      || Object.fromEntries(agentNodes.map((id) => [id, snap.states?.[id] ?? "clean"]));
    return { synthetic: !!snap.synthetic, agents, edges,
             counters: snap.counters || [],
             controls: snap.controls || [],
             time_since_poisoning_s: snap.time_since_poisoning_s ?? null,
             events: snap.events || [] };
  }
  return snap;
}

function applySnapshot(snap) {
  snap = normalizeSnapshot(snap);
  document.getElementById("synthetic-badge").classList.toggle("on", !!snap.synthetic);
  const els = Object.keys(snap.agents).map((id) => ({
    group: "nodes", data: { id }, classes: snap.agents[id] === "clean" ? "" : snap.agents[id],
  }));
  for (const [i, e] of (snap.edges || []).entries()) {
    els.push({ group: "edges", data: {
      id: `e${i}`, source: e.source, target: e.target, path: e.path } });
  }
  cy.elements().remove();
  cy.add(els.length ? els : [{ group: "nodes", data: { id: "—" } }]);
  refit();
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
  const elapsed = ev.elapsed ?? (ev.payload || {}).elapsed;
  ev = { ...ev, elapsed };
  div.className = ev.kind === "freeze" ? "freeze"
    : ev.kind === "infection" ? "infection"
    : ev.kind === "release" || ev.kind === "steer" ? "release" : "";
  div.innerHTML = `<b>${who}</b> ${ev.kind} @ ${fmt(elapsed)}`
    + (ev.seq ? ` <span class="src">seq:${ev.seq}</span>` : "");
  t.prepend(div);
  if (ev.kind === "freeze") {
    const n = cy.getElementById(ev.agent_id);
    if (n.nonempty()) { n.addClass("frozen"); pulse(n); }
  }
  if (ev.kind === "infection") {
    const n = cy.getElementById(ev.agent_id);
    if (n.nonempty()) n.addClass("infected");
  }
  if (ev.kind === "trace") {
    const n = cy.getElementById(ev.agent_id);
    if (n.nonempty()) pulse(n);  // trace ring moment
  }
  if (ev.kind === "release") {
    const n = cy.getElementById(ev.agent_id);
    if (n.nonempty()) n.removeClass("frozen");
  }
  updateR24(ev);
}

// R24 live counters, computed ONLY from retained events/controls; citations
// name the contributing seq range, never a new metric file.
const r24 = { infected: new Set(), frozen: new Set(), released: new Set(),
  children: {}, poisoned_at: null, first_seq: null, last_seq: null };

function updateR24(ev) {
  if (r24.poisoned_at === null && ev.kind === "infection")
    r24.poisoned_at = ev.elapsed;
  if (ev.seq !== null && ev.seq !== undefined) {
    if (r24.first_seq === null) r24.first_seq = ev.seq;
    r24.last_seq = ev.seq;
  }
  if (ev.kind === "infection") {
    r24.infected.add(ev.agent_id);
    const src = ev.payload?.source_agent ?? ev.source_agent ?? null;
    if (src) (r24.children[src] ??= new Set()).add(ev.agent_id);
  }
  if (ev.kind === "freeze") r24.frozen.add(ev.agent_id);
  if (ev.kind === "release") {
    r24.frozen.delete(ev.agent_id);
    r24.released.add(ev.agent_id);
  }
  const el = document.getElementById("r24");
  if (!el) return;
  const n_infected = r24.infected.size;
  // mean secondary infections PER INFECTED AGENT (KTD4): only children of
  // infected agents count; a clean forwarder is not an infected source.
  const live_children = [...r24.infected]
    .reduce((sum, a) => sum + (r24.children[a]?.size ?? 0), 0);
  const live_r = n_infected ? live_children / n_infected : null;
  el.innerHTML =
    `<span>since poisoning <b>${fmt(r24.poisoned_at)}</b></span>` +
    ` <span>infected <b>${n_infected}</b></span>` +
    ` <span>frozen <b>${r24.frozen.size}</b></span>` +
    ` <span>released <b>${r24.released.size}</b></span>` +
    ` <span>live R <b>${live_r === null ? "unmeasured" : live_r.toFixed(2)}</b></span>` +
    ` <span class="src">events seq:${r24.first_seq ?? "—"}..${r24.last_seq ?? "—"}</span>`;
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
