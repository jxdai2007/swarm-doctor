import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from belowone.events import Event, artifact_bytes
from belowone.runlog import EventLog


def test_append_roundtrip_and_reopen(tmp_path):
    path = tmp_path / 'events.jsonl'
    log = EventLog(path)
    first = log.append('a', 'write', paths=['shared.txt'], payload={'text': 'hello'})
    prefix = path.read_bytes()
    second = EventLog(path).append('b', 'read', paths=['shared.txt'])
    assert [first.seq, second.seq] == [1, 2]
    assert first.timestamp.endswith('+00:00')
    assert first.monotonic > 0
    assert len(first.content_hash) == 64
    assert log.read() == [first, second]
    assert path.read_bytes().startswith(prefix)
    assert not hasattr(log, 'rewrite')
    assert not hasattr(log, 'update')


def test_crash_truncated_tail_warns_without_rewriting(tmp_path):
    path = tmp_path / 'events.jsonl'
    log = EventLog(path)
    first = log.append('a', 'spawn')
    with path.open('ab') as stream:
        stream.write(b'{"agent_id":"crashed",\xff')
    damaged = path.read_bytes()
    with pytest.warns(RuntimeWarning, match='truncated final line'):
        assert log.read() == [first]
    with pytest.warns(RuntimeWarning, match='truncated final line'):
        with pytest.raises(ValueError, match='truncated'):
            log.append('b', 'stop')
    assert path.read_bytes() == damaged


def test_complete_corruption_is_not_silently_skipped(tmp_path):
    path = tmp_path / 'events.jsonl'
    path.write_bytes(b'{broken}\n')
    with pytest.raises(ValueError, match='line 1'):
        EventLog(path).read()


def test_sequence_and_payload_tampering_rejected(tmp_path):
    path = tmp_path / 'events.jsonl'
    log = EventLog(path)
    log.append('a', 'write', payload={'text': 'original'})
    original = json.loads(path.read_text())
    for changes in ({'seq': 2}, {'payload': {'text': 'changed'}}):
        path.write_text(json.dumps(original | changes) + '\n')
        with pytest.raises(ValueError):
            log.read()


def test_concurrent_instances_assign_one_sequence(tmp_path):
    path = tmp_path / 'events.jsonl'
    def append(index):
        return EventLog(path).append(str(index), 'step').seq
    with ThreadPoolExecutor(max_workers=8) as pool:
        sequences = list(pool.map(append, range(32)))
    assert sorted(sequences) == list(range(1, 33))
    assert len(EventLog(path).read()) == 32


def test_all_event_serialization_scrubs_secrets(tmp_path, monkeypatch):
    secret = 'synthetic-private-value'
    monkeypatch.setenv('OPENROUTER_API_KEY', secret)
    payload = {'echo': secret, 'nested': {'api-key': 'another-private-value'},
               'response': 'another-private-value', 'headers': {'Authorization': 'Bearer ' + secret},
               'raw_headers': {'X-Custom-Key': 'raw-header-private'},
               'trace': 'Authorization: Bearer unregistered-value\nnext',
               'url': 'https://example.test/?api_key=url-private&ok=1',
               'token_text': 'sk-or-v1-' + 'a' * 64}
    log = EventLog(tmp_path / 'events.jsonl')
    event = log.append('a', 'model_call', payload=payload)
    text = (tmp_path / 'events.jsonl').read_text()
    assert secret not in text
    assert 'another-private-value' not in text
    assert 'raw-header-private' not in text
    assert 'unregistered-value' not in text
    assert 'url-private' not in text
    assert 'sk-or-v1-' not in text
    assert 'Authorization' not in text
    assert 'headers' not in event.payload
    assert log.read() == [event]
    manual = Event('a', 1, event.timestamp, event.monotonic, 'call', [], '', payload)
    assert secret.encode() not in artifact_bytes(manual.to_dict())


def test_reject_non_json_payload_without_changing_log(tmp_path):
    path = tmp_path / 'events.jsonl'
    log = EventLog(path)
    with pytest.raises((TypeError, ValueError)):
        log.append('a', 'step', payload={'value': float('nan')})
    assert path.read_bytes() == b''
