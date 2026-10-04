"""Submission binding, executable monitor handoff, and capture failure regressions."""
import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from belowone import experiments
from belowone.eval import monitor
from belowone.viz.doc_templates import (
    AUTHORED_TEMPLATES, build_authored, build_docs, compare_authored, load_metrics,
)
from scripts import capture_video, check_doc_numbers

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def submission(tmp_path):
    source = ROOT / "experiments/derived/analysis.json"
    if not source.exists():
        # Fresh clone: derived/ is a gitignored build output; rebuild it from the
        # sealed archives exactly as `make regenerate` does (keyless, offline).
        experiments.regenerate(ROOT / "experiments/committed/runs", ROOT / "experiments/derived")
    metrics = load_metrics([source])
    build_authored(metrics, tmp_path)
    build_docs(metrics, tmp_path / "docs/generated")
    (tmp_path / "docs/plans").symlink_to(ROOT / "docs/plans", target_is_directory=True)
    (tmp_path / "clips").symlink_to(ROOT / "clips", target_is_directory=True)
    args = ["--root", str(tmp_path), "--metrics-glob", str(source)]
    return tmp_path, metrics, args


def test_clean_source_render_and_links_pass(submission):
    root, metrics, args = submission
    assert compare_authored(metrics, root) == []
    assert check_doc_numbers.main(args) == 0
    assert set(AUTHORED_TEMPLATES) == {
        "README.md", "docs/writeup.md", "docs/threat-model.md",
        "docs/capability-table.md", "docs/video-script.md",
    }


@pytest.mark.parametrize("name", [
    "README.md", "docs/writeup.md", "docs/threat-model.md",
    "docs/capability-table.md", "docs/video-script.md",
])
def test_entire_authored_document_bound_not_number_heuristic(submission, name):
    root, _, args = submission
    path = root / name
    path.write_text(path.read_text() + "\nMeasured live R: 0.01.\n")
    assert check_doc_numbers.main(args) == 1


def test_real_authored_empirical_number_negative_control(submission):
    root, metrics, args = submission
    altered_metrics = dict(metrics)
    altered_metrics["hypotheses.h1.n"] = metrics["hypotheses.h1.n"] + 999
    build_authored(altered_metrics, root)
    assert check_doc_numbers.main(args) == 1
    build_authored(metrics, root)
    assert check_doc_numbers.main(args) == 0


def test_missing_sources_fail_closed(submission):
    root, _, _ = submission
    assert check_doc_numbers.main([
        "--root", str(root), "--metrics-glob", "missing/analysis.json",
    ]) == 1


def test_append_only_generated_edit_fails(submission):
    root, _, args = submission
    path = root / "docs/generated/README-metrics.md"
    path.write_bytes(path.read_bytes() + b"\nFalse additional measured result: 0.01\n")
    assert check_doc_numbers.main(args) == 1


def test_reproduce_compares_authored_without_repair(submission, tmp_path, monkeypatch):
    root, metrics, _ = submission
    # Isolate the source-binding boundary, not a full study regeneration.
    derived = tmp_path / "derived"
    derived.mkdir()
    source = ROOT / "experiments/derived/analysis.json"
    shutil.copyfile(source, derived / "analysis.json")

    def regenerate_source(runs, out, **kwargs):
        out.mkdir()
        shutil.copyfile(source, out / "analysis.json")

    monkeypatch.setattr(experiments, "regenerate", regenerate_source)
    assert experiments.reproduce(tmp_path / "runs", derived, submission_root=root) == []
    path = root / "docs/writeup.md"
    altered_metrics = dict(metrics)
    altered_metrics["hypotheses.h1.n"] = metrics["hypotheses.h1.n"] + 999
    build_authored(altered_metrics, root)
    edited = path.read_bytes()
    diffs = experiments.reproduce(tmp_path / "runs", derived, submission_root=root)
    assert any("docs/writeup.md" in diff for diff in diffs)
    assert path.read_bytes() == edited


def test_monitor_handoff_uses_real_pilot_dirs_and_export(tmp_path):
    runs = sorted((ROOT / "experiments/committed/runs").glob("pilot-*/"))
    sample = tmp_path / "sample.jsonl"
    labels = tmp_path / "labels.jsonl"
    report = tmp_path / "report.json"
    assert monitor.main([
        "sample", "--runs", *map(str, runs), "--per-stratum", "50",
        "--seed", "0", "--out", str(sample),
    ]) == 0
    rows = [json.loads(line) for line in sample.read_text().splitlines()]
    assert rows
    # Test-only synthetic annotations; never written to operator labels.
    labels.write_text("".join(json.dumps({"event_id": row["event_id"],
                                          "label": row["stratum"]}) + "\n"
                              for row in rows))
    assert monitor.main([
        "analyze", "--labels", str(labels), "--checks",
        str(ROOT / "experiments/derived/monitor-checks.json"), "--out", str(report),
    ]) == 0
    assert json.loads(report.read_text())["synthetic"] is True


@pytest.fixture
def snapshot_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()



def test_required_capture_real_http_failure_rejects_stale_inventory(
        tmp_path, monkeypatch, snapshot_server):
    stale = tmp_path / "intro-board-SYNTHETIC-DEV.webm"
    stale.write_bytes(b"prior invocation, not evidence")
    (tmp_path / "inventory.json").write_text(json.dumps({"produced": [stale.name]}))
    monkeypatch.setattr(capture_video, "build_pages", lambda *args, **kwargs: ({}, False))
    monkeypatch.setattr(capture_video, "storyboard", lambda base, *args, **kwargs: [
        ("intro-board-SYNTHETIC-DEV", base + "/?run=pilot-0", 0, "board", {"synthetic": True}),
    ])
    assert capture_video.main(["--out", str(tmp_path), "--base", snapshot_server]) == 1
    inventory = json.loads((tmp_path / "inventory.json").read_text())
    assert inventory["status"] == "failed"
    assert inventory["produced"] == []
    assert inventory["failures"][0]["beat"] == "intro-board-SYNTHETIC-DEV"
    assert stale.read_bytes() == b"prior invocation, not evidence"


async def test_capture_assert_reads_real_archived_sse_events(tmp_path):
    """Seam regression: assert_board validates the REAL /events?run=… SSE stream."""
    import asyncio
    import socket
    from contextlib import asynccontextmanager

    import httpx
    import uvicorn

    from belowone.server.api import create_app

    log = tmp_path / "pilot-0" / "events.jsonl"
    log.parent.mkdir()
    events = [
        {"agent_id": "a0", "kind": "action_executed", "paths": ["task.py"],
         "payload": {"elapsed": 1.0}},
        {"agent_id": "a1", "kind": "infection", "paths": ["task.py"],
         "payload": {"elapsed": 2.0}},
    ]
    from belowone.runlog import EventLog

    logger = EventLog(log)
    for event in events:
        logger.append(**event)
    (log.parent / "snapshot.json").write_text(json.dumps({
        "agents": {"a0": "infected", "a1": "infected"},
        "counters": [{"label": "Infected agents", "source": "manifest infection events",
                      "value": 2}],
        "graph": {"edges": [], "nodes": []},
        "synthetic": True,
    }))
    engine = SimpleNamespace(
        lifecycle=SimpleNamespace(states={"a0": "active"}),
    )
    app = create_app(engine, operator_token="operator-private-capability-123456789",
                     agent_tokens={"a0": "agent-private-capability-123456789"},
                     artifact_root=tmp_path)

    @asynccontextmanager
    async def serving():
        config = uvicorn.Config(app, log_level="error", lifespan="off")
        server = uvicorn.Server(config)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            task = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                while not server.started:
                    if task.done():
                        await task
                    await asyncio.sleep(.01)
                yield f"http://127.0.0.1:{port}"
            finally:
                server.should_exit = True
                await asyncio.wait_for(task, 5)

    async def probe():
        async with serving() as base:
            await asyncio.to_thread(
                capture_video.assert_board, base, base + "/?run=pilot-0", "board-events")
            def missing_freeze():
                with pytest.raises(RuntimeError, match="no actual freeze event"):
                    capture_video.assert_board(base, base + "/?run=pilot-0", "freeze-event")
            await asyncio.to_thread(missing_freeze)
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.get(base + "/events?run=pilot-0")
            return response.status_code

    assert await probe() == 200


def test_optional_source_absence_not_required_capture_failure(
        tmp_path, monkeypatch, snapshot_server):
    monkeypatch.setattr(capture_video, "build_pages", lambda *args, **kwargs: ({}, False))
    monkeypatch.setattr(capture_video, "storyboard", lambda base, *args, **kwargs: [
        ("everyday-drift-SYNTHETIC-DEV", base + "/?run=drift-demo", 0, "steer", {"synthetic": True}),
    ])
    assert capture_video.main(["--out", str(tmp_path), "--base", snapshot_server]) == 0
    inventory = json.loads((tmp_path / "inventory.json").read_text())
    assert inventory["produced"] == []
    assert inventory["skipped"][0]["beat"] == "everyday-drift-SYNTHETIC-DEV"


def test_reported_stale_video_cannot_be_current_success(tmp_path, monkeypatch):
    stale = tmp_path / "intro-board-SYNTHETIC-DEV.webm"
    stale.write_bytes(b"prior capture")
    monkeypatch.setattr(capture_video, "build_pages", lambda *args, **kwargs: ({}, False))
    monkeypatch.setattr(capture_video, "storyboard", lambda base, *args, **kwargs: [
        ("intro-board-SYNTHETIC-DEV", "file:///unused", 0, "static", {"synthetic": True}),
    ])
    monkeypatch.setattr(capture_video, "capture_one", lambda *args, **kwargs: stale)
    assert capture_video.main(["--out", str(tmp_path)]) == 1
    inventory = json.loads((tmp_path / "inventory.json").read_text())
    assert inventory["produced"] == []
    assert "no current invocation video" in inventory["failures"][0]["reason"]


def test_present_optional_source_capture_failure_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_video, "build_pages", lambda *args, **kwargs: ({}, False))
    monkeypatch.setattr(capture_video, "storyboard", lambda base, *args, **kwargs: [
        ("everyday-drift-SYNTHETIC-DEV", "file:///unused", 0, "steer", {"synthetic": True}),
    ])

    def failed_browser(*args):
        raise RuntimeError("present drift source, browser capture failed")

    monkeypatch.setattr(capture_video, "capture_one", failed_browser)
    assert capture_video.main(["--out", str(tmp_path)]) == 1
    inventory = json.loads((tmp_path / "inventory.json").read_text())
    assert inventory["skipped"] == []
    assert inventory["produced"] == []


def test_only_current_stage_video_published(tmp_path, monkeypatch):
    stale = tmp_path / "unrelated-old-clip.webm"
    stale.write_bytes(b"old")
    monkeypatch.setattr(capture_video, "build_pages", lambda *args, **kwargs: ({}, False))
    monkeypatch.setattr(capture_video, "storyboard", lambda base, *args, **kwargs: [
        ("current-test-beat", "file:///unused", 0, "static", {"synthetic": True}),
    ])

    def current_video(name, url, seconds, kind, stage, base, source=None):
        # Test-only bytes exercise publication, not a scientific video claim.
        path = stage / (name + ".webm")
        path.write_bytes(b"current invocation test payload")
        return path

    monkeypatch.setattr(capture_video, "capture_one", current_video)
    assert capture_video.main(["--out", str(tmp_path)]) == 0
    inventory = json.loads((tmp_path / "inventory.json").read_text())
    assert [row["file"] for row in inventory["produced"]] == ["current-test-beat.webm"]
    assert (tmp_path / inventory["produced"][0]["file"]).is_file()
    assert stale.read_bytes() == b"old"
