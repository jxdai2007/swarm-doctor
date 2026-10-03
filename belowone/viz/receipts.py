"""Outbreak receipts (U14): patient zero, catching layer, containment, cost.

Provenance comes from run artifacts via extract_provenance (infection events,
freeze/decision events, meter report). Unknown origin is treated SYNTHETIC —
never silently presented as live evidence.
"""
from __future__ import annotations

from belowone.viz.cards import cites_metrics


def extract_provenance(events, meter_report: dict | None = None) -> dict:
    """Real-recording extractor: patient zero = agent of the earliest
    ground-truth infection (None when none); catching layer = the layer of
    the first recorded detector decision that led to a freeze (None when no
    freeze); cost = meter total or the sum of retained outcome cost_usd."""
    infections = [e for e in events if getattr(e, "kind", None) == "infection"]

    def elapsed(e):
        p = e.payload if isinstance(e.payload, dict) else {}
        return float(p.get("elapsed", getattr(e, "elapsed", 0.0)))

    patient_zero = None
    if infections:
        first = min(infections, key=lambda e: (elapsed(e), e.seq))
        patient_zero = getattr(first, "agent_id", None)
    catching_layer = None
    freezes = [e for e in events if getattr(e, "kind", None) == "freeze"]
    if freezes:
        first_freeze = min(freezes, key=lambda e: (elapsed(e), e.seq))
        payload = first_freeze.payload or {}
        catching_layer = payload.get("layer") or payload.get("decision_layer") \
            or "freeze"
    cost = None
    if meter_report and meter_report.get("total_usd") is not None:
        cost = meter_report["total_usd"]
    else:
        costs = [float((e.payload or {}).get("cost_usd", 0.0))
                 for e in events if getattr(e, "kind", None) == "outcome"]
        cost = sum(costs) if costs else None
    # explicit live flag only; unknown origin is synthetic by default
    synthetic = not (meter_report or {}).get("live", False)
    return {"patient_zero": patient_zero, "catching_layer": catching_layer,
            "cost_usd": cost, "synthetic": synthetic}


def receipt(run_name: str, outbreak: dict, provenance: dict) -> dict:
    if not isinstance(provenance.get("synthetic"), bool):
        raise ValueError("provenance.synthetic must be an explicit bool; "
                         "unknown origin is synthetic via extract_provenance")
    contain = outbreak.get("time_to_contain")
    bill = {
        "time_to_contain": contain,  # preserved exactly (None stays None)
        "infected": outbreak["infected"],
    }
    missing = cites_metrics(bill, outbreak)
    if missing:
        raise ValueError(f"receipt values not bound to metrics: {missing}")
    return {
        "run": run_name,
        "patient_zero": provenance["patient_zero"],
        "catching_layer": provenance["catching_layer"],
        "time_to_contain": contain,
        "infected": outbreak["infected"],
        "cost_usd": provenance["cost_usd"],
        "synthetic": provenance["synthetic"],
    }


def render(receipt_row: dict) -> str:
    """Plain-text receipt; unmeasured values print 'unmeasured', never None.
    SYNTHETIC label surfaces whenever flagged."""
    def shown(value, unit=""):
        return "unmeasured" if value is None else f"{value}{unit}"

    lines = [
        f"OUTBREAK RECEIPT — {receipt_row['run']}"
        + ("  [SYNTHETIC DEV — fixture, not live evidence]"
           if receipt_row.get("synthetic") else ""),
        f"patient zero:      {shown(receipt_row['patient_zero'])}",
        f"catching layer:    {shown(receipt_row['catching_layer'])}",
        f"time to contain:   {shown(receipt_row['time_to_contain'])}",
        f"agents infected:   {shown(receipt_row['infected'])}",
        f"cost:              {shown(receipt_row['cost_usd'])}",
    ]
    return "\n".join(lines) + "\n"
