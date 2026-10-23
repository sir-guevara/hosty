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
        item = {'id': 'abcdef123456', 'runtime': 'python', 'path': '/var/www/app', 'port': 9000, 'domain': 'example.com'}
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
