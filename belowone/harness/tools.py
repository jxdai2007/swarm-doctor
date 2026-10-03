"""Confined coding tools. Cwd confinement is not an operating-system sandbox."""
import asyncio
import os
from pathlib import Path
import signal
import sys

from scenarios import ROOT, check_scenario


FIELDS = {
    'list': {'path'}, 'read': {'path'}, 'write': {'path', 'content', 'subtask_id'},
    'run-tests': set(), 'send-message': {'recipient', 'content'},
    'read-inbox': set(), 'finish': set(),
}
OPERATIONS = {'run-tests': 'run', 'send-message': 'send', 'read-inbox': 'receive'}


def normalize(reply):
    if not isinstance(reply, dict) or reply.get('tool') not in FIELDS:
        raise ValueError('Exactly one known tool object required')
    tool = reply['tool']
    if set(reply) - {'tool'} - FIELDS[tool]:
        raise ValueError('Unexpected tool arguments')
    args = {key: value for key, value in reply.items() if key != 'tool'}
    if tool in {'read', 'write'} and not isinstance(args.get('path'), str):
        raise ValueError('File tool requires path')
    if tool in {'write', 'send-message'} and not isinstance(args.get('content'), str):
        raise ValueError('Tool content must be text')
    if tool == 'send-message' and not isinstance(args.get('recipient'), str):
        raise ValueError('Message requires recipient')
    if 'subtask_id' in args and not isinstance(args['subtask_id'], str):
        raise ValueError('Subtask ID must be text')
    if tool == 'list':
        args.setdefault('path', '.')
        if not isinstance(args['path'], str):
            raise ValueError('List path must be text')
    return {'tool': tool, 'operation': OPERATIONS.get(tool, tool),
            'paths': [args['path']] if 'path' in args else [], 'input': args}


class Tools:
    def __init__(self, workspace, *, test_timeout=10):
        self.workspace = Path(workspace).resolve()
        checkout = ROOT.parent.resolve()
        if self.workspace == checkout or checkout in self.workspace.parents:
            raise ValueError('Tools require throwaway workspace outside checkout')
        if not self.workspace.is_dir() or test_timeout <= 0:
            raise ValueError('Existing workspace and positive timeout required')
        self.test_timeout = test_timeout

    def path(self, name):
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or '\x00' in name:
            raise ValueError('Path must stay inside throwaway workspace')
        path = self.workspace / relative
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != self.workspace.parent):
            raise ValueError('Symlink tool paths forbidden')
        resolved = path.resolve()
        if resolved != self.workspace and self.workspace not in resolved.parents:
            raise ValueError('Path escapes workspace')
        if resolved.name == 'goal-spec.json':
            raise ValueError('Locked goal spec is not an agent tool target')
        return resolved

    async def tests(self):
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-I', '-c',
            'import sys,unittest;sys.path.insert(0,sys.argv[1]);r=unittest.TextTestRunner().run(unittest.defaultTestLoader.discover("tests"));sys.exit(not r.wasSuccessful())',
            str(self.workspace),
            cwd=self.workspace, env={'PATH': os.environ.get('PATH', '')},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), self.test_timeout)
        except (TimeoutError, asyncio.CancelledError) as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            return {'ok': False, 'error': 'Test process-group timeout'}
        return {'ok': process.returncode == 0, 'output': output.decode(errors='replace'),
                'returncode': process.returncode}

    async def execute(self, action, *, messages=()):
        try:
            tool, args = action['tool'], action['input']
            if tool in {'list', 'read', 'write'}:
                path = self.path(args['path'])
                if tool == 'list':
                    return {'ok': True, 'entries': sorted(child.name for child in path.iterdir())}
                if tool == 'read':
                    return {'ok': True, 'content': path.read_text()}
                before = path.read_bytes() if path.exists() else None
                content = args['content'].encode()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                return {'ok': True, 'changed': before != content}
            if tool == 'run-tests':
                return await self.tests()
            if tool == 'read-inbox':
                return {'ok': True, 'messages': list(messages)}
            if tool == 'send-message':
                return {'ok': True, 'delivered': True}
            if tool == 'finish':
                grader = await asyncio.to_thread(check_scenario, self.workspace, timeout=self.test_timeout)
                return {'ok': True, 'completed': grader['passed'], 'grader': grader}
            raise ValueError('Unknown coding tool')
        except (OSError, UnicodeError, ValueError) as exc:
            return {'ok': False, 'error': str(exc)}
