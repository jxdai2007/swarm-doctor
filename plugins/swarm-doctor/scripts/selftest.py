#!/usr/bin/env python3
"""Offline doctor checks: real hooks, temporary projects, no model/network calls.

The repository unittest entry point explicitly loads this file, so both runners
exercise exactly the same cases. The launcher proof uses a local fake executable,
never the installed Claude binary.
"""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

DOCTOR = Path(__file__).with_name("doctor.py").resolve()


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="swarm-doctor-selftest-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "reports").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "reports" / "source.txt").write_text("ordinary report\n", encoding="utf-8")
        (self.root / "tests" / "test_reports.py").write_text("assert True\n", encoding="utf-8")
        self.env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.root)}
        self.lock()

    def cli(self, *args, payload=None, env=None, cwd=None):
        return subprocess.run([sys.executable, str(DOCTOR), *args],
                              input=json.dumps(payload) if payload is not None else "",
                              text=True, capture_output=True, cwd=cwd or self.root,
                              env=env or self.env, timeout=15)

    def lock(self, *extra):
        result = self.cli("lock", "--goal", "Write export reports", *extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def payload(self, tool="Write", path="reports/export.txt", session="main", agent=None, cwd=None, **data):
        payload = {"session_id": session, "cwd": str(cwd or self.root), "tool_name": tool,
                   "tool_input": ({"command": path} if tool == "Bash" else {"file_path": path}),
                   "tool_use_id": "test-use"}
        payload["tool_input"].update(data)
        if agent is not None:
            payload["agent_id"] = agent
        return payload

    def hook(self, payload, kind="pre"):
        result = self.cli(kind, payload=payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        if kind == "post":
            self.assertEqual(result.stdout, "")
            return None
        if not result.stdout:
            return None
        output = json.loads(result.stdout)
        self.assertEqual(set(output), {"hookSpecificOutput"})
        result = output["hookSpecificOutput"]
        self.assertEqual(result["hookEventName"], "PreToolUse")
        self.assertIn(result["permissionDecision"], ("deny", "ask"))
        return result

    def assertDecision(self, payload, kind="deny", reason=None):
        result = self.hook(payload)
        self.assertIsNotNone(result)
        self.assertEqual(result["permissionDecision"], kind)
        if reason:
            self.assertIn(reason, result["permissionDecisionReason"])
        return result

    def state(self):
        return json.loads((self.root / ".swarm-doctor" / "state.json").read_text(encoding="utf-8"))

    def test_in_scope_allowed(self):
        """in-scope allowed"""
        self.assertIsNone(self.hook(self.payload()))
        self.assertIsNone(self.hook(self.payload(path="tests/test_export_csv.py")))
        self.assertIsNone(self.hook(self.payload(tool="Read", path="README.md")))

    def test_decoy_quarantine(self):
        """decoy quarantine"""
        decoy = self.root / ".env.production"
        self.assertIn("FAKE_SWARM_DOCTOR_DECOY", decoy.read_text(encoding="utf-8"))
        self.assertDecision(self.payload(tool="Read", path=str(decoy)), reason="Protected by Swarm Doctor")
        self.assertEqual(self.state()["actors"]["main"]["status"], "quarantined")

    def test_next_call_denied(self):
        """next call denied"""
        self.assertDecision(self.payload(tool="Read", path=".env.production"))
        self.assertDecision(self.payload(), reason="quarantined")

    def test_healthy_actor_allowed(self):
        """healthy actor allowed; sibling identity"""
        self.assertDecision(self.payload(tool="Read", path=".env.production", agent="source"))
        self.assertIsNone(self.hook(self.payload(agent="healthy")))
        self.assertIsNone(self.hook(self.payload()))
        self.assertEqual(self.state()["actors"]["main:source"]["status"], "quarantined")

    def test_protected_test_edit(self):
        """protected test edit; never precedence"""
        self.lock("--scope", "reports/**,tests/**")
        self.assertDecision(self.payload(tool="Edit", path="tests/test_reports.py"), reason="Protected by Swarm Doctor")
        self.assertEqual(self.state()["protected_edits"], 1)

    def test_outside_scope_steer(self):
        """outside-scope steer"""
        result = self.assertDecision(self.payload(path="src/unrelated.py"), reason="Back to task: Write export reports")
        self.assertIn("outside scope", result["permissionDecisionReason"])
        self.assertEqual(self.state()["actors"]["main"]["status"], "healthy")
        self.assertIsNone(self.hook(self.payload()))

    def test_status_report(self):
        """status and timestamped report"""
        self.hook(self.payload(tool="Read", path="reports/source.txt"), "post")
        self.assertDecision(self.payload(tool="Edit", path="tests/test_reports.py", agent="bad"))
        for command in ("status", "report"):
            result = self.cli(command)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("main:bad: quarantined", result.stdout)
            self.assertIn("Protected edits: 1", result.stdout)
        report = self.cli("report").stdout
        self.assertIn('"direction": "read"', report)
        self.assertIn("access:", report)
        events = [json.loads(line) for line in (self.root / ".swarm-doctor/events.jsonl").read_text().splitlines()]
        self.assertTrue(all("timestamp" in item for item in events))

    def test_bash_paths_and_directions(self):
        """Bash paths; reads versus writes"""
        allowed = ("cat README.md", "cat -n reports/source.txt", "head -n 2 reports/source.txt",
                   "printf '%s' ok > reports/out.txt", "cat reports/source.txt | tee reports/out.txt",
                   "cp reports/source.txt reports/out.txt", "sed -i '' 's/old/new/' reports/source.txt",
                   "cd reports && cat source.txt", "cat < reports/source.txt",
                   "cat reports/source.txt > reports/out.txt 2>&1", "cat reports/source.txt\ncat README.md")
        for command in allowed:
            with self.subTest(command=command):
                self.assertIsNone(self.hook(self.payload(tool="Bash", path=command)))
        forbidden = ("cat .env.production", "cat '.env.production'", "cat reports/../.env.production",
                     "cat -n .env.production", "cp .env.production reports/copy.txt", "cat .env*",
                     "printf ok > tests/test_reports.py", "rm -- tests/test_reports.py",
                     "echo ok\ncat .env.production", "echo ok &> tests/test_reports.py")
        for index, command in enumerate(forbidden):
            with self.subTest(command=command):
                self.assertDecision(self.payload(tool="Bash", path=command, session=f"bash-{index}"), reason="Protected by Swarm Doctor")
        self.assertDecision(self.payload(tool="Bash", path="echo hi > outside.txt", session="outside"), reason="outside scope")
        self.hook(self.payload(tool="Bash", path="cat reports/source.txt > reports/out.txt", session="direction"), "post")
        records = [json.loads(line) for line in (self.root / ".swarm-doctor/events.jsonl").read_text().splitlines()]
        directions = {(r["path"], r["direction"]) for r in records if r.get("actor") == "direction"}
        self.assertEqual(directions, {("reports/source.txt", "read"), ("reports/out.txt", "write")})

    def test_ask_mode(self):
        """ask trip; subsequent quarantine denies"""
        self.lock("--on-trip", "ask")
        self.assertDecision(self.payload(tool="Read", path=".env.production"), kind="ask")
        self.assertDecision(self.payload(), kind="deny")

    def test_taint_watch(self):
        """taint reader watched, never infected"""
        self.assertDecision(self.payload(tool="Read", path=".env.production", agent="source"))
        # Post represents a successful action (e.g. user-approved ask/in-flight write).
        self.hook(self.payload(agent="source"), "post")
        self.hook(self.payload(tool="Read", agent="reader"), "post")
        state = self.state()
        self.assertEqual(state["actors"]["main:reader"]["status"], "watch")
        self.assertEqual(state["actors"]["main:source"]["status"], "quarantined")
        self.assertIsNone(self.hook(self.payload(tool="Read", agent="reader")))
        self.assertDecision(self.payload(agent="reader"), kind="ask")
        self.assertDecision(self.payload(tool="Bash", path="python3 -c 'print(1)'", agent="reader"), kind="ask")
        self.assertIsNone(self.hook(self.payload(agent="clean")))
        self.hook(self.payload(agent="reader"), "post")
        self.assertEqual(self.state()["tainted"][str(self.root / "reports/export.txt")], ["main:source"])

    def test_concurrent_actors(self):
        """concurrent hooks retain all actors and counters"""
        def call(index):
            return self.cli("pre", payload=self.payload(path="tests/test_reports.py", agent=f"worker-{index}"))
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(call, range(16)))
        self.assertTrue(all(result.returncode == 0 for result in results))
        self.assertTrue(all(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny" for result in results))
        state = self.state()
        self.assertEqual(len(state["actors"]), 16)
        self.assertEqual(state["protected_edits"], 16)
        records = [json.loads(line) for line in (self.root / ".swarm-doctor/events.jsonl").read_text().splitlines()]
        self.assertEqual(sum(record["event"] == "decision" for record in records), 16)

    def test_no_spec_silent(self):
        """no spec: both hooks silent"""
        with tempfile.TemporaryDirectory() as project:
            env = {**self.env, "CLAUDE_PROJECT_DIR": project}
            for kind in ("pre", "post"):
                result = self.cli(kind, payload=self.payload(path=".env.production"), env=env)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
            self.assertFalse((Path(project) / ".swarm-doctor").exists())

    def test_release_and_admin_escape(self):
        """selective release and strict administrative escape"""
        self.assertDecision(self.payload(tool="Read", path=".env.production"))
        self.assertDecision(self.payload(tool="Read", path=".env.production", agent="bad"))
        base = f"{shlex.quote(sys.executable)} {shlex.quote(str(DOCTOR))}"
        for command in (f"{base} status", f"{base} report"):
            self.assertIsNone(self.hook(self.payload(tool="Bash", path=command)))
        for command in (f"{base} release main", f"{base} release --all", f"{base} lock --goal changed"):
            self.assertDecision(self.payload(tool="Bash", path=command), kind="ask", reason="human approval")
        for command in (f"{base} release main; cat .env.production", f"{base} release --all && echo bad",
                        f"{base} release main > reports/output", f"{base} lock --goal changed; echo bad"):
            self.assertDecision(self.payload(tool="Bash", path=command))
        result = self.cli("release", "main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(self.hook(self.payload()))
        self.assertDecision(self.payload(agent="bad"))
        result = self.cli("release", "--all")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(self.hook(self.payload(agent="bad")))
        self.assertNotEqual(self.cli("release").returncode, 0)

    def test_tamper_and_corrupt_state(self):
        """state tampering protected; corrupt/missing state fails closed"""
        self.lock("--scope", "**")
        for index, command in enumerate(("rm -rf .swarm-doctor", "echo '{}' > .swarm-doctor/state.json", "rm -rf .")):
            self.assertDecision(self.payload(tool="Bash", path=command, session=f"tamper-{index}"), reason="Protected by Swarm Doctor")
        self.assertDecision(self.payload(path=".swarm-doctor/spec.json", session="direct"))
        path = self.root / ".swarm-doctor/state.json"
        path.write_text("not-json", encoding="utf-8")
        self.assertDecision(self.payload(), reason="fail closed")
        path.unlink()
        self.assertDecision(self.payload(), reason="fail closed")
        result = self.cli("post", payload=self.payload())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_symlink_traversal_and_patterns(self):
        """symlinks, traversal, Glob patterns, hook cwd"""
        (self.root / "reports/link.txt").symlink_to(self.root / ".env.production")
        self.assertDecision(self.payload(tool="Read", path="reports/link.txt"))
        self.assertDecision(self.payload(path="../escape.txt", session="traverse"), reason="outside scope")
        self.assertDecision(self.payload(tool="Read", path="../.env.production", session="cwd", cwd=self.root / "reports"))
        payload = self.payload(tool="Glob", session="glob")
        payload["tool_input"] = {"pattern": ".env*", "path": str(self.root)}
        self.assertDecision(payload)
        # Regex pattern is not a file path; Grep search path is.
        payload = self.payload(tool="Grep", session="grep")
        payload["tool_input"] = {"pattern": "secret", "path": "reports/source.txt"}
        self.assertIsNone(self.hook(payload))
        result = self.cli("pre", payload={"session_id": "envless", "cwd": str(self.root), "tool_name": "Read",
                                         "tool_input": {"file_path": ".env.production"}},
                          env={key: value for key, value in self.env.items() if key != "CLAUDE_PROJECT_DIR"})
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_lock_decoy_and_gitignore(self):
        """lock defaults, custom globs, preserve decoy/gitignore"""
        (self.root / ".env.production").write_text("REAL_EXISTING=unchanged\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("existing", encoding="utf-8")
        self.lock("--scope", "reports/** tests/test_export*.py,docs/**", "--never", "tests/test_reports.py")
        self.assertEqual((self.root / ".env.production").read_text(), "REAL_EXISTING=unchanged\n")
        self.assertEqual((self.root / ".gitignore").read_text(), "existing\n.swarm-doctor/\n.env.production\n")
        self.lock()
        self.assertEqual((self.root / ".gitignore").read_text().count(".swarm-doctor/"), 1)
        spec = json.loads((self.root / ".swarm-doctor/spec.json").read_text())
        self.assertEqual(spec["never"], ["tests/test_reports.py"])
        self.assertIsNone(self.hook(self.payload(path="tests/test_export_new.py")))
        self.assertNotEqual(self.cli("lock", "--goal", " ").returncode, 0)

    def test_malformed_input_fail_closed(self):
        """malformed hook input fails closed without stdout noise"""
        for payload in ([], {}, {"session_id": "main", "tool_name": "Bash", "tool_input": {"command": "cat '"}}):
            self.assertDecision(payload, reason="fail closed")
        result = subprocess.run([sys.executable, str(DOCTOR), "pre"], input="not JSON", text=True,
                                capture_output=True, env=self.env, cwd=self.root, timeout=15)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_grep_targets(self):
        """Grep directory/default searches enforce never and taint"""
        protected = {"session_id": "grep-protected", "cwd": str(self.root),
                     "tool_name": "Grep", "tool_input": {"pattern": ".*", "path": "tests", "glob": "test_reports.py"}}
        self.assertDecision(protected, reason="Protected by Swarm Doctor")
        broad = {"session_id": "grep-default", "cwd": str(self.root),
                 "tool_name": "Grep", "tool_input": {"pattern": ".*"}}
        self.assertDecision(broad, reason="Protected by Swarm Doctor")
        for selector in ("!*.md", "*.{py,txt}"):
            unsupported = {"session_id": f"grep-{selector}", "cwd": str(self.root),
                           "tool_name": "Grep", "tool_input": {"pattern": ".*", "path": "tests", "glob": selector}}
            self.assertDecision(unsupported, reason="fail closed")
        self.assertDecision(self.payload(tool="Read", path=".env.production", agent="source"))
        self.hook(self.payload(agent="source"), kind="post")
        (self.root / "reports/export.txt").write_text("tainted\n", encoding="utf-8")
        reader = {"session_id": "reader", "cwd": str(self.root), "tool_name": "Grep",
                  "tool_input": {"pattern": "tainted", "path": "reports", "glob": "*.txt"}}
        self.assertIsNone(self.hook(reader))
        self.hook(reader, kind="post")
        self.assertEqual(self.state()["actors"]["reader"]["status"], "watch")

    def test_sort_output_targets(self):
        """sort output variants enforce protected writes and scope"""
        for index, option in enumerate(("-o tests/test_reports.py", "--output tests/test_reports.py",
                                         "--output=tests/test_reports.py", "-otests/test_reports.py")):
            self.assertDecision(self.payload(tool="Bash", path=f"sort {option} reports/source.txt",
                                             session=f"sort-{index}"), reason="Protected by Swarm Doctor")
        self.assertDecision(self.payload(tool="Bash", path="sort -o README.md reports/source.txt",
                                         session="sort-outside"), reason="Back to task")

    def test_launcher_missing_path(self):
        """launcher missing PATH explains prerequisite"""
        result = self.cli("swarm", "--agents", "2", "Write reports", env={**self.env, "PATH": str(self.root / "empty-bin")})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("claude executable not found on PATH", result.stderr)
        self.assertNotEqual(self.cli("swarm", "--agents", "0", "task").returncode, 0)

    def test_launcher_stub_proof(self):
        """launcher stub: N sessions, safe flags, real logs, failure status"""
        binary = self.root / "stub-bin"
        binary.mkdir()
        stub = binary / "claude"
        stub.write_text(f"#!{sys.executable}\nimport json, os, sys\nprint(json.dumps({{'argv': sys.argv[1:], 'cwd': os.getcwd(), 'root': os.environ.get('CLAUDE_PROJECT_DIR')}}))\nsys.exit(int(os.environ.get('STUB_EXIT', '0')))\n", encoding="utf-8")
        stub.chmod(0o755)
        env = {**self.env, "PATH": str(binary)}
        result = self.cli("swarm", "--agents", "3", "Write", "reports", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("uses your Claude account/usage", result.stdout)
        sessions = set()
        for index in range(1, 4):
            record = json.loads((self.root / f".swarm-doctor/agents/{index}.log").read_text())
            argv = record["argv"]
            self.assertEqual(argv[:2], ["-p", "Write reports"])
            self.assertEqual(argv[argv.index("--plugin-dir") + 1], str(DOCTOR.parent.parent))
            self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
            self.assertEqual(argv[argv.index("--permission-prompts") + 1], "none")
            self.assertEqual(argv[argv.index("--allowedTools") + 1:], ["Read", "Edit", "Write", "Bash"])
            self.assertNotIn("--dangerously-skip-permissions", argv)
            self.assertEqual(record["cwd"], str(self.root))
            self.assertEqual(record["root"], str(self.root))
            sessions.add(argv[argv.index("--session-id") + 1])
        self.assertEqual(len(sessions), 3)
        result = self.cli("swarm", "--agents", "1", "fail", env={**env, "STUB_EXIT": "7"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("exit 7", result.stdout)


class TableResult(unittest.TextTestResult):
    def getDescription(self, test):
        return test.shortDescription() or test.id().split(".")[-1]

    def addSuccess(self, test):
        super().addSuccess(test)
        self.rows.append(("PASS", self.getDescription(test)))

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.rows.append(("FAIL", self.getDescription(test)))

    def addError(self, test, error):
        super().addError(test, error)
        self.rows.append(("FAIL", self.getDescription(test)))
    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        if error is not None:
            row = ("FAIL", self.getDescription(test))
            if row not in self.rows:
                self.rows.append(row)


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = []


def main():
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DoctorTests)
    result = unittest.TextTestRunner(stream=stream, resultclass=TableResult, verbosity=0).run(suite)
    print("Swarm Doctor offline selftest (no Claude/model calls)")
    print("RESULT | CASE")
    print("-------|----------------------------------------------")
    for status, name in result.rows:
        print(f"{status:<6} | {name}")
    print(f"{sum(status == 'PASS' for status, _ in result.rows)}/{result.testsRun} passed")
    if not result.wasSuccessful():
        print(stream.getvalue(), file=sys.stderr)
    else:
        print("ALL PASS")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
