"""Below One CLI. `python -m belowone.cli interview --help` for usage.

interview: scan task+workspace, ask <=8 typed questions (Enter accepts the
recommendation; invalid answers retry), dry-run ~8 workspace-grounded example
actions through the REAL U5 Detector, apply validated operator corrections,
always re-run the final spec's verdicts, then lock through U3. Scripted
answers reproduce the same spec byte-identically. Nothing launches agents.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from belowone.spec import interview as iv
from belowone.spec.lock import load_locked_spec, spec_hash


def _task_text(task_path: Path) -> str:
    if not task_path.is_file():
        raise SystemExit(f"task file not found: {task_path}")
    return task_path.read_text()


def _collect_answers(questions, answers: dict, interactive: bool) -> dict:
    """Parse every answer by declared type; interactive input retries on
    invalid input, scripted input fails with the question named."""
    parsed = {}
    for q in questions:
        raw = answers.get(q["key"])
        attempts = 3 if interactive else 1
        for _ in range(attempts):
            if interactive and raw is None:
                rec = q["recommend"]
                rec = json.dumps(rec, ensure_ascii=False) if isinstance(
                    rec, (list, dict)) else str(rec)
                raw = input(f"[{q['key']}] {q['question']}\n"
                            f"  recommended: {rec}\n  > ")
            try:
                parsed[q["key"]] = iv.parse_answer(q, raw)
                break
            except (ValueError, json.JSONDecodeError) as error:
                if not interactive:
                    raise SystemExit(f"scripted answer for {q['key']!r} "
                                     f"invalid: {error}")
                print(f"  invalid ({error}); Enter accepts the recommendation.")
                raw = None
        else:
            parsed[q["key"]] = q["recommend"]
    return parsed


def _print_verdicts(verdicts):
    offline = any(v["offline_failclosed"] for v in verdicts)
    print("\ndry-run verdicts (real detection stack"
          + (", OFFLINE FAIL-CLOSED where marked" if offline else "") + "):")
    for i, v in enumerate(verdicts):
        tag = " [offline fail-closed]" if v["offline_failclosed"] else ""
        print(f"  [{i}] {v['label']:9} layer={v['layer'] or '-':8} "
              f"{v['action']['input']} -> {v['reason']}{tag}")


def _parse_correction_input(raw: str) -> dict:
    # documented syntax: "<index>=allow|violation" or "field,op,value"
    if "=" in raw and "," not in raw.split("=")[0]:
        index, verdict = raw.split("=", 1)
        return {"index": int(index.strip()), "verdict": verdict.strip()}
    field, op, value = (p.strip() for p in raw.split(",", 2))
    if field in ("gray_zones", "compartments"):
        value = json.loads(value)
    return {"field": field, "op": op, "value": value}


async def cmd_interview(args) -> int:
    root = Path(args.workspace).resolve()
    iv.load_env(root)
    task_text = _task_text(Path(args.task))
    scan = iv.scan_workspace(root)
    cache = root / ".belowone" / "interview-cache"
    cache.mkdir(parents=True, exist_ok=True)

    mode = args.model_mode
    if mode == "auto":
        has_keys = os.environ.get("KIMI_API_KEY") and \
            os.environ.get("OPENROUTER_API_KEY")
        mode = "live" if has_keys else "synthetic"
    clients = iv.make_clients(mode, cache)
    if mode in ("synthetic", "replay"):
        print(f"note: {mode} model mode — "
              + ("heuristic recommendations and fail-closed Jev cassette; "
                 "NOT a live interview or model verdicts" if mode == "synthetic"
                 else "recorded cassettes; cache misses fail closed"))

    config = iv.load_config(root)
    questions = iv.heuristic_questions(task_text, scan, config)
    suggestions, rejected = await iv.model_suggestions(
        clients["kimi"], task_text, questions)
    for reason in rejected:
        print(f"note: model suggestion rejected — {reason} "
              "(safe default kept)")
    if suggestions:
        for q in questions:
            if q["key"] in suggestions:
                q["recommend"] = suggestions[q["key"]]

    interactive = args.scripted is None
    answers: dict = {}
    corrections: list[dict] = []
    if interactive:
        answers = _collect_answers(questions, {}, interactive=True)
    else:
        scripted = json.loads(Path(args.scripted).read_text())
        answers = _collect_answers(questions, scripted.get("answers", {}),
                                   interactive=False)
        corrections = scripted.get("corrections", [])

    spec_dict = iv.build_spec(questions, answers, task_text)
    out = Path(args.out)
    if not out.is_absolute():
        out = Path.cwd() / out
    try:
        spec_out = out.resolve().relative_to(root).as_posix()
    except ValueError:
        spec_out = out.resolve().as_posix()  # outside workspace: tripwire fail
    examples = iv.generate_examples(spec_dict, scan, spec_out=spec_out)

    shown_key: str | None = None

    async def show_verdicts():
        nonlocal shown_key
        spec = iv.GoalSpec.from_dict(spec_dict, workspace=root)
        key = json.dumps(spec_dict, sort_keys=True)
        if key == shown_key:
            return  # already shown for this exact spec; no duplicate checks
        verdicts = await iv.dry_run(spec, cache, examples, clients,
                                    spec_path=spec_out)
        _print_verdicts(verdicts)
        shown_key = key

    await show_verdicts()

    if interactive:
        while True:
            raw = input("correct any verdict? <index>=allow|violation "
                        "or field,op,value (blank to finish) > ").strip()
            if not raw:
                break
            try:
                correction = _parse_correction_input(raw)
                spec_dict = iv.apply_correction(spec_dict, correction, examples)
            except (ValueError, KeyError, json.JSONDecodeError) as error:
                print(f"  rejected: {error}")
                continue
            await show_verdicts()
    else:
        for correction in corrections:
            spec_dict = iv.apply_correction(spec_dict, correction, examples)
            await show_verdicts()  # re-run after EACH correction

    # Final state always evaluated at least once (show_verdicts skips only
    # exact repeats, so nothing is ever double-charged or silently skipped).
    await show_verdicts()

    if iv.load_or_none(out, root) is not None and not args.confirm_overwrite:
        print("refusing to overwrite locked spec without --confirm-overwrite "
              "(operator re-confirmation required)")
        return 2
    iv.lock(spec_dict, root, out, operator_confirmed=args.confirm_overwrite)
    locked = load_locked_spec(out, workspace=root)
    print(f"locked spec {out} hash={spec_hash(locked)}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="belowone")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("interview", help="pre-launch setup interview and lock")
    p.add_argument("--task", required=True,
                   help="task text file path (or literal filename)")
    p.add_argument("--workspace", default=".",
                   help="workspace root scanned for decoys/configs/tests")
    p.add_argument("--out", default="goal-spec.json", help="locked spec path")
    p.add_argument("--model-mode",
                   choices=["auto", "live", "replay", "synthetic"],
                   default="auto",
                   help="live: real Kimi interviewer, OpenRouter Jev and Kimi "
                        "judge via .env or environment keys; replay: recorded "
                        "cassettes, fail-closed on miss; synthetic: no model "
                        "calls, explicitly labeled")
    p.add_argument("--scripted",
                   help="JSON file {answers:{}, corrections:[...]} reproducing "
                        "the interview non-interactively")
    p.add_argument("--confirm-overwrite", action="store_true",
                   help="operator re-confirmation to replace an existing lock")
    p.set_defaults(func=cmd_interview)

    args = parser.parse_args(argv)
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
