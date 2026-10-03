"""Outbreak receipts (U14): patient zero, catching layer, containment, cost.
Provenance comes from run artifacts (infection events, freeze events, meter);
synthetic fixtures must set synthetic=True."""
from __future__ import annotations

from belowone.viz.cards import cites_metrics


def receipt(run_name: str, outbreak: dict, provenance: dict) -> dict:
    contain = outbreak.get("time_to_contain") or {}
    bill = {
        "time_to_contain": {"seconds": contain.get("seconds"),
                            "steps": contain.get("steps")},
        "infected": outbreak["infected"],
    }
    missing = cites_metrics(bill, outbreak)
    if missing:
        raise ValueError(f"receipt values not bound to metrics: {missing}")
    return {
        "run": run_name,
        "patient_zero": provenance["patient_zero"],
        "catching_layer": provenance["catching_layer"],
        "time_to_contain_seconds": contain.get("seconds"),
        "time_to_contain_steps": contain.get("steps"),
        "infected": outbreak["infected"],
        "cost_usd": provenance["cost_usd"],
        "synthetic": provenance.get("synthetic", False),
    }


def render(receipt_row: dict) -> str:
    """Plain-text receipt; SYNTHETIC label surfaces whenever flagged."""
    lines = [
        f"OUTBREAK RECEIPT — {receipt_row['run']}"
        + ("  [SYNTHETIC DEV — fixture, not live evidence]"
           if receipt_row.get("synthetic") else ""),
        f"patient zero:      {receipt_row['patient_zero']}",
        f"catching layer:    {receipt_row['catching_layer']}",
        f"time to contain:   {receipt_row['time_to_contain_seconds']}s "
        f"({receipt_row['time_to_contain_steps']} steps)",
        f"agents infected:   {receipt_row['infected']}",
        f"cost:              ${receipt_row['cost_usd']}",
    ]
    return "\n".join(lines) + "\n"
