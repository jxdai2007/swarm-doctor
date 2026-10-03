"""Bounded real dependency probes; unavailable credentials are explicit fallbacks."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]


def env_keys(path):
    values = dict(os.environ)
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('\"\'')
    return values


def run_checks(env_path=None, report_path=None, probe_omp=True):
    values = env_keys(env_path or ROOT / '.env')
    config_path = ROOT / 'config/default.yaml'
    config = yaml.safe_load(config_path.read_text())
    checks = []

    def result(name, passed, detail, fallback):
        checks.append({'check': name, 'status': 'PASS' if passed else 'FAIL', 'detail': detail, 'fallback': None if passed else fallback})

    with httpx.Client(timeout=20) as client:
        for name, key, url, payload in [
            ('kimi_identity_call', 'KIMI_API_KEY', config['kimi']['base_url'] + '/chat/completions', {'model': config['kimi']['model'], 'messages': [{'role': 'user', 'content': 'Reply OK.'}], 'max_tokens': 16}),
            ('jev_decisions_call', 'OPENROUTER_API_KEY', 'https://openrouter.ai/api/alpha/decisions', {'model': config['jev']['model'], 'state': {'action': 'read task source'}, 'questions': {'serves_goal': {'type': 'noul', 'instructions': 'Does reading task source serve a coding goal?', 'criteria': {'true': 'Useful task work', 'false': 'Off-task work'}}}}),
        ]:
            if not values.get(key):
                result(name, False, 'operator action needed: ' + key + ' missing', 'synthetic development responses; cached-only replay; live runs blocked')
                continue
            try:
                response = client.post(url, headers={'Authorization': 'Bearer ' + values[key], 'User-Agent': 'below-one/0.1', 'X-Title': 'below-one'}, json=payload)
                response.raise_for_status()
                data = response.json()
                passed = bool(data.get('choices')) if name.startswith('kimi') else 'answers' in data
                result(name, passed, 'served model: ' + str(data.get('model')), 'cached responses; Jev failure escalates to judge then fail closed')
            except Exception as exc:
                result(name, False, type(exc).__name__, 'cached responses; unavailable decisions fail closed')
        try:
            response = client.get('https://openrouter.ai/api/v1/models')
            response.raise_for_status()
            candidates = [m for m in response.json()['data'] if 'tools' in m.get('supported_parameters', []) and float(m['pricing']['prompt']) > 0 and float(m['pricing']['completion']) > 0 and m.get('context_length', 0) >= 32000]
            # Pin once; later checks verify availability instead of silently switching.
            selected = next((m for m in candidates if m['id'] == config['openrouter']['fallback_model']), None) if config['openrouter']['fallback_model'] else min(candidates, key=lambda m: (float(m['pricing']['prompt']) + 2 * float(m['pricing']['completion']), m['id']))
            if selected is None:
                raise ValueError('pinned fallback unavailable')
            if config['openrouter']['fallback_model'] is None:
                config['openrouter'].update(fallback_model=selected['id'], fallback_canonical_slug=selected['canonical_slug'], fallback_prompt_price=selected['pricing']['prompt'], fallback_completion_price=selected['pricing']['completion'])
                config_path.write_text(yaml.safe_dump(config, sort_keys=False))
                with (ROOT / 'DECISIONS.md').open('a') as stream:
                    stream.write(f"- U1: pin fallback {selected['id']} ({selected['canonical_slug']}) from public models list: tools supported, >=32k context; lowest prompt + twice completion price among positive-price candidates. Exact prices in default config.\n")
            result('openrouter_models_fallback', True, selected['id'] + ' / ' + selected['canonical_slug'], 'block fallback live runs; continue cached replay')
        except Exception as exc:
            result('openrouter_models_fallback', False, type(exc).__name__, 'retain pin if present; block fallback live runs; continue cached replay')

    records = []
    if probe_omp and shutil.which('omp'):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                records.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='below-one-probe-') as tmp:
                Path(tmp, 'probe.txt').write_text('BELOW_ONE_PATH_PROBE\n')
                process = subprocess.run(['omp', '-p', '--model', 'openai-codex/gpt-6.1-sol', '--no-session', '--no-extensions', '--no-skills', '--no-rules', '--no-title', '--no-lsp', '--tools', 'read', '--auto-approve', '-e', str(ROOT / 'scripts/omp_probe.ts'), 'Read probe.txt using read tool. Then reply BEFORE_STEER. Obey any later operator correction.'], cwd=tmp, env={**os.environ, 'BELOW_ONE_PROBE_URL': f'http://127.0.0.1:{server.server_port}/'}, capture_output=True, text=True, timeout=120)
                output = process.stdout
                ok = process.returncode == 0
                results = [r for r in records if r['kind'] == 'tool_result']
                result('omp_noninteractive_extension', ok and any(r['kind'] == 'extension_loaded' for r in records), f'exit={process.returncode}; extension observed={bool(records)}; builder Codex probe, not Kimi experiment', 'experiment harness carries measurements; adapter blocks until supported')
                result('extension_local_http', bool(records), f'{len(records)} local callbacks observed', 'adapter unavailable: experiment harness remains in-process')
                result('omp_result_file_paths', any('probe.txt' in json.dumps(r['payload']) for r in results), 'tool result includes path in input or content' if results else 'no tool result observed', 'normalize original tool inputs; shell accesses unknown/best effort')
                result('omp_steer_redirection', any(r['kind'] == 'steer_sent' for r in records) and 'AFTER_STEER' in output, 'steer delivered and corrected final output' if 'AFTER_STEER' in output else 'redirection not observed', 'deny reason carries steer; do not rely on asynchronous steer')
        except Exception as exc:
            for name in ['omp_noninteractive_extension', 'extension_local_http', 'omp_result_file_paths', 'omp_steer_redirection']:
                result(name, False, type(exc).__name__, 'experiment harness; deny-reason steer; unknown shell paths')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    else:
        for name in ['omp_noninteractive_extension', 'extension_local_http', 'omp_result_file_paths', 'omp_steer_redirection']:
            result(name, False, 'omp probe disabled or executable unavailable', 'experiment harness; fail-closed adapter; deny-reason steer')
    report_path = report_path or ROOT / 'docs/first-hour-checks.md'
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text('# First-hour checks\n\nThese are real probes, not simulated passes. Missing keys do not block offline building.\n\n' + '\n'.join(f"- {c['status']} {c['check']}: {c['detail']}" + (f". Fallback: {c['fallback']}" if c['fallback'] else '') for c in checks) + '\n')
    report_path.with_suffix('.json').write_text(json.dumps(checks, indent=2) + '\n')
    print(report_path.read_text())
    print('FIRST_HOUR_REPORT_WRITTEN')
    return checks


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-omp', action='store_true')
    args = parser.parse_args()
    run_checks(probe_omp=not args.skip_omp)
