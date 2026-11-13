"""Private Windows installer. Copies selected source; never copies credentials or data."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime,timezone
from runtime import TESTED_CLIENT_SHA256,SetupError,exclusive,read_json,write_json

HERE=Path(__file__).resolve().parent
CONNECTOR_FILES = ('install.py', 'runtime.py', 'manage.py', 'probe.mjs', 'core.mjs',
    'project-access.mjs', 'codex-messages.mjs', 'Send-NativeCodexMessage.ps1',
    'start-codex-chat.py', 'codex_desktop.py', 'server.mjs', 'cli.mjs',
    'broker.mjs', 'broker-workers.mjs', 'tell-chatgpt.mjs', 'rollout.mjs', 'broker_desktop.py',
    'package.json', 'package-lock.json', 'PORTABLE.md', 'setup.py', 'windows_setup.py')
EXTRA_FILES = ('supervise_tunnel.py', 'Register-TunnelTask.ps1', 'rmm_actions.py', 'launch-server.mjs',
    'COMPANION.md', 'install_skill.py', 'skills/work-partners-chatgpt/SKILL.md',
    'skills/work-partners-chatgpt/scripts/chatgpt.py')
FILES = CONNECTOR_FILES + (EXTRA_FILES if (HERE/'COMPANION.md').is_file() else ())
digest=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()

def bundle(output):
    output=Path(output).resolve()
    if output.exists(): raise SetupError('bundle_output_exists')
    sources=[]
    for name in FILES:
        source=HERE/name
        if source.resolve()!=source or not source.is_file(): raise SetupError('source_file_missing_or_redirected')
        sources.append(source)
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sources: archive.write(source,source.relative_to(HERE).as_posix())
    return {'bundle':str(output),'files':len(sources),'bytes':output.stat().st_size}

def dependencies(app):
    node=shutil.which('node'); npm=shutil.which('npm')
    if not node or not npm: raise SetupError('node_24_and_npm_required')
    npm_cli=Path(npm).parent/'node_modules/npm/bin/npm-cli.js'
    if not npm_cli.is_file(): raise SetupError('npm_cli_not_found_use_standard_node_installation')
    result=subprocess.run([node,str(npm_cli),'ci','--ignore-scripts','--no-audit','--no-fund'],cwd=app,capture_output=True,timeout=240,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode: raise SetupError('dependency_install_failed_rerun_install')

def install(home,*,workspaces=(),tunnel_id=None,binary=None,expected=TESTED_CLIENT_SHA256,vault_item=None,credential_command=None,context_thread=None,source=HERE,guided=None,publish_home=None):
    home=Path(home).resolve(); source=Path(source).resolve(); app=home/'app'; state=home/'state'
    published=Path(publish_home).resolve() if publish_home else home
    if published==source or published.is_relative_to(source): raise SetupError('installation_must_be_outside_source')
    old_path=home/'installation.json'; config_path=home/'config.json'
    old=read_json(old_path) if old_path.exists() else None
    for path in [app,home/'bin',old_path,config_path]:
        if path.exists() and path.resolve()!=path: raise SetupError('installation_path_redirected')
    if home.exists() and any(home.iterdir()) and not old: raise SetupError('destination_not_empty_or_managed')
    if old:
        if old.get('format')!=1: raise SetupError('unknown_installation_format')
        config=read_json(config_path)
        if any([tunnel_id and tunnel_id!=config['tunnelId'],workspaces and [str(Path(p).resolve()) for p in workspaces]!=config['workspaceRoots'],vault_item,credential_command,context_thread]):
            raise SetupError('existing_configuration_preserved_edit_config_explicitly')
        for name,sha in old['files'].items():
            path=app/name
            if path.resolve()!=path or not path.is_file() or digest(path)!=sha: raise SetupError('installed_source_has_local_edits')
        if not (home/'work-partners.cmd').is_file() or digest(home/'work-partners.cmd')!=old['launcher_sha256']:
            raise SetupError('launcher_has_local_edits')
    else:
        if not (workspaces or guided) or not tunnel_id: raise SetupError('workspace_and_tunnel_id_required')
        roots=[str(Path(p).resolve()) for p in workspaces]
        scope = roots if not guided else [guided['project']['path']]
        if any(published.is_relative_to(Path(p)) or Path(p).is_relative_to(published) for p in scope):
            raise SetupError('installation_must_be_separate_from_workspace')
        for p in scope:
            if not Path(p).is_dir(): raise SetupError('workspace_not_found')
        config={'projectRoot':str(published/'app'),'stateDir':str(published/'state'),'workspaceRoots':roots,'projects':[],
                'legacyTools':False,'tunnelId':tunnel_id,'tunnelBinary':str(published/'bin/tunnel-client.exe'),
                'tunnelSha256':expected,'credentialEnv':'WORK_PARTNERS_TUNNEL_KEY'}
        if guided:
            config.update(projects=[guided['project']], guided={k:v for k,v in guided.items() if k!='project'})
        if vault_item: config['credentialCommand']=['bw','get','password',vault_item]
        if credential_command: config['credentialCommand']=credential_command
    if not re.fullmatch(r'tunnel_[A-Za-z0-9]+',config['tunnelId']): raise SetupError('invalid_tunnel_id')
    if config.get('credentialCommand') and (not isinstance(config['credentialCommand'],list) or not all(isinstance(x,str) and x and not re.search(r'sk-[A-Za-z0-9_-]{20,}',x) for x in config['credentialCommand'])):
        raise SetupError('credential_command_must_contain_references_not_secrets')
    node=shutil.which('node')
    if not node or not shutil.which('git'): raise SetupError('node_24_and_git_required')
    version=subprocess.run([node,'--version'],capture_output=True,text=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
    if version.returncode or int(version.stdout.strip().lstrip('v').split('.')[0])<24: raise SetupError('node_24_required')
    if context_thread:
        if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', context_thread):
            raise SetupError('codex_context_thread_must_be_a_local_chat_id')
        powershell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
        config['codexMessaging'] = {
            'enabled': True,
            'creationContextThread': context_thread,
            'command': [str(powershell), '-NoProfile', '-NonInteractive',
                        '-File', str(published/'app/Send-NativeCodexMessage.ps1'),
                        '-PythonPath', sys.executable, '-NodePath', node],
        }
        if guided:
            config['codexMessaging'].update(
                nativeStart=True,
                command=[guided['python'], '-B', str(published/'app/start-codex-chat.py'), guided['node']],
            )
    selected=Path(binary or (config['tunnelBinary'] if old else shutil.which('tunnel-client') or '')).resolve()
    wanted=config['tunnelSha256'] if old and not binary else expected
    if old and wanted!=config['tunnelSha256']: raise SetupError('existing_binary_configuration_preserved')
    if not re.fullmatch(r'[a-f0-9]{64}',wanted) or not selected.is_file() or digest(selected)!=wanted:
        raise SetupError('provide_verified_tunnel_binary_and_matching_sha256')
    payload={}
    guided_install = bool(config.get('guided'))
    for name in CONNECTOR_FILES if guided_install else FILES:
        # A private checkout also contains a broader MCP server. Guided
        # handoffs must install the same restricted server as the public build.
        source_name = ('launch-server.mjs' if guided_install and name == 'server.mjs'
                       and (source/'COMPANION.md').is_file() else name)
        path=source/source_name
        if path.resolve()!=path or not path.is_file(): raise SetupError('source_file_missing_or_redirected')
        payload[name]=path.read_bytes()
    # This lock is also held throughout an installed runtime's lifetime.
    state=(Path(config['stateDir']) if old else state).resolve()
    state.mkdir(parents=True,exist_ok=True)
    with exclusive(state/'runtime.lock'):
        app.mkdir(parents=True,exist_ok=True); (home/'bin').mkdir(exist_ok=True)
        for name,data in payload.items():
            path=app/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        target=home/'bin/tunnel-client.exe'
        if selected!=target: shutil.copyfile(selected,target)
        config['tunnelSha256']=wanted
        if not old: write_json(config_path,config)
        launcher='@echo off\r\nwhere py >nul 2>nul\r\nif errorlevel 1 (\r\n  python "%~dp0app\\manage.py" --config "%~dp0config.json" %*\r\n) else (\r\n  py -3 "%~dp0app\\manage.py" --config "%~dp0config.json" %*\r\n)\r\n'
        (home/'work-partners.cmd').write_bytes(launcher.encode('utf-8'))
        receipt={'format':1,'version':json.loads(payload['package.json'])['version'],'installed_at':datetime.now(timezone.utc).isoformat(),
                 'files':{name:hashlib.sha256(data).hexdigest() for name,data in payload.items()},'launcher_sha256':digest(home/'work-partners.cmd'),'dependencies_ready':False}
        write_json(old_path,receipt)
        lock_hash=digest(app/'package-lock.json')
        if not old or old.get('lock_sha256')!=lock_hash or not (app/'node_modules/@modelcontextprotocol/sdk/package.json').exists():
            dependencies(app)
        receipt.update(dependencies_ready=True,lock_sha256=lock_hash); write_json(old_path,receipt)
    return {'installed':str(published),'config':str(published/'config.json'),'next':'work-partners.cmd doctor --local-only; then configure a credential and start'}

def main():
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest='command',required=True)
    pack=sub.add_parser('bundle'); pack.add_argument('--output',type=Path,required=True)
    setup=sub.add_parser('install'); setup.add_argument('--home',type=Path,default=Path(os.environ.get('LOCALAPPDATA',Path.home()))/'WorkPartnersChatGPT')
    setup.add_argument('--workspace',action='append',default=[]); setup.add_argument('--tunnel-id'); setup.add_argument('--tunnel-binary',type=Path)
    setup.add_argument('--tunnel-sha256',default=TESTED_CLIENT_SHA256)
    setup.add_argument('--codex-context-thread', help='ID of an existing local Codex chat for desktop routing')
    auth=setup.add_mutually_exclusive_group();auth.add_argument('--vault-item');auth.add_argument('--credential-command-json')
    args=parser.parse_args()
    try:
        if sys.version_info<(3,12): raise SetupError('python_312_required')
        result=bundle(args.output) if args.command=='bundle' else install(args.home,workspaces=args.workspace,tunnel_id=args.tunnel_id,binary=args.tunnel_binary,expected=args.tunnel_sha256,vault_item=args.vault_item,credential_command=json.loads(args.credential_command_json) if args.credential_command_json else None,context_thread=args.codex_context_thread)
        print(json.dumps(result,indent=2)); return 0
    except SetupError as error:
        print(json.dumps({'error':str(error)}),file=sys.stderr); return 1
    except Exception as error:
        print(json.dumps({'error':'installation_failed','type':type(error).__name__}),file=sys.stderr); return 1

if __name__=='__main__': raise SystemExit(main())
