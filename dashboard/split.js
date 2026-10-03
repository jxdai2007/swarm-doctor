/* Split view: three runs of the same seed, stepped in sync by time since
   first poisoning (relative clock shared with live.js). */
"use strict";

(() => {
  if (new URLSearchParams(location.search).get("mode") !== "split") return;
  const wrap = document.getElementById("split");
  const runs = ["no-defense", "prompt-only", "below-one-verify"];
  const params = new URLSearchParams(location.search);
  const seed = params.get("seed") || "fixture-outbreak";
  const panels = runs.map((name) => {
    const p = document.createElement("div");
    p.className = "panel";
    p.innerHTML = `<h3>${name}<span data-role="t"></span></h3>
      <div class="graph"></div>
      <div class="pcount">infected <b data-role="infected">—</b>
       · R <b data-role="r">—</b></div>`;
    wrap.appendChild(p);
    return {
      el: p,
      cy: cytoscape({
        container: p.querySelector(".graph"),
        style: [
          { selector: "node", style: {
            "background-color": CLEAN, label: "data(id)",
            color: "#e8edf2", "font-size": 9, width: 18, height: 18 } },
          { selector: "node.infected", style: { "background-color": POISON } },
          { selector: "node.frozen", style: {
            "background-color": POISON, "border-width": 2,
            "border-color": POISON } },
          { selector: "edge", style: { width: 1, "line-color": DIM } },
        ],
        layout: { name: "grid", fit: true, padding: 20 },
      }),
      events: [],
    };
  });

  async function load(name) {
    const res = await fetch(`/snapshot?run=${encodeURIComponent(name + "-" + seed)}`);
    if (!res.ok) { throw new Error(`run ${name}-${seed} missing`); }
    const snap = await res.json();
    return snap;
  }

  Promise.all(runs.map(load)).then((snaps) => {
    document.getElementById("run-name").textContent =
      `three-arm split race (seed ${seed}, fixture)`;
    document.getElementById("synthetic-badge").classList
      .toggle("on", snaps.some((s) => s.synthetic));
    snaps.forEach((snap, i) => {
      const p = panels[i];
      p.cy.add(Object.keys(snap.agents).map((id) => ({
        group: "nodes", data: { id },
        classes: snap.agents[id] === "clean" ? "" : snap.agents[id],
      })));
      p.cy.layout({ name: "grid", fit: true, padding: 20 }).run();
      p.counters = Object.fromEntries(
        snap.counters.map((c) => [c.source.split(".").pop(), c.value]));
      p.events = snap.events.filter((e) => e.kind === "infection" || e.kind === "freeze");
    });
    const maxT = Math.max(1, ...panels.flatMap((p) =>
      p.events.map((e) => e.elapsed || 0)));
    document.dispatchEvent(new CustomEvent("splitmax", { detail: maxT }));
    // Split owns the shared clock: step it so every panel reaches the same
    // relative time together.
    const scrub = document.getElementById("scrub");
    const clock = document.getElementById("clock");
    const dur = Math.min(maxT * 100, 30000);
    const t0 = performance.now();
    (function step(now) {
      const t = ((now - t0) / dur) * maxT;
      clock.textContent = `t = ${fmt(t)} (relative)`;
      scrub.value = Math.round((t / maxT) * 1000);
      document.dispatchEvent(new CustomEvent("clock", { detail: t }));
      if (t < maxT) requestAnimationFrame(step);
    })(t0);
    // Panels advance together: same relative t, per-panel state derived.
    document.addEventListener("clock", (e) => {
      const t = Math.min(e.detail, maxT);
      panels.forEach((p) => {
        const inf = new Set(), frozen = new Set();
        for (const ev of p.events) {
          if ((ev.elapsed || 0) > t) break;
          (ev.kind === "infection" ? inf : frozen).add(ev.agent_id);
        }
        p.cy.nodes().forEach((n) => {
          const id = n.id();
          n.removeClass("infected frozen");
          if (frozen.has(id)) n.addClass("frozen");
          else if (inf.has(id)) n.addClass("infected");
        });
        p.el.querySelector('[data-role="t"]').textContent = fmt(t);
        const shown = inf.size;
        p.el.querySelector('[data-role="infected"]').textContent = shown;
        const r = p.counters?.r_mean;
        p.el.querySelector('[data-role="r"]').textContent =
          shown === 0 ? "unmeasured" : (typeof r === "number" ? r.toFixed(2) : "—");
      });
    });
  }).catch((err) => {
    wrap.innerHTML = `<div class="panel"><h3>Split unavailable</h3>
      <div class="pcount">${err.message} — generate run snapshots first
      (SYNTHETIC DEV fixture via scripts/make_fixture_runs.py).</div></div>`;
  });
})();
