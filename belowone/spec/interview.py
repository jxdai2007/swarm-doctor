"""Setup interview (U11): scan, ask <=8 typed questions, dry-run the REAL U5
detection stack, apply validated corrections, lock through U3.

Model modes (--model-mode):
  live      real Kimi interviewer + real OpenRouter Jev + real Kimi judge,
            shared Meter, cassettes recording; needs KIMI_API_KEY and
            OPENROUTER_API_KEY (from environment or .env).
  replay    same clients, cassette replay, no keys; cache miss fails closed.
  synthetic no live calls: heuristic recommendations + Jev cassette replay;
            every deny is labeled offline fail-closed, never a model verdict.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from belowone.detect.classify import Detector
from belowone.meter import Meter
from belowone.models.cassette import Cassette
from belowone.models.jev import JevClient
from belowone.models.kimi import KimiClient
from belowone.spec.lock import load_locked_spec, lock_spec, spec_hash
from belowone.spec.schema import GoalSpec

MAX_QUESTIONS = 8
DECOY_PATTERNS = (".env*", "*secret*", "*credential*", "*.production*",
                  ".npmrc", ".netrc")
SECRET_PATTERNS = ("id_rsa*", "*.pem", "*.key", ".netrc")
CONFIG_PATTERNS = ("config*", "*.yaml", "*.yml", "*.toml", "*.ini")
SOURCE_PATTERNS = ("*.py", "*.md", "*.csv", "*.ts", "*.js")
RESPONSE_MODES = ("verify", "strict")
CORRECTABLE_LISTS = ("decoys", "hard_lines", "high_risk_paths")


def load_config(root: Path | None = None) -> dict:
    """config/default.yaml (JSON-subset safe via yaml if present)."""
    path = (root or Path.cwd()) / "config" / "default.yaml"
    if not path.is_file():
        path = Path(__file__).resolve().parents[2] / "config" / "default.yaml"
    try:
        import yaml
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def load_env(root: Path) -> None:
    """Populate os.environ from workspace .env without overriding existing."""
    env = root / ".env"
    if not env.is_file():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def scan_workspace(root: Path) -> dict[str, list[str]]:
    """Relative names of decoy/config/test/source/secret-like files."""
    found = {"decoys": [], "secrets": [], "configs": [], "tests": [], "sources": []}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        name = path.name.lower()
        if _matches(name, DECOY_PATTERNS):
            found["decoys"].append(rel)
        if _matches(name, SECRET_PATTERNS):
            found["secrets"].append(rel)
        if _matches(name, CONFIG_PATTERNS):
            found["configs"].append(rel)
        if name.startswith("test_") or name.endswith("_test.py"):
            found["tests"].append(rel)
        elif _matches(name, SOURCE_PATTERNS) and rel not in found["decoys"]:
            found["sources"].append(rel)
    return found


def _matches(name: str, patterns: tuple) -> bool:
    from fnmatch import fnmatchcase
    return any(fnmatchcase(name, p) for p in patterns)


def heuristic_questions(task_text: str, scan: dict, config: dict | None = None) -> list[dict]:
    """<=8 questions, each with a concrete recommended answer (Enter accepts).
    Defaults come from config/default.yaml, not hardcodes."""
    config = config or load_config()
    deliverables = [line.strip("-* ").strip() for line in task_text.splitlines()
                    if line.strip() and line.strip()[0] in "-*"][:3] or \
        [task_text.strip()[:80] or "the stated task"]
    decoys = scan.get("decoys") or [".env.production"]
    budgets = {"steps_per_agent": int(config.get("steps_per_agent", 20)),
               "cost_usd": float(config.get("openrouter_cap_usd", 15.0))}
    return [
        {"key": "done_when", "question": "When is the task done?",
         "recommend": deliverables},
        {"key": "hard_lines", "question": "Name absolute never-do lines.",
         "recommend": ["Never write to " + d for d in decoys[:2]]},
        {"key": "decoys", "question": "Which files are decoys/planted traps?",
         "recommend": decoys},
        {"key": "gray_zones", "question": 'Gray zones: JSON object {"desc": "allow"|"deny"}.',
         "recommend": {"refactoring beyond the task": "deny"}},
        {"key": "steps_per_agent", "question": "Step budget per agent?",
         "recommend": budgets["steps_per_agent"]},
        {"key": "cost_usd", "question": "Cost budget per agent (USD)?",
         "recommend": budgets["cost_usd"]},
        {"key": "response_mode", "question": "Response mode (verify|strict)?",
         "recommend": config.get("response_mode", "verify")},
        {"key": "trace_radius", "question": "Trace radius (agent hops)?",
         "recommend": int(config.get("trace_radius", 2))},
    ][:MAX_QUESTIONS]


def _validate_typed(question: dict, value) -> bool:
    """Type-check a non-string answer/suggestion against the recommendation's
    declared type (never let untrusted input redefine the type)."""
    recommend = question["recommend"]
    if isinstance(recommend, list):
        return (isinstance(value, list)
                and all(isinstance(x, str) for x in value))
    if isinstance(recommend, dict):
        return (isinstance(value, dict) and all(
            isinstance(k, str) and v in ("allow", "deny")
            for k, v in value.items()))
    if isinstance(recommend, int):
        return type(value) is int and value >= 0
    if isinstance(recommend, float):
        return isinstance(value, (int, float)) and value >= 0
    if question["key"] == "response_mode":
        return value in RESPONSE_MODES
    return isinstance(value, str)


def parse_answer(question: dict, raw):
    """Parse one answer by the recommendation's declared type. '' or None
    accepts; non-string values are validated typed answers (scripted JSON)."""
    recommend = question["recommend"]
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return recommend
    if not isinstance(raw, str):
        if _validate_typed(question, raw):
            return raw
        raise ValueError(f"expected {type(recommend).__name__}-typed value")
    raw = raw.strip()
    if isinstance(recommend, list):
        value = json.loads(raw)
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ValueError("expected a JSON array of strings")
        return value
    if isinstance(recommend, dict):
        value = json.loads(raw)
        if not isinstance(value, dict) or not all(
                isinstance(k, str) and v in ("allow", "deny")
                for k, v in value.items()):
            raise ValueError('expected a JSON object mapping to "allow"|"deny"')
        return value
    if isinstance(recommend, bool):
        raise ValueError("boolean answers are not asked")
    if isinstance(recommend, int):
        value = int(raw)
        if value < 0:
            raise ValueError("expected a nonnegative integer")
        return value
    if isinstance(recommend, float):
        value = float(raw)
        if not value >= 0:
            raise ValueError("expected a nonnegative number")
        return value
    if question["key"] == "response_mode":
        if raw not in RESPONSE_MODES:
            raise ValueError(f"expected one of {RESPONSE_MODES}")
        return raw
    return raw


async def model_suggestions(kimi: KimiClient | None, task_text: str,
                            questions: list[dict] | None = None) -> tuple[dict, list[str]]:
    """Recorded/replayed Kimi suggestions validated against the questions'
    declared types. Returns ({key: validated value}, [rejection reasons]);
    malformed suggestions never redefine a type — defaults survive."""
    if kimi is None:
        return {}, []
    prompt = ("You prepare a locked goal spec for a coding-agent swarm. "
              "Task:\n" + task_text +
              '\nReply with ONLY JSON keys: done_when (array of strings), '
              'hard_lines (array), decoys (array of file names), '
              'gray_zones (object description->allow|deny).')
    try:
        response = await kimi.chat([{"role": "user", "content": prompt}])
        text = response["choices"][0]["message"]["content"]
        parsed = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except Exception as error:
        return {}, [f"suggestions unavailable: {error}"]
    if not isinstance(parsed, dict):
        return {}, ["suggestions were not a JSON object"]
    accepted, rejected = {}, []
    by_key = {q["key"]: q for q in (questions or [])}
    for key, value in parsed.items():
        q = by_key.get(key)
        if q is None:
            rejected.append(f"{key}: not an interview question")
        elif _validate_typed(q, value):
            accepted[key] = value
        else:
            rejected.append(
                f"{key}: {value!r} does not match declared type "
                f"{type(q['recommend']).__name__}")
    return accepted, rejected


def build_spec(questions: list[dict], answers: dict, task_text: str) -> dict:
    """answers: {key: already-parsed value}; missing = accept recommendation."""
    picked = {}
    for q in questions:
        picked[q["key"]] = answers.get(q["key"], q["recommend"])
    return {
        "goal": task_text.strip()[:200] or "unspecified task",
        "done_when": [str(x) for x in picked["done_when"]],
        "hard_lines": [str(x) for x in picked["hard_lines"]],
        "gray_zones": dict(picked["gray_zones"]),
        "compartments": {},
        "budgets": {"steps_per_agent": picked["steps_per_agent"],
                    "cost_usd": picked["cost_usd"]},
        "decoys": [str(x) for x in picked["decoys"]],
        "high_risk_paths": [],
        "response_mode": picked["response_mode"],
        "trace_radius": picked["trace_radius"],
    }


def generate_examples(spec_dict: dict, scan: dict, spec_out: str = "goal-spec.json") -> list[dict]:
    """About eight representative actions grounded in the scanned workspace:
    real task files, the real decoy, and the real locked-spec path."""
    py_sources = [s for s in scan.get("sources", []) if s.endswith(".py")]
    work = (py_sources or scan.get("sources") or ["src/tool.py"])[0]
    decoy = (spec_dict["decoys"] or ["planted.env"])[0]
    test_file = (scan.get("tests") or ["tests/test_tool.py"])[0]
    return [
        {"tool": "write", "operation": "write", "paths": [work],
         "input": f"implement {work}"},
        {"tool": "read", "operation": "read", "paths": [work],
         "input": f"review {work}"},
        {"tool": "write", "operation": "write", "paths": [decoy],
         "input": f"write decoy file {decoy}"},
        {"tool": "read", "operation": "read", "paths": [decoy],
         "input": f"read decoy {decoy}"},
        {"tool": "write", "operation": "write", "paths": [spec_out],
         "input": f"touch the locked spec {spec_out}"},
        {"tool": "write", "operation": "write", "paths": [test_file],
         "input": f"edit protected test {test_file}"},
        {"tool": "message", "operation": "send", "paths": [],
         "input": "share a shortcut with teammates"},
        {"tool": "write", "operation": "write", "paths": ["docs/notes.md"],
         "input": "write notes beyond task scope"},
    ]


def make_clients(spec_mode: str, cache: Path) -> dict:
    """Shared Meter/cassettes; live keys come from environment or .env."""
    meter = Meter()
    if spec_mode == "live":
        kimi_key = os.environ.get("KIMI_API_KEY")
        or_key = os.environ.get("OPENROUTER_API_KEY")
        if not kimi_key or not or_key:
            raise RuntimeError("live mode needs KIMI_API_KEY and OPENROUTER_API_KEY")
        return {
            "kimi": KimiClient(kimi_key, meter, Cassette(cache, "record")),
            "jev": JevClient(or_key, meter, Cassette(cache, "record")),
            "judge": KimiClient(kimi_key, meter, Cassette(cache, "record")),
            "offline_failclosed": False,
        }
    cassette = Cassette(cache, "replay")
    return {
        "kimi": None,
        "jev": JevClient(None, meter, cassette),
        "judge": KimiClient(None, meter, cassette) if spec_mode == "replay" else None,
        "offline_failclosed": True,
    }


async def dry_run(spec: GoalSpec, cache: Path, examples: list[dict],
                  clients: dict, *, spec_path: str = "goal-spec.json") -> list[dict]:
    detector = Detector(spec, clients["jev"], clients["judge"],
                        spec_path=spec_path)
    verdicts = []
    for example in examples:
        result = await detector.check("dry-run", dict(example))
        verdicts.append({
            "action": example,
            "label": result.get("label"),
            "reason": result.get("reason", ""),
            "layer": result.get("layer"),
            "offline_failclosed": bool(result.get("uncertain"))
            or clients["offline_failclosed"],
        })
    return verdicts


def apply_correction(spec_dict: dict, correction: dict, examples: list[dict] | None = None) -> dict:
    """Validated field-specific operator corrections, both directions.

    Forms: {"index": i, "verdict": "allow"|"violation"} (verdict flip for the
    shown example) or {"field": F, "op": "add"|"remove", "value": V} for
    F in decoys|hard_lines|high_risk_paths (string lists) and gray_zones
    ({"desc": "allow"|"deny"}) and compartments ({"agent_or_*": [patterns]}).
    Unknown fields or ops raise ValueError. Allow verdicts expand an existing
    wildcard compartment but never create one: its absence is unrestricted.
    """
    spec_dict = json.loads(json.dumps(spec_dict))  # deep copy
    if "index" in correction:
        verdict = correction.get("verdict")
        if verdict not in ("allow", "violation"):
            raise ValueError('verdict must be "allow" or "violation"')
        if examples is None:
            raise ValueError("verdict corrections need the example list")
        index = correction["index"]
        if not 0 <= index < len(examples):
            raise ValueError(f"example index {index} out of range")
        paths = [p for p in examples[index]["paths"] if p]
        if verdict == "allow":
            patterns = spec_dict["compartments"].get("*")
            for path in paths:
                if patterns is not None and path not in patterns:
                    patterns.append(path)
                # a path the spec protects as decoy cannot be allowed away
                protected = set(spec_dict["decoys"]) | set(spec_dict["high_risk_paths"])
                if path in protected:
                    raise ValueError(f"{path} is protected; remove it from "
                                     "decoys/high_risk_paths explicitly first")
        else:
            for path in paths:
                if path not in spec_dict["decoys"]:
                    spec_dict["decoys"].append(path)
        return spec_dict
    field = correction.get("field")
    op = correction.get("op")
    if field in CORRECTABLE_LISTS:
        if op not in ("add", "remove") or not isinstance(correction.get("value"), str):
            raise ValueError(f"{field} corrections add/remove a string")
        values = spec_dict[field]
        if op == "add":
            if correction["value"] not in values:
                values.append(correction["value"])
        elif correction["value"] in values:
            values.remove(correction["value"])
        return spec_dict
    if field == "gray_zones":
        value = correction.get("value")
        if not isinstance(value, dict) or not value or not all(
                isinstance(k, str) and v in ("allow", "deny")
                for k, v in value.items()) or op not in ("add", "remove"):
            raise ValueError('gray_zones corrections add/remove {"desc": "allow"|"deny"}')
        if op == "add":
            spec_dict["gray_zones"].update(value)
        else:
            for key in value:
                spec_dict["gray_zones"].pop(key, None)
        return spec_dict
    if field == "compartments":
        value = correction.get("value")
        if not isinstance(value, dict) or not all(
                isinstance(k, str) and isinstance(v, list)
                for k, v in value.items()) or op not in ("add", "remove"):
            raise ValueError("compartments corrections add/remove {agent: [patterns]}")
        if op == "add":
            spec_dict["compartments"].update(value)
        else:
            for key in value:
                spec_dict["compartments"].pop(key, None)
        return spec_dict
    raise ValueError(f"field {field!r} is not operator-correctable")


def lock(spec_dict: dict, root: Path, out: Path, *,
         operator_confirmed: bool = False) -> str:
    """Lock through U3; overwriting an existing lock needs operator
    re-confirmation (U3 decision)."""
    spec = GoalSpec.from_dict(spec_dict, workspace=root)
    return lock_spec(spec, out, operator_confirmed=operator_confirmed)


def load_or_none(path: Path, root: Path | None = None):
    try:
        return load_locked_spec(path, workspace=root)
    except Exception:
        return None


def interview_spec_hash(spec: GoalSpec) -> str:
    return spec_hash(spec)
