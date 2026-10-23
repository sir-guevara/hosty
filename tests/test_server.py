import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer
from hosty import server


class PanelTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = server.DATA, server.TOKEN, server.SYSTEM
        server.DATA, server.TOKEN, server.SYSTEM = Path(self.temp.name), 'test-token', False
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        server.DATA, server.TOKEN, server.SYSTEM = self.original
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

    def test_unknown_file_not_exposed(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.request('/../../etc/passwd')
        self.assertEqual(ctx.exception.code, 404)

if __name__ == '__main__':
    unittest.main()
