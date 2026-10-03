import hashlib
import json

import pytest

from belowone.meter import Meter
from belowone.runlog import EventLog
from belowone.runstore import RunStore


def sample_run(tmp_path):
    store = RunStore(tmp_path / 'runs')
    run = store.create('offline-sample', {'seed': 3, 'model': 'synthetic'}, 'abc123')
    EventLog(run / 'events.jsonl').append('a', 'spawn')
    store.write_json(run, 'metrics/result.json', {'synthetic': True, 'score': 1})
    return store, run


def test_complete_run_layout_and_manifest(tmp_path):
    store, run = sample_run(tmp_path)
    assert json.loads((run / 'config.json').read_text())['seed'] == 3
    assert (run / 'commit.txt').read_text() == 'abc123\n'
    assert all((run / name).is_dir() for name in ('cache', 'metrics', 'figures', 'manifests'))
    assert not store.verify(run)
    manifest_path = store.seal(run)
    manifest = json.loads(manifest_path.read_text())
    for name, digest in manifest['files'].items():
        assert hashlib.sha256((run / name).read_bytes()).hexdigest() == digest
    assert store.verify(run)
    assert RunStore(store.root).verify(run)
    with pytest.raises(ValueError, match='sealed'):
        store.seal(run)
    with pytest.raises(ValueError, match='sealed'):
        store.write_json(run, 'metrics/result.json', {})


@pytest.mark.parametrize('change', ['edit', 'append', 'remove', 'add', 'empty_dir', 'remove_dir', 'manifest', 'recomputed_manifest'])
def test_any_sealed_byte_or_inventory_change_fails(tmp_path, change):
    store, run = sample_run(tmp_path)
    store.seal(run)
    artifact = run / 'metrics/result.json'
    if change == 'edit':
        artifact.write_bytes(b'changed')
    elif change == 'append':
        with artifact.open('ab') as stream:
            stream.write(b' ')
    elif change == 'remove':
        artifact.unlink()
    elif change == 'add':
        (run / 'extra.txt').write_text('extra')
    elif change == 'empty_dir':
        (run / 'extra').mkdir()
    elif change == 'remove_dir':
        (run / 'figures').rmdir()
    elif change == 'manifest':
        with (run / 'manifest.json').open('ab') as stream:
            stream.write(b' ')
    else:
        artifact.write_bytes(b'changed')
        manifest = json.loads((run / 'manifest.json').read_text())
        manifest['files']['metrics/result.json'] = hashlib.sha256(b'changed').hexdigest()
        (run / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n')
    assert not store.verify(run)


def test_scrub_all_json_artifacts_and_meter_log(tmp_path, monkeypatch):
    secret = 'synthetic-private-value'
    monkeypatch.setenv('KIMI_API_KEY', secret)
    store = RunStore(tmp_path / 'runs')
    run = store.create('safe', {'headers': {'Authorization': secret}, 'api_key': secret,
                                'prompt': 'echo ' + secret}, 'abc')
    log = EventLog(run / 'events.jsonl')
    meter = Meter('15', event_log=log)
    hold = meter.reserve('openrouter', '0.5')
    meter.record('openrouter', 'synthetic', 3, 5, '0.2', 0.01, reservation=hold)
    store.write_json(run, 'metrics/meter.json', meter.report())
    store.write_json(run, 'cache/response.json', {'headers': {'Authorization': secret}, 'echo': secret})
    store.seal(run)
    for path in run.rglob('*'):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()
            assert b'Authorization' not in path.read_bytes()
    assert log.read()[0].kind == 'model_call'
    assert store.verify(run)


def test_paths_cannot_escape_or_follow_symlinks(tmp_path):
    store = RunStore(tmp_path / 'runs')
    for name in ('../escape', '/absolute', '.', '.seals', 'a/b'):
        with pytest.raises(ValueError):
            store.create(name, {}, 'abc')
    run = store.create('safe', {}, 'abc')
    with pytest.raises(FileExistsError):
        store.create('safe', {}, 'abc')
    with pytest.raises(ValueError):
        store.write_json(run, '../escape.json', {})
    outside = tmp_path / 'outside'
    outside.mkdir()
    (run / 'cache/link').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        store.seal(run)
    assert not store.verify(outside)


def test_artifact_names_and_preexisting_scratch_are_preserved(tmp_path):
    store, run = sample_run(tmp_path)
    scratch = run / 'metrics/result.json.writing'
    scratch.write_bytes(b'user-owned bytes')
    store.write_json(run, 'metrics/result.json', {'new': True})
    assert scratch.read_bytes() == b'user-owned bytes'
    (run / 'secret').write_text('synthetic decoy')
    store.seal(run)
    assert store.verify(run)


def test_invalid_json_and_credential_paths_leave_no_artifact(tmp_path, monkeypatch):
    store = RunStore(tmp_path / 'runs')
    with pytest.raises(ValueError):
        store.create('bad', {'seed': float('nan')}, 'abc')
    assert not (store.root / 'bad').exists()
    monkeypatch.setenv('OPENROUTER_API_KEY', 'synthetic-private-value')
    with pytest.raises(ValueError, match='credential'):
        store.create('synthetic-private-value', {}, 'abc')
    run = store.create('safe', {}, 'abc')
    with pytest.raises(ValueError, match='credential'):
        store.write_json(run, 'cache/synthetic-private-value.json', {})
    assert not (run / 'cache/synthetic-private-value.json').exists()
