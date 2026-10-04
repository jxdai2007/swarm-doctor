"""Local HTTP: agent-scoped Bearer capabilities, separate operator control, read-only artifacts."""
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


def require_agent_capability(agent_tokens, agent_id, authorization):
    """Authenticate one launch-issued agent capability; operator tokens never substitute."""
    if not authorization or not authorization.startswith('Bearer ') or not authorization[7:]:
        raise HTTPException(401, 'Agent Bearer capability required', headers={'WWW-Authenticate': 'Bearer'})
    expected = agent_tokens.get(agent_id)
    supplied = authorization[7:]
    if expected is None or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(403, 'Agent capability required')


def create_app(engine, *, operator_token, agent_tokens, artifact_root=None, display_root=None,
               dashboard_dir=None, run_id='live', record_action=None, start_action=None):
    if not isinstance(operator_token, str) or len(operator_token) < 24:
        raise ValueError('Operator token must be a private capability of at least 24 characters')
    if not isinstance(agent_tokens, dict) or set(agent_tokens) != set(engine.lifecycle.states):
        raise ValueError('Agent capabilities must cover exactly the known agent IDs')
    if any(not isinstance(token, str) or len(token) < 24 or not token.isascii()
           or any(character.isspace() for character in token) for token in agent_tokens.values()):
        raise ValueError('Agent capabilities must be private Bearer tokens of at least 24 characters')
    if len(set(agent_tokens.values())) != len(agent_tokens) or operator_token in agent_tokens.values():
        raise ValueError('Agent capabilities must be independent of each other and the operator capability')
    agent_tokens = dict(agent_tokens)
    if not SAFE_RUN.fullmatch(run_id):
        raise ValueError('Invalid run ID')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    root = Path(artifact_root).resolve() if artifact_root is not None else None
    display = Path(display_root).resolve() if display_root is not None else None
    if root is not None and display is not None and (
            display.is_relative_to(root) or root.is_relative_to(display)):
        raise ValueError('Display artifacts require a separate root from archived runs')

    def artifact(run, name):
        if not SAFE_RUN.fullmatch(run) or name not in PUBLIC_ARTIFACTS:
            raise HTTPException(404, 'Artifact not found')
        archived = root / run if root is not None else None
        shown = display / run if display is not None else None
        if archived is not None and archived.exists():
            directory, source_root = archived, root
        elif shown is not None and shown.exists():
            directory, source_root = shown, display
        else:
            raise HTTPException(404, 'Artifact not found')
        target = directory / name
        if directory.is_symlink() or target.is_symlink() or not target.is_file():
            raise HTTPException(404, 'Artifact not found')
        if not target.resolve().is_relative_to(source_root) or not directory.resolve().is_relative_to(source_root):
            raise HTTPException(404, 'Artifact not found')
        return target

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={'detail': str(error)})

    @app.post('/ready')
    async def ready(body: ReadyRequest, authorization: str | None = Header(default=None)):
        require_agent_capability(agent_tokens, body.agent_id, authorization)
        return await engine.ready(body.agent_id)

    @app.post('/decide')
    async def decide(body: DecideRequest, authorization: str | None = Header(default=None)):
        require_agent_capability(agent_tokens, body.agent_id, authorization)
        return await engine.decide(body.agent_id, body.action)

    @app.post('/start')
    async def start(body: StartRequest, authorization: str | None = Header(default=None)):
        require_agent_capability(agent_tokens, body.agent_id, authorization)
        return await (start_action or engine.start)(body.agent_id, body.decision_id)

    @app.post('/record')
    async def record(body: RecordRequest, authorization: str | None = Header(default=None)):
        require_agent_capability(agent_tokens, body.agent_id, authorization)
        return (record_action or engine.record)(body.agent_id, body.action, body.result,
                             decision_id=body.decision_id, elapsed=body.elapsed).to_dict()

    @app.get('/state/{agent_id}')
    async def state(agent_id: str, authorization: str | None = Header(default=None)):
        require_agent_capability(agent_tokens, agent_id, authorization)
        return engine.agent_state(agent_id)

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
