"""Dashboard data contract (U13): build page JSON from U10 metrics + events.

Every displayed number traces to a metrics dict or event payload; nothing is
hand-typed here. `synthetic` labels fixture data on the page.
"""
from __future__ import annotations

import copy


def agent_state(events, controls=()) -> dict[str, str]:
    """End-of-replay state per agent from FULL control records only.
    freeze_windows yields (agent, start, end): end None = still frozen;
    a release closes the window (active); kill/end is terminal and wins."""
    from .eval.replay import freeze_windows

    windows = freeze_windows((), controls)
    frozen = {agent for agent, _start, end in windows if end is None}
    killed = {c["agent_id"] for c in controls
              if c.get("kind") in {"kill", "end"}}
    infected = {getattr(ev, "agent_id", None) for ev in events
                if getattr(ev, "kind", None) == "infection"}
    agents: dict[str, str] = {}
    for ev in events:
        a = getattr(ev, "agent_id", None)
        if a and a not in agents:
            agents[a] = "clean"
    for control in controls:
        if control.get("agent_id"):
            agents.setdefault(control["agent_id"], "clean")
    for a in infected:
        agents[a] = "infected"
    for a in frozen:
        agents[a] = "frozen"
    for a in killed:
        agents[a] = "killed"
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


def snapshot(events, controls, outbreak: dict, drift: dict | None = None,
             synthetic: bool = True) -> dict:
    """One page payload. controls are the FULL sourced control records from
    the evaluator (freeze/release/kill/steer/trace with order); freeze pairs
    for pruning derive from them. Clock events keep the retained FULL payload
    (provenance included, so the evaluator can re-consume them); control
    records are cited with synthetic keys "C<order>", never fake ground-truth
    seq ids."""
    from .eval.graph import rebuild
    from .eval.replay import prune_log
    kept = prune_log(events, controls=controls)
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
                      "elapsed": edge["elapsed"],
                      "operation": "write->read"})
    return {
        "synthetic": synthetic,
        "last_seq": max((ev.seq for ev in events), default=0),
        "agents": agent_state(kept, controls),
        "edges": edges,
        "counters": counters(outbreak, drift),
        "controls": [
            {"seq": f"C{c.get('order', i)}",
             "agent_id": c.get("agent_id"), "kind": c.get("kind"),
             "elapsed": c.get("elapsed"), "order": c.get("order", i)}
            for i, c in enumerate(sorted(controls,
                                         key=lambda c: (c.get("elapsed", 0),
                                                        c.get("order", 0))))
        ],
        "time_since_poisoning_s": min(
            ((ev.payload or {}).get("elapsed") for ev in kept
             if getattr(ev, "kind", None) == "infection"),
            default=None),
        "events": sorted(
            [{"seq": ev.seq, "agent_id": getattr(ev, "agent_id", None),
              "kind": getattr(ev, "kind", None), "elapsed":
              (ev.payload or {}).get("elapsed"),
              "paths": list(getattr(ev, "paths", []) or []),
              "payload": copy.deepcopy(ev.payload or {})}
             for ev in kept],
            key=lambda e: (e["elapsed"] or 0, e["seq"]),
        ),
    }
