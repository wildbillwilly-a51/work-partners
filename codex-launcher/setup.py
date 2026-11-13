"""Agent entry point for the Windows ChatGPT-to-Codex launcher."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from uuid import UUID, uuid4
from zipfile import ZipFile

from codex_desktop import AppTools, desktop_server, native_data
from install import digest, install
from runtime import (
    SetupError, TESTED_CLIENT_SHA256, credential, credential_record, exclusive, read_json,
    request_stop, run, status, tunnel_readiness, validate, write_json,
)
from windows_setup import key_window, prompt_status, register_task, wait_for_exit, windows_tool


DEFAULT_HOME = Path.home() / '.a51/WorkPartnersChatGPT'
PLATFORM = 'https://platform.openai.com/settings/organization'
RELEASE = 'https://github.com/openai/tunnel-client/releases/download/v0.0.19/'
ASSET = 'tunnel-client-v0.0.19-windows-amd64.zip'


def chat_id(link):
    match = re.fullmatch(r'(?:codex://threads/)?([0-9a-fA-F-]{36})', link)
    if not match:
        raise SetupError('local_codex_chat_link_required')
    try:
        return str(UUID(match[1]))
    except ValueError:
        raise SetupError('local_codex_chat_link_required') from None


@contextmanager
def desktop(node, context):
    try:
        server = desktop_server()
    except RuntimeError as error:
        raise SetupError(str(error)) from None
    try:
        client = AppTools(node, server, context)
        try:
            yield client
        finally:
            client.close()
    except RuntimeError:
        raise SetupError('codex_desktop_unreachable_check_sign_in_and_local_chat') from None


def local_projects(node, context):
    with desktop(node, context) as client:
        if not {'list_projects', 'read_thread', 'create_thread'} <= client.available:
            raise SetupError('codex_desktop_required_tools_unavailable')
        thread = native_data(client.call('read_thread', {
            'threadId': context, 'hostId': 'local', 'turnLimit': 1,
        }))['thread']
        if thread.get('kind') != 'codex' or thread.get('hostId') != 'local':
            raise SetupError('local_codex_chat_link_required')
        projects = native_data(client.call('list_projects', {}))['projects']
    return [p for p in projects if p.get('hostId') == 'local' and p.get('path')]


def prerequisites():
    node, git, npm = (shutil.which(name) for name in ('node', 'git', 'npm'))
    if not all((node, git, npm)):
        raise SetupError('install_node_24_with_npm_and_git')
    version = subprocess.run([node, '--version'], capture_output=True, text=True, timeout=10,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    match = re.fullmatch(r'v(\d+)\.\d+\.\d+.*', version.stdout.strip())
    if version.returncode or not match or int(match[1]) < 24:
        raise SetupError('install_node_24_with_npm_and_git')
    python = Path(sys.executable).resolve()
    pythonw = python.with_name('pythonw.exe')
    if any(part.lower() == 'windowsapps' for tool in (python, Path(node).resolve(), Path(git).resolve())
           for part in tool.parts):
        raise SetupError('standalone_python_node_and_git_required')
    if not pythonw.is_file():
        raise SetupError('standard_windows_python_with_tk_required')
    try:
        import tkinter  # noqa: F401
    except ImportError:
        raise SetupError('standard_windows_python_with_tk_required') from None
    return {'node': str(Path(node).resolve()), 'git': str(Path(git).resolve()),
            'python': str(python), 'pythonw': str(pythonw.resolve())}


def download_client(directory):
    archive = directory / ASSET
    binary = directory / 'tunnel-client.exe'
    with urlopen(RELEASE + ASSET, timeout=30) as response, archive.open('wb') as output:
        remaining = 64 * 1024 * 1024
        while data := response.read(min(1024 * 1024, remaining + 1)):
            remaining -= len(data)
            if remaining < 0:
                raise SetupError('tunnel_client_archive_too_large')
            output.write(data)
    with ZipFile(archive) as package:
        entries = [i for i in package.infolist()
                   if not i.is_dir() and PurePosixPath(i.filename).name == binary.name]
        if len(entries) != 1 or entries[0].file_size > 64 * 1024 * 1024:
            raise SetupError('unexpected_tunnel_client_archive')
        with package.open(entries[0]) as source, binary.open('wb') as output:
            shutil.copyfileobj(source, output)
    if digest(binary) != TESTED_CLIENT_SHA256:
        raise SetupError('tunnel_client_download_hash_mismatch')
    return binary


def configure(home, args):
    if not re.fullmatch(r'tunnel_[A-Za-z0-9]+', args.tunnel):
        raise SetupError('secure_mcp_tunnel_id_required')
    context = chat_id(args.context)
    project_path = Path(args.project).resolve(strict=True)
    if home.is_relative_to(project_path) or project_path.is_relative_to(home):
        raise SetupError('installation_must_be_separate_from_workspace')
    home.parent.mkdir(parents=True, exist_ok=True)
    with exclusive(home.parent / (home.name + '.setup.lock')):
        if home.exists():
            config = load_guided(home)
            if (config['tunnelId'] != args.tunnel or config['projects'][0]['path'] != str(project_path)
                    or config['codexMessaging']['creationContextThread'] != context):
                raise SetupError('existing_launcher_selection_preserved')
            return setup_status(home)
        if portable_home(home):
            raise SetupError('portable_launcher_present_preserved')
        tools = prerequisites()
        matches = [p for p in local_projects(tools['node'], context)
                   if Path(p['path']).resolve() == project_path]
        if len(matches) != 1:
            raise SetupError('project_not_registered_in_local_codex')
        guided = {**tools, 'id': uuid4().hex[:16],
                  'project': {'path': str(project_path), 'name': matches[0]['label']}}
        with tempfile.TemporaryDirectory(prefix='.work-partners-setup-', dir=home.parent) as temp:
            scratch = Path(temp)
            binary = download_client(scratch)
            staged = scratch / 'installation'
            install(staged, tunnel_id=args.tunnel, binary=binary, context_thread=context,
                    guided=guided, publish_home=home)
            staged.rename(home)
    return setup_status(home)


def load_guided(home):
    config = read_json(home / 'config.json')
    if not config.get('guided'):
        raise SetupError('portable_launcher_present_preserved')
    return config


def portable_home(home):
    local_app_data = os.environ.get('LOCALAPPDATA')
    if home == DEFAULT_HOME and local_app_data:
        candidate = Path(local_app_data) / 'WorkPartnersChatGPT'
        if (candidate / 'installation.json').is_file():
            return candidate
    return None


def setup_status(home):
    result = {'home': str(home), 'platform_tunnels': PLATFORM + '/tunnels',
              'platform_keys': PLATFORM + '/api-keys'}
    if not home.exists():
        portable = portable_home(home)
        if portable:
            return {**result, 'next': 'existing_portable_launcher', 'existing_home': str(portable)}
        return {**result, 'next': 'configure'}
    config = read_json(home / 'config.json')
    if not config.get('guided'):
        return {**result, 'next': 'existing_portable_launcher', 'existing_home': str(home)}
    state = Path(config['stateDir'])
    path = home / 'config.json'
    runtime = status(path)
    ready = tunnel_readiness(state, runtime) == 'ready'
    revision = credential_record(config)['revision'] if (state / 'credential.json').exists() else None
    prompt = state / 'key-prompt.json'
    error = state / 'background-error.json'
    result.update(project=config['projects'][0], tunnel_id=config['tunnelId'], runtime=runtime,
                  tunnel_ready=ready, key_prompt=prompt_status(read_json(prompt)) if prompt.exists() else None,
                  background_error=read_json(error) if error.exists() else None)
    if not revision:
        return {**result, 'next': 'prompt-key'}
    if not (ready and runtime.get('background') and runtime.get('credential_revision') == revision):
        return {**result, 'next': 'activate'}
    path = state / 'setup-proof.json'
    proof = read_json(path) if path.exists() else {}
    return {**result, 'next': 'ready' if proof.get('credential_revision') == revision else 'verify_chatgpt',
            'last_verified_run': proof.get('run_id')}


def activate(home):
    path = home / 'config.json'
    config = load_guided(home)
    state = Path(config['stateDir'])
    with exclusive(state / 'activation.lock'):
        revision = credential_record(config)['revision']
        credential(config)
        task_name = register_task(path, config)
        current = status(path)
        if current['state'] == 'stale_inspect_before_restart':
            raise SetupError('stale_launcher_requires_inspection')
        if current['state'] == 'running':
            if current.get('background') and current.get('credential_revision') == revision:
                return setup_status(home)
            request_stop(path)
            if not wait_for_exit(current['pid'], 10):
                raise SetupError('previous_launcher_is_still_exiting')
        (state / 'background-error.json').unlink(missing_ok=True)
        if windows_tool('schtasks.exe', '/Run', '/TN', task_name).returncode:
            raise SetupError('scheduled_task_start_failed')
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = setup_status(home)
            background = result['background_error'] or {}
            if result['next'] != 'activate' or background.get('state') == 'failed':
                return result
            time.sleep(1)
    return setup_status(home)


def launcher_read(home, config, command, args_file=None):
    path = home / 'config.json'
    command_line = [config['guided']['node'], str(home / 'app/cli.mjs'), command, str(path)]
    if args_file:
        command_line.append(str(args_file))
    result = subprocess.run(command_line,
                            capture_output=True, text=True, encoding='utf-8', timeout=25,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise SetupError('launcher_readback_unavailable')
    return json.loads(result.stdout)


def project_id(home, config):
    projects = launcher_read(home, config, 'projects')
    if len(projects) != 1:
        raise SetupError('configured_project_scope_changed')
    return projects[0]['project_id']


def prepare_test(home):
    config = load_guided(home)
    current = setup_status(home)
    if current['next'] not in ('verify_chatgpt', 'ready'):
        raise SetupError('background_tunnel_not_ready')
    state = Path(config['stateDir'])
    path = state / 'setup-test.json'
    with exclusive(state / 'setup-test.lock'):
        if path.exists():
            previous = read_json(path)
            if previous.get('run_id') == current['runtime']['run_id']:
                return previous
        nonce = uuid4().hex
        token = 'WP-SETUP-' + nonce
        ident = project_id(home, config)
        handoff = 'setup-' + nonce
        markdown = f'Reply exactly {token}. Do not modify any files.'
        test = {'run_id': current['runtime']['run_id'], 'handoff_id': handoff,
                'project_id': ident, 'token': token, 'markdown': markdown,
                'prompt': f'Use the Work Partners connector to start a new Codex chat in local project ID '
                          f'{ident}, titled Work Partners setup test. '
                          f'Use handoff ID {handoff}. Send this complete handoff: {markdown} '
                          f'Return the new Codex chat link.'}
        write_json(path, test)
        return test


def verify_test(home, link):
    config = load_guided(home)
    state = Path(config['stateDir'])
    path = state / 'setup-test.json'
    with exclusive(state / 'setup-test.lock'):
        test = read_json(path)
        current = setup_status(home)
        if not current['tunnel_ready'] or test['run_id'] != current['runtime'].get('run_id'):
            raise SetupError('test_belongs_to_an_earlier_tunnel_run')
        ident = chat_id(link)
        signature = hashlib.sha256(f"{test['project_id']}:{test['handoff_id']}".encode()).hexdigest()
        receipt = state / f'codex-start-{signature}.json'
        if not receipt.exists():
            return {'verified': False, 'state': 'handoff_receipt_missing'}
        delivery = read_json(receipt)
        if delivery.get('phase') != 'native_call_returned' or delivery.get('thread_id') != ident:
            return {'verified': False, 'state': 'handoff_delivery_unconfirmed'}
        with tempfile.TemporaryDirectory(prefix='handoff-check-', dir=state) as scratch:
            args = Path(scratch) / 'request.json'
            write_json(args, {'project': test['project_id'], 'handoff_id': test['handoff_id']})
            handoff = launcher_read(home, config, 'project-handoffs', args).get('handoff')
        if not handoff or handoff.get('markdown') != test['markdown']:
            return {'verified': False, 'state': 'handoff_text_mismatch'}
        context = config['codexMessaging']['creationContextThread']
        with desktop(config['guided']['node'], context) as client:
            data = native_data(client.call('read_thread', {
                'threadId': ident, 'hostId': 'local', 'turnLimit': 1,
                'includeOutputs': True, 'maxOutputCharsPerItem': 4000,
            }))
        thread = data['thread']
        turns = data['turns']
        if (thread.get('hostId') != 'local' or thread.get('kind') != 'codex'
                or not thread.get('cwd') or Path(thread['cwd']).resolve() != Path(config['projects'][0]['path'])):
            return {'verified': False, 'state': 'wrong_codex_project'}
        if not turns or turns[0]['status'] == 'inProgress':
            return {'verified': False, 'state': 'codex_reply_pending'}
        items = turns[0]['items']
        prompt_seen = any(i.get('type') == 'functionCallOutput' and i.get('name') == 'create_thread'
                          and test['handoff_id'] in i.get('output', {}).get('text', '')
                          and test['markdown'] in i.get('output', {}).get('text', '') for i in items)
        reply_seen = any(i.get('type') == 'agentMessage' and i.get('phase') == 'final_answer'
                         and i.get('text') == test['token'] for i in items)
        if turns[0]['status'] != 'completed' or not (prompt_seen and reply_seen):
            return {'verified': False, 'state': 'expected_handoff_and_reply_not_observed'}
        codex_chat = f'codex://threads/{ident}'
        write_json(state / 'setup-proof.json', {
            'run_id': test['run_id'], 'credential_revision': current['runtime']['credential_revision'],
            'handoff_id': test['handoff_id'], 'codex_chat': codex_chat,
        })
        return {'verified': True, 'codex_chat': codex_chat}


def launch_key_prompt(home):
    config = load_guided(home)
    state = Path(config['stateDir'])
    progress = state / 'key-prompt.json'
    with exclusive(state / 'key-launch.lock'):
        if progress.exists():
            current = prompt_status(read_json(progress))
            if current['state'] in ('opening', 'awaiting_key', 'checking', 'retry'):
                return current
        task_name = register_task(home / 'config.json', config, key_prompt=True)
        write_json(progress, {'state': 'opening', 'started_at': time.time()})
        if windows_tool('schtasks.exe', '/Run', '/TN', task_name).returncode:
            write_json(progress, {'state': 'failed', 'error': 'key_window_could_not_start'})
            raise SetupError('key_window_could_not_start')
    return {'state': 'opening'}


def serve(path):
    load_guided(path.parent)
    diagnostic = path.parent / 'state/background-error.json'
    diagnostic.unlink(missing_ok=True)
    delays = (5, 15, 30, 60)
    for attempt in range(len(delays) + 1):
        try:
            if run(path, background=True) == 0:
                diagnostic.unlink(missing_ok=True)
                return 0
            error = 'tunnel_client_exited'
        except SetupError as failure:
            error = str(failure)
            if error not in ('tunnel_doctor_unavailable', 'tunnel_doctor_failed_check_access_and_credential'):
                raise
        if attempt == len(delays):
            break
        write_json(diagnostic, {'state': 'retrying', 'error': error, 'delay_seconds': delays[attempt]})
        time.sleep(delays[attempt])
    write_json(diagnostic, {'state': 'failed', 'error': error})
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=DEFAULT_HOME)
    commands = parser.add_subparsers(dest='action', required=True)
    for command in ('status', 'preflight', 'prompt-key', 'activate', 'prepare-test', '_key', '_serve'):
        commands.add_parser(command)
    projects = commands.add_parser('projects')
    projects.add_argument('--context', required=True)
    config = commands.add_parser('configure')
    config.add_argument('--context', required=True)
    config.add_argument('--project', type=Path, required=True)
    config.add_argument('--tunnel', required=True)
    test = commands.add_parser('verify-test')
    test.add_argument('--codex-link', required=True)
    args = parser.parse_args()
    home = args.home.absolute()
    try:
        if sys.platform != 'win32' or sys.version_info < (3, 12):
            raise SetupError('windows_and_python_312_required')
        if home.resolve() != home:
            raise SetupError('launcher_home_redirected')
        path = home / 'config.json'
        if args.action == '_key':
            key_window(path)
            return 0
        if args.action == '_serve':
            return serve(path)
        actions = {
            'status': lambda: setup_status(home),
            'preflight': prerequisites,
            'projects': lambda: {'projects': local_projects(prerequisites()['node'], chat_id(args.context))},
            'configure': lambda: configure(home, args),
            'prompt-key': lambda: launch_key_prompt(home),
            'activate': lambda: activate(home),
            'prepare-test': lambda: prepare_test(home),
            'verify-test': lambda: verify_test(home, args.codex_link),
        }
        print(json.dumps(actions[args.action](), indent=2))
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, SetupError) else type(error).__name__
        if args.action in ('_key', '_serve'):
            file = 'key-prompt.json' if args.action == '_key' else 'background-error.json'
            if code != 'already_running_or_installing':
                write_json(home / 'state' / file, {'state': 'failed', 'error': code})
        else:
            print(json.dumps({'error': code}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
