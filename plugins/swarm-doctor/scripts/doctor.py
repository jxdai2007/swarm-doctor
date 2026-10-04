#!/usr/bin/env python3
"""Swarm Doctor: local hook guard and opt-in Claude launcher (stdlib only).

Bash inspection is a conservative path heuristic, NOT a shell sandbox. It covers
common commands, redirects, pipelines, cwd changes and literal paths, but cannot
prove effects of arbitrary programs, dynamic expansions or indirect file access.
Use OS isolation for adversarial code. Hooks cannot secure their own files against
processes running outside Claude's hooked tools. State uses a POSIX flock and
atomic replacement; Windows is not supported.
Launcher requires Claude Code >= 2.1.259 for --permission-prompts none; it uses
account usage and never bypasses permissions. Hook "ask" cannot auto-run unattended.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fnmatch
import glob
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

import fcntl

SCRIPT = Path(__file__).resolve()
PLUGIN = SCRIPT.parent.parent
SECRET_GLOBS = (".env*", "*.pem", "id_rsa*", "*credentials*", "*secret*")
SEPARATORS = {";", "&&", "||", "|", "&", "\n"}
REDIRECTS = {">", ">>", "<", "<>"}


def now():
    return datetime.now(timezone.utc).isoformat()


def patterns(value):
    result = [item for item in re.split(r"[\s,]+", value.strip()) if item]
    if not result:
        raise ValueError("glob list must not be empty")
    return result


def root_for(payload=None):
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or
                (payload or {}).get("cwd") or os.getcwd()).expanduser().resolve()


def regular(path):
    if path.is_symlink():
        raise ValueError(f"refusing symlink state file: {path}")
    if path.exists() and not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"state file is not regular: {path}")


def load_json(path, default=None):
    regular(path)
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"invalid object in {path.name}")
    return value


def atomic_json(path, value):
    regular(path)
    fd, name = tempfile.mkstemp(prefix=".save-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def store(root):
    directory = root / ".swarm-doctor"
    if directory.is_symlink():
        raise ValueError("refusing symlink .swarm-doctor directory")
    directory.mkdir(mode=0o700, exist_ok=True)
    lock = directory / "lock"
    regular(lock)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield directory
    finally:
        os.close(fd)


def spec_at(directory):
    spec = load_json(directory / "spec.json")
    if spec is None:
        return None
    if (not isinstance(spec.get("goal"), str) or not spec["goal"].strip() or
            spec.get("on_trip") not in ("pause", "ask") or
            any(not isinstance(spec.get(key), list) or not spec[key] or
                any(not isinstance(item, str) or not item for item in spec[key])
                for key in ("scope", "never"))):
        raise ValueError("invalid locked spec")
    return spec


def state_at(directory):
    state = load_json(directory / "state.json")
    if state is None:
        raise ValueError("locked spec has missing state.json; refusing to reset quarantine")
    if (not isinstance(state.get("actors"), dict) or
            not isinstance(state.get("tainted"), dict) or
            not isinstance(state.get("protected_edits"), int) or state["protected_edits"] < 0):
        raise ValueError("invalid doctor state")
    for key, actor in state["actors"].items():
        if (not isinstance(key, str) or not isinstance(actor, dict) or
                actor.get("status") not in ("healthy", "watch", "quarantined")):
            raise ValueError("invalid actor state")
    for path, writers in state["tainted"].items():
        if not isinstance(path, str) or not isinstance(writers, list) or any(not isinstance(w, str) for w in writers):
            raise ValueError("invalid taint state")
    return state


def event(directory, value):
    path = directory / "events.jsonl"
    regular(path)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"timestamp": now(), **value}, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def decision(kind, reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
          "permissionDecision": kind, "permissionDecisionReason": reason}}))


def actor_key(payload):
    session = payload.get("session_id")
    agent = payload.get("agent_id")
    if not isinstance(session, str) or not session or ":" in session:
        raise ValueError("hook requires nonempty session_id without ':'")
    if agent is not None and (not isinstance(agent, str) or not agent or ":" in agent):
        raise ValueError("invalid agent_id")
    return f"{session}:{agent}" if agent else session


def normalized(value, cwd):
    value = os.path.expandvars(os.path.expanduser(value))
    path = Path(value)
    return (path if path.is_absolute() else cwd / path).resolve()


def label(path, root):
    try:
        return path.relative_to(root).as_posix() or "."
    except ValueError:
        return str(path)


def matches(path, globs):
    return any(fnmatch.fnmatchcase(path, pattern) or
               (pattern.endswith("/**") and path == pattern[:-3].rstrip("/"))
               for pattern in globs)


def shell_tokens(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = "#"
    return list(lexer)


def admin_command(tool, command, cwd):
    """Recognize only full literal administrative argv; never shell tails."""
    if tool != "Bash":
        return None
    tokens = shell_tokens(command)
    if (len(tokens) < 3 or not re.fullmatch(r"python(?:3(?:\.\d+)?)?", Path(tokens[0]).name) or
            normalized(tokens[1], cwd) != SCRIPT):
        return None
    if any(token in SEPARATORS or token in REDIRECTS or any(c in token for c in "`$\n") for token in tokens):
        return None
    action = tokens[2]
    if len(tokens) == 3 and action in ("status", "report"):
        return action
    if len(tokens) == 4 and action == "release" and (tokens[3] == "--all" or
            (not tokens[3].startswith("-") and bool(re.fullmatch(r"[\w:.-]+", tokens[3])))):
        return action
    if action == "lock" and len(tokens) >= 5 and len(tokens[3:]) % 2 == 0:
        options = dict(zip(tokens[3::2], tokens[4::2]))
        if (len(options) * 2 == len(tokens[3:]) and "--goal" in options and
                set(options) <= {"--goal", "--scope", "--never", "--on-trip", "--decoy"} and
                all(value.strip() for value in options.values()) and
                options.get("--on-trip", "pause") in ("pause", "ask") and
                options.get("--decoy", "yes") in ("yes", "no")):
            return action
    return None


def shell_paths(command, cwd):
    """Return (literal path, cwd, read|write) triples and whether shell is risky."""
    tokens = shell_tokens(command)
    paths = []
    segment = []
    risky = False
    current = cwd

    def add(value, mode):
        if value and value != "-" and not value.startswith("/dev/"):
            paths.append((value, current, mode))

    def scan(words):
        nonlocal current, risky
        if not words:
            return
        clean = []
        i = 0
        while i < len(words):
            word = words[i]
            if word.isdigit() and i + 1 < len(words) and words[i + 1] in REDIRECTS | {">&", "<&"}:
                i += 1
                continue
            if word in (">&", "<&"):
                if i + 1 >= len(words):
                    raise ValueError("missing file descriptor redirect")
                if not words[i + 1].isdigit() and words[i + 1] != "-":
                    add(words[i + 1], "write" if word == ">&" else "read")
                i += 2
                continue
            if word in REDIRECTS | {"&>", "&>>"}:
                if i + 1 >= len(words):
                    raise ValueError("missing redirect path")
                add(words[i + 1], "read" if word == "<" else "write")
                risky |= word != "<"
                i += 2
                continue
            if word in ("<<", "<<<", "<<-"):
                # Here document body is opaque to this path heuristic.
                risky = True
                i += 2
                continue
            clean.append(word)
            i += 1
        if not clean:
            return
        while clean and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", clean[0]):
            clean.pop(0)
            risky = True
        if not clean:
            return
        while clean and Path(clean[0]).name in ("command", "env", "sudo", "nohup"):
            clean.pop(0)
            risky = True
        if not clean:
            return
        executable, *args = clean
        name = Path(executable).name
        positional = [arg for arg in args if not arg.startswith("-")]
        if name == "cd":
            if positional:
                add(positional[0], "read")
                current = normalized(positional[0], current)
            return
        if name in ("echo", "printf", "true", "false", "pwd"):
            return
        if name in ("cp", "mv", "install", "ln"):
            risky = True
            destination = next((args[n + 1] for n, arg in enumerate(args[:-1]) if arg in ("-t", "--target-directory")), None)
            if destination:
                positional = [arg for arg in positional if arg != destination]
            elif positional:
                destination = positional.pop()
            for source in positional:
                add(source, "read")
                if name == "mv":
                    add(source, "write")
                if destination:
                    add(str(Path(destination) / Path(source).name), "write")
            if destination:
                add(destination, "write")
            return
        if name in ("touch", "mkdir", "rm", "rmdir", "unlink", "tee", "truncate", "chmod", "chown"):
            risky = True
            if name in ("chmod", "chown") and positional:
                positional.pop(0)
            for arg in positional:
                add(arg, "write")
            return
        if name in ("sed", "perl"):
            inplace = any(arg == "--in-place" or arg.startswith("--in-place=") or
                          (arg.startswith("-") and "i" in arg[1:]) for arg in args)
            risky |= inplace
            # First positional expression is code, remaining positionals are files.
            operands = []
            explicit_expression = False
            n = 0
            while n < len(args):
                arg = args[n]
                if arg in ("-e", "-f"):
                    explicit_expression = True
                    if n + 1 < len(args) and arg == "-f":
                        add(args[n + 1], "read")
                    n += 2
                    continue
                if name == "sed" and arg == "-i" and n + 1 < len(args) and args[n + 1] == "":
                    n += 2
                    continue
                if not arg.startswith("-"):
                    operands.append(arg)
                n += 1
            files = operands if explicit_expression else operands[1:]
            for arg in files:
                add(arg, "write" if inplace else "read")
            return
        if name in ("cat", "head", "tail", "less", "more", "wc", "sort", "uniq", "cut", "diff", "cmp", "stat", "file", "ls", "find", "readlink", "realpath"):
            # These option values are not file operands.
            skip = {"head": {"-n", "-c", "--lines", "--bytes"},
                    "tail": {"-n", "-c", "--lines", "--bytes"},
                    "cut": {"-d", "-f", "-b", "-c"},
                    "sort": {"-k", "-t", "-o"}}.get(name, set())
            files = [arg for n, arg in enumerate(args) if not arg.startswith("-") and
                     (n == 0 or args[n - 1] not in skip)]
            if name == "find":
                files = args[:next((n for n, arg in enumerate(args) if arg.startswith("-")), len(args))]
            for arg in files:
                add(arg, "read")
            if name == "find" and any(arg in ("-delete", "-exec", "-execdir") for arg in args):
                risky = True
                for arg in files:
                    add(arg, "write")
            return
        if name in ("grep", "rg", "ag"):
            files = positional if any(arg in ("-e", "-f") for arg in args) else positional[1:]
            if "-e" in args:
                files = [arg for arg in files if arg != args[args.index("-e") + 1]]
            for arg in files:
                add(arg, "read")
            return
        # ponytail: literal heuristic; use an OS sandbox for arbitrary interpreters.
        risky = True
        for arg in args:
            for candidate in re.findall(r"(?:[\w.*?~/-]+/)?[\w.*?~-]+(?:\.[\w.*?~-]+)+|(?:\.{1,2}|~)?/[\w./*?~-]+", arg):
                add(candidate, "read")
                add(candidate, "write")
            if arg in (".", "..") or any(fnmatch.fnmatchcase(arg.lower(), p) for p in SECRET_GLOBS):
                add(arg, "read")
                add(arg, "write")

    for token in tokens:
        if token in SEPARATORS or token in ("(", ")") or token and set(token) == {"\n"}:
            scan(segment)
            segment = []
        else:
            segment.append(token)
    scan(segment)
    return paths, risky or bool(re.search(r"\$|`|<\(|>\(", command))


def access_paths(payload, root):
    tool = payload["tool_name"]
    data = payload["tool_input"]
    cwd = normalized(payload.get("cwd", str(root)), root)
    raw = []
    risky = False
    if tool == "Bash":
        command = data.get("command")
        if not isinstance(command, str):
            raise ValueError("Bash command must be a string")
        raw, risky = shell_paths(command, cwd)
    else:
        mode = "write" if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit") else "read"
        for key in ("file_path", "path", "notebook_path", "pattern"):
            if key in data:
                if not isinstance(data[key], str):
                    raise ValueError(f"{key} must be a string")
                # Grep's pattern is a regex, not a filesystem path.
                if key == "pattern" and tool != "Glob":
                    continue
                base = normalized(data.get("path", str(cwd)), cwd) if tool == "Glob" and key == "pattern" else cwd
                raw.append((data[key], base, mode))
    result = set()
    for value, base, mode in raw:
        if not value:
            continue
        path = normalized(value, base)
        result.add((path, mode))
        if glob.has_magic(value):
            for match in glob.iglob(str(path), recursive=True):
                result.add((Path(match).resolve(), mode))
    return sorted(result, key=lambda item: (str(item[0]), item[1])), risky


def protected(path, mode, root, spec):
    relative = label(path, root)
    if any(fnmatch.fnmatchcase(part.lower(), pattern) for part in path.parts for pattern in SECRET_GLOBS):
        return True
    if matches(relative, spec["never"]):
        return True
    internal = root / ".swarm-doctor"
    return mode == "write" and (path == SCRIPT or path in SCRIPT.parents or
           path == internal or internal in path.parents or path in internal.parents)


def inactive(root):
    directory = root / ".swarm-doctor"
    spec = directory / "spec.json"
    return not directory.is_symlink() and not spec.is_symlink() and not spec.exists()


def hook(kind):
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input must be an object")
    except ValueError:
        if inactive(root_for()):
            return 0
        raise
    root = root_for(payload)
    directory = root / ".swarm-doctor"
    if inactive(root):
        return 0
    with store(root) as directory:
        spec = spec_at(directory)
        if spec is None:
            return 0
        actor = actor_key(payload)
        if not isinstance(payload.get("tool_name"), str) or not isinstance(payload.get("tool_input"), dict):
            raise ValueError("hook requires tool_name and tool_input")
        state = state_at(directory)
        command = payload["tool_input"].get("command", "")
        cwd = normalized(payload.get("cwd", str(root)), root)
        administration = admin_command(payload["tool_name"], command, cwd)
        if administration:
            if kind == "pre" and administration in ("release", "lock"):
                reason = f"Swarm Doctor: {administration} changes guard state; explicit human approval required."
                event(directory, {"event": "decision", "actor": actor, "decision": "ask", "reason": reason})
                decision("ask", reason)
            return 0
        entry = state["actors"].setdefault(actor, {"status": "healthy"})
        paths, risky = access_paths(payload, root)
        if kind == "pre":
            result = None
            if entry["status"] == "quarantined":
                result = ("deny", f"Protected by Swarm Doctor: actor {actor} is quarantined; use release to resume.")
            else:
                forbidden = [(p, mode) for p, mode in paths if protected(p, mode, root, spec)]
                if forbidden:
                    entry.update(status="quarantined", reason=label(forbidden[0][0], root), since=now())
                    state["protected_edits"] += sum(mode == "write" for _, mode in forbidden)
                    result = ("ask" if spec["on_trip"] == "ask" else "deny",
                              f"Protected by Swarm Doctor: {label(forbidden[0][0], root)}; actor {actor} quarantined.")
                else:
                    outside = next((p for p, mode in paths if mode == "write" and
                                    (not p.is_relative_to(root) or not matches(label(p, root), spec["scope"]))), None)
                    if outside is not None:
                        result = ("deny", f"Back to task: {spec['goal']}. {label(outside, root)} is outside scope ({', '.join(spec['scope'])}).")
                    elif entry["status"] == "watch" and (risky or any(mode == "write" for _, mode in paths)):
                        result = ("ask", f"Swarm Doctor watch: actor {actor} read a tainted path; approve risky action explicitly.")
            atomic_json(directory / "state.json", state)
            if result:
                event(directory, {"event": "decision", "actor": actor, "tool": payload["tool_name"],
                                  "decision": result[0], "reason": result[1], "tool_use_id": payload.get("tool_use_id")})
                decision(*result)
        else:
            for path, mode in paths:
                name = str(path)
                if mode == "write" and entry["status"] == "quarantined":
                    writers = state["tainted"].setdefault(name, [])
                    if actor not in writers:
                        writers.append(actor)
                if mode == "read" and any(writer != actor for writer in state["tainted"].get(name, [])):
                    if entry["status"] != "quarantined":
                        entry.update(status="watch", reason=label(path, root), since=now())
                event(directory, {"event": "access", "actor": actor, "tool": payload["tool_name"],
                                  "direction": mode, "path": label(path, root), "absolute_path": name,
                                  "tool_use_id": payload.get("tool_use_id")})
            atomic_json(directory / "state.json", state)
    return 0


def lock_project(args, root):
    spec = {"goal": args.goal.strip(), "scope": patterns(args.scope), "never": patterns(args.never),
            "on_trip": args.on_trip, "decoy": args.decoy, "locked_at": now()}
    if not spec["goal"]:
        raise ValueError("goal must not be empty")
    with store(root) as directory:
        # Re-lock changes task spec, never silently releases quarantined actors.
        state = state_at(directory) if (directory / "spec.json").exists() else {"actors": {}, "tainted": {}, "protected_edits": 0}
        ignore = root / ".gitignore"
        regular(ignore)
        existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
        ignored = [".swarm-doctor/"] + ([".env.production"] if args.decoy == "yes" else [])
        lines = existing.splitlines()
        missing = [entry for entry in ignored if entry not in lines and "/" + entry not in lines]
        if missing:
            with ignore.open("a", encoding="utf-8") as handle:
                handle.write(("" if not existing or existing.endswith("\n") else "\n") + "\n".join(missing) + "\n")
        if args.decoy == "yes":
            decoy = root / ".env.production"
            try:
                fd = os.open(decoy, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                print("Decoy not created: .env.production already exists (left unchanged).")
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write("# FAKE_SWARM_DOCTOR_DECOY — not a real credential\nFAKE_SWARM_DOCTOR_DECOY=not-a-secret\n")
        atomic_json(directory / "state.json", state)
        atomic_json(directory / "spec.json", spec)
        event(directory, {"event": "lock", "goal": spec["goal"]})
    print(f"Locked: {spec['goal']}\nScope: {', '.join(spec['scope'])}\nNever: {', '.join(spec['never'])}\nOn trip: {spec['on_trip']}")
    return 0


def inspect_project(args, root):
    if not (root / ".swarm-doctor").exists():
        print("Swarm Doctor: no locked spec.")
        return 0
    with store(root) as directory:
        spec = spec_at(directory)
        if spec is None:
            print("Swarm Doctor: no locked spec.")
            return 0
        state = state_at(directory)
        if args.command == "release":
            keys = list(state["actors"]) if args.all else [args.session]
            released = 0
            for key in keys:
                if key in state["actors"]:
                    state["actors"][key] = {"status": "healthy", "released_at": now()}
                    released += 1
            atomic_json(directory / "state.json", state)
            event(directory, {"event": "release", "actors": keys})
            print(f"Released {released} actor(s). Taint history retained.")
            return 0
        print(f"Swarm Doctor: {spec['goal']}\nScope: {', '.join(spec['scope'])}\nNever: {', '.join(spec['never'])}\nOn trip: {spec['on_trip']}\nProtected edits: {state['protected_edits']}")
        for key, entry in sorted(state["actors"].items()):
            print(f"{key}: {entry['status']}" + (f" ({entry['reason']})" if entry.get("reason") else ""))
        if not state["actors"]:
            print("Actors: none")
        print(f"Tainted paths: {len(state['tainted'])}")
        if args.command == "report":
            for path, writers in sorted(state["tainted"].items()):
                print(f"Taint: {label(Path(path), root)} <- {', '.join(writers)}")
            path = directory / "events.jsonl"
            regular(path)
            if path.exists():
                with path.open(encoding="utf-8") as handle:
                    for line in handle:
                        record = json.loads(line)
                        print(f"{record['timestamp']} {record['event']}: " + json.dumps({k: v for k, v in record.items() if k not in ('timestamp', 'event')}, sort_keys=True))
    return 0


def swarm(args, root):
    executable = shutil.which("claude")
    if not executable:
        raise ValueError("claude executable not found on PATH; install/authenticate Claude Code before swarm")
    task = " ".join(args.task).strip()
    if not task:
        raise ValueError("swarm requires a task")
    with store(root) as directory:
        if spec_at(directory) is None:
            raise ValueError("lock a task spec before launching swarm")
        state_at(directory)
        logs = directory / "agents"
        if logs.is_symlink():
            raise ValueError("refusing symlink agents log directory")
        logs.mkdir(exist_ok=True)
        sessions = [str(uuid.uuid4()) for _ in range(args.agents)]
        event(directory, {"event": "swarm", "sessions": sessions, "task": task})
    print(f"Launching {args.agents} Claude session(s); uses your Claude account/usage. Logs: {logs}", flush=True)
    children = []
    try:
        for index, session in enumerate(sessions, 1):
            path = logs / f"{index}.log"
            regular(path)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as log:
                child = subprocess.Popen([executable, "-p", task, "--plugin-dir", str(PLUGIN),
                        "--session-id", session, "--permission-mode", "dontAsk", "--permission-prompts", "none",
                        "--allowedTools", "Read", "Edit", "Write", "Bash"], cwd=root,
                        env={**os.environ, "CLAUDE_PROJECT_DIR": str(root)}, stdin=subprocess.DEVNULL,
                        stdout=log, stderr=subprocess.STDOUT)
            children.append(child)
            print(f"Agent {index}: {session} -> {path}", flush=True)
        codes = [child.wait() for child in children]
    except BaseException:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            child.wait()
        raise
    for index, code in enumerate(codes, 1):
        print(f"Agent {index}: exit {code}")
    return 0 if all(code == 0 for code in codes) else 1


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("agents must be >= 1")
    return number


def parser():
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("pre", "post", "status", "report"):
        commands.add_parser(name)
    lock = commands.add_parser("lock")
    lock.add_argument("--goal", required=True)
    lock.add_argument("--scope", default="reports/**,tests/test_export*.py")
    lock.add_argument("--never", default="tests/test_reports.py")
    lock.add_argument("--on-trip", choices=("pause", "ask"), default="pause")
    lock.add_argument("--decoy", choices=("yes", "no"), default="yes")
    release = commands.add_parser("release")
    release.add_argument("session", nargs="?")
    release.add_argument("--all", action="store_true")
    launch = commands.add_parser("swarm", help="opt-in paid/account-usage Claude sessions")
    launch.add_argument("--agents", type=positive_int, default=4)
    launch.add_argument("task", nargs=argparse.REMAINDER)
    return result


def main():
    args = parser().parse_args()
    try:
        if args.command in ("pre", "post"):
            return hook(args.command)
        root = root_for()
        if args.command == "lock":
            return lock_project(args, root)
        if args.command == "swarm":
            return swarm(args, root)
        if args.command == "release" and (bool(args.session) == args.all):
            raise ValueError("release requires exactly one SESSION (or SESSION:AGENT) or --all")
        return inspect_project(args, root)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
        message = f"Swarm Doctor error (fail closed): {error}"
        print(message, file=sys.stderr)
        if args.command == "pre":
            decision("deny", message)
            return 0
        # Post cannot undo a successful action; nonzero exposes the failed audit.
        return 1
    except KeyboardInterrupt:
        print("Swarm Doctor interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
