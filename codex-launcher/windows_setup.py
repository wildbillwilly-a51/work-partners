"""Windows-only helpers for the agent-managed launcher."""

import base64
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from uuid import uuid4
from xml.etree import ElementTree as ET

from runtime import (
    SetupError, check_client, child_environment, exclusive, profile, validate, write_json,
)


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(value, install_id, *, decrypt=False):
    """Use the current Windows user's DPAPI without PowerShell modules."""
    data = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    binding = (ctypes.c_ubyte * len(install_id)).from_buffer_copy(install_id.encode('ascii'))
    source, entropy, result = Blob(len(data), data), Blob(len(binding), binding), Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32')
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob),
    ]
    operation.restype = wintypes.BOOL
    try:
        if not operation(ctypes.byref(source), None, ctypes.byref(entropy), None, None,
                         1, ctypes.byref(result)):
            raise SetupError('windows_credential_encryption_failed')
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.memset(data, 0, len(data))
        if result.data:
            ctypes.memset(result.data, 0, result.size)
            kernel.LocalFree(result.data)


def save_verified_key(config_path, key):
    import re

    if not re.fullmatch(r'sk-[A-Za-z0-9_-]{20,600}', key):
        raise SetupError('key_format_invalid')
    config, state, binary, node = validate(config_path)
    with tempfile.TemporaryDirectory(prefix='credential-check-', dir=state) as scratch:
        env = child_environment(config_path, key, config=config)
        check_client(binary, profile(config, Path(scratch), node), env)
    record = {
        'revision': uuid4().hex,
        'encrypted': base64.b64encode(dpapi(key.encode('ascii'), config['guided']['id'])).decode('ascii'),
    }
    write_json(state / 'credential.json', record)
    return record['revision']


def key_window(config_path):
    import tkinter as tk
    from tkinter import ttk

    _, state, _, _ = validate(config_path)
    progress = state / 'key-prompt.json'
    with exclusive(state / 'key-prompt.lock'):
        started = process_started(os.getpid())
        if started is None:
            raise SetupError('key_window_process_identity_unavailable')

        def report(outcome, **details):
            write_json(progress, {'state': outcome, 'pid': os.getpid(), 'process_started': started, **details})

        window = tk.Tk()
        window.title('Work Partners tunnel key')
        window.attributes('-topmost', True)
        window.after(1000, lambda: window.attributes('-topmost', False))
        frame = ttk.Frame(window, padding=20)
        frame.grid()
        ttk.Label(frame, text='Paste your restricted OpenAI Platform tunnel key.').grid(sticky='w')
        entry = ttk.Entry(frame, width=50, show='*')
        entry.grid(sticky='ew', pady=8)
        message = tk.StringVar(value='Required permissions: Tunnels Read and Use.')
        ttk.Label(frame, textvariable=message, wraplength=390).grid(sticky='w')
        checking = False

        def cancel():
            if not checking:
                report('cancelled')
                window.destroy()

        def submit(_event=None):
            nonlocal checking
            if checking:
                return
            key = entry.get().strip()
            entry.delete(0, 'end')
            entry.configure(state='disabled')
            submit_button.configure(state='disabled')
            message.set('Checking access to the tunnel…')
            checking = True
            report('checking')
            results = []

            def verify():
                try:
                    results.append(('verified', save_verified_key(config_path, key)))
                except SetupError as error:
                    results.append(('retry', str(error)))
                except Exception:
                    results.append(('retry', 'key_check_unavailable'))

            def finish():
                nonlocal checking
                if not results:
                    window.after(150, finish)
                    return
                outcome, detail = results[0]
                if outcome == 'verified':
                    report(outcome, revision=detail)
                    window.destroy()
                    return
                report(outcome, error=detail)
                message.set(
                    'Paste the complete key from OpenAI Platform.' if detail == 'key_format_invalid' else
                    'Could not verify the key. Check its permissions, expiration, and tunnel workspace, then try again.'
                )
                checking = False
                entry.configure(state='normal')
                submit_button.configure(state='normal')
                entry.focus_set()

            threading.Thread(target=verify, daemon=True).start()
            window.after(150, finish)

        submit_button = ttk.Button(frame, text='Verify and save', command=submit)
        submit_button.grid(sticky='e', pady=(10, 0))
        window.bind('<Return>', submit)
        window.protocol('WM_DELETE_WINDOW', cancel)
        report('awaiting_key')
        entry.focus_force()
        window.mainloop()


TASK_NS = 'http://schemas.microsoft.com/windows/2004/02/mit/task'


def task_xml(owner, sid, executable, arguments, directory, *, logon=True):
    task = ET.Element('Task', xmlns=TASK_NS, version='1.2')
    registration = ET.SubElement(task, 'RegistrationInfo')
    ET.SubElement(registration, 'Description').text = owner
    if logon:
        trigger = ET.SubElement(ET.SubElement(task, 'Triggers'), 'LogonTrigger')
        ET.SubElement(trigger, 'UserId').text = sid
        ET.SubElement(trigger, 'Delay').text = 'PT15S'
    principal = ET.SubElement(ET.SubElement(task, 'Principals'), 'Principal', id='Launcher')
    for name, value in {'UserId': sid, 'LogonType': 'InteractiveToken', 'RunLevel': 'LeastPrivilege'}.items():
        ET.SubElement(principal, name).text = value
    settings = ET.SubElement(task, 'Settings')
    for name, value in {
        'MultipleInstancesPolicy': 'IgnoreNew', 'DisallowStartIfOnBatteries': 'false',
        'StopIfGoingOnBatteries': 'false', 'ExecutionTimeLimit': 'PT0S' if logon else 'PT30M',
        'Hidden': 'true',
    }.items():
        ET.SubElement(settings, name).text = value
    if logon:
        restart = ET.SubElement(settings, 'RestartOnFailure')
        ET.SubElement(restart, 'Interval').text = 'PT1M'
        ET.SubElement(restart, 'Count').text = '5'
    action = ET.SubElement(ET.SubElement(task, 'Actions', Context='Launcher'), 'Exec')
    for name, value in {'Command': executable, 'Arguments': arguments, 'WorkingDirectory': directory}.items():
        ET.SubElement(action, name).text = str(value)
    return ET.ElementTree(task)


def windows_tool(name, *args):
    path = Path(os.environ['SystemRoot']) / 'System32' / name
    return subprocess.run([str(path), *args], stdin=subprocess.DEVNULL, capture_output=True,
                          timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)


def current_sid():
    import csv

    result = windows_tool('whoami.exe', '/user', '/fo', 'csv', '/nh')
    if result.returncode:
        raise SetupError('windows_user_identity_unavailable')
    return next(csv.reader([result.stdout.decode('ascii', errors='replace').strip()]))[-1]


def register_task(config_path, config, *, key_prompt=False):
    guided = config['guided']
    owner = 'Work Partners launcher ' + guided['id']
    task_name = ('Work Partners key prompt ' if key_prompt else 'Work Partners ChatGPT ') + guided['id']
    home = config_path.parent
    executable = guided['pythonw']
    action = '_key' if key_prompt else '_serve'
    arguments = subprocess.list2cmdline(['-B', str(home / 'app/setup.py'), '--home', str(home), action])
    query = windows_tool('schtasks.exe', '/Query', '/TN', task_name, '/XML')
    if query.returncode == 0:
        payload = query.stdout
        if b'\0' in payload[:10]:
            payload = payload.decode('utf-16' if payload.startswith(b'\xff\xfe') else 'utf-16-le')
        else:
            try:
                payload = payload.decode('utf-8-sig')
            except UnicodeDecodeError:
                payload = payload.decode('oem')
        existing = ET.fromstring(payload)
        ns = {'t': TASK_NS}
        if (existing.findtext('t:RegistrationInfo/t:Description', namespaces=ns) != owner
                or existing.findtext('t:Actions/t:Exec/t:Command', namespaces=ns) != executable
                or existing.findtext('t:Actions/t:Exec/t:Arguments', namespaces=ns) != arguments
                or existing.findtext('t:Principals/t:Principal/t:UserId', namespaces=ns) != current_sid()):
            raise SetupError('scheduled_task_ownership_mismatch')
    else:
        with tempfile.TemporaryDirectory(prefix='task-', dir=home / 'state') as scratch:
            path = Path(scratch) / 'task.xml'
            task_xml(owner, current_sid(), executable, arguments, home, logon=not key_prompt).write(
                path, encoding='utf-16', xml_declaration=True,
            )
            if windows_tool('schtasks.exe', '/Create', '/TN', task_name, '/XML', str(path)).returncode:
                raise SetupError('scheduled_task_registration_failed')
    return task_name


def wait_for_exit(pid, timeout):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    process = kernel.OpenProcess(0x00100000, False, pid)
    if not process:
        return ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER: PID no longer exists.
    try:
        return kernel.WaitForSingleObject(process, int(timeout * 1000)) == 0
    finally:
        kernel.CloseHandle(process)


def process_started(pid):
    kernel = ctypes.WinDLL('kernel32')
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    process = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        return None
    try:
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if kernel.GetProcessTimes(process, ctypes.byref(created), ctypes.byref(exited),
                                  ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        kernel.CloseHandle(process)
    return None


def prompt_status(value):
    if value['state'] == 'opening' and time.time() - value['started_at'] > 20:
        return {'state': 'failed', 'error': 'key_window_did_not_open'}
    if (value['state'] in ('awaiting_key', 'checking', 'retry')
            and (wait_for_exit(value['pid'], 0)
                 or process_started(value['pid']) != value['process_started'])):
        return {'state': 'failed', 'error': 'key_window_closed_unexpectedly'}
    return value
