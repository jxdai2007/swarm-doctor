"""Dashboard data contract (U13): build page JSON from U10 metrics + events.

Every displayed number traces to a metrics dict or event payload; nothing is
hand-typed here. `synthetic` labels fixture data on the page.
"""
from __future__ import annotations


def agent_state(events, freezes) -> dict[str, str]:
    """Per agent: clean | infected | frozen. Infection = ground-truth event."""
    frozen_agents = {a for a, _ in freezes}
    infected = {getattr(ev, "agent_id", None) for ev in events
                if getattr(ev, "kind", None) == "infection"}
    agents: dict[str, str] = {}
    for ev in events:
        a = getattr(ev, "agent_id", None)
        if a and a not in agents:
            agents[a] = "clean"
    for a in infected:
        agents[a] = "infected"
    for a in frozen_agents:
        agents[a] = "frozen"
    return agents


def counters(outbreak: dict, drift: dict | None = None) -> list[dict]:
    """Ordered counter rows; each value cites its metrics key."""
    rows = [
        {"label": "Infected agents", "value": outbreak["infected"],
         "source": "metrics.infected"},
        {"label": "R (secondary infections per infected)",
         "value": outbreak["r_mean"], "source": "metrics.r_mean"},
    ]
    if outbreak.get("time_to_contain"):
        rows.append({"label": "Time to contain (s)",
                     "value": outbreak["time_to_contain"]["seconds"],
                     "source": "metrics.time_to_contain.seconds"})
    if outbreak.get("r_mean") is None and outbreak["infected"] == 0:
        rows.append({"label": "R", "value": "unmeasured (no infections)",
                     "source": "metrics.r_mean"})
    if drift:
        rows += [
            {"label": "Wasted spend (USD)", "value": drift["wasted_spend_usd"],
             "source": "metrics.wasted_spend_usd"},
            {"label": "False steers", "value": drift["false_steers"],
             "source": "metrics.false_steers"},
        ]
    return rows


def snapshot(events, freezes, outbreak: dict, drift: dict | None = None,
             synthetic: bool = True, spec_path: str = "goal-spec.json") -> dict:
    """One page payload: graph nodes, DIRECTIONAL write->read agent edges,
    counters, provenance label. Events are the counterfactually pruned log
    (KTD6), so replayed panels never show actions a defense prevented."""
    from .eval.graph import rebuild
    from .eval.replay import prune_log
    kept = prune_log(events, freezes)
    graph = rebuild(kept)
    writes = {e["seq"]: e for e in graph.edges if e["operation"] == "write"}
    edges = []
    for edge in graph.edges:
        if edge["operation"] != "read" and edge.get("write_seq") is None:
            continue
        write = writes.get(edge.get("write_seq"))
        if not write or write["agent"] == edge["agent"]:
            continue
        edges.append({"source": write["agent"], "target": edge["agent"],
                      "path": edge["path"], "seq": edge["seq"],
                      "operation": "write->read"})
    return {
        "synthetic": synthetic,
        "agents": agent_state(kept, freezes),
        "edges": edges,
        "counters": counters(outbreak, drift),
        "controls": sorted(
            [{"seq": ev.seq, "agent_id": getattr(ev, "agent_id", None),
              "kind": getattr(ev, "kind", None), "elapsed":
              (ev.payload or {}).get("elapsed")}
             for ev in kept
             if getattr(ev, "kind", None) in ("freeze", "release", "steer",
                                              "trace")],
            key=lambda c: c["elapsed"] or 0,
        ),
        "time_since_poisoning_s": min(
            ((ev.payload or {}).get("elapsed") for ev in kept
             if getattr(ev, "kind", None) == "infection"),
            default=None),
        "events": sorted(
            [{"seq": ev.seq, "agent_id": getattr(ev, "agent_id", None),
              "kind": getattr(ev, "kind", None), "elapsed":
              (ev.payload or {}).get("elapsed"),
              "payload": {"source_agent":
                          (ev.payload or {}).get("source_agent")}}
             for ev in kept],
            key=lambda e: e["elapsed"] or 0,
        ),
    }
