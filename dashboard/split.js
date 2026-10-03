/* Split view: three runs of the same seed, stepped in sync by each run's time
   since first poisoning (R25/U13). Panels derive agent lifecycle + infection
   from RETAINED events + controls up to the shown time; R shown per panel is
   the run's final metrics R, cited explicitly as final (never a fake live R).
   Edges come from the snapshot graph; agents initialize clean. */
"use strict";

(() => {
  if (new URLSearchParams(location.search).get("mode") !== "split") return;
  const wrap = document.getElementById("split");
  const POISON = "#ff5d5d", CLEAN = "#4ecf8d", DIM = "#39434e",
    FROZEN = "#ffd27a";
  const runs = ["no-defense", "prompt-only", "below-one-verify"];
  const params = new URLSearchParams(location.search);
  const seed = params.get("seed") || "fixture-outbreak";
  const panels = runs.map((name) => {
    const p = document.createElement("div");
    p.className = "panel";
    p.innerHTML = `<h3>${name}<span data-role="t"></span></h3>
      <div class="graph"></div>
      <div class="pcount">since poisoning <b data-role="t2p">unmeasured</b>
       · infected <b data-role="infected">—</b>
       · frozen <b data-role="frozen">0</b>
       · R <b data-role="r">—</b> <span data-role="rsrc" class="src"></span></div>`;
    wrap.appendChild(p);
    return {
      el: p,
      cy: cytoscape({
        container: p.querySelector(".graph"),
        style: [
          { selector: "node", style: {
            "background-color": CLEAN, label: "data(id)",
            color: "#e8edf2", "font-size": 9, width: 18, height: 18 } },
          { selector: "node.infected", style: {
            "background-color": POISON } },
          { selector: "node.frozen", style: {
            "background-color": "#3a3325", "border-width": 2,
            "border-color": FROZEN, color: FROZEN } },
          { selector: "edge", style: {
            width: 1, "line-color": DIM, "curve-style": "bezier",
            "target-arrow-shape": "triangle", "arrow-color": DIM } },
        ],
        layout: { name: "grid", fit: true, padding: 20 },
      }),
      events: [], controls: [],
    };
  });

  async function load(name) {
    const res = await fetch(`/snapshot?run=${encodeURIComponent(name + "-" + seed)}`);
    if (!res.ok) throw new Error(`run ${name}-${seed} missing`);
    return res.json();
  }

  Promise.all(runs.map(load)).then((snaps) => {
    document.getElementById("run-name").textContent =
      `three-arm split race (seed ${seed}, synthetic-development)`;
    // split owns the badge: never let a shared snapshot overwrite it off
    document.getElementById("synthetic-badge").classList.add("on");
    snaps.forEach((snap, i) => {
      const p = panels[i];
      const agents = snap.agents || {};
      const els = Object.keys(agents).map((id) => ({
        group: "nodes", data: { id }, classes: "",  // all start clean
      }));
      for (const [j, e] of (snap.edges || []).entries()) {
        els.push({ group: "edges", data: {
          id: `e${i}-${j}`, source: e.source, target: e.target } });
      }
      p.cy.add(els.length ? els : [{ group: "nodes", data: { id: "—" } }]);
      p.counters = Object.fromEntries(
        snap.counters.map((c) => [c.source.split(".").pop(), c.value]));
      p.events = snap.events || [];
      p.controls = snap.controls || [];
      // per-run poisoning reference: earliest retained infection
      p.poisoned_at = Math.min(...p.events
        .filter((e) => e.kind === "infection")
        .map((e) => e.elapsed ?? 0), Infinity);
      if (!isFinite(p.poisoned_at)) p.poisoned_at = null;
    });
    const maxT = Math.max(1, ...panels.flatMap((p) =>
      p.events.map((e) => e.elapsed || 0)));
    // shared axis = time since (earliest) first poisoning across panels
    const poison0 = Math.min(...panels
      .filter((p) => p.poisoned_at !== null)
      .map((p) => p.poisoned_at));
    const relMax = Math.max(1, maxT - (isFinite(poison0) ? poison0 : 0));
    window.__splitPanels = panels;  // capture-proof harness reads node boxes
    // fit AFTER panels have real boxes (cytoscape was built on hidden wrap)
    const fitAll = () => panels.forEach((p) => {
      p.cy.resize();
      p.cy.layout({ name: "cose", fit: true, padding: 20 }).run();
    });
    requestAnimationFrame(fitAll);
    if (window.ResizeObserver) {
      const ro = new ResizeObserver(() => fitAll());
      panels.forEach((p) => ro.observe(p.el.querySelector(".graph")));
    }

    const scrub = document.getElementById("scrub");
    const clock = document.getElementById("clock");
    const dur = Math.min(relMax * 100, 30000);
    const t0 = performance.now();
    (function step(now) {
      const relT = ((now - t0) / dur) * relMax;  // since first poisoning
      clock.textContent = `t = ${relT.toFixed(1)}s (since poisoning)`;
      scrub.value = Math.round((relT / relMax) * 1000);
      panels.forEach((p) => applyAt(p, relT));
      if (relT < relMax) requestAnimationFrame(step);
    })(t0);
  }).catch((err) => {
    wrap.innerHTML = `<div class="panel"><h3>Split unavailable</h3>
      <div class="pcount">${err.message} — SYNTHETIC DEV fixtures via
      scripts/make_fixture_runs.py.</div></div>`;
  });

  // Derive per-panel state from retained events + controls up to time-since-
  // poisoning t (missing poisoning reference -> unmeasured, state at task t).
  function applyAt(p, taskT) {
    const t = p.poisoned_at === null ? taskT : p.poisoned_at + taskT;
    const infected = new Set(), frozen = new Set(), released = new Set();
    for (const ev of [...p.events, ...p.controls]
        .sort((a, b) => (a.elapsed ?? 0) - (b.elapsed ?? 0))) {
      if ((ev.elapsed ?? 0) > t) break;
      if (ev.kind === "infection") infected.add(ev.agent_id);
      if (ev.kind === "freeze") frozen.add(ev.agent_id);
      if (ev.kind === "release") {
        frozen.delete(ev.agent_id);
        released.add(ev.agent_id);
      }
    }
    p.cy.nodes().forEach((n) => {
      const id = n.id();
      n.removeClass("infected frozen");
      if (frozen.has(id)) n.addClass("frozen");
      else if (infected.has(id)) n.addClass("infected");
    });
    p.el.querySelector('[data-role="t"]').textContent =
      p.poisoned_at === null ? "unmeasured" : `+${taskT.toFixed(1)}s`;
    p.el.querySelector('[data-role="t2p"]').textContent =
      p.poisoned_at === null ? "unmeasured" : `${taskT.toFixed(1)}s`;
    p.el.querySelector('[data-role="infected"]').textContent = infected.size;
    p.el.querySelector('[data-role="frozen"]').textContent = frozen.size;
    const r = p.counters?.r_mean;
    p.el.querySelector('[data-role="r"]').textContent =
      infected.size === 0 ? "unmeasured" :
      (typeof r === "number" ? r.toFixed(2) : "—");
    // R shown is the run's FINAL metrics value, cited as such (no fake live R)
    p.el.querySelector('[data-role="rsrc"]').textContent =
      infected.size === 0 ? "" : "final metrics.r_mean";
  }
})();
