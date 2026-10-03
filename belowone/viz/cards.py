"""Number cards (U14): every value is a metrics-key citation."""
from __future__ import annotations


def outbreak_card(outbreak: dict) -> dict:
    # containment is preserved EXACTLY as the metrics report it: None stays
    # None (unmeasured/prevented/uncontained), never a fabricated zero-shape
    return {
        "infected": outbreak["infected"],
        "r_mean": outbreak["r_mean"],
        "time_to_contain": outbreak.get("time_to_contain"),
        "clean_wrongly_frozen": outbreak["clean_wrongly_frozen"],
        "work_completed": outbreak["work_completed"],
        "status": outbreak["status"],
    }


def drift_card(drift: dict) -> dict:
    return {
        "wasted_spend_usd": drift["wasted_spend_usd"],
        "finish_rate": drift["finish_rate"],
        "false_steers": drift["false_steers"],
    }


def cites_metrics(card: dict, metrics: dict, prefix: str = "") -> list[str]:
    """Return every card entry that does NOT equal the metrics value at the
    same key path (empty list = fully sourced)."""
    missing = []
    for key, value in card.items():
        if isinstance(metrics, dict) and key in metrics:
            if isinstance(value, dict) and isinstance(metrics[key], dict):
                missing += cites_metrics(value, metrics[key], prefix + key + ".")
            elif metrics[key] != value:
                missing.append(f"{prefix}{key}: card {value!r} != "
                               f"metrics {metrics[key]!r}")
        else:
            missing.append(f"{prefix}{key}: no metrics key")
    return missing
