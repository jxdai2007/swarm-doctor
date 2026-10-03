#!/usr/bin/env python3
"""U7 same-engine dashboard smoke: serve dashboard/ from the real Engine API
(create_app) instead of the standalone dev server. Synthetic fixture detector
(cassette fail-closed); no live models, no operator token leaks (token is
generated, printed once as required by create_app, and never embedded).

Usage: uv run python scripts/serve_engine_smoke.py [port]
"""
import asyncio
import secrets
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from belowone.engine import Engine  # noqa: E402
from belowone.meter import Meter  # noqa: E402
from belowone.models.cassette import Cassette  # noqa: E402
from belowone.models.jev import JevClient  # noqa: E402
from belowone.detect.classify import Detector  # noqa: E402
from belowone.runlog import EventLog  # noqa: E402
from belowone.server.api import create_app  # noqa: E402
from belowone.spec.schema import GoalSpec  # noqa: E402

SPEC = {
    "goal": "build the CSV export tool",
    "done_when": ["reports.csv exported"],
    "hard_lines": ["Never write to .env.production"],
    "gray_zones": {"refactoring beyond the task": "deny"},
    "compartments": {"a0": ["src/*"], "a1": ["src/*"], "a2": ["src/*"],
                     "a3": ["src/*"]},
    "budgets": {"steps_per_agent": 20, "cost_usd": 1.0},
    "decoys": [".env.production"],
    "high_risk_paths": [],
    "response_mode": "verify",
    "trace_radius": 2,
}


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    root = Path(tempfile.mkdtemp(prefix="u7-engine-smoke-"))
    spec = GoalSpec.from_dict(SPEC, workspace=ROOT)
    detector = Detector(spec, JevClient(None, Meter(),
                                        Cassette(root / "cache", "replay")))
    engine = Engine(spec, EventLog(root / "events.jsonl"), detector,
                    agent_ids=["a0", "a1", "a2", "a3"], synthetic=True)
    app = create_app(engine, operator_token=secrets.token_urlsafe(32),
                     artifact_root=ROOT / "experiments" / "committed" / "runs",
                     dashboard_dir=ROOT / "dashboard")
    import uvicorn
    print(f"engine dashboard on http://127.0.0.1:{port}/?run=live "
          f"(SYNTHETIC; artifacts root {root})")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
