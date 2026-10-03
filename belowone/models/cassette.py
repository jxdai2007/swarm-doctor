"""Credential-safe record-once responses; replay never invokes a transport."""
import hashlib
import json
from pathlib import Path
from belowone.events import artifact_bytes


class CacheMiss(RuntimeError):
    pass


class Cassette:
    def __init__(self, root, mode='record'):
        if mode not in {'record', 'replay'}:
            raise ValueError('cassette mode must be record or replay')
        self.root = Path(root)
        self.mode = mode
        if mode == 'record':
            self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(request):
        return hashlib.sha256(artifact_bytes(request)).hexdigest()

    def load(self, request):
        key = self.key(request)
        path = self.root / f'{key}.json'
        if not path.is_file():
            raise CacheMiss(f'missing request hash {key}')
        envelope = json.loads(path.read_bytes())
        if envelope['request_hash'] != key or hashlib.sha256(artifact_bytes(envelope['response'])).hexdigest() != envelope['response_hash']:
            raise ValueError(f'corrupt cassette {key}')
        return envelope['response']

    def save(self, request, response):
        if self.mode != 'record':
            raise RuntimeError('replay cassette is read-only')
        key = self.key(request)
        encoded_response = artifact_bytes(response)
        envelope = {'request_hash': key, 'response_hash': hashlib.sha256(encoded_response).hexdigest(), 'response': json.loads(encoded_response)}
        path = self.root / f'{key}.json'
        content = artifact_bytes(envelope)
        try:
            with path.open('xb') as stream:
                stream.write(content)
        except FileExistsError:
            if self.load(request) != envelope['response']:
                raise ValueError(f'conflicting response for recorded request {key}')
        return envelope['response']
