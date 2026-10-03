"""U14 viz tests: deterministic regeneration, metric-bound cards, receipts."""
import json
import xml.etree.ElementTree as ET

from belowone.viz import figures
from belowone.viz.cards import cites_metrics, drift_card, outbreak_card
from belowone.viz.receipts import extract_provenance, receipt, render

OUTBREAK = {
    "infected": 2, "r_mean": 0.5, "r_ci95": [0.0, 1.0],
    "time_to_contain": {"seconds": 19.5, "steps": 2},
    "outbreaks_contained": 1, "clean_wrongly_frozen": 0,
    "work_completed": 1, "status": "contained",
}
DRIFT = {"wasted_spend_usd": 0.07, "finish_rate": 0.5,
         "time_to_done": 40.0, "false_steers": 1}


def test_figures_regenerate_identically(tmp_path):
    per_arm = {"no-defense": [(0, 0), (10, 1), (25, 2)],
               "verify": [(0, 0), (10, 0), (25, 0)]}
    svg_a, data_a = figures.epidemic_curve(per_arm)
    svg_b, data_b = figures.epidemic_curve(dict(per_arm))
    assert svg_a == svg_b and data_a == data_b
    path_a = figures.write_figure(tmp_path / "a", "epidemic", svg_a, data_a)
    path_b = figures.write_figure(tmp_path / "b", "epidemic", svg_b, data_b)
    assert path_a.read_bytes() == path_b.read_bytes()
    ET.fromstring(svg_a)  # actual renderable SVG document, not a stub


def test_delay_damage_marks_measured_points():
    points = [{"delta": 0.0, "damage": 1, "label": "jev 0.4s"},
              {"delta": 86400.0, "damage": 3, "label": "daily review"}]
    svg, data = figures.delay_damage(points)
    ET.fromstring(svg)
    assert "jev 0.4s" in svg and "daily review" in svg
    payload = json.loads(data)
    assert payload["points"] == points


def test_r_bar_draws_containment_line_and_unmeasured():
    svg, data = figures.r_bar({
        "no-defense": {"mean": 1.5, "ci95": [1.0, 2.0]},
        "verify": {"mean": 0.0, "ci95": [0.0, 0.0]},
        "prevented": {"mean": None, "ci95": None},
    })
    ET.fromstring(svg)
    assert "R = 1" in svg
    assert "prevented: R unmeasured" in svg
    assert json.loads(data)["per_arm"]["verify"]["mean"] == 0.0


def test_cards_values_trace_to_metrics_keys():
    card = outbreak_card(OUTBREAK)
    assert cites_metrics(card, OUTBREAK) == []
    drifted = dict(card, infected=99)
    assert cites_metrics(drifted, OUTBREAK)  # mismatch named
    dcard = drift_card(DRIFT)
    assert cites_metrics(dcard, DRIFT) == []


def test_receipt_names_patient_zero_and_catching_layer():
    row = receipt("outbreak-seed0", OUTBREAK,
                  {"patient_zero": "a0", "catching_layer": "tripwire",
                   "cost_usd": 0.02, "synthetic": True})
    assert row["patient_zero"] == "a0"
    assert row["catching_layer"] == "tripwire"
    assert row["time_to_contain"] == {"seconds": 19.5, "steps": 2}
    assert row["synthetic"] is True
    text = render(row)
    assert "SYNTHETIC DEV" in text and "a0" in text and "tripwire" in text
    # unbound receipt values fail loud, never silently printed
    import pytest
    with pytest.raises(ValueError):
        receipt("x", {"infected": 9, "time_to_contain": None}, {})


def test_null_containment_card_and_receipt_from_actual_metrics():
    """Actual U10 zero-infection outcome: card cites cleanly, receipt renders
    'unmeasured' — the most common real pilot outcome."""
    from belowone.eval.metrics import outbreak_metrics
    from belowone.eval.replay import replay_no_defense
    from belowone.viz.cards import cites_metrics

    def ev(seq, agent, kind, elapsed, **payload):
        from types import SimpleNamespace
        return SimpleNamespace(seq=seq, agent_id=agent, kind=kind,
                               payload={"elapsed": elapsed, **payload})
    events = [ev(0, "a3", "outcome", 9.0, completed=True, waste=0.0,
                 cost_usd=0.01, served_model="synthetic-dev")]
    metrics = outbreak_metrics(replay_no_defense(events), events, seed=0)
    assert metrics["time_to_contain"] is None and metrics["r_mean"] is None
    card = outbreak_card(metrics)
    assert card["time_to_contain"] is None and cites_metrics(card, metrics) == []

    # infected run without freezes: containment stays unmeasured honestly
    from belowone.eval.replay import replay_freeze_schedule
    infected_events = [
        ev(1, "a0", "action_executed", 5.0, completed=True),
        ev(2, "a0", "infection", 10.0, source_agent=None,
           provenance_paths=[[]], manifest_action="cheat"),
        ev(3, "a3", "outcome", 60.0, completed=True, waste=0.0,
           cost_usd=0.02, served_model="synthetic-dev"),
    ]
    spread = replay_freeze_schedule(infected_events, [])
    spread_metrics = outbreak_metrics(spread, infected_events, seed=0)
    assert spread_metrics["infected"] == 1
    provenance = {"patient_zero": None, "catching_layer": None,
                  "cost_usd": None, "synthetic": True}
    row = receipt("no-outbreak", metrics, provenance)
    text = render(row)
    assert "unmeasured" in text and "None" not in text
    row2 = receipt("spread", spread_metrics, provenance)
    assert "unmeasured" in render(row2)


def test_extract_provenance_from_artifact_events():
    from types import SimpleNamespace as Ev
    events = [
        Ev(seq=0, agent_id="a0", kind="action_executed",
           payload={"elapsed": 1.0}),
        Ev(seq=1, agent_id="a0", kind="action_executed",
           payload={"elapsed": 2.0}),
        Ev(seq=2, agent_id="a0", kind="infection", payload={"elapsed": 2.0}),
        Ev(seq=3, agent_id="a1", kind="action_executed",
           payload={"elapsed": 3.0}),
        Ev(seq=4, agent_id="a1", kind="infection", payload={"elapsed": 5.0}),
        Ev(seq=5, agent_id="a1", kind="freeze",
           payload={"elapsed": 6.0, "layer": "tripwire"}),
        Ev(seq=6, agent_id="a1", kind="outcome",
           payload={"elapsed": 8.0, "cost_usd": 0.03}),
    ]
    prov = extract_provenance(events)
    assert prov["patient_zero"] == "a0"
    assert prov["catching_layer"] == "tripwire"
    assert prov["cost_usd"] == 0.03
    assert prov["synthetic"] is True  # unknown origin: safe default
    # meter report wins for cost and can mark a live recording
    live = extract_provenance(events, {"total_usd": 1.25, "live": True})
    assert live["cost_usd"] == 1.25 and live["synthetic"] is False
    # no infections, no freezes: honest Nones
    empty = extract_provenance([Ev(seq=0, agent_id="a3", kind="outcome",
                                  payload={"elapsed": 1.0})])
    assert empty["patient_zero"] is None and empty["catching_layer"] is None
    # missing/no-fallthrough: synthetic without explicit key is safe
    import pytest
    with pytest.raises(ValueError):
        receipt("r", OUTBREAK, {"patient_zero": "a0"})  # synthetic omitted
