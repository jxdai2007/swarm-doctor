"""Local FastAPI transport, same-engine dashboard, allowlisted run artifacts."""
from __future__ import annotations

import hmac
import json
import math
from pathlib import Path
import re

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from belowone.events import artifact_bytes, scrub


class DecideRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    agent_id: str
    action: dict


class RecordRequest(DecideRequest):
    result: dict
    decision_id: str
    elapsed: float | None = None


class StartRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    agent_id: str
    decision_id: str


class ReadyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    agent_id: str


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    agent_id: str
    command: str
    reason: str = 'Operator action'


PUBLIC_ARTIFACTS = {'events.jsonl', 'metrics.json', 'snapshot.json', 'manifest.json'}
SAFE_RUN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$')


def create_app(engine, *, operator_token, artifact_root=None, dashboard_dir=None, run_id='live', record_action=None):
    if not isinstance(operator_token, str) or len(operator_token) < 24:
        raise ValueError('Operator token must be a private capability of at least 24 characters')
    if not SAFE_RUN.fullmatch(run_id):
        raise ValueError('Invalid run ID')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    root = Path(artifact_root).resolve() if artifact_root is not None else None

    def artifact(run, name):
        if root is None or not SAFE_RUN.fullmatch(run) or name not in PUBLIC_ARTIFACTS:
            raise HTTPException(404, 'Artifact not found')
        directory = root / run
        target = directory / name
        if directory.is_symlink() or target.is_symlink() or not target.is_file():
            raise HTTPException(404, 'Artifact not found')
        if not target.resolve().is_relative_to(root) or not directory.resolve().is_relative_to(root):
            raise HTTPException(404, 'Artifact not found')
        return target

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={'detail': str(error)})

    @app.post('/ready')
    async def ready(body: ReadyRequest):
        return await engine.ready(body.agent_id)

    @app.post('/decide')
    async def decide(body: DecideRequest):
        return await engine.decide(body.agent_id, body.action)

    @app.post('/start')
    async def start(body: StartRequest):
        return engine.start(body.agent_id, body.decision_id)

    @app.post('/record')
    async def record(body: RecordRequest):
        return (record_action or engine.record)(body.agent_id, body.action, body.result,
                             decision_id=body.decision_id, elapsed=body.elapsed).to_dict()

    @app.post('/control')
    async def control(body: ControlRequest, authorization: str | None = Header(default=None)):
        supplied = authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
        if not hmac.compare_digest(supplied.encode(), operator_token.encode()):
            raise HTTPException(403, 'Operator capability required')
        return await engine.control(body.agent_id, body.command, reason=body.reason)

    @app.get('/snapshot')
    async def snapshot(run: str | None = None):
        if run is None or run == run_id:
            return engine.snapshot()
        return scrub(json.loads(artifact(run, 'snapshot.json').read_bytes()))

    @app.get('/artifacts/{run}/{name}')
    async def read_artifact(run: str, name: str):
        # JSONL/JSON artifacts cross the same credential-safe serializer as logs.
        target = artifact(run, name)
        if name == 'events.jsonl':
            content = b''.join(artifact_bytes(json.loads(line)) for line in target.read_bytes().splitlines() if line.strip())
            from fastapi.responses import Response
            return Response(content, media_type='application/x-ndjson')
        return scrub(json.loads(target.read_bytes()))

    @app.get('/events')
    async def events(request: Request, after: int = 0, run: str | None = None,
                     last_event_id: str | None = Header(default=None)):
        if last_event_id is not None:
            try:
                after = int(last_event_id)
            except ValueError:
                raise HTTPException(400, 'Invalid event cursor')
        if after < 0:
            raise HTTPException(400, 'Invalid event cursor')
        historical = run is not None and run != run_id
        if historical:
            try:
                history = [json.loads(line) for line in artifact(run, 'events.jsonl').read_bytes().splitlines() if line.strip()]
                frames = []
                for event in history:
                    seq = event['seq']
                    elapsed = event['payload']['elapsed']
                    if type(seq) is not int or seq < 1 or type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
                        raise ValueError('Historical event cursor/time must be finite and nonnegative')
                    if seq > after:
                        frames.append(f'id: {seq}\n'.encode() + b'data: ' + artifact_bytes({**event, 'elapsed': elapsed}).rstrip(b'\n') + b'\n\n')
                initial = scrub(json.loads(artifact(run, 'snapshot.json').read_bytes()))
            except (KeyError, TypeError, ValueError) as error:
                raise HTTPException(400, f'Invalid historical event artifact: {error}')
        else:
            history, initial = None, engine.snapshot()

        async def stream():
            yield b'data: ' + artifact_bytes(initial).rstrip(b'\n') + b'\n\n'
            if history is not None:
                for frame in frames:
                    yield frame
                yield b'data: [done]\n\n'
                return
            async for event in engine.events(after=after):
                if await request.is_disconnected():
                    return
                payload = {**event.to_dict(), 'elapsed': event.payload['elapsed']}
                yield f'id: {event.seq}\n'.encode() + b'data: ' + artifact_bytes(payload).rstrip(b'\n') + b'\n\n'

        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    assets = Path(dashboard_dir) if dashboard_dir is not None else Path(__file__).resolve().parents[2] / 'dashboard'
    @app.get('/')
    async def dashboard():
        target = assets / 'index.html'
        if target.is_symlink() or not target.is_file():
            raise HTTPException(404, 'Dashboard not found')
        return FileResponse(target)

    @app.get('/{asset:path}')
    async def dashboard_asset(asset: str):
        if asset not in {'live.js', 'split.js', 'vendor/cytoscape.min.js'}:
            raise HTTPException(404, 'Asset not found')
        target = assets / asset
        if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(assets.resolve()):
            raise HTTPException(404, 'Asset not found')
        return FileResponse(target)
    return app
