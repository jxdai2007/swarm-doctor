import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from capture_video import capture_one

BASE = "http://127.0.0.1:8899"  # engine server (scripts/serve_engine_smoke.py)
out = Path(tempfile_out := __import__("tempfile").mkdtemp(prefix="u13-smoke-"))

# 1. navigation/assert failure path: unknown run -> assert raises inside
#    capture_one, browser closed by finally (no leaked chrome)
try:
    capture_one("bad-run-SYNTHETIC-DEV",
                f"{BASE}/?run=does-not-exist", 1, "board", out, BASE)
    raise SystemExit("FAIL: bad-run capture should have raised")
except Exception as e:
    print("failure path OK:", str(e)[:60], "- browser closed in finally")

# 2. success path: real archived recording captured to a real webm
capture_one("static-smoke-SYNTHETIC-DEV",
            f"{BASE}/?run=pilot-0", 2, "static", out, BASE)
clip = out / "static-smoke-SYNTHETIC-DEV.webm"
assert clip.is_file() and clip.stat().st_size > 10000, "no real webm"
print("SMOKE OK:", clip, clip.stat().st_size, "bytes")
