import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from hosty.cli import PACKAGES, install_bun, install_dependencies
from hosty.server import provision


class DependenciesTest(unittest.TestCase):
    @patch('hosty.server.command')
    @patch.object(Path, 'write_text')
    def test_python_apps_use_installed_runtime_or_project_venv(self, write, command):
        item = {'id': 'abcdef123456', 'runtime': 'python', 'path': '/var/www/app', 'port': 9000, 'domain': 'example.com', 'environment': 'production'}
        for has_venv, executable in [(False, '/usr/bin/python3'), (True, '/var/www/app/.venv/bin/python')]:
            with self.subTest(has_venv=has_venv), patch.object(Path, 'is_file', return_value=has_venv):
                write.reset_mock()
                provision(item)
                self.assertIn(f'ExecStart={executable} -m uvicorn main:app', write.call_args_list[0].args[0])

    @patch('hosty.cli.subprocess.run')
    def test_installed_packages_do_not_trigger_apt(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, 'install ok installed')
        install_dependencies()
        self.assertEqual(run.call_count, len(PACKAGES))
        self.assertTrue(all(call.args[0][0] == 'dpkg-query' for call in run.call_args_list))

    @patch('hosty.cli.subprocess.run')
    def test_only_missing_packages_are_installed(self, run):
        missing = {'python3-uvicorn', 'unzip', 'openssh-server'}
        def result(args, **kwargs):
            if args[0] == 'dpkg-query':
                return subprocess.CompletedProcess(args, 1 if args[-1] in missing else 0,
                                                   '' if args[-1] in missing else 'install ok installed')
            return subprocess.CompletedProcess(args, 0)
        run.side_effect = result
        install_dependencies()
        apt_calls = [call for call in run.call_args_list if call.args[0][0] == 'apt-get']
        self.assertEqual(apt_calls[0].args[0], ['apt-get', 'update'])
        self.assertEqual(set(apt_calls[1].args[0][3:]), missing)
        self.assertTrue(apt_calls[1].kwargs['check'])
        self.assertEqual(apt_calls[1].kwargs['env']['DEBIAN_FRONTEND'], 'noninteractive')

    @patch('hosty.cli.subprocess.run')
    def test_package_failure_stops_installation(self, run):
        def result(args, **kwargs):
            if args[0] == 'apt-get':
                raise subprocess.CalledProcessError(100, args)
            return subprocess.CompletedProcess(args, 1, '')
        run.side_effect = result
        with self.assertRaises(subprocess.CalledProcessError):
            install_dependencies()
        self.assertFalse(any(call.args[0][:2] == ['apt-get', 'install'] for call in run.call_args_list))

    @patch('hosty.cli.shutil.which', return_value='/usr/local/bin/bun')
    @patch('hosty.cli.subprocess.run')
    def test_usable_bun_is_preserved(self, run, which):
        run.return_value = subprocess.CompletedProcess([], 0, '1.3.0\n')
        self.assertEqual(install_bun(), '/usr/local/bin/bun')
        run.assert_called_once_with(['runuser', '-u', 'hosty', '--', '/usr/local/bin/bun', '--version'], capture_output=True, text=True)

    @patch('hosty.cli.shutil.which', return_value='/usr/local/bin/bun')
    @patch('hosty.cli.subprocess.run')
    def test_inaccessible_bun_replaced_and_download_failure_stops(self, run, which):
        def result(args, **kwargs):
            if args[0] == 'curl':
                raise subprocess.CalledProcessError(22, args)
            return subprocess.CompletedProcess(args, 1, '')
        run.side_effect = result
        with self.assertRaises(subprocess.CalledProcessError):
            install_bun()
        self.assertEqual(run.call_args_list[-1].args[0][0], 'curl')
        self.assertFalse(any(call.args[0][0] == 'bash' for call in run.call_args_list))

if __name__ == '__main__':
    unittest.main()

class PanelAliasTest(unittest.TestCase):
    def test_alias_added_once_and_other_entries_preserved(self):
        import tempfile
        from hosty.cli import configure_panel_alias
        with tempfile.TemporaryDirectory() as directory:
            hosts = Path(directory) / 'hosts'
            hosts.write_text('127.0.0.1 localhost\n192.0.2.1 example.test\n')
            configure_panel_alias(hosts)
            configure_panel_alias(hosts)
            text = hosts.read_text()
            self.assertEqual(text.count('hosty.local'), 1)
            self.assertIn('127.0.0.1 hosty.local', text)
            self.assertIn('192.0.2.1 example.test', text)

    def test_conflicting_alias_is_not_overwritten(self):
        import tempfile
        from hosty.cli import configure_panel_alias
        with tempfile.TemporaryDirectory() as directory:
            hosts = Path(directory) / 'hosts'
            original = '192.0.2.1 hosty.local\n'
            hosts.write_text(original)
            with self.assertRaises(RuntimeError):
                configure_panel_alias(hosts)
            self.assertEqual(hosts.read_text(), original)

    @patch('hosty.server.command')
    @patch.object(Path, 'unlink')
    @patch.object(Path, 'write_text')
    def test_development_service_has_no_public_nginx_route(self, write, unlink, command):
        item = {'id': 'abcdef123456', 'runtime': 'html', 'path': '/var/www/app', 'port': 9000, 'domain': 'example.com', 'environment': 'development'}
        provision(item)
        self.assertEqual(write.call_count, 1)
        self.assertIn('--bind 127.0.0.1', write.call_args.args[0])
        self.assertIn('NODE_ENV=development', write.call_args.args[0])
        unlink.assert_called_once_with(missing_ok=True)

class AppStartupDiagnosticsTest(unittest.TestCase):
    @patch('hosty.server.command')
    def test_service_failure_includes_journal(self, command):
        from hosty.server import service_action
        command.side_effect = ['', ValueError('Failed to start service'), 'status=200/CHDIR: Permission denied']
        with self.assertRaises(ValueError) as error:
            service_action({'id': '96d70ac41722', 'name': 'Savings'}, 'restart')
        self.assertIn('200/CHDIR', str(error.exception))
        self.assertIn('Savings', str(error.exception))
        command.assert_any_call(['journalctl', '-u', 'hosty-app-96d70ac41722', '-n', '40', '--no-pager'])

    @patch('hosty.server.command', side_effect=ValueError('Command failed'))
    def test_unreadable_app_has_actionable_error(self, command):
        from hosty.server import check_app_access
        with self.assertRaises(ValueError) as error:
            check_app_access({'path': '/root/typewriter-savings'})
        self.assertIn('Apps run as hosty', str(error.exception))
        self.assertIn('/var/www', str(error.exception))

class AutomaticPermissionsTest(unittest.TestCase):
    @patch('hosty.server.shutil.which', return_value='/usr/bin/setfacl')
    @patch('hosty.server.command')
    def test_project_grants_are_scoped_and_new_files_inherit_access(self, command, which):
        import tempfile
        from hosty.server import prepare_app_permissions
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / 'project'
            nested = project / 'data'
            nested.mkdir(parents=True)
            external = Path(temporary) / 'outside'
            external.mkdir()
            (project / 'link').symlink_to(external, target_is_directory=True)
            prepare_app_permissions({'path': str(project)})
            commands = [call.args[0] for call in command.call_args_list]
            command.assert_any_call(['setfacl', '-R', '-P', '-m', 'u:hosty:rwX', '--', str(project.resolve())])
            defaults = [args for args in commands if 'd:u:hosty:rwx' in args]
            self.assertEqual(len(defaults), 1)
            self.assertIn(str(nested.resolve()), defaults[0])
            self.assertNotIn(str(external), defaults[0])
            self.assertNotIn(str(project / 'link'), defaults[0])
            for args in commands:
                if 'u:hosty:--x' in args:
                    self.assertNotIn('-R', args)
            self.assertFalse(any(args[0] in ('chown', 'chmod') for args in commands))

    @patch('hosty.server.command')
    def test_home_and_system_directories_are_not_modified(self, command):
        from hosty.server import prepare_app_permissions
        for path in ['/', '/etc', str(Path.home())]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                prepare_app_permissions({'path': path})
        command.assert_not_called()

    @patch('hosty.server.shutil.which', return_value=None)
    @patch('hosty.server.command')
    def test_missing_acl_explains_how_to_upgrade(self, command, which):
        import tempfile
        from hosty.server import prepare_app_permissions
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / 'project'
            project.mkdir()
            with self.assertRaisesRegex(ValueError, 'sudo sh install.sh'):
                prepare_app_permissions({'path': str(project)})
        command.assert_not_called()
