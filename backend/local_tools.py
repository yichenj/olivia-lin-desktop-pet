"""Asynchronous local execution; shell authority is explicit, not a sandbox."""
import asyncio
import os
from pathlib import Path
import signal
import stat
import tempfile


class LocalTools:
    def __init__(self, workspace, shell_enabled=False, timeout=60, secrets=()):
        self.workspace = Path(workspace).expanduser().resolve()
        self.shell_enabled = shell_enabled
        self.timeout = timeout
        self.secrets = tuple(value for value in secrets if value)

    def sanitize(self, value):
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, '[redacted]')
        elif isinstance(value, dict):
            value = {key: self.sanitize(item) for key, item in value.items()}
        return value

    def path(self, value):
        target = (self.workspace / value).resolve()
        if not target.is_relative_to(self.workspace):
            raise ValueError('path_outside_workspace')
        return target

    def file_operation(self, name, args):
        if name == 'read_file':
            target = self.path(args['path'])
            if not stat.S_ISREG(target.stat().st_mode):
                raise ValueError('not_a_regular_file')
            with target.open('rb') as handle:
                data = handle.read(65537)
            return {'text': data[:65536].decode('utf-8', errors='replace'), 'truncated': len(data) > 65536}
        if name == 'write_file':
            target = self.path(args['path'])
            content = args['content'].encode('utf-8')
            if len(content) > 200000:
                raise ValueError('file_too_large')
            # Only existing directories: avoid implicitly creating a broad directory tree.
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                temporary = Path(handle.name)
                try:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                    if target.exists():
                        os.chmod(temporary, target.stat().st_mode & 0o777)
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            return {'path': str(target), 'bytes': len(content)}

    async def execute(self, name, args):
        if name in ('read_file', 'write_file'):
            operation = asyncio.create_task(asyncio.to_thread(self.file_operation, name, args))
            try:
                return await asyncio.shield(operation)
            except asyncio.CancelledError:
                # Filesystem calls cannot be interrupted. Keep the slot until their outcome settles.
                await operation
                raise
        if name != 'run_shell' or not self.shell_enabled:
            raise ValueError('shell_not_enabled')
        spawning = asyncio.create_task(asyncio.create_subprocess_exec('/bin/sh', '-c', args['command'],
            cwd=self.workspace, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True))
        try:
            process = await asyncio.shield(spawning)
        except asyncio.CancelledError:
            process = await spawning
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.communicate()
            raise
        output = bytearray()
        truncated = False

        async def drain():
            nonlocal truncated
            while data := await process.stdout.read(8192):
                room = max(0, 65536 - len(output))
                output.extend(data[:room])
                truncated |= len(data) > room
            await process.wait()

        def kill_group():
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        timed_out = False
        try:
            await asyncio.wait_for(drain(), self.timeout)
        except asyncio.TimeoutError:
            timed_out = True
        finally:
            # Also close descendants left behind by a shell which has exited.
            kill_group()
            # Keep draining after cancellation so a full pipe cannot prevent wait() completing.
            while await process.stdout.read(8192):
                pass
            await process.wait()
        return {'exit_code': process.returncode, 'output': output.decode('utf-8', errors='replace'),
                'truncated': truncated, 'timed_out': timed_out}
