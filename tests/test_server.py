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
        self.assertEqual(app['status'], 'configured')
        self.assertEqual(server.apps()[0]['id'], app['id'])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.request(f'/api/apps/{app["id"]}/start', {})
        self.assertEqual(ctx.exception.code, 400)

    def test_invalid_domain_and_unit_injection_rejected(self):
        for domain, path in [('x.com; bad', self.temp.name), ('example.com', '/tmp/evil\nExecStart=bad')]:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.request('/api/apps', {'name': 'App', 'domain': domain, 'runtime': 'html', 'path': path})
            self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(server.apps(), [])

    def test_home_paths_expand_and_persist(self):
        project = Path(self.temp.name) / 'my-app'
        project.mkdir()
        with patch.dict(os.environ, {'HOME': self.temp.name}):
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
        self.assertEqual(app['local_url'], f'http://localhost:{app["port"]}')
        with urllib.request.urlopen(app['local_url']) as response:
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

if __name__ == '__main__':
    unittest.main()
