"""Open sealed historical seed101; no models, credentials, or recordings."""
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:8767/?run=injected-kimi-101-verify'
API = 'http://127.0.0.1:8767/artifacts/injected-kimi-101-verify/metrics.json'
expected = json.loads((ROOT / 'experiments/committed/pressure-campaign/injected-kimi-101-verify/metrics.json').read_text())
environments = [os.environ.get('UV_PROJECT_ENVIRONMENT'),
                '/tmp/below-one-live-20261004T0750Z/venv',
                '/tmp/below-one-live-20261003T0750Z/venv', str(ROOT / '.venv')]
python = next((Path(env) / 'bin/python' for env in environments
               if env and (Path(env) / 'bin/python').is_file()), None)
if python is None:
    raise SystemExit('No existing Below One Python environment found; no dependencies installed.')


def archived_ready():
    try:
        with urlopen(API, timeout=1) as response:
            actual = json.load(response)
    except (HTTPError, URLError, TimeoutError):
        return False
    if actual != expected:
        raise SystemExit('Port8767 serves a different archive; refusing to relabel it.')
    return True


if not archived_ready():
    server = subprocess.Popen([str(python), '-u', 'scripts/serve_engine_smoke.py', '8767', '--real',
        '--artifact-root', 'experiments/committed/pressure-campaign', '--display-root', 'experiments/display'],
        cwd=ROOT, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 10
    while not archived_ready():
        if server.poll() is not None or time.monotonic() >= deadline:
            raise SystemExit('Historical dashboard failed startup; no browser opened.')
        time.sleep(.1)
subprocess.run(['open', URL], check=True)
print('HISTORICAL DASHBOARD OPENED — seed101 original READ-freeze bug retained, not posthoc live policy')
print(URL)
