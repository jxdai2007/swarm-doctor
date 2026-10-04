"""U14 viz tests: deterministic regeneration, metric-bound cards, receipts."""
import json
import xml.etree.ElementTree as ET

import pytest

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


SVG = "{http://www.w3.org/2000/svg}"


def _text_box(node):
    """Conservative monospace cell bounds, including baseline descent."""
    size = float(node.attrib["font-size"])
    width = len(node.text or "") * size * 0.65
    x, y = float(node.attrib["x"]), float(node.attrib["y"])
    anchor = node.attrib["text-anchor"]
    left = x - (width / 2 if anchor == "middle" else width if anchor == "end" else 0)
    return left, y - size, left + width, y + size * 0.25


def _assert_readable_text(root, nodes):
    width, height = float(root.attrib["width"]), float(root.attrib["height"])
    boxes = [_text_box(node) for node in nodes]
    for left, top, right, bottom in boxes:
        assert 0 <= left < right <= width
        assert 0 <= top < bottom <= height
    for i, (left, top, right, bottom) in enumerate(boxes):
        for other_left, other_top, other_right, other_bottom in boxes[i + 1:]:
            assert (right <= other_left or other_right <= left
                    or bottom <= other_top or other_bottom <= top)


def test_r_bar_full_labels_and_caption_have_separate_visible_geometry():
    per_arm = {
        "SYNTHETIC: no-defense": {"mean": 0.5, "ci95": [0.0, 1.0]},
        "SYNTHETIC: periodic-review": {"mean": 0.0, "ci95": [0.0, 0.0]},
        "SYNTHETIC: verify & isolate": {"mean": None, "ci95": None},
        "SYNTHETIC: " + "long-arm-name-" * 8: {"mean": 0.75, "ci95": None},
    }
    svg, data = figures.r_bar(per_arm)
    root = ET.fromstring(svg)
    texts = root.findall(f"{SVG}text")
    assert {arm for arm, value in per_arm.items() if value["mean"] is not None} <= {
        node.text for node in texts}
    assert "SYNTHETIC: verify & isolate: R unmeasured (zero infections)" in {
        node.text for node in texts}
    _assert_readable_text(root, texts)
    caption = next(node for node in texts if node.text.startswith("R = 1"))
    bars = [node for node in root.findall(f"{SVG}rect")
            if node.attrib.get("fill-opacity") == "0.35"]
    assert _text_box(caption)[3] < min(float(bar.attrib["y"]) for bar in bars)
    assert json.loads(data)["per_arm"] == per_arm
    assert figures.r_bar(dict(reversed(list(per_arm.items())))) == (svg, data)


def test_epidemic_full_legend_is_visible_and_outside_plot():
    per_arm = {
        "SYNTHETIC: periodic-review": [(0, 0), (25, 2)],
        "SYNTHETIC: verify & isolate " + "long-arm-" * 12: [(0, 0), (25, 0)],
    }
    svg, data = figures.epidemic_curve(per_arm)
    root = ET.fromstring(svg)
    legend = [node for node in root.findall(f"{SVG}text") if node.text in per_arm]
    assert {node.text for node in legend} == set(per_arm)
    _assert_readable_text(root, root.findall(f"{SVG}text"))
    plot_bottom = max(float(point.split(",")[1])
                      for line in root.findall(f"{SVG}polyline")
                      for point in line.attrib["points"].split())
    assert min(_text_box(node)[1] for node in legend) > plot_bottom
    markers = [node for node in root.findall(f"{SVG}rect") if "x" in node.attrib]
    assert len(markers) == len(legend)
    for marker, label in zip(markers, legend):
        assert float(marker.attrib["x"]) + float(marker.attrib["width"]) < _text_box(label)[0]
    assert json.loads(data)["per_arm"] == {
        arm: [list(point) for point in series] for arm, series in per_arm.items()}


def test_epidemic_subsecond_and_fractional_ticks_are_distinct():
    svg, data = figures.epidemic_curve({"observed": [(0, 0), (0.06, 3)]})
    root = ET.fromstring(svg)
    texts = root.findall(f"{SVG}text")
    x_ticks = [node.text for node in texts if node.attrib["text-anchor"] == "middle"]
    y_ticks = [node.text for node in texts if node.attrib["text-anchor"] == "end"]
    assert x_ticks == ["0.015s", "0.03s", "0.045s", "0.06s"]
    assert y_ticks == ["0.75", "1.5", "2.25", "3"]
    _assert_readable_text(root, texts)
    payload = json.loads(data)
    assert payload["t_max"] == 0.06 and payload["y_max"] == 3


@pytest.mark.parametrize("per_arm", [
    {"single": {"mean": 9.0, "ci95": None}},
    {"missing-first": {"mean": 9.0, "ci95": None},
     "measured": {"mean": 2.0, "ci95": [1.0, 3.0]}},
    {"measured": {"mean": 2.0, "ci95": [1.0, 3.0]},
     "missing-last": {"mean": 9.0}},
    {"own-bound-below-mean": {"mean": 9.0, "ci95": [1.0, 3.0]}},
])
def test_r_bar_every_mean_scales_visible_plot_independently(per_arm):
    svg, data = figures.r_bar(per_arm)
    root = ET.fromstring(svg)
    bars = [node for node in root.findall(f"{SVG}rect")
            if node.attrib.get("fill-opacity") == "0.35"]
    assert len(bars) == len(per_arm)
    for bar in bars:
        x, y = float(bar.attrib["x"]), float(bar.attrib["y"])
        width, height = float(bar.attrib["width"]), float(bar.attrib["height"])
        assert 0 <= x < x + width <= float(root.attrib["width"])
        assert 0 < y < y + height < float(root.attrib["height"])
    highest = min(bars, key=lambda bar: float(bar.attrib["y"]))
    bottom = float(highest.attrib["y"]) + float(highest.attrib["height"])
    threshold = next(node for node in root.findall(f"{SVG}polyline")
                     if node.attrib["stroke"] == "#ffd27a")
    threshold_y = float(threshold.attrib["points"].split()[0].split(",")[1])
    assert float(highest.attrib["height"]) / (bottom - threshold_y) == pytest.approx(9.0)
    _assert_readable_text(root, root.findall(f"{SVG}text"))
    assert json.loads(data)["per_arm"] == per_arm


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
