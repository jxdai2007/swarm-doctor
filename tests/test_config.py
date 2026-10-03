import importlib.util
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_default_config_loads():
    config = yaml.safe_load((ROOT / 'config/default.yaml').read_text())
    assert isinstance(config['agents_per_run'], int)
    assert isinstance(config['openrouter_cap_usd'], float)
    assert isinstance(config['escalation_band'], list)
    assert isinstance(config['kimi']['base_url'], str)
    assert config['response_mode'] in {'verify', 'strict'}


def test_missing_keys_report_operator_action(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('checks', ROOT / 'scripts/first_hour_checks.py')
    checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checks)
    monkeypatch.delenv('KIMI_API_KEY', raising=False)
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    # Public catalog probe remains real but bounded; offline failure has an explicit fallback.
    import httpx
    def offline(*args, **kwargs):
        raise httpx.ConnectError('network disabled')
    monkeypatch.setattr(httpx.Client, 'get', offline)
    results = checks.run_checks(tmp_path / 'missing.env', tmp_path / 'report.md', probe_omp=False)
    assert len(results) == 7
    assert all(item['fallback'] for item in results if item['status'] == 'FAIL')
    assert all('operator action needed' in item['detail'] for item in results[:2])
    assert (tmp_path / 'report.md').is_file()
