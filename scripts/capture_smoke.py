import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import tempfile

from capture_video import capture_one

out = Path(tempfile.mkdtemp(prefix="u13-smoke-"))
page = ("data:text/html,<body style='background:%23101418;color:%23fff;"
        "font:20px monospace;padding:40px'>static smoke beat</body>")
capture_one("static-smoke-SYNTHETIC-DEV", page, 2, "static", out,
            "http://127.0.0.1:1")
clip = out / "static-smoke-SYNTHETIC-DEV.webm"
assert clip.is_file() and clip.stat().st_size > 10000, "no real webm"
print("SMOKE OK:", clip, clip.stat().st_size, "bytes")
