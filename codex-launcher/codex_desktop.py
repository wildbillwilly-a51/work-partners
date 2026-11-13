"""Connect to the signed-in Codex desktop app over its native local pipe."""
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time


PIPE_PREFIX = '\\\\.\\pipe\\'


def native_data(result):
    if isinstance(result.get('structuredContent'), dict):
        return result['structuredContent']
    for item in result.get('content', []):
        if item.get('type') == 'text':
            data = json.loads(item['text'])
            if isinstance(data, dict):
                return data
    raise ValueError('Unexpected native response')


def desktop_server():
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    env = {k: v for k, v in os.environ.items() if k.lower() != 'psmodulepath'}
    result = subprocess.run(
        [str(powershell), '-NoProfile', '-NonInteractive', '-Command',
         '(Get-AppxPackage -Name OpenAI.Codex).InstallLocation'],
        env=env, capture_output=True, timeout=25, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode or not result.stdout.strip():
        raise RuntimeError('codex_desktop_package_unavailable')
    server = Path(os.fsdecode(result.stdout.strip())) / 'app/resources/plugins/openai-bundled/plugins/codex-app-tools/server.mjs'
    if not server.is_file():
        raise RuntimeError('codex_desktop_bridge_unavailable')
    return server


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
        # Counts native invocations, not initialization/catalog requests. Update
        # before flush: a flush failure cannot prove that the call was not sent.
        self.native_calls_written = 0
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
        written = self.process.stdin.write(json.dumps(message) + '\n')
        if message.get('method') == 'tools/call' and written:
            self.native_calls_written += 1
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
            text = ' '.join(item.get('text', '') for item in result.get('content', []) if isinstance(item, dict))
            lowered = text.lower()
            # "Too many requests" is the history endpoints' wording for the same limit.
            error = RuntimeError('native_rate_limited' if 'rate limited' in lowered or 'too many requests' in lowered
                                 else 'native_tool_refused')
            # The native error message, short, so a refusal can be diagnosed.
            error.native_text = ' '.join(text.split())[:160]
            raise error
        return result

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        self.process.stdin.close()
        self.process.stdout.close()


def read_thread(client, thread, limit=8):
    chat = None
    cursor = None
    turns = []
    for _ in range(limit):
        arguments = {'threadId': thread, 'turnLimit': 1, 'maxOutputCharsPerItem': 20000,
                     'includeOutputs': True}
        if cursor:
            arguments['cursor'] = cursor
        try:
            page = native_data(client.call('read_thread', arguments))
        except RuntimeError as error:
            if chat is None or str(error) == 'native_rate_limited':
                raise
            break
        if chat is None:
            chat = page
        turns.extend(page.get('turns', []))
        cursor = page.get('page', {}).get('nextCursor')
        if not cursor:
            break
    chat['turns'] = turns
    for turn in chat.get('turns', []):
        turn['items'] = [
            item for item in turn.get('items', [])
            if item.get('type') in ('userMessage', 'agentMessage')
            or (item.get('type') == 'functionCallOutput'
                and item.get('namespace') == 'codex_app' and item.get('name') in ('send_message_to_thread', 'create_thread'))
        ]
    return chat


def origin_matches(chat, marker):
    return chat['thread']['kind'] == 'chatgpt' and any(
        item.get('type') == 'agentMessage' and marker in item.get('text', '')
        for turn in chat.get('turns', []) for item in turn.get('items', [])
    )


def conversation_operation(client, operation, thread, args):
    if operation == 'read':
        chat = read_thread(client, thread, args.get('limit', 8))
        if args.get('resolve_project') and chat['thread']['id'] == thread and chat['thread']['kind'] == 'codex':
            recent = native_data(client.call('list_threads', {'limit': 50}))
            matches = [item for item in recent.get('threads', []) + recent.get('pinnedThreads', [])
                       if item.get('id') == thread and item.get('kind') == 'codex'
                       and item.get('hostId') == 'local' and item.get('projectId')]
            project_ids = {item['projectId'] for item in matches}
            if len(project_ids) == 1:
                projects = native_data(client.call('list_projects', {}))['projects']
                roots = {item['path'] for item in projects
                         if item.get('projectId') in project_ids and item.get('hostId') == 'local'
                         and item.get('projectKind') == 'local' and item.get('path')}
                if len(roots) == 1:
                    chat['thread']['assignedProjectRoot'] = roots.pop()
        return chat
    if operation == 'origin':
        recent = native_data(client.call('list_threads', {'source': 'chatgpt', 'mode': 'history', 'limit': 24}))
        # The marker is written after the request starts, so older conversations cannot hold it.
        since = args.get('since')
        candidates = [item for item in recent['threads']
                      if since is None or not isinstance(item.get('updatedAt'), (int, float))
                      or item['updatedAt'] * 1000 >= since - 600000]
        matches = []
        for candidate in candidates:
            try:
                chat = read_thread(client, candidate['id'], 2)
            except RuntimeError as error:
                if str(error) == 'native_rate_limited':
                    return {'matches': matches, 'rate_limited': True}
                continue
            if chat['thread']['id'] == candidate['id'] and origin_matches(chat, args['marker']):
                matches.append(candidate['id'])
        return {'matches': matches}
    if operation == 'return':
        try:
            chat = read_thread(client, thread, 5)
        except RuntimeError as error:
            if str(error) == 'native_rate_limited':
                return {'status': 'rate_limited'}  # Nothing was sent.
            raise
        if chat['thread']['id'] != thread or chat['thread']['kind'] != args['kind']:
            return {'status': 'origin_unconfirmed'}
        if chat['thread']['status']['type'] != 'idle':
            return {'status': 'busy'}
        # The acknowledgement is not delivery evidence (native readback is), so its
        # shape must not turn a completed send into a failure that invites a resend.
        client.call('send_message_to_thread', {'threadId': thread, 'prompt': args['message']})
        return {'status': 'accepted'}
    raise ValueError('invalid_conversation_operation')


if __name__ == '__main__':
    import sys

    node, server, context, operation, target, argument_file = sys.argv[1:]
    client = AppTools(node, server, context)
    try:
        args = json.loads(Path(argument_file).read_text(encoding='utf-8'))
        try:
            print(json.dumps(conversation_operation(client, operation, target, args)))
        except RuntimeError as error:
            if str(error) != 'native_rate_limited':
                raise
            # Tell the server to pause ChatGPT history reads instead of retrying at once.
            print(json.dumps({'error': 'native_rate_limited'}))
            sys.exit(3)
    finally:
        client.close()
