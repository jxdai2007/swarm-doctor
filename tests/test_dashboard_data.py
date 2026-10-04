"""U13 dashboard data tests: counters equal U10 metrics for the fixture run."""
import json
import subprocess
import sys
from pathlib import Path
from contextlib import contextmanager
import socket
import threading
from urllib.parse import urlsplit

import httpx
import pytest
import uvicorn
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.make_fixture_runs import ARMS, outbreak_fixture, write_run  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from belowone.eval.replay import replay_freeze_schedule  # noqa: E402
from belowone.eval.metrics import outbreak_metrics, drift_metrics  # noqa: E402
from belowone.dashboard_data import snapshot, counters  # noqa: E402


def _fixture_result():
    events = outbreak_fixture()
    return events, replay_freeze_schedule(events, ARMS["below-one-verify"])


def test_counters_equal_u10_metrics_for_fixture_run():
    events, result = _fixture_result()
    outbreak = outbreak_metrics(result, events, seed=0)
    drift = drift_metrics(events, ARMS["below-one-verify"], total_agents=4)
    rows = {r["source"]: r["value"] for r in counters(outbreak, drift)}
    # canonical U10: a0's pre-cheat freeze removes a0/a1, but a1's retained
    # independent write still reaches a2 -> exactly one infection survives
    assert rows["metrics.infected"] == outbreak["infected"] == 1
    assert rows["metrics.r_mean"] == (outbreak["r_mean"]
                                      if outbreak["r_mean"] is not None
                                      else "unmeasured (no infections)")
    if outbreak["time_to_contain"]:
        assert rows["metrics.time_to_contain.seconds"] == \
            outbreak["time_to_contain"]["seconds"]
    assert rows["metrics.wasted_spend_usd"] == drift["wasted_spend_usd"]
    assert rows["metrics.false_steers"] == drift["false_steers"]
    # canonical provenance semantics: even a pre-cheat freeze leaves a2
    # reached through a1's retained independent write — R measured, not 0
    empty = replay_freeze_schedule(events, [("a0", 0.0)])
    z = outbreak_metrics(empty, events, seed=0)
    zrows = {r["source"]: r["value"] for r in counters(z)}
    assert zrows["metrics.infected"] == 1
    assert zrows["metrics.r_mean"] == 0.0
    # a genuinely zero-infection recording surfaces the unmeasured row
    from types import SimpleNamespace
    bare = [SimpleNamespace(seq=0, agent_id="a3", kind="outcome", paths=[],
                            payload={"elapsed": 9.0, "completed": True})]
    bresult = replay_freeze_schedule(bare, [])
    bz = outbreak_metrics(bresult, bare, seed=0)
    assert any("unmeasured" in str(row) for row in counters(bz))


def test_snapshot_labels_synthetic_and_carries_event_stream():
    events = outbreak_fixture()
    result = replay_freeze_schedule(events, ARMS["no-defense"])
    snap = snapshot(result.events, ARMS["no-defense"],
                    outbreak_metrics(result, events, seed=0))
    assert snap["synthetic"] is True
    assert set(snap["agents"]) == {"a0", "a1", "a2", "a3"}
    assert any(e["kind"] == "infection" for e in snap["events"])


def _state_at(snap_events, t):
    inf, frozen = set(), set()
    for e in snap_events:
        if (e.get("elapsed") or 0) > t:
            break
        if e["kind"] == "infection":
            inf.add(e["agent_id"])
        elif e["kind"] == "freeze":
            frozen.add(e["agent_id"])
    return inf, frozen


def test_fixture_runs_written_and_split_panels_share_relative_time(tmp_path):
    for arm in ARMS:
        write_run(tmp_path, arm)
    panels = {}
    for arm in ARMS:
        d = tmp_path / f"{arm}-fixture-outbreak"
        snap = json.loads((d / "snapshot.json").read_text())
        panels[arm] = snap
        assert snap["synthetic"] is True
    # all panels share one relative axis: the max event time across panels
    max_t = max((e.get("elapsed") or 0)
                for s in panels.values() for e in s["events"])
    for t in (5.0, 26.0, 45.0, max_t):
        states = {arm: _state_at(p["events"], t) for arm, p in panels.items()}
        # canonical U10: a0 frozen from 9.5; a1 prevented; a2 still reached
        inf_v, frozen_v = states["below-one-verify"]
        assert inf_v == ({"a2"} if t >= 40.0 else set())
        assert frozen_v == ({"a0"} if t >= 9.5 else set())
        # no-defense never freezes
        assert states["no-defense"][1] == set()


def test_strict_control_records_release_restores_active_and_retains_next_legit():
    """Actual U10 strict control records: freeze@1, release@2 -> the next
    recorded legit action at t=3 is RETAINED, agent state clean, released
    counter 1, frozen counter 0."""
    from belowone.eval.replay import replay_freeze_schedule
    from belowone.viz.cards import cites_metrics

    def ev(seq, agent, kind, elapsed, **payload):
        from types import SimpleNamespace
        paths = payload.pop("paths", [])
        payload.setdefault("action", {"operation": "write",
                                      "paths": paths})
        return SimpleNamespace(seq=seq, agent_id=agent, kind=kind,
                               paths=paths, payload={"elapsed": elapsed,
                                                     **payload})
    events = [
        ev(0, "a0", "action_executed", 0.5, completed=True, tool="write",
           paths=["reports/export.py"], action_id="x1"),
        ev(1, "a0", "action_executed", 1.0, completed=True, tool="write",
           paths=["utils/formatting.py"], action_id="x2"),
        ev(2, "a0", "action_executed", 3.0, completed=True, tool="write",
           paths=["reports/export.py"], action_id="x3"),
        ev(3, "a0", "outcome", 4.0, completed=True, waste=0.0, cost_usd=0.01),
    ]
    controls = [
        {"agent_id": "a0", "kind": "freeze", "elapsed": 1.0, "order": 1},
        {"agent_id": "a0", "kind": "release", "elapsed": 2.0, "order": 2},
    ]
    result = replay_freeze_schedule(events, controls=controls)
    snap = snapshot(result.events, result.controls,
                    {"infected": 0, "r_mean": None}, synthetic=True)
    assert snap["agents"]["a0"] == "clean"          # released -> active
    assert [c["kind"] for c in snap["controls"]] == ["freeze", "release"]
    assert sum(c["kind"] == "release" for c in snap["controls"]) == 1
    # the post-release legit action survives pruning, full payload retained
    kept_x3 = next(e for e in snap["events"] if e["seq"] == 2)
    assert kept_x3["payload"].get("action_id") == "x3"
    assert kept_x3["paths"] == ["reports/export.py"]
    assert snap["counters"][0]["value"] == 0  # sourced row, no fake metrics


@pytest.fixture(scope="module")
def browser():
    """Exercise the real DOM and vendored Cytoscape, not source-text assertions."""
    with sync_playwright() as pw:
        chromium = pw.chromium.launch()
        yield chromium
        chromium.close()


STREAM_SETUP = r"""
window.boardTest = {requests: [], controllers: []};
const nativeFetch = window.fetch.bind(window);
window.fetch = async (url, options) => {
  const path = new URL(url, location.href);
  if (path.pathname === "/events") {
    boardTest.requests.push(path.search);
    const number = boardTest.controllers.length;
    return new Response(new ReadableStream({start(controller) {
      boardTest.controllers.push(controller);
      boardTest.send(number ? boardTest.recovery : boardTest.initial, number);
      if (boardTest.complete) boardTest.done(number);
    }}), {headers: {"Content-Type": "text/event-stream"}});
  }
  if (path.pathname === "/snapshot") {
    boardTest.snapshotPending = true;
    return new Promise((resolve) => {
      boardTest.reply = (snapshot) => {
        resolve(new Response(JSON.stringify(snapshot)));
        boardTest.snapshotPending = false;
      };
    });
  }
  return nativeFetch(url, options);
};
boardTest.send = (value, number = 0) => {
  boardTest.controllers[number].enqueue(new TextEncoder().encode(
    "data: " + JSON.stringify(value) + "\n\n"));
};
boardTest.done = (number = 0) => boardTest.controllers[number].enqueue(
  new TextEncoder().encode("data: [done]\n\n"));
"""


def _mount_board(page, setup, query=""):
    page.add_init_script(STREAM_SETUP + setup)

    def serve(route):
        path = urlsplit(route.request.url).path
        asset = ROOT / "dashboard" / ("index.html" if path == "/" else path.lstrip("/"))
        route.fulfill(path=asset)

    page.route("http://dashboard.test/**", serve)
    page.goto("http://dashboard.test/" + query, wait_until="domcontentloaded")
    page.wait_for_function("document.getElementById('run-name').textContent !== 'loading run…'")


def _snapshot(seq=1, state="clean", events=(), controls=()):
    return {"last_seq": seq, "synthetic": True, "agents": {"a0": state},
            "graph": {"nodes": [], "edges": []}, "counters": [],
            "events": list(events), "controls": list(controls)}


def _event(seq, kind, elapsed=None):
    return {"seq": seq, "agent_id": "a0", "kind": kind,
            "elapsed": seq if elapsed is None else elapsed, "payload": {}}


def _scrub(page, value):
    page.evaluate("""value => {
      const slider = document.getElementById("scrub");
      slider.value = value;
      slider.dispatchEvent(new Event("input", {bubbles: true}));
    }""", str(value))


def _frames(page):
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


def test_live_rejects_stale_snapshot_and_manual_reconnect_resumes(browser):
    with browser.new_page() as page:
        _mount_board(page, "boardTest.initial = " + json.dumps(_snapshot()) + ";"
                     + "boardTest.recovery = " + json.dumps(_snapshot(4, "frozen")) + ";")
        # Bare / requests the engine stream, with no nonexistent fixture run.
        assert page.evaluate("boardTest.requests[0]") == "?after=0"
        page.evaluate("boardTest.send(" + json.dumps(_event(2, "action_executed")) + ")")
        page.wait_for_function("boardTest.snapshotPending === true")
        page.evaluate("boardTest.send(" + json.dumps(_event(3, "infection")) + ");"
                      + "boardTest.send(" + json.dumps(_event(4, "freeze")) + ")")
        page.wait_for_function("cy.getElementById('a0').hasClass('frozen')")
        page.evaluate("boardTest.reply(" + json.dumps(_snapshot(2)) + ")")
        _frames(page)
        assert page.evaluate("cy.getElementById('a0').hasClass('infected')")
        assert page.evaluate("cy.getElementById('a0').hasClass('frozen')")
        assert "infected 1" in page.locator("#r24").inner_text()
        page.evaluate("boardTest.controllers[0].error(new Error('connection lost'))")
        page.wait_for_function("document.getElementById('connection-status').textContent.includes('Disconnected')")
        assert page.locator("#reconnect").is_visible()
        page.locator("#reconnect").click()
        page.wait_for_function("boardTest.requests.length === 2")
        assert page.evaluate("boardTest.requests[1]") == "?after=4"
        page.evaluate("boardTest.send(" + json.dumps(_event(5, "release")) + ", 1)")
        page.wait_for_function("!cy.getElementById('a0').hasClass('frozen')")
        assert not page.locator("#reconnect").is_visible()
        assert page.locator("#ticker div").count() == 4


def test_replay_scrub_rebuilds_prefix_classes_ticker_counters_and_edges(browser):
    events = [_event(1, "infection"), _event(2, "freeze"),
              _event(3, "trace"), _event(4, "release"),
              _event(5, "kill"), _event(6, "end")]
    snap = _snapshot(6, "killed", events)
    snap["graph"] = {"nodes": [{"id": "file:reports.csv"}],
                     "edges": [{"source": "agent:a0", "target": "file:reports.csv",
                                "seq": 3, "elapsed": 3}]}
    with browser.new_page() as page:
        _mount_board(page, "boardTest.complete = true; boardTest.initial = "
                     + json.dumps(snap) + ";", "?run=recorded")
        page.wait_for_function("document.getElementById('connection-status').textContent.startsWith('Replay')")
        _scrub(page, 1000)
        assert page.evaluate("cy.getElementById('a0').hasClass('killed')")
        assert page.locator("#ticker div").count() == 6
        _scrub(page, 500)  # exact prefix through trace@3, before release/kill/end
        assert page.evaluate("cy.getElementById('a0').hasClass('frozen')")
        assert not page.evaluate("cy.getElementById('a0').hasClass('killed')")
        assert page.locator("#ticker div").count() == 3
        assert "seq:4" not in page.locator("#r24").inner_text()
        _scrub(page, 0)
        _frames(page)
        assert page.locator("#scrub").input_value() == "0"
        assert page.locator("#ticker div").count() == 0
        assert not page.evaluate("cy.getElementById('a0').hasClass('infected')")
        assert not page.evaluate("cy.getElementById('a0').hasClass('frozen')")
        assert not page.evaluate("cy.getElementById('a0').hasClass('ended')")
        assert page.evaluate("cy.edges()[0].style('display')") == "none"
        assert "infected 0" in page.locator("#r24").inner_text()
        assert "controls seq:" not in page.locator("#r24").inner_text()
        _scrub(page, 500)
        assert page.locator("#ticker div").count() == 3  # no duplicate tick lines


@contextmanager
def _serve_app(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        # Successful HTTP request is the readiness oracle.
        for _ in range(100):
            try:
                response = httpx.get(f"http://127.0.0.1:{port}/", timeout=1)
                if response.status_code == 200:
                    break
            except httpx.TransportError:
                threading.Event().wait(.01)
        else:
            raise AssertionError("dashboard server failed to start")
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()


def test_split_fetches_actual_display_artifacts_and_scrub_stops_play(browser, tmp_path):
    from scripts.serve_engine_smoke import smoke_app
    archive, display = tmp_path / "archive", tmp_path / "display"
    archive.mkdir()
    display.mkdir()
    payload = {"synthetic": True, "agents": {"a0": "frozen"}, "edges": [],
               "counters": [{"value": 0, "source": "metrics.r_mean"}],
               "events": [_event(1, "infection", 2)],
               "controls": [{"seq": "C2", "order": 2, "agent_id": "a0",
                             "kind": "freeze", "elapsed": 10}]}
    for arm in ("no-defense", "prompt-only", "verify"):
        folder = display / f"derived-{arm}"
        folder.mkdir()
        (folder / "snapshot.json").write_text(json.dumps(payload))
    before = {p: p.read_bytes() for p in display.rglob("*") if p.is_file()}
    with _serve_app(smoke_app(archive, display, tmp_path / "log")) as base:
        with browser.new_page() as page:
            page.goto(base + "/?mode=split", wait_until="domcontentloaded")
            page.wait_for_function("document.getElementById('connection-status').textContent.includes('three recorded arms')")
            assert page.locator("#split .panel").count() == 3
            assert page.evaluate("fetch('/snapshot?run=derived-verify').then(r => r.json())") == payload
            _scrub(page, 1000)
            assert page.locator('[data-role="frozen"]').all_text_contents() == ["1"] * 3
            _scrub(page, 250)
            _frames(page)
            assert page.locator("#scrub").input_value() == "250"
            assert page.locator('[data-role="frozen"]').all_text_contents() == ["0"] * 3
            assert "+2.0s" in page.locator("#clock").inner_text()
        with browser.new_page() as page:
            page.goto(base + "/", wait_until="domcontentloaded")
            page.wait_for_function("document.getElementById('run-name').textContent.startsWith('live')")
            assert page.evaluate("cy.getElementById('a0').nonempty()")
        assert httpx.post(base + "/ready", json={"agent_id": "a0"}).status_code == 401
    assert before == {p: p.read_bytes() for p in display.rglob("*") if p.is_file()}
    assert list(archive.iterdir()) == []


def test_capture_preps_write_only_new_display_and_reject_stale_outputs(tmp_path):
    from scripts.make_capture_preps import split_derived
    archive = ROOT / "experiments" / "committed" / "runs"
    source = archive / "pilot-0"
    original = {name: (source / name).read_bytes()
                for name in ("events.jsonl", "decisions.jsonl", "config.json", "manifest.json")}
    display = tmp_path / "display"
    folders = split_derived("test-source-commit", archive, display)
    assert [p.name for p in folders] == [
        "derived-no-defense", "derived-prompt-only", "derived-verify"]
    snap = json.loads((folders[0] / "snapshot.json").read_text())
    assert snap["provenance"]["synthetic"] is True
    assert snap["provenance"]["source_run"] == "pilot-0"
    assert snap["last_seq"] >= max(e["seq"] for e in snap["events"])
    for line in (folders[0] / "events.jsonl").read_text().splitlines():
        event = json.loads(line)
        retained = next(e for e in snap["events"] if e["seq"] == event["seq"])
        assert event["payload"] == retained["payload"]
        assert event["paths"] == retained["paths"]
    # Re-running identical sources validates rather than rewriting output.
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in display.rglob("*") if p.is_file()}
    split_derived("test-source-commit", archive, display)
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns)
                      for p in display.rglob("*") if p.is_file()}
    altered = dict(snap, agents={"fabricated": "infected"})
    path = folders[0] / "snapshot.json"
    path.write_text(json.dumps(altered))
    damaged = path.read_bytes()
    with pytest.raises(ValueError, match="choose a new --display-root"):
        split_derived("test-source-commit", archive, display)
    assert path.read_bytes() == damaged
    assert original == {name: (source / name).read_bytes() for name in original}
