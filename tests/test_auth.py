import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from hosty import server
from hosty.auth import ensure_config, read_config, verify


class AccountTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = server.DATA, server.SYSTEM, server.TOKEN
        server.DATA, server.SYSTEM, server.TOKEN = Path(self.temp.name), False, 'old-token'
        server.SESSIONS.clear()
        server.LOGIN_ATTEMPTS.clear()
        with patch('hosty.auth.sys.stdin.isatty', return_value=False):
            self.config = ensure_config(server.DATA)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        server.DATA, server.SYSTEM, server.TOKEN = self.original
        server.SESSIONS.clear()
        server.LOGIN_ATTEMPTS.clear()
        self.temp.cleanup()

    def request(self, path, body=None, cookie='', header=True, token=''):
        headers = {'Cookie': cookie, 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}
        if header:
            headers['X-Hosty-Request'] = '1'
        req = urllib.request.Request(self.url + '/api/' + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        response = urllib.request.urlopen(req)
        with response:
            return json.load(response), response.headers

    def login(self):
        result, headers = self.request('login', {'username': 'hostyuser', 'password': 'h0styp4ss'})
        self.assertEqual(result['username'], 'hostyuser')
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        return headers['Set-Cookie'].split(';')[0]

    def test_defaults_hashed_and_setup_idempotent(self):
        self.assertEqual(self.config['domain'], 'hosty.localhost')
        self.assertTrue(verify(self.config, 'h0styp4ss'))
        raw = (server.DATA / 'panel.json').read_text()
        self.assertNotIn('h0styp4ss', raw)
        self.assertEqual((server.DATA / 'panel.json').stat().st_mode & 0o777, 0o600)
        with patch('hosty.auth.sys.stdin.isatty', return_value=True), patch('builtins.input') as prompt:
            self.assertEqual(ensure_config(server.DATA), self.config)
            prompt.assert_not_called()

    def test_login_and_logout(self):
        cookie = self.login()
        settings, _ = self.request('settings', cookie=cookie)
        self.assertEqual(settings['domain'], 'hosty.localhost')
        self.assertNotIn('password_hash', settings)
        self.request('logout', {}, cookie)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('settings', cookie=cookie)
        self.assertEqual(error.exception.code, 401)

    def test_legacy_token_cannot_bypass_account(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('settings', token='old-token')
        self.assertEqual(error.exception.code, 401)

    def test_password_and_domain_update_revokes_old_session(self):
        cookie = self.login()
        self.request('settings', {'username': 'owner', 'domain': 'panel.example.com', 'password': 'updated-password', 'current_password': 'h0styp4ss'}, cookie)
        with self.assertRaises(urllib.error.HTTPError):
            self.request('settings', cookie=cookie)
        config = read_config(server.DATA)
        self.assertTrue(verify(config, 'updated-password'))
        self.assertEqual(config['domain'], 'panel.example.com')
        self.request('login', {'username': 'owner', 'password': 'updated-password'})

    def test_wrong_password_and_cross_site_request_rejected(self):
        cookie = self.login()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('settings', {'current_password': 'wrong'}, cookie)
        self.assertEqual(error.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('logout', {}, cookie, header=False)
        self.assertEqual(error.exception.code, 403)
        self.assertEqual(read_config(server.DATA), self.config)

    def test_panel_ssl_requires_public_hostname(self):
        cookie = self.login()
        with patch.object(server, 'SYSTEM', True), self.assertRaises(urllib.error.HTTPError) as error:
            self.request('settings/ssl', {'email': 'me@example.com'}, cookie)
        self.assertEqual(error.exception.code, 400)
        self.assertIn('public panel domain', json.load(error.exception)['error'])

    def test_panel_ssl_can_be_enabled_from_settings(self):
        from hosty.auth import write_config
        config = {**self.config, 'domain': 'panel.example.com'}
        write_config(server.DATA, config)
        cookie = self.login()
        with patch.object(server, 'SYSTEM', True), patch.object(server, 'configure_panel_domain'), patch.object(server, 'command') as command:
            result, _ = self.request('settings/ssl', {'email': 'me@example.com'}, cookie)
        self.assertTrue(result['ssl'])
        self.assertTrue(read_config(server.DATA)['ssl'])
        command.assert_any_call(['certbot', '--nginx', '-d', 'panel.example.com', '--non-interactive', '--agree-tos', '-m', 'me@example.com', '--redirect'])

    def test_app_ssl_automatically_includes_panel_setup(self):
        from hosty.auth import write_config
        write_config(server.DATA, {**self.config, 'domain': 'panel.example.com'})
        app = {'id': 'abcdef123456', 'name': 'Tool', 'domain': 'app.example.com', 'environment': 'production', 'ssl': False}
        server.save([app])
        cookie = self.login()
        with patch.object(server, 'SYSTEM', True), patch.object(server, 'configure_panel_domain'), patch.object(server, 'write_app_route'), patch.object(server, 'command') as command, patch.object(server, 'status', return_value={'ssl': True}):
            self.request('apps/abcdef123456/ssl', {'email': 'me@example.com'}, cookie)
        domains = [call.args[0][3] for call in command.call_args_list if call.args[0][0] == 'certbot']
        self.assertEqual(domains, ['app.example.com', 'panel.example.com'])
        self.assertTrue(read_config(server.DATA)['ssl'])
        self.assertTrue(server.apps()[0]['ssl'])

    def test_panel_ssl_failure_does_not_undo_app_ssl(self):
        from hosty.auth import write_config
        write_config(server.DATA, {**self.config, 'domain': 'panel.example.com'})
        server.save([{'id': 'abcdef123456', 'name': 'Tool', 'domain': 'app.example.com', 'environment': 'production', 'ssl': False}])
        cookie = self.login()
        with patch.object(server, 'SYSTEM', True), patch.object(server, 'write_app_route'), patch.object(server, 'command'), patch.object(server, 'status', return_value={'ssl': True}), patch.object(server, 'issue_panel_certificate', side_effect=ValueError('DNS check failed')):
            result, _ = self.request('apps/abcdef123456/ssl', {'email': 'me@example.com'}, cookie)
        self.assertIn('DNS check failed', result['warning'])
        self.assertTrue(server.apps()[0]['ssl'])



class FirstRunTest(unittest.TestCase):
    def test_interactive_setup_uses_requested_defaults(self):
        with tempfile.TemporaryDirectory() as temporary, patch('hosty.auth.sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['', '']), patch('hosty.auth.getpass.getpass', return_value=''):
            config = ensure_config(Path(temporary))
            self.assertEqual(config['username'], 'hostyuser')
            self.assertEqual(config['domain'], 'hosty.localhost')
            self.assertTrue(verify(config, 'h0styp4ss'))

    def test_interactive_setup_accepts_custom_account(self):
        with tempfile.TemporaryDirectory() as temporary, patch('hosty.auth.sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['owner', 'panel.example.com']), patch('hosty.auth.getpass.getpass', return_value='my-password'):
            config = ensure_config(Path(temporary))
            self.assertEqual(config['username'], 'owner')
            self.assertEqual(config['domain'], 'panel.example.com')
            self.assertTrue(verify(config, 'my-password'))

    def test_panel_route_preserves_existing_https(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'panel.conf'
            original = 'server { listen 443 ssl; server_name panel.example.com; location / { proxy_pass http://127.0.0.1:8787; } }'
            path.write_text(original)
            with patch.object(server, 'SYSTEM', True), patch.object(server, 'Path', return_value=path), patch.object(server, 'command') as command:
                server.configure_panel_domain('panel.example.com', 8787)
                command.assert_not_called()
            self.assertEqual(path.read_text(), original)

    def test_panel_route_updates_domain_and_rolls_back_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'panel.conf'
            original = 'server { server_name old.example.com; }'
            path.write_text(original)
            with patch.object(server, 'SYSTEM', True), patch.object(server, 'Path', return_value=path), patch.object(server, 'command', side_effect=ValueError('Nginx failed')):
                with self.assertRaises(ValueError):
                    server.configure_panel_domain('new.example.com', 8787)
            self.assertEqual(path.read_text(), original)
