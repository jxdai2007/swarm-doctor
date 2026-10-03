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
    { selector: "node.file", style: {
      "background-color": "#2a333d", "border-width": 1,
      "border-color": "#9fb0bf", color: "#9fb0bf", "font-size": 8,
      shape: "round-rectangle", width: "label", height: 18,
      "padding-left": "4px", "padding-right": "4px" } },
    { selector: "node.untrusted", style: {
      "background-color": "#5a4a22", "border-width": 2,
      "border-color": "#ffd27a", color: "#ffd27a" } },
    { selector: "node.killed", style: {
      "background-color": "#1a1d21", "border-width": 3,
      "border-color": POISON, color: POISON, opacity: 0.6 } },
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
function refit() {
  window.__refits = (window.__refits || 0) + 1;
  cy.resize();
  const runLayout = () => {
    try {
      cy.layout({ name: "cose", fit: true, padding: 40 }).run();
    } catch (e) {
      document.title = "LAYOUTERR cose: " + e.message;
      cy.layout({ name: "grid", fit: true, padding: 40 }).run();
    }
  };
  runLayout();
  // A layout during a zero/hidden container parks nodes at (0,0); retry
  // across frames until the box is real.
  let tries = 0;
  const retry = () => {
    tries += 1;
    const first = cy.nodes()[0];
    if (first && first.renderedPosition().x === 0
        && first.renderedPosition().y === 0 && tries < 5) {
      cy.resize();
      runLayout();
      requestAnimationFrame(retry);
    }
  };
  requestAnimationFrame(retry);
}
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
  // {agents, fileNodes, edges:[{source,target,path}], counters}.
  // Agent ids lose the agent: prefix; file nodes KEEP file: so agent↔file
  // edges render directionally (write: agent→file, read: file→agent).
  if (snap.graph) {
    const nodes = snap.graph.nodes || [];
    const agentIds = new Set(Object.keys(snap.agents || {}));
    const nodeEls = nodes.map((n) => {
      const raw = String(n.id ?? n);
      const isFile = raw.startsWith("file:");
      const id = isFile ? raw : raw.replace(/^agent:/, "");
      return { group: "nodes", data: { id },
        classes: isFile ? "file" : "", keep: true };
    });
    const known = new Set(nodeEls.map((n) => n.data.id));
    const edges = (snap.graph.edges || []).map((e, i) => ({
      group: "edges", data: {
        id: `g${i}`, source: String(e.source).replace(/^agent:/, ""),
        target: String(e.target).replace(/^agent:/, ""),
        path: e.path ?? "",
      },
    })).filter((e) => known.has(e.data.source) && known.has(e.data.target));
    const agents = snap.agents
      || Object.fromEntries([...agentIds].map((id) => [id, snap.states?.[id] ?? "clean"]));
    return { synthetic: !!snap.synthetic, agents, nodeEls, edges,
             counters: snap.counters || [],
             controls: snap.controls || [],
             time_since_poisoning_s: snap.time_since_poisoning_s ?? null,
             events: snap.events || [] };
  }
  if (!snap.nodeEls && snap.edges) {
    snap.nodeEls = Object.keys(snap.agents || {}).map((id) => ({
      group: "nodes", data: { id }, classes: "", keep: true }));
  }
  return snap;
}

function applySnapshot(snap) {
  snap = normalizeSnapshot(snap);
  document.getElementById("synthetic-badge").classList.toggle("on", !!snap.synthetic);
  const els = [];
  for (const n of (snap.nodeEls || [])) {
    const id = n.data.id;
    // agent state classes apply to agents only; file/untrusted keep their own
    const state = snap.agents[id];
    const cls = n.classes || (state && state !== "clean" ? state : "");
    els.push({ group: "nodes", data: { id }, classes: cls });
  }
  for (const [i, e] of (snap.edges || []).entries()) {
    const d = e.data || e;  // normalized wrappers vs raw edge objects
    if (!d.source || !d.target) continue;
    els.push({ group: "edges", data: {
      id: `e${i}`, source: d.source, target: d.target, path: d.path } });
  }
  cy.elements().remove();
  cy.add(els.length ? els : [{ group: "nodes", data: { id: "—" } }]);
  refit();
  renderCounters(snap.counters);
}

// Debounced same-engine snapshot refresh: executed actions change the graph,
// so re-pull /snapshot (serial — never parallel requests) and re-apply while
// keeping the clock and R24 citations untouched.
let refreshTimer = null;
let refreshBusy = false;
let refreshQueued = false;
function scheduleGraphRefresh(run) {
  if (refreshTimer) return;
  refreshTimer = setTimeout(async () => {
    refreshTimer = null;
    if (refreshBusy) { refreshQueued = true; return; }
    refreshBusy = true;
    try {
      const res = await fetch(`/snapshot?run=${encodeURIComponent(run)}`);
      if (res.ok) applySnapshot(normalizeSnapshot(await res.json()));
    } finally {
      refreshBusy = false;
      if (refreshQueued) { refreshQueued = false; scheduleGraphRefresh(run); }
    }
  }, 500);
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
  children: {}, poisoned_at: null, first_seq: null, last_seq: null,
  control_seqs: [] };

function updateR24(ev) {
  if (r24.poisoned_at === null && ev.kind === "infection")
    r24.poisoned_at = ev.elapsed;
  if (ev.seq !== null && ev.seq !== undefined) {
    if (["freeze", "release", "steer", "trace", "kill", "end"]
        .includes(ev.kind)) {
      if (!r24.control_seqs.includes(ev.seq)) r24.control_seqs.push(ev.seq);
    } else {
      if (r24.first_seq === null) r24.first_seq = ev.seq;
      r24.last_seq = ev.seq;
    }
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
    ` <span class="src">events seq:${r24.first_seq ?? "—"}..${r24.last_seq ?? "—"}` +
    (r24.control_seqs.length ? ` · controls seq:${r24.control_seqs.join(",")}` : "") +
    `</span>`;
}

const fmt = (s) => (typeof s === "number" ? s.toFixed(1) + "s" : "unmeasured");

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
            `${run}${obj.synthetic ? " (synthetic-development)" : ""}`;
          applySnapshot(obj);
        } else {
          events.push(obj); tick(obj);
          if (obj.kind === "action_executed")
            scheduleGraphRefresh(run);  // executed writes/reads change graph
        }
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

