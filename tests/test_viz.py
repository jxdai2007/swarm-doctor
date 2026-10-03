"""U14 viz tests: deterministic regeneration, metric-bound cards, receipts."""
import json
import xml.etree.ElementTree as ET

from belowone.viz import figures
from belowone.viz.cards import cites_metrics, drift_card, outbreak_card
from belowone.viz.receipts import receipt, render

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
    assert row["time_to_contain_seconds"] == 19.5
    assert row["synthetic"] is True
    text = render(row)
    assert "SYNTHETIC DEV" in text and "a0" in text and "tripwire" in text
    # unbound receipt values fail loud, never silently printed
    import pytest
    with pytest.raises(ValueError):
        receipt("x", {"infected": 9, "time_to_contain": None}, {})
