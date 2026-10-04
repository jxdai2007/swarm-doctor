/* Split view (U13): three counterfactual DERIVED snapshots of the canonical
   pilot-0 recording (derived-<arm> folders written by
   scripts/make_capture_preps.py via canonical harness.arms.replay_arm).
   Per panel: retained events + controls derive lifecycle at time-since-
   poisoning; R is the arm's final metrics.r_mean, cited as final. */
"use strict";

(() => {
  if (new URLSearchParams(location.search).get("mode") !== "split") return;
  const wrap = document.getElementById("split");
  const status = document.getElementById("connection-status");
  const reconnect = document.getElementById("reconnect");
  status.textContent = "Loading split recordings…";
  reconnect.addEventListener("click", () => location.reload());
  const POISON = "#ff5d5d", CLEAN = "#4ecf8d", DIM = "#39434e",
    FROZEN = "#ffd27a";
  const ARMS = ["derived-no-defense", "derived-prompt-only",
    "derived-verify"];
  const LABEL = { "derived-no-defense": "no-defense",
    "derived-prompt-only": "prompt-only", "derived-verify": "verify" };
  const panels = ARMS.map((name) => {
    const p = document.createElement("div");
    p.className = "panel";
    p.innerHTML = `<h3>${LABEL[name]}<span data-role="t"></span></h3>
      <div class="graph"></div>
      <div class="pcount">since poisoning <b data-role="t2p">—</b>
       · infected <b data-role="infected">—</b>
       · frozen <b data-role="frozen">0</b>
       · R <b data-role="r">—</b> <span data-role="rsrc" class="src">final
       metrics.r_mean</span></div>`;
    wrap.appendChild(p);
    return {
      el: p, name,
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
          { selector: "node.killed", style: {
            "background-color": "#1a1d21", "border-width": 2,
            "border-color": POISON, color: POISON, opacity: 0.6 } },
          { selector: "edge", style: {
            width: 1, "line-color": DIM, "curve-style": "bezier",
            "target-arrow-shape": "triangle", "arrow-color": DIM } },
        ],
        layout: { name: "grid", fit: true, padding: 20 },
      }),
      events: [], controls: [], poisoned_at: null,
    };
  });

  async function load(name) {
    const res = await fetch(`/snapshot?run=${encodeURIComponent(name)}`);
    if (!res.ok) throw new Error(`run ${name} missing`);
    return res.json();
  }

  Promise.all(ARMS.map(load)).then((snaps) => {
    document.getElementById("run-name").textContent =
      "three-arm split race (pilot-0 counterfactuals, "
      + "synthetic-development)";
    document.getElementById("synthetic-badge").classList.add("on");
    snaps.forEach((snap, i) => {
      const p = panels[i];
      const els = Object.keys(snap.agents).map((id) => ({
        group: "nodes", data: { id }, classes: "",
      }));
      for (const [j, e] of (snap.edges || []).entries()) {
        els.push({ group: "edges", data: {
          ...e, id: `e${i}-${j}`, source: e.source, target: e.target } });
      }
      p.cy.add(els.length ? els : [{ group: "nodes", data: { id: "—" } }]);
      p.counters = Object.fromEntries(
        (snap.counters || []).map((c) => [c.source.split(".").pop(),
                                          c.value]));
      p.events = snap.events || [];
      p.controls = snap.controls || [];
      // render stream: full retained timeline (events + controls, elapsed
      // present), events before controls at equal elapsed, stable order
      p.stream = [...p.events, ...p.controls]
        .filter((e) => e.elapsed !== undefined)
        .sort((a, b) => (a.elapsed ?? 0) - (b.elapsed ?? 0) ||
          ((a.order ?? (typeof a.seq === "number" ? a.seq : 0)) -
           (b.order ?? (typeof b.seq === "number" ? b.seq : 0))));
      p.poisoned_at = Math.min(...p.events
        .filter((e) => e.kind === "infection")
        .map((e) => e.elapsed ?? 0), Infinity);
      if (!isFinite(p.poisoned_at)) p.poisoned_at = null;
    });
    const maxT = Math.max(1, ...panels.flatMap((p) =>
      p.stream.map((e) => Math.max(0, (e.elapsed || 0) - (p.poisoned_at ?? 0)))));
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
    let animation = null;
    const stop = () => {
      if (animation !== null) cancelAnimationFrame(animation);
      animation = null;
    };
    const show = (taskT) => {
      clock.textContent = `+${taskT.toFixed(1)}s (since poisoning)`;
      scrub.value = Math.round((taskT / maxT) * 1000);
      panels.forEach((p) => applyAt(p, taskT));
    };
    scrub.addEventListener("pointerdown", stop);
    scrub.addEventListener("input", () => {
      stop();
      show((scrub.value / 1000) * maxT);
    });
    status.textContent = "Replay — three recorded arms";
    const t0 = performance.now();
    (function step(now) {
      const taskT = Math.min(maxT, ((now - t0) / 30000) * maxT);
      show(taskT);
      animation = taskT < maxT ? requestAnimationFrame(step) : null;
    })(t0);
  }).catch((err) => {
    status.textContent = "Disconnected — split recordings unavailable. Reconnect to reload.";
    status.dataset.disconnected = "true";
    reconnect.hidden = false;
    wrap.innerHTML = '<div class="panel"><h3>Split unavailable</h3><div class="pcount"></div></div>';
    wrap.querySelector(".pcount").textContent = err.message;
  });

  function applyAt(p, taskT) {
    const t = p.poisoned_at === null ? taskT : p.poisoned_at + taskT;
    const infected = new Set(), frozen = new Set(), killed = new Set();
    for (const ev of p.stream) {
      if ((ev.elapsed ?? 0) > t) break;
      if (ev.kind === "infection") infected.add(ev.agent_id);
      if (ev.kind === "freeze") frozen.add(ev.agent_id);
      if (ev.kind === "release") frozen.delete(ev.agent_id);
      if (ev.kind === "kill" || ev.kind === "end") {
        frozen.delete(ev.agent_id);
        killed.add(ev.agent_id);
      }
    }
    p.cy.nodes().forEach((n) => {
      n.removeClass("infected frozen killed");
      if (infected.has(n.id())) n.addClass("infected");
      if (killed.has(n.id())) n.addClass("killed");
      else if (frozen.has(n.id())) n.addClass("frozen");
    });
    p.cy.edges().forEach((edge) => {
      edge.style("display", (edge.data("elapsed") ?? 0) <= t ? "element" : "none");
    });
    p.el.querySelector('[data-role="t"]').textContent =
      `+${taskT.toFixed(1)}s`;
    p.el.querySelector('[data-role="t2p"]').textContent =
      p.poisoned_at === null ? "unmeasured" : `${taskT.toFixed(1)}s`;
    p.el.querySelector('[data-role="infected"]').textContent = infected.size;
    p.el.querySelector('[data-role="frozen"]').textContent = frozen.size;
    const r = p.counters?.r_mean;
    p.el.querySelector('[data-role="r"]').textContent =
      infected.size === 0 ? "unmeasured" :
      (typeof r === "number" ? r.toFixed(2) : "—");
  }
})();
