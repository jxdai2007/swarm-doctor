/* Split view (U13): three arms from the CANONICAL U9 pilot all-arms output
   (experiments/committed/runs/pilot-0/metrics/all-arms.json via the engine
   /artifacts allowlist) plus each run's retained events for the actual
   policy. Values shown are the evaluator's per-arm final infected/R with an
   explicit "final" citation; per-arm time series require the U10 replay_arm
   (engine-integration) and are not improvised here. Agents initialize clean
   and derive lifecycle from the run's retained events + controls. */
"use strict";

(() => {
  if (new URLSearchParams(location.search).get("mode") !== "split") return;
  const wrap = document.getElementById("split");
  const POISON = "#ff5d5d", CLEAN = "#4ecf8d", DIM = "#39434e",
    FROZEN = "#ffd27a";
  const ARMS = ["no-defense", "prompt-only", "verify"];
  const params = new URLSearchParams(location.search);
  const run = params.get("run") || "pilot-0";
  const panels = ARMS.map((name) => {
    const p = document.createElement("div");
    p.className = "panel";
    p.innerHTML = `<h3>${name}<span data-role="t"></span></h3>
      <div class="graph"></div>
      <div class="pcount">infected <b data-role="infected">—</b>
       · frozen <b data-role="frozen">—</b>
       · R <b data-role="r">—</b> <span data-role="rsrc" class="src">final
       metrics all-arms.json</span></div>`;
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
          { selector: "edge", style: {
            width: 1, "line-color": DIM, "curve-style": "bezier",
            "target-arrow-shape": "triangle", "arrow-color": DIM } },
        ],
        layout: { name: "grid", fit: true, padding: 20 },
      }),
    };
  });

  async function json_or_null(url) {
    const res = await fetch(url);
    return res.ok ? res.json() : null;
  }

  Promise.all([
    json_or_null(`/artifacts/${run}/metrics/all-arms.json`),
    json_or_null(`/snapshot?run=${encodeURIComponent(run)}`),
    json_or_null(`/events?run=${encodeURIComponent(run)}`),
  ]).then(([allArms, snap, evs]) => {
    if (!allArms) throw new Error(
      "all-arms.json not published on /artifacts yet (engine-integration "
      + "allowlist) — split shows the actual-policy board only");
    document.getElementById("run-name").textContent =
      `${run}: 3 of 8 arms (synthetic-development)`;
    document.getElementById("synthetic-badge").classList.add("on");

    // shared agents/graph from the run's real snapshot (engine shape)
    const agents = snap?.agents
      || Object.fromEntries((snap?.graph?.nodes || [])
        .map((n) => [String(n.id ?? n).replace(/^agent:/, ""), "clean"]));
    const edges = (snap?.edges || (snap?.graph?.edges || []).map((e) => ({
      source: String(e.source).replace(/^agent:/, ""),
      target: String(e.target).replace(/^agent:/, ""),
    }))).filter((e) => agents[e.source] && agents[e.target]);

    // actual-policy retention over the recorded stream, if served
    let retainedEvents = [];
    if (evs) {
      const reader = evs.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      const pump = () => {
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
          const data = chunk.split("\n")
            .filter((l) => l.startsWith("data: "))
            .map((l) => l.slice(6)).join("");
          if (!data || data === "[done]") continue;
          try {
            const o = JSON.parse(data);
            if (o.kind === "infection" || o.kind === "freeze"
                || o.kind === "release") retainedEvents.push(o);
          } catch { /* tolerate */ }
        }
      };
      // drain what's buffered; historical streams end on their own
      (async () => {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          pump();
        }
        pump();
      })();
    }

    panels.forEach((p) => {
      const els = Object.keys(agents).map((id) => ({
        group: "nodes", data: { id }, classes: "",
      }));
      for (const [j, e] of edges.entries()) {
        els.push({ group: "edges", data: {
          id: `e${j}`, source: e.source, target: e.target } });
      }
      p.cy.add(els.length ? els : [{ group: "nodes", data: { id: "—" } }]);
    });
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
    const dur = 30000;
    const t0 = performance.now();
    (function step(now) {
      const relT = ((now - t0) / dur) * 60;  // fixture window 0..60s
      clock.textContent = `t = ${relT.toFixed(1)}s (since poisoning)`;
      scrub.value = Math.round((relT / 60) * 1000);
      // actual-policy retention over the recorded stream
      const infected = new Set(), frozen = new Set();
      for (const ev of retainedEvents) {
        if ((ev.elapsed ?? 0) > relT) break;
        if (ev.kind === "infection") infected.add(ev.agent_id);
        if (ev.kind === "freeze") frozen.add(ev.agent_id);
        if (ev.kind === "release") frozen.delete(ev.agent_id);
      }
      panels.forEach((p) => {
        const arm = allArms[p.name];
        p.cy.nodes().forEach((n) => {
          n.removeClass("infected frozen");
          if (frozen.has(n.id())) n.addClass("frozen");
          else if (infected.has(n.id())) n.addClass("infected");
        });
        p.el.querySelector('[data-role="t"]').textContent =
          `+${relT.toFixed(1)}s`;
        p.el.querySelector('[data-role="infected"]').textContent =
          infected.size;
        p.el.querySelector('[data-role="frozen"]').textContent =
          frozen.size;
        p.el.querySelector('[data-role="r"]').textContent =
          arm && arm.r_mean !== null && arm.r_mean !== undefined
            ? arm.r_mean.toFixed(2) : "unmeasured";
      });
      if (relT < 60) requestAnimationFrame(step);
    })(t0);
  }).catch((err) => {
    wrap.innerHTML = `<div class="panel"><h3>Split unavailable</h3>
      <div class="pcount">${err.message}</div></div>`;
  });
})();
