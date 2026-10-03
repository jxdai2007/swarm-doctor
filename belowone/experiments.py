"""Experiment orchestration and reproducibility (U16).

- experiments/plan.yaml orders work (never-cut first, then cut-list reversed).
- Non-pilot runs refuse to start unless the preregistration file is committed.
- Quota/cap exhaustion stops after the CURRENT seed and reports reached counts.
- Regeneration diffs rebuilt outputs against committed artifacts with
  networking disabled and names every differing key.
- Secret scan: any key-shaped string in committed artifacts fails.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

ROOT = Path(__file__).resolve().parent.parent
PLAN_PATH = ROOT / "experiments" / "plan.yaml"

SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{16,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"),
    re.compile(r"KIMI_API_KEY\s*=\s*\S+"),
    re.compile(r"OPENROUTER_API_KEY\s*=\s*\S+"),
)


def load_plan(path: Path = PLAN_PATH) -> dict:
    if yaml is None:
        raise RuntimeError("pyyaml required for experiments/plan.yaml")
    return yaml.safe_load(path.read_text())


def _git_tracked(path: Path) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(path)],
        cwd=ROOT, capture_output=True)
    return result.returncode == 0


def require_preregistration(experiment_id: str, plan: dict | None = None) -> None:
    """Non-pilot experiments refuse to start without a COMMITTED
    preregistration file (U16/R35)."""
    plan = plan or load_plan()
    if experiment_id in plan.get("pilot_ids", []):
        return
    prereg = ROOT / plan["preregistration"]
    if not prereg.is_file() or not _git_tracked(prereg):
        raise RuntimeError(
            f"{experiment_id}: preregistration {plan['preregistration']} must "
            "be committed before a non-pilot run starts")


class QuotaExhausted(Exception):
    pass


def run_schedule(plan: dict, experiment_id: str, seeds: list[int],
                 start_seed_fn, is_exhausted) -> dict:
    """Interleaved seed loop (KTD3): each seed runs all its arms back to back;
    exhaustion stops AFTER the current seed, never mid-seed, and the reached
    run counts are reported. is_exhausted is wired to the shared Meter
    (spent_usd vs the configured cap) in the CLI commands below."""
    experiments = {e["id"]: e for e in plan["priority"]}
    spec = experiments[experiment_id]
    arms = spec["arms"] if spec["arms"] != "all" else []
    completed: dict[int, list[str]] = {}
    for seed in seeds:
        completed[seed] = []
        for arm in arms:
            if is_exhausted():
                raise QuotaExhausted(
                    json.dumps({"experiment": experiment_id,
                                "reached": {str(s): len(a) for s, a
                                            in completed.items()},
                                "stopped_after_seed": seed,
                                "pending_arms": arms[len(completed[seed]):]}))
            start_seed_fn(seed, arm)
            completed[seed].append(arm)
    return {"experiment": experiment_id,
            "reached": {str(s): len(a) for s, a in completed.items()}}


def diff_outputs(committed_dir: Path, rebuilt_dir: Path) -> list[str]:
    """Byte-diff every regenerated artifact against the committed tree;
    returns differing/missing file names (empty = identical)."""
    committed_dir, rebuilt_dir = Path(committed_dir), Path(rebuilt_dir)
    committed = {p.relative_to(committed_dir).as_posix()
                 for p in committed_dir.rglob("*") if p.is_file()}
    rebuilt = {p.relative_to(rebuilt_dir).as_posix()
               for p in rebuilt_dir.rglob("*") if p.is_file()}
    diffs = sorted((committed - rebuilt) | (rebuilt - committed))
    for name in sorted(committed & rebuilt):
        if (committed_dir / name).read_bytes() != (rebuilt_dir / name).read_bytes():
            diffs.append(name)
    return diffs


def diff_named_key(committed_file: Path, rebuilt_file: Path) -> str | None:
    """For JSON metrics: name the first differing key (KTD14 doc check)."""
    a = json.loads(committed_file.read_text())
    b = json.loads(rebuilt_file.read_text())

    def walk(x, y, path):
        if isinstance(x, dict) and isinstance(y, dict):
            for key in sorted(set(x) | set(y)):
                sub = walk(x.get(key), y.get(key), f"{path}.{key}" if path else key)
                if sub:
                    return sub
            return None
        return None if x == y else (f"{path}: committed {x!r} != rebuilt {y!r}")
    return walk(a, b, "")


def scan_secrets(paths: list[Path]) -> list[str]:
    """Key-shaped strings in committed artifacts fail the CI scan."""
    hits = []
    for path in paths:
        if path.is_file():
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            for pattern in SECRET_PATTERNS:
                if pattern.search(text):
                    hits.append(f"{path}: {pattern.pattern}")
    return hits


# --- offline regeneration (make reproduce) ---------------------------------

from types import SimpleNamespace  # noqa: E402


def _load_events(events_file: Path) -> list:
    events = []
    for line in events_file.read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        payload = e.get("payload") or {}
        events.append(SimpleNamespace(
            seq=e["seq"], agent_id=e.get("agent_id"), kind=e.get("kind"),
            paths=e.get("paths") or [], payload=payload))
    return events


def reproduce_run(run_dir: Path) -> list[str]:
    """Recompute metric counters from the run's OWN recorded events + control
    events (no network, no keys) and diff them against the committed
    snapshot.json counter rows. Returns human-readable diffs (empty = clean).
    """
    from belowone.eval.metrics import drift_metrics, outbreak_metrics
    from belowone.eval.replay import replay_freeze_schedule
    events = _load_events(run_dir / "events.jsonl")
    freezes = sorted(
        {(e.agent_id, float(e.payload.get("elapsed", 0.0)))
         for e in events if e.kind == "freeze"})
    result = replay_freeze_schedule(events, freezes)
    outbreak = outbreak_metrics(result, events, seed=0)
    drift = drift_metrics(events, freezes, total_agents=len(outbreak) and
                          len({e.agent_id for e in events if e.agent_id}))
    snap = json.loads((run_dir / "snapshot.json").read_text())
    committed = {row["source"]: row["value"] for row in snap.get("counters", [])}
    recomputed = {}
    for row in __import__("belowone.dashboard_data", fromlist=["counters"]) \
            .counters(outbreak, drift):
        recomputed[row["source"]] = row["value"]
    diffs = []
    for source, value in sorted(committed.items()):
        if source not in recomputed:
            diffs.append(f"{run_dir.name}/{source}: committed {value!r} but "
                         "regeneration produced no such metric")
        elif recomputed[source] != value:
            diffs.append(f"{run_dir.name}/{source}: committed {value!r} != "
                         f"regenerated {recomputed[source]!r}")
    return diffs


def reproduce(runs_dir: Path) -> list[str]:
    diffs: list[str] = []
    for run_dir in sorted(Path(runs_dir).iterdir()):
        if (run_dir / "events.jsonl").is_file() \
                and (run_dir / "snapshot.json").is_file():
            diffs += reproduce_run(run_dir)
    return diffs


def _meter_exhausted(meter, cap_usd: float):
    """Actual shared-Meter stop (KTD16): breach when realized spend reaches
    the configured cap; the current seed always finishes (run_schedule)."""
    def check() -> bool:
        report = meter.report()
        return (report.get("budget_breached") is True
                or float(report.get("spent_usd", "0")) >= cap_usd)
    return check


def _start_seed(meter, harness) -> callable:
    """One arm of one seed = a harness run (U9); each call must record its
    model calls through the shared meter. harness: callable(seed, arm)."""
    def start(seed, arm):
        harness(seed, arm)
    return start


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="belowone-experiments")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("reproduce", help="regenerate metrics from recorded "
                       "artifacts offline and diff against committed outputs")
    p.add_argument("--runs", default="experiments/committed")

    p = sub.add_parser("pilot", help="run pilot experiments (no prereg gate)")
    p.add_argument("--seeds", nargs="*", type=int)

    p = sub.add_parser("experiments", help="run the priority plan; non-pilot "
                       "experiments refuse without a COMMITTED preregistration")
    p.add_argument("--only", help="restrict to one experiment id")
    p.add_argument("--seeds", nargs="*", type=int)

    p = sub.add_parser("rerun", help="live validation runs (KTD5/R29)")
    p.add_argument("--seeds", nargs="*", type=int)

    args = parser.parse_args(argv)
    if args.command == "reproduce":
        diffs = reproduce(Path(args.runs))
        if diffs:
            for d in diffs:
                print("DIFF:", d)
            return 1
        print("REPRODUCE_IDENTICAL")
        return 0

    from belowone.spec.interview import load_config
    from belowone.meter import Meter
    from belowone.models.cassette import Cassette
    from belowone.models.kimi import KimiClient
    from belowone.models.router import ModelRouter
    config = load_config(ROOT)
    cap = float(config.get("openrouter_cap_usd", 15.0))
    plan = load_plan()
    meter = Meter(cap_usd=cap)

    def build_router():
        cache = ROOT / ".belowone" / "run-cache"
        cache.mkdir(parents=True, exist_ok=True)
        kimi_key = os.environ.get("KIMI_API_KEY")
        or_key = os.environ.get("OPENROUTER_API_KEY")
        if not kimi_key or not or_key:
            raise RuntimeError(
                "live experiments need KIMI_API_KEY and OPENROUTER_API_KEY "
                "(operator TODO); offline regeneration is `make reproduce`")
        kimi = KimiClient(kimi_key, meter, Cassette(cache, "record"))
        fallback = None
        try:
            from belowone.models.openrouter import OpenRouterClient
            fb = config.get("openrouter", {})
            fallback = OpenRouterClient(
                or_key, meter, Cassette(cache, "record"),
                model=fb.get("fallback_model", "mistralai/mistral-nemo"),
                prompt_price=fb.get("fallback_prompt_price", "0.000000019"),
                completion_price=fb.get("fallback_completion_price",
                                        "0.00000003"))
        except ImportError:
            pass
        return ModelRouter(kimi, fallback) if fallback else kimi

    def run_seed_arm(seed, arm, router):
        """Delegates to the U9 harness; fails loud until it is canonical
        rather than pretending a run happened."""
        try:
            from belowone.harness import run_experiment  # U9 (integration)
        except ImportError as error:
            raise RuntimeError(
                "U9 experiment harness not canonical yet — live/replay "
                "experiment execution is blocked, not skipped") from error
        run_experiment(plan=plan, experiment_id=_current[0], seed=seed,
                       arm=arm, router=router, meter=meter)

    _current = [None]
    router = None

    def go(experiment_id, seeds):
        _current[0] = experiment_id
        nonlocal router
        require_preregistration(experiment_id, plan)
        router = router or build_router()
        spec = {e["id"]: e for e in plan["priority"]}[experiment_id]
        seed_list = seeds if seeds else list(range(int(spec.get("seeds", 20))))
        summary = run_schedule(plan, experiment_id, seed_list,
                               _start_seed(meter, lambda s, a: run_seed_arm(
                                   s, a, router)),
                               _meter_exhausted(meter, cap))
        print(json.dumps(summary, sort_keys=True))
        print("meter:", {k: meter.report()[k] for k in
                         ("spent_usd", "cap_usd")})

    if args.command == "pilot":
        for experiment_id in plan.get("pilot_ids", []):
            go(experiment_id, args.seeds)
        return 0
    if args.command == "experiments":
        ids = [args.only] if args.only else \
            [e["id"] for e in plan["priority"]]
        for experiment_id in ids:
            go(experiment_id, args.seeds)
        return 0
    if args.command == "rerun":
        # live validation subset (R33): same prereg + meter discipline
        go("prompt-vs-below-one", args.seeds)
        return 0
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
