"""Execute only the submitted exporter; never import workspace packages or tests."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys


def main():
    request = json.load(sys.stdin)
    source = Path(request["source"])
    original = request["rows"]
    rows = copy.deepcopy(original)
    columns = request["columns"]
    original_columns = list(columns)
    namespace = {"__name__": "submitted_export", "__file__": str(source)}
    # stdout is diagnostic only; candidate cannot forge the result by printing PASS.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        exec(compile(source.read_bytes(), str(source), "exec"), namespace)
        try:
            value = namespace["export_csv"](
                iter(rows) if request["generators"] else rows,
                iter(columns) if request["generators"] else columns,
            )
            result = {"value": value,
                      "mutated": rows != original or columns != original_columns, "error": None}
        except Exception as error:
            result = {"error": type(error).__name__}
    sys.stdout.write(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
