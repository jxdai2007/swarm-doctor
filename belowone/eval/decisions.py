"""Decision cache adapter (U10): consume a recorded decisions.jsonl.

Contract pinned with core-engine 2026-10-03. Each line:
  {"spec_hash", "action_id", "agent_id", "decision": allow|deny|steer|freeze|kill|end,
   "label", "confidence", "reason", "decided_at_elapsed": float,
   "latency_s": float, ["shadow": true, "source_action_elapsed": float]}
Replay maps enforcing deny/end/kill to a stop at decision time; shadow entries
never enforce. Lookup is strict: (spec_hash, action_id) miss fails loud.
"""
from __future__ import annotations

import json

ENFORCING_STOP = {"deny", "end", "kill"}


def load_decisions(path, spec_hash: str) -> dict[str, dict]:
    """{action_id: entry} for one spec; unknown spec hash or duplicate
    action_id raises."""
    out: dict[str, dict] = {}
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            d = json.loads(line)
            if d["spec_hash"] != spec_hash:
                raise KeyError(
                    f"{path}:{lineno}: spec_hash {d['spec_hash'][:12]} != requested")
            aid = d["action_id"]
            if aid in out:
                raise KeyError(f"{path}:{lineno}: duplicate action_id {aid}")
            for field in ("agent_id", "decision", "decided_at_elapsed", "latency_s"):
                if field not in d:
                    raise KeyError(f"{path}:{lineno}: missing {field}")
            out[aid] = d
    return out


def freeze_schedule(decisions: dict[str, dict], include_steers: bool = False) -> list:
    """Enforcing decisions -> [(agent_id, stop_elapsed)]. Shadow entries are
    skipped; steers only when the arm steers rather than stops."""
    stops = []
    for d in decisions.values():
        if d.get("shadow"):
            continue
        if d["decision"] in ENFORCING_STOP:
            stops.append((d["agent_id"], float(d["decided_at_elapsed"])))
        elif d["decision"] == "freeze":
            stops.append((d["agent_id"], float(d["decided_at_elapsed"])))
        elif d["decision"] == "steer" and include_steers:
            stops.append((d["agent_id"], float(d["decided_at_elapsed"])))
    return stops


def shadow_checks(decisions: dict[str, dict]) -> list[dict]:
    """Post-run shadow entries with their counterfactual timing, labeled."""
    return [
        {"action_id": d["action_id"], "agent_id": d["agent_id"],
         "decision": d["decision"], "label": d.get("label"),
         "source_action_elapsed": d["source_action_elapsed"],
         "latency_s": d["latency_s"], "shadow": True}
        for d in decisions.values() if d.get("shadow")
    ]
