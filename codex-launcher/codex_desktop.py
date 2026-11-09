"""Connect to the signed-in Codex desktop app over its native local pipe."""
import json
import os
import queue
import re
import subprocess
import threading
import time


PIPE_PREFIX = '\\\\.\\pipe\\'


def select_pipe(configured=None):
    names = [n for n in os.listdir(PIPE_PREFIX)
             if re.fullmatch(r'codex-app-tools-[0-9a-fA-F-]{36}', n)]
    if configured:
        if configured.removeprefix(PIPE_PREFIX) not in names:
            raise RuntimeError('configured_app_pipe_unavailable')
        return configured
    if len(names) != 1:
        raise RuntimeError('app_unavailable_or_ambiguous')
    return PIPE_PREFIX + names[0]


class AppTools:
    def __init__(self, node, server, context_thread, pipe=None):
        env = os.environ.copy()
        env['CODEX_APP_TOOLS_PIPE_PATH'] = select_pipe(pipe)
        self.context_thread = context_thread
        self.process = subprocess.Popen([node, server], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            encoding='utf-8', env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.messages = queue.Queue()
        self.sequence = 0
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.request('initialize', {'protocolVersion': '2024-11-05',
                'capabilities': {}, 'clientInfo': {'name': 'work-partners-launch', 'version': '1'}})
            self._send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
            catalog = self.request('tools/list', {})
            self.available = {t['name'] for t in catalog['tools']}
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
        except (ValueError, OSError):
            pass
        finally:
            self.messages.put(None)

    def _send(self, message):
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()

    def request(self, method, params):
        self.sequence += 1
        ident = self.sequence
        self._send({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params})
        deadline = time.monotonic() + 60
        while True:
            try:
                message = self.messages.get(timeout=max(0, deadline-time.monotonic()))
            except queue.Empty:
                raise RuntimeError('app_response_timeout') from None
            if message is None:
                raise RuntimeError('app_connection_closed')
            if message.get('id') == ident:
                if 'error' in message:
                    raise RuntimeError('app_request_failed')
                return message['result']

    def call(self, name, arguments):
        if name not in self.available:
            raise RuntimeError('native_tool_unavailable')
        result = self.request('tools/call', {'name': name, 'arguments': arguments,
            '_meta': {'openai/threadId': self.context_thread}})
        if result.get('isError'):
            raise RuntimeError('native_tool_refused')
        return result

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        self.process.stdin.close()
        self.process.stdout.close()
