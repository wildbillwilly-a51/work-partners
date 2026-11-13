"""Windows tunnel runner, with portable and agent-managed credential sources."""

import base64
import contextlib
import hashlib
from http.client import HTTPConnection, HTTPException
import json
import logging
from logging.handlers import RotatingFileHandler
import msvcrt
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import getpass
import ctypes
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
TESTED_CLIENT_SHA256 = '7e93a39a567c87154a0aef2091b81100d78f9093c4603412cc2b55bed3dfe5ea'
SAFE_ENV = {'systemroot','windir','systemdrive','comspec','pathext','path','temp','tmp','userprofile','localappdata','appdata','homedrive','homepath','programdata'}

class SetupError(Exception):
    pass

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def write_json(path, data):
    path = Path(path)
    output = tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False)
    temporary = Path(output.name)
    try:
        with output:
            json.dump(data, output, indent=2)
        for attempt in range(6):
            try:
                temporary.replace(path)
                return
            except PermissionError as error:
                # A Windows reader can briefly deny replacing the destination.
                if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 5:
                    raise
                time.sleep(.05)
    finally:
        temporary.unlink(missing_ok=True)

def validate(config_path):
    config = read_json(config_path)
    if not re.fullmatch(r'tunnel_[A-Za-z0-9]+', config.get('tunnelId','')):
        raise SetupError('invalid_tunnel_id')
    for name in ['projectRoot','stateDir','tunnelBinary']:
        if not Path(config.get(name,'')).is_absolute():
            raise SetupError('absolute_config_paths_required')
    state = Path(config['stateDir']).resolve()
    root = Path(config['projectRoot']).resolve()
    guided = config.get('guided', {})
    if guided:
        home = Path(config_path).resolve().parent
        if (config.get('workspaceRoots') != [] or config.get('legacyTools') is not False
                or len(config.get('projects', [])) != 1 or state != home / 'state'
                or root != home / 'app'):
            raise SetupError('guided_launcher_scope_changed')
    exposed = [root, *(Path(p).resolve() for p in config.get('workspaceRoots',[str(root.parent)])), *(Path(p['path']).resolve() for p in config.get('projects',[]))]
    if any(state.is_relative_to(p) for p in exposed):
        raise SetupError('state_must_be_outside_projects')
    binary = Path(config['tunnelBinary']).resolve()
    expected = config.get('tunnelSha256',TESTED_CLIENT_SHA256)
    if not re.fullmatch(r'[a-f0-9]{64}',expected) or not binary.is_file() or hashlib.sha256(binary.read_bytes()).hexdigest() != expected:
        raise SetupError('tunnel_binary_hash_mismatch')
    node = guided.get('node') or shutil.which('node')
    if not node:
        raise SetupError('node_24_required')
    version = subprocess.run([node,'--version'],capture_output=True,text=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
    if version.returncode or int(version.stdout.strip().lstrip('v').split('.')[0]) < 24:
        raise SetupError('node_24_required')
    if not (Path(guided['git']).is_file() if guided else shutil.which('git')):
        raise SetupError('git_required')
    state.mkdir(parents=True,exist_ok=True)
    return config,state,binary,node

def can_prompt():
    try:
        mode=ctypes.c_ulong()
        check=ctypes.windll.kernel32.GetConsoleMode
        check.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)];check.restype=ctypes.c_int
        return sys.stdin.isatty() and bool(check(msvcrt.get_osfhandle(sys.stdin.fileno()),ctypes.byref(mode)))
    except (OSError,ValueError,AttributeError,ctypes.ArgumentError):
        return False

def credential_record(config):
    try:
        return read_json(Path(config['stateDir']) / 'credential.json')
    except FileNotFoundError:
        raise SetupError('tunnel_key_required') from None


def credential(config,prompt=False,record=None):
    command = config.get('credentialCommand')
    if config.get('guided'):
        from windows_setup import dpapi

        record = credential_record(config) if record is None else record
        try:
            encrypted = base64.b64decode(record['encrypted'], validate=True)
            secret = dpapi(encrypted, config['guided']['id'], decrypt=True).decode('ascii')
        except (KeyError, ValueError, SetupError):
            raise SetupError('saved_tunnel_key_unavailable') from None
    elif command:
        if not isinstance(command,list) or not all(isinstance(x,str) and x for x in command):
            raise SetupError('invalid_credential_command')
        if any(re.search(r'sk-[A-Za-z0-9_-]{20,}',x) for x in command):
            raise SetupError('credential_values_not_allowed_in_commands')
        try:
            result = subprocess.run(command,capture_output=True,text=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError,subprocess.TimeoutExpired):
            raise SetupError('credential_command_failed') from None
        if result.returncode:
            raise SetupError('credential_command_failed')
        secret = result.stdout.strip()
    else:
        name = config.get('credentialEnv','WORK_PARTNERS_TUNNEL_KEY')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',name):
            raise SetupError('invalid_credential_environment_name')
        secret = os.environ.get(name,'')
        if not secret and prompt and can_prompt():
            secret=getpass.getpass('Tunnel runtime credential (hidden; not saved): ')
    if not re.fullmatch(r'sk-[A-Za-z0-9_-]{20,600}',secret):
        raise SetupError('tunnel_credential_missing_or_invalid')
    return secret

def child_environment(config_path,secret,*,config=None):
    env = {k:v for k,v in os.environ.items() if k.lower() in SAFE_ENV}
    guided = (config or {}).get('guided')
    if guided:
        path_key = next((key for key in env if key.lower() == 'path'), 'PATH')
        env[path_key] = str(Path(guided['git']).parent) + os.pathsep + env.get(path_key, '')
    env.update(WORK_PARTNERS_TUNNEL_KEY=secret,WORK_PARTNERS_CONTEXT_CONFIG=str(Path(config_path).resolve()))
    return env

@contextlib.contextmanager
def exclusive(path, *, busy='already_running_or_installing'):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.resolve()!=path.absolute(): raise SetupError('lock_path_redirected')
    with path.open('a+b') as stream:
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            raise SetupError(busy) from None
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)

def profile(config,state,node):
    value={'config_version':1,'control_plane':{'base_url':'https://api.openai.com','tunnel_id':config['tunnelId'],'api_key':'env:WORK_PARTNERS_TUNNEL_KEY'},
           'health':{'listen_addr':'127.0.0.1:0'},'admin_ui':{'open_browser':False},'log':{'level':'info','format':'json'},
           'mcp':{'commands':[{'channel':'main','command':f'"{Path(node).as_posix()}" "{(HERE / "server.mjs").as_posix()}"'}]}}
    path=state/'tunnel.yaml'; write_json(path,value); return path

def check_client(binary,profile_path,env):
    try:
        result=subprocess.run([str(binary),'doctor','--config',str(profile_path),'--json'],env=env,capture_output=True,timeout=100,creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError,subprocess.TimeoutExpired):
        raise SetupError('tunnel_doctor_unavailable') from None
    if result.returncode:
        raise SetupError('tunnel_doctor_failed_check_access_and_credential')

def status(config_path):
    config=read_json(config_path); state=Path(config['stateDir']); path=state/'runtime.json'
    if not path.exists():
        return {'state':'stopped'}
    value=read_json(path)
    if value.get('state')=='running' and time.time()-value.get('heartbeat',0)>10:
        value['state']='stale_inspect_before_restart'
    return {k:value[k] for k in ['state','run_id','pid','child_pid','heartbeat','credential_revision','background'] if k in value}

def tunnel_readiness(state, runtime_status):
    if runtime_status['state'] != 'running':
        return 'not_running'
    try:
        address = urlsplit((state/'health.url').read_text(encoding='utf-8').strip())
        if address.scheme != 'http' or address.netloc != f'127.0.0.1:{address.port}' or not address.port:
            return 'unavailable'
        with contextlib.closing(HTTPConnection('127.0.0.1', address.port, timeout=2)) as connection:
            connection.request('GET', '/readyz')
            code = connection.getresponse().status
    except (OSError, ValueError, HTTPException):
        return 'unavailable'
    return {200: 'ready', 503: 'not_ready'}.get(code, 'unavailable')

def request_stop(config_path,timeout=15):
    config=read_json(config_path); state=Path(config['stateDir']); value=status(config_path)
    if value.get('state')!='running':
        return value
    write_json(state/'stop.request',{'run_id':value['run_id']})
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        current=status(config_path)
        if current.get('run_id')!=value['run_id'] or current.get('state')!='running':
            return current
        time.sleep(.2)
    raise SetupError('stop_timeout_no_unverified_process_was_killed')

def run(config_path, *, background=False):
    import threading
    config,state,binary,node=validate(config_path)
    # Shared tunnel lock is compatible with the existing personal prototype.
    global_lock=Path(os.environ['LOCALAPPDATA'])/'WorkPartnersPrivateContext'/'locks'/(config['tunnelId']+'.lock')
    busy = 'tunnel_in_use_by_another_launcher' if config.get('guided') else 'already_running_or_installing'
    with exclusive(state/'runtime.lock'),exclusive(global_lock, busy=busy):
        prior=status(config_path)
        if prior.get('state') in ['running','stale_inspect_before_restart']:
            # Never kill or replace an unverified surviving client after a crash.
            pid=prior.get('child_pid')
            if isinstance(pid,int):
                check=subprocess.run(['tasklist','/FI',f'PID eq {pid}','/FO','CSV','/NH'],capture_output=True,text=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
                if f'"{binary.name.lower()}"' in check.stdout.lower():
                    raise SetupError('stale_child_requires_inspection')
        record = credential_record(config) if config.get('guided') else None
        secret=credential(config,prompt=True,record=record); env=child_environment(config_path,secret,config=config)
        profile_path=profile(config,state,node); check_client(binary,profile_path,env)
        run_id=uuid.uuid4().hex; child=None; threads=[]; handlers=[]
        try:
            (state/'health.url').unlink(missing_ok=True)
            child=subprocess.Popen([str(binary),'run','--config',str(profile_path),'--health.url-file',str(state/'health.url'),'--pid.file',str(state/'tunnel.pid'),'--mcp.max-concurrent-requests','1','--mcp.stdio-send-initialized-notification'],cwd=HERE,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
            if background:
                (state/'background-error.json').unlink(missing_ok=True)
            def pump(stream,handler):
                for line in iter(stream.readline,b''):
                    try:
                        value=json.loads(line.decode('utf-8','replace'))
                        safe={k:value[k] for k in ['time','level','msg','component','status_code'] if k in value}
                        text=json.dumps(safe).replace(secret,'[REDACTED]')
                        text=re.sub(r'sk-[A-Za-z0-9_-]{20,}','[REDACTED]',text)
                    except (ValueError,TypeError):
                        text=json.dumps({'event':'non_json_diagnostic','bytes':len(line)})
                    handler.emit(logging.LogRecord('tunnel',logging.INFO,'',0,text,(),None))
            for stream,name in [(child.stdout,'stdout'),(child.stderr,'stderr')]:
                handler=RotatingFileHandler(state/f'tunnel-{name}.log',maxBytes=1024*1024,backupCount=2,encoding='utf-8');handlers.append(handler)
                thread=threading.Thread(target=pump,args=(stream,handler),daemon=True);thread.start();threads.append(thread)
            if not record:
                print('Private ChatGPT connection running. Leave this window open; use stop or Ctrl+C.',flush=True)
            while child.poll() is None:
                heartbeat={'state':'running','run_id':run_id,'pid':os.getpid(),'child_pid':child.pid,'heartbeat':time.time()}
                if record:
                    heartbeat['credential_revision'] = record['revision']
                    heartbeat['background'] = background
                write_json(state/'runtime.json',heartbeat)
                stop=state/'stop.request'
                if stop.exists() and read_json(stop).get('run_id')==run_id:
                    return 0
                time.sleep(.5)
            # The managed tunnel should run until explicitly stopped.
            return child.returncode or 1
        except KeyboardInterrupt:
            return 0
        finally:
            if child and child.poll() is None:
                subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
                child.wait(timeout=20)
            for thread in threads: thread.join(timeout=3)
            for handler in handlers: handler.close()
            write_json(state/'runtime.json',{'state':'stopped','run_id':run_id})
