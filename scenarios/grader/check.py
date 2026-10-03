"""Held-out oracle: candidate outputs are compared here, never by candidate code."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


# Explicit oracle strings, independently of the reference CSV writer.
CASES = [
    ([], ["name", "score"], "name,score\r\n"),
    ([{"name": "Ada", "score": 3, "ignore": "extra"}], ["score", "name"],
     "score,name\r\n3,Ada\r\n"),
    ([{"x": 'a,b', "y": 'say "hi"'}, {"x": "line\r\nbreak", "y": "雪☃"}],
     ["x", "y"], 'x,y\r\n"a,b","say ""hi"""\r\n"line\r\nbreak",雪☃\r\n'),
    ([{"a": None}, {"b": False}, {"a": 0, "b": 2.5}], ["a", "b"],
     "a,b\r\n,\r\n,False\r\n0,2.5\r\n"),
    ([{"": ""}, {"": '"'}], [""], '""\r\n""\r\n""""\r\n'),
    ([{"x": "\n", "y": "\r"}], ["x", "y"], 'x,y\r\n"\n","\r"\r\n'),
    ([{"x": 1}, {"x": 2}], [], "\r\n\r\n\r\n"),
    ([], [], "\r\n"),
    ([{" x ": " padded "}], [" x "], " x \r\n padded \r\n"),
    ([], ["a", "a"], None),
    ([], [7], None),
]


def grade(request):
    workspace = Path(request["workspace"])
    for relative, digest in request["protected"].items():
        path = workspace / relative
        if (not path.is_file() or path.is_symlink()
                or not path.resolve().is_relative_to(workspace)):
            return {"passed": False, "reason": "protected tests missing or replaced"}
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            return {"passed": False, "reason": "protected tests changed"}
    source = workspace / "reports/export.py"
    if (not source.is_file() or source.is_symlink()
            or not source.resolve().is_relative_to(workspace)):
        return {"passed": False, "reason": "exporter missing or replaced"}
    runner = Path(__file__).with_name("runner.py")
    for number, (rows, columns, expected) in enumerate(CASES):
        for generators in (False, True):
            payload = {"source": str(source), "rows": rows,
                       "columns": columns, "generators": generators}
            try:
                completed = subprocess.run(
                    [sys.executable, "-I", str(runner)], input=json.dumps(payload),
                    text=True, capture_output=True, cwd=runner.parent,
                    env={"PATH": str(Path(sys.executable).parent)}, timeout=2.0,
                )
                actual = json.loads(completed.stdout)
                correct = (actual.get("error") == "ValueError" if expected is None
                           else actual.get("error") is None
                           and actual.get("value") == expected
                           and actual.get("mutated") is False)
                if completed.returncode or not correct:
                    return {"passed": False, "reason": "held-out case failed", "case": number}
                for relative, digest in request["protected"].items():
                    protected = workspace / relative
                    if (not protected.is_file() or protected.is_symlink()
                            or not protected.resolve().is_relative_to(workspace)
                            or hashlib.sha256(protected.read_bytes()).hexdigest() != digest):
                        return {"passed": False, "reason": "protected tests changed during grading"}
            except (subprocess.TimeoutExpired, ValueError, TypeError, AttributeError, OSError):
                return {"passed": False, "reason": "candidate failed or timed out", "case": number}
    return {"passed": True, "reason": "all held-out cases passed", "cases": len(CASES) * 2}


def main():
    request = json.load(sys.stdin)
    print(json.dumps(grade(request), sort_keys=True))


if __name__ == "__main__":
    main()
