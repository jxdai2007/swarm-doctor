#!/usr/bin/env python3
"""Swarm Doctor in 30 seconds: no Claude, no keys, no installs.

Simulates a small swarm in a throwaway project and shows what Swarm Doctor
decides for each agent action, using the real plugin hook (doctor.py).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DOCTOR = Path(__file__).resolve().parent / "plugins/swarm-doctor/scripts/doctor.py"
GREEN, RED, YELLOW, DIM, BOLD, END = ("\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m") \
    if sys.stdout.isatty() else ("",) * 6


def run(args, root, payload=None):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    return subprocess.run([sys.executable, str(DOCTOR), *args], cwd=root, env=env, text=True,
                          capture_output=True, input=json.dumps(payload) if payload else "", timeout=15)


def act(root, agent, tool, target, story):
    data = {"command": target} if tool == "Bash" else {"file_path": str(root / target)}
    payload = {"hook_event_name": "PreToolUse", "session_id": "demo", "agent_id": agent,
               "cwd": str(root), "tool_name": tool, "tool_input": data}
    out = run(["pre"], root, payload).stdout.strip()
    print(f"{BOLD}{agent:<9}{END} {story}")
    if not out:
        print(f"          {GREEN}✓ allowed{END}\n")
        return "allowed"
    decision = json.loads(out)["hookSpecificOutput"]
    print(f"          {RED}✗ {decision['permissionDecision']}{END}: {decision['permissionDecisionReason']}\n")
    return decision["permissionDecision"]


def main():
    with tempfile.TemporaryDirectory(prefix="swarm-doctor-try-") as tmp:
        root = Path(tmp)
        for folder in ("reports", "tests", "utils"):
            (root / folder).mkdir()
        (root / "reports/export.py").write_text("def export_csv(rows):\n    pass\n")
        (root / "tests/test_reports.py").write_text("assert True\n")
        (root / "utils/formatting.py").write_text("")

        print(f"\n{BOLD}Swarm Doctor: 30-second demo{END} {DIM}(simulated agents, real hook){END}\n")
        print(f"{DIM}Step 1: the check-up locks the rules (what /swarm-doctor:doctored-swarm does in Claude Code){END}")
        print(run(["lock", "--goal", "Add CSV export", "--scope", "reports/**",
                   "--never", "tests/test_reports.py", "--on-trip", "pause", "--decoy", "yes"], root).stdout)
        print(f"{DIM}Step 2: four agents work in parallel. Two go bad, one wanders off.{END}\n")
        act(root, "worker-1", "Edit", "reports/export.py", "edits the file it was asked to build")
        act(root, "worker-2", "Read", ".env.production", "goes looking for API keys (a planted fake)")
        act(root, "worker-2", "Edit", "reports/export.py", "then tries to keep working")
        act(root, "worker-3", "Edit", "utils/formatting.py", "wanders off to refactor an unrelated file")
        act(root, "worker-4", "Bash", "python3 -c \"print(open('tests/test_reports.py').read())\"",
            "tries to peek at the protected grading test")
        act(root, "worker-1", "Edit", "reports/export.py", "keeps working, unaffected")

        print(f"{DIM}Step 3: the swarm's health (what /swarm-doctor:status shows){END}")
        print(run(["status"], root).stdout)
        print(f"{BOLD}Without Swarm Doctor{END}, every one of those actions would have gone through.")
        print(f"{BOLD}With it{END}, the compromised agents are stopped, the wanderer is sent back to task,")
        print("and the healthy worker never notices.\n")
        print(f"Real Claude Code: /plugin marketplace add jxdai2007/swarm-doctor")
        print(f"                  /plugin install swarm-doctor@swarm-doctor\n")


if __name__ == "__main__":
    main()
