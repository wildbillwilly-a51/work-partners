"""Offline regression checks for Windows launcher setup."""

from contextlib import contextmanager
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4


if sys.platform != 'win32':
    raise unittest.SkipTest('Windows launcher')

root = Path(__file__).resolve().parents[1]
launcher = root / 'codex-launcher'
if not launcher.is_dir():
    launcher = root / 'experiments/chatgpt-context'
sys.path.insert(0, str(launcher))
import setup
import windows_setup
from runtime import SetupError, credential, read_json, write_json


class GuidedLauncherTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.state = self.home / 'state'
        self.state.mkdir()
        self.project = self.home / 'example-project'
        self.project.mkdir()
        self.config = {
            'stateDir': str(self.state), 'tunnelId': 'tunnel_test',
            'guided': {'id': uuid4().hex, 'node': 'node', 'git': 'git.exe'},
            'codexMessaging': {'creationContextThread': str(uuid4())},
            'projects': [{'name': 'example', 'path': str(self.project)}],
        }
        self.config_path = self.home / 'config.json'
        write_json(self.config_path, self.config)

    def test_key_rotation_checks_new_value_before_replacing_existing_credential(self):
        first = 'sk-' + 'x' * 24
        second = 'sk-' + 'y' * 24
        validation = self.config, self.state, Path('tunnel-client.exe'), 'node.exe'
        with patch.object(windows_setup, 'validate', return_value=validation):
            with patch.object(windows_setup, 'check_client'):
                windows_setup.save_verified_key(self.config_path, first)
            original = (self.state / 'credential.json').read_bytes()
            with patch.object(windows_setup, 'check_client', side_effect=SetupError('key_rejected')):
                with self.assertRaisesRegex(SetupError, 'key_rejected'):
                    windows_setup.save_verified_key(self.config_path, second)
            self.assertEqual((self.state / 'credential.json').read_bytes(), original)
            self.assertNotIn(first.encode(), original)
            self.assertEqual(credential(self.config), first)
            with patch.object(windows_setup, 'check_client'):
                windows_setup.save_verified_key(self.config_path, second)
            self.assertEqual(credential(self.config), second)

    def test_configure_publishes_only_after_staged_install_completes(self):
        destination = self.home / 'installation'
        context = str(uuid4())
        args = SimpleNamespace(tunnel='tunnel_test', context=context, project=self.project)
        tools = {'node': 'node', 'git': 'git', 'pythonw': 'pythonw'}
        projects = [{'label': 'example', 'path': str(self.project)}]

        def fail(staged, **options):
            self.assertEqual(options['publish_home'], destination)
            self.assertEqual(options['guided']['project'], {'name': 'example', 'path': str(self.project)})
            staged.mkdir()
            raise SetupError('install_interrupted')

        with (patch.object(setup, 'prerequisites', return_value=tools),
              patch.object(setup, 'local_projects', return_value=projects),
              patch.object(setup, 'download_client'),
              patch.object(setup, 'install', side_effect=fail)):
            with self.assertRaisesRegex(SetupError, 'install_interrupted'):
                setup.configure(destination, args)
        self.assertFalse(destination.exists())

    def test_task_registers_only_the_current_user_and_does_not_expire(self):
        task = windows_setup.task_xml('owner', 'S-1-5-21-123', 'pythonw.exe', 'script.py', self.home)
        self.assertEqual(task.findtext('./Principals/Principal/UserId'), 'S-1-5-21-123')
        self.assertEqual(task.findtext('./Principals/Principal/LogonType'), 'InteractiveToken')
        self.assertEqual(task.findtext('./Principals/Principal/RunLevel'), 'LeastPrivilege')
        self.assertEqual(task.findtext('./Triggers/LogonTrigger/UserId'), 'S-1-5-21-123')
        self.assertEqual(task.findtext('./Settings/ExecutionTimeLimit'), 'PT0S')
        self.assertEqual(task.findtext('./Settings/MultipleInstancesPolicy'), 'IgnoreNew')
        self.assertEqual(task.findtext('./Actions/Exec/Command'), 'pythonw.exe')
        self.assertEqual(task.findtext('./Triggers/LogonTrigger/Delay'), 'PT15S')
        prompt = windows_setup.task_xml('owner', 'S-1-5-21-123', 'pythonw.exe', 'prompt.py',
                                        self.home, logon=False)
        self.assertIsNone(prompt.find('./Triggers'))
        self.assertEqual(prompt.findtext('./Settings/ExecutionTimeLimit'), 'PT30M')

    def test_prompt_reports_when_the_window_dies_before_key_verification(self):
        with (patch.object(windows_setup, 'wait_for_exit', return_value=False),
              patch.object(windows_setup, 'process_started', return_value=1234)):
            result = windows_setup.prompt_status({'state': 'checking', 'pid': 987, 'process_started': 1233})
        self.assertEqual(result, {'state': 'failed', 'error': 'key_window_closed_unexpectedly'})

    def test_pending_retest_after_restart_preserves_verification_for_the_key(self):
        write_json(self.state / 'credential.json', {'revision': 'same-key'})
        write_json(self.state / 'setup-proof.json', {'run_id': 'old-run', 'credential_revision': 'same-key'})
        write_json(self.state / 'setup-test.json', {'run_id': 'old-run'})
        runtime = {'state': 'running', 'run_id': 'new-run', 'background': True, 'credential_revision': 'same-key'}
        with (patch.object(setup, 'status', return_value=runtime),
              patch.object(setup, 'tunnel_readiness', return_value='ready'),
              patch.object(setup, 'project_id', return_value='example-id')):
            self.assertEqual(setup.prepare_test(self.home)['run_id'], 'new-run')
            self.assertEqual(setup.setup_status(self.home)['next'], 'ready')
            self.assertEqual(setup.setup_status(self.home)['last_verified_run'], 'old-run')

    def test_background_retries_transient_startup_failures_without_an_agent(self):
        error = SetupError('tunnel_doctor_unavailable')
        with (patch.object(setup, 'run', side_effect=[error, 1, 0]) as run,
              patch.object(setup.time, 'sleep') as sleep):
            self.assertEqual(setup.serve(self.config_path), 0)
        self.assertEqual(run.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 15])
        self.assertFalse((self.state / 'background-error.json').exists())

    def test_background_does_not_retry_shared_tunnel_lock(self):
        with (patch.object(setup, 'run', side_effect=SetupError('tunnel_in_use_by_another_launcher')) as run,
              patch.object(setup.time, 'sleep') as sleep):
            with self.assertRaisesRegex(SetupError, 'tunnel_in_use_by_another_launcher'):
                setup.serve(self.config_path)
        run.assert_called_once()
        sleep.assert_not_called()

    def test_unowned_task_is_preserved(self):
        self.config['guided']['pythonw'] = 'pythonw.exe'
        other = (b'<?xml version="1.0" encoding="UTF-16"?>'
                 b'<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"/>')
        with patch.object(windows_setup, 'windows_tool',
                          return_value=SimpleNamespace(returncode=0, stdout=other)) as tool:
            with self.assertRaisesRegex(SetupError, 'scheduled_task_ownership_mismatch'):
                windows_setup.register_task(self.config_path, self.config)
        tool.assert_called_once()

    def test_verification_requires_matching_receipt_staged_text_and_native_reply(self):
        link_id = str(uuid4())
        test = {'run_id': 'run', 'project_id': 'project', 'handoff_id': 'setup-nonce',
                'token': 'WP-SETUP-nonce', 'markdown': 'Reply exactly WP-SETUP-nonce.'}
        test_path = self.state / 'setup-test.json'
        write_json(test_path, test)
        signature = hashlib.sha256(b'project:setup-nonce').hexdigest()
        receipt = self.state / f'codex-start-{signature}.json'
        write_json(receipt, {'phase': 'dispatching'})
        state = {'tunnel_ready': True, 'runtime': {'run_id': 'run', 'credential_revision': 'key'}}
        thread = {'hostId': 'local', 'kind': 'codex', 'cwd': str(self.project)}
        items = [
            {'type': 'functionCallOutput', 'name': 'create_thread',
             'output': {'text': 'setup-nonce: ' + test['markdown']}},
            {'type': 'agentMessage', 'phase': 'final_answer', 'text': 'wrong reply'},
        ]

        @contextmanager
        def desktop(*_):
            def call(*_):
                return {'structuredContent': {'thread': thread, 'turns': [{'status': 'completed', 'items': items}]}}
            yield SimpleNamespace(call=call)

        with (patch.object(setup, 'setup_status', return_value=state),
              patch.object(setup, 'desktop', desktop),
              patch.object(setup, 'launcher_read', return_value={'handoff': {'markdown': test['markdown']}}) as read):
            self.assertEqual(setup.verify_test(self.home, link_id)['state'], 'handoff_delivery_unconfirmed')
            write_json(receipt, {'phase': 'native_call_returned', 'thread_id': link_id})
            read.return_value = {'handoff': {'markdown': 'unrelated'}}
            self.assertEqual(setup.verify_test(self.home, link_id)['state'], 'handoff_text_mismatch')
            read.return_value = {'handoff': {'markdown': test['markdown']}}
            self.assertEqual(setup.verify_test(self.home, link_id)['state'], 'expected_handoff_and_reply_not_observed')
            items[1]['text'] = test['token']
            self.assertTrue(setup.verify_test(self.home, link_id)['verified'])
        self.assertEqual(read_json(self.state / 'setup-proof.json')['credential_revision'], 'key')


if __name__ == '__main__':
    unittest.main()
