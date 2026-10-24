import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer
from hosty import server


class PanelTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = server.DATA, server.TOKEN, server.SYSTEM, server.DEV
        server.DEV = False
        server.DATA, server.TOKEN, server.SYSTEM = Path(self.temp.name), 'test-token', False
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        server.stop_all_local()
        server.DATA, server.TOKEN, server.SYSTEM, server.DEV = self.original
        self.temp.cleanup()

    def request(self, path, data=None, token='test-token'):
        request = urllib.request.Request(self.url + path, data=json.dumps(data).encode() if data is not None else None, headers={'Authorization': 'Bearer ' + token})
        return json.load(urllib.request.urlopen(request))

    def test_authentication_required(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.request('/api/overview', token='wrong')
        self.assertEqual(ctx.exception.code, 401)

    def test_app_creation_persistence_and_local_controls(self):
        app = self.request('/api/apps', {'name': 'Personal tool', 'domain': 'tool.example.com', 'runtime': 'html', 'path': self.temp.name})
        self.assertEqual(app['status'], 'running')
        self.assertEqual(server.apps()[0]['id'], app['id'])
        self.assertEqual(self.request(f'/api/apps/{app["id"]}/stop', {})['status'], 'stopped')
        self.assertFalse(server.apps()[0]['autostart'])

    def test_invalid_domain_and_unit_injection_rejected(self):
        for domain, path in [('x.com; bad', self.temp.name), ('example.com', '/tmp/evil\nExecStart=bad')]:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.request('/api/apps', {'name': 'App', 'domain': domain, 'runtime': 'html', 'path': path})
            self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(server.apps(), [])

    def test_home_paths_expand_and_persist(self):
        project = Path(self.temp.name) / 'my-app'
        project.mkdir()
        with patch.dict(os.environ, {'HOME': self.temp.name}), patch.object(server, 'start_local'):
            for index, (path, expected) in enumerate([
                ('~/', self.temp.name + '/'),
                ('~/my-app', str(project)),
                ('~', self.temp.name),
            ]):
                app = self.request('/api/apps', {'name': 'Home app', 'domain': f'app{index}.example.com', 'runtime': 'html', 'path': path})
                self.assertEqual(app['path'], expected)
                self.assertEqual(server.apps()[-1]['path'], expected)

    def test_missing_home_directory_rejected(self):
        with patch.dict(os.environ, {'HOME': self.temp.name}):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.request('/api/apps', {'name': 'Missing app', 'domain': 'missing.example.com', 'runtime': 'html', 'path': '~/missing'})
            self.assertEqual(ctx.exception.code, 400)
            error = json.load(ctx.exception)['error']
            self.assertIn(f'Directory not found on the Hosty server: {self.temp.name}/missing', error)
            self.assertIn(f'~ expands to {self.temp.name}', error)
        self.assertEqual(server.apps(), [])

    def test_unknown_file_not_exposed(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.request('/../../etc/passwd')
        self.assertEqual(ctx.exception.code, 404)

    def test_dev_app_local_url_lifecycle_and_logs(self):
        server.DEV = True
        (Path(self.temp.name) / 'index.html').write_text('Hello from Hosty dev mode')
        app = self.request('/api/apps', {'name': 'Local site', 'runtime': 'html', 'path': self.temp.name})
        self.assertEqual(app['status'], 'running')
        self.assertEqual(app['local_url'], f'http://local-site.localhost:{app["port"]}')
        self.assertEqual(app['dev_url'], app['local_url'])
        with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{app["port"]}', headers={'Host': f'local-site.localhost:{app["port"]}'})) as response:
            self.assertIn(b'Hello from Hosty dev mode', response.read())
        base = f'/api/apps/{app["id"]}/'
        self.assertEqual(self.request(base + 'restart', {})['status'], 'running')
        self.assertIn('Serving HTTP', self.request(base + 'logs', {})['logs'])
        self.assertEqual(self.request(base + 'stop', {})['status'], 'stopped')
        self.assertEqual(self.request(base + 'start', {})['status'], 'running')
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.request(base + 'ssl', {'email': 'dev@example.com'})
        self.assertEqual(ctx.exception.code, 400)

    def test_dev_start_failure_preserves_app_for_logs(self):
        server.DEV = True
        app = self.request('/api/apps', {'name': 'Missing venv', 'runtime': 'python', 'path': self.temp.name})
        self.assertEqual(app['status'], 'stopped')
        self.assertIn('.venv', app['warning'])
        self.assertEqual(server.apps()[0]['id'], app['id'])

    def test_switch_environment_on_installed_server(self):
        import subprocess
        item = {'id': 'abcdef123456', 'name': 'Tool', 'domain': 'tool.example.com', 'path': self.temp.name, 'runtime': 'html', 'port': 9000, 'environment': 'production', 'ssl': True}
        server.save([item])
        with patch.object(server, 'SYSTEM', True), patch.object(server, 'provision') as provision, patch.object(server.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'active')):
            app = self.request('/api/apps/abcdef123456/environment', {'environment': 'development'})
            self.assertEqual(app['environment'], 'development')
            self.assertEqual(app['local_url'], 'http://tool.localhost:9000')
            self.assertEqual(server.apps()[0]['domain'], 'tool.example.com')
            self.assertFalse(app['ssl'])
            app = self.request('/api/apps/abcdef123456/environment', {'environment': 'production'})
            self.assertEqual(app['environment'], 'production')
            self.assertIsNone(app['local_url'])
            self.assertEqual(provision.call_count, 2)

    def test_failed_switch_keeps_saved_environment(self):
        import subprocess
        item = {'id': 'abcdef123456', 'name': 'Tool', 'domain': 'tool.example.com', 'path': self.temp.name, 'runtime': 'html', 'port': 9000, 'environment': 'production', 'ssl': True}
        server.save([item])
        with patch.object(server, 'SYSTEM', True), patch.object(server, 'provision', side_effect=ValueError('Nginx configuration failed')), patch.object(server, 'command') as command, patch.object(Path, 'unlink'), patch.object(server.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'active')):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.request('/api/apps/abcdef123456/environment', {'environment': 'development'})
            self.assertEqual(ctx.exception.code, 400)
            self.assertEqual(server.apps()[0]['environment'], 'production')
            self.assertTrue(server.apps()[0]['ssl'])
            command.assert_any_call(['systemctl', 'restart', 'hosty-app-abcdef123456'])

    def test_production_unavailable_locally_preserves_development(self):
        item = {'id': 'abcdef123456', 'name': 'Tool', 'domain': 'tool.example.com', 'path': self.temp.name, 'runtime': 'html', 'port': 9000, 'environment': 'development'}
        server.save([item])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.request('/api/apps/abcdef123456/environment', {'environment': 'production'})
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(server.apps()[0]['environment'], 'development')

    def test_deletion_requires_exact_confirmation(self):
        item = {'id': 'abcdef123456', 'name': 'Keep me', 'domain': '', 'path': self.temp.name, 'runtime': 'html', 'port': 9000, 'environment': 'development'}
        server.save([item])
        with patch.object(server, 'delete_app') as delete:
            for confirmation in [None, '', 'delete', 'DELETE ', 'Delete']:
                with self.subTest(confirmation=confirmation), self.assertRaises(urllib.error.HTTPError) as ctx:
                    self.request('/api/apps/abcdef123456/delete', {'confirmation': confirmation})
                self.assertEqual(ctx.exception.code, 400)
            delete.assert_not_called()
        self.assertEqual(server.apps(), [item])

    def test_delete_running_local_app_preserves_project(self):
        project = Path(self.temp.name) / 'project'
        project.mkdir()
        index = project / 'index.html'
        index.write_text('Keep this source file')
        app = self.request('/api/apps', {'name': 'Delete me', 'runtime': 'html', 'path': str(project)})
        process = server.PROCESSES[app['id']]
        result = self.request(f'/api/apps/{app["id"]}/delete', {'confirmation': 'DELETE'})
        self.assertEqual(result['deleted'], app['id'])
        self.assertEqual(server.apps(), [])
        self.assertIsNotNone(process.poll())
        self.assertNotIn(app['id'], server.PROCESSES)
        self.assertEqual(index.read_text(), 'Keep this source file')

    def test_delete_installed_app_removes_only_managed_files(self):
        import subprocess
        item = {'id': 'abcdef123456', 'name': 'Tool', 'domain': 'tool.example.com', 'path': self.temp.name, 'runtime': 'html', 'port': 9000, 'environment': 'production'}
        other = {**item, 'id': '123456abcdef', 'name': 'Other'}
        server.save([item, other])
        managed = Path(self.temp.name) / 'managed'
        managed.mkdir()
        unit = managed / 'hosty-app-abcdef123456.service'
        route = managed / 'hosty-abcdef123456.conf'
        unit.write_text('unit')
        route.write_text('nginx')
        source = Path(self.temp.name) / 'source.txt'
        source.write_text('keep')
        def mapped(path):
            return managed / Path(path).name
        with patch.object(server, 'SYSTEM', True), patch.object(server, 'Path', side_effect=mapped), patch.object(server, 'command') as command, patch.object(server.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'active')):
            self.request('/api/apps/abcdef123456/delete', {'confirmation': 'DELETE'})
            command.assert_any_call(['systemctl', 'disable', '--now', 'hosty-app-abcdef123456'])
            command.assert_any_call(['systemctl', 'reload', 'nginx'])
        self.assertFalse(unit.exists())
        self.assertFalse(route.exists())
        self.assertEqual(server.apps(), [other])
        self.assertEqual(source.read_text(), 'keep')

    def test_failed_delete_restores_configuration(self):
        import subprocess
        item = {'id': 'abcdef123456', 'name': 'Tool', 'domain': 'tool.example.com', 'path': self.temp.name, 'runtime': 'html', 'port': 9000, 'environment': 'production'}
        server.save([item])
        managed = Path(self.temp.name) / 'managed'
        managed.mkdir()
        unit = managed / 'hosty-app-abcdef123456.service'
        route = managed / 'hosty-abcdef123456.conf'
        unit.write_text('original unit')
        route.write_text('original nginx')
        def mapped(path):
            return managed / Path(path).name
        def command(args):
            if args == ['nginx', '-t']:
                raise ValueError('Nginx check failed')
            return ''
        with patch.object(server, 'SYSTEM', True), patch.object(server, 'Path', side_effect=mapped), patch.object(server, 'command', side_effect=command), patch.object(server.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'active')):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.request('/api/apps/abcdef123456/delete', {'confirmation': 'DELETE'})
            self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(unit.read_text(), 'original unit')
        self.assertEqual(route.read_text(), 'original nginx')
        self.assertEqual(server.apps(), [item])

    def test_edit_running_app_changes_port_and_preserves_identity(self):
        import socket
        app = self.request('/api/apps', {'name': 'Before', 'runtime': 'html', 'path': self.temp.name})
        old_process = server.PROCESSES[app['id']]
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        updated = self.request(f'/api/apps/{app["id"]}/edit', {'name': 'After', 'port': str(port)})
        self.assertEqual(updated['id'], app['id'])
        self.assertEqual(updated['created'], app['created'])
        self.assertEqual(updated['name'], 'After')
        self.assertEqual(updated['port'], port)
        self.assertEqual(updated['status'], 'running')
        self.assertTrue(updated['local_url'].endswith(f':{port}'))
        self.assertIsNotNone(old_process.poll())
        with urllib.request.urlopen(f'http://127.0.0.1:{port}') as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(len(server.apps()), 1)

    def test_invalid_edit_ports_leave_saved_app_unchanged(self):
        item = {'id': 'abcdef123456', 'name': 'Tool', 'domain': '', 'dev_domain': '', 'path': self.temp.name, 'runtime': 'html', 'port': 9000, 'created': 1, 'environment': 'development'}
        server.save([item])
        for port in [True, 'abc', '3.5', 80, 65536, self.http.server_port]:
            with self.subTest(port=port), self.assertRaises(urllib.error.HTTPError) as ctx:
                self.request('/api/apps/abcdef123456/edit', {'port': port})
            self.assertEqual(ctx.exception.code, 400)
            self.assertEqual(server.apps(), [item])

    def test_edit_failure_restores_previous_app(self):
        item = {'id': 'abcdef123456', 'name': 'Tool', 'domain': '', 'path': self.temp.name, 'runtime': 'html', 'port': 9000, 'created': 1, 'environment': 'development'}
        server.save([item])
        with patch.object(server, 'status', return_value={'status': 'running'}), patch.object(server, 'stop_local'), patch.object(server, 'start_local', side_effect=[ValueError('New app failed'), None]) as start:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.request('/api/apps/abcdef123456/edit', {'name': 'Changed'})
            self.assertEqual(ctx.exception.code, 400)
            self.assertEqual(start.call_args.args[0], item)
        self.assertEqual(server.apps(), [item])

if __name__ == '__main__':
    unittest.main()


class EnvironmentAddressTest(unittest.TestCase):
    def test_existing_app_has_separate_production_and_development_addresses(self):
        item = {'id': 'abcdef123456', 'name': 'Tool', 'domain': 'app.example.com', 'port': 9000, 'ssl': True}
        with patch.object(server, 'DEV', True), patch.object(server, 'SYSTEM', False):
            app = server.status(item)
            self.assertEqual(app['production_url'], 'https://app.example.com')
            self.assertEqual(app['dev_url'], 'http://app.localhost:9000')
            self.assertEqual(app['local_url'], app['dev_url'])
            self.assertEqual(app['domain'], 'app.example.com')
        item['environment'] = 'production'
        with patch.object(server, 'DEV', False), patch.object(server, 'SYSTEM', False):
            app = server.status(item)
            self.assertIsNone(app['local_url'])
            self.assertEqual(app['production_url'], 'https://app.example.com')
            self.assertEqual(app['dev_url'], 'http://app.localhost:9000')

    def test_custom_development_domain(self):
        item = {'id': 'abcdef123456', 'name': 'Tool', 'domain': 'app.example.com', 'dev_domain': 'tool.localhost', 'port': 9001}
        with patch.object(server, 'DEV', True):
            app = server.status(item)
        self.assertEqual(app['dev_url'], 'http://tool.localhost:9001')
        self.assertEqual(app['production_url'], 'http://app.example.com')
