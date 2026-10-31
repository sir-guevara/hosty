import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from hosty import cli, server


class CaddyRoutesTest(unittest.TestCase):
    def test_public_domains_enable_automatic_https_without_certificate_paths(self):
        config = server.route_config('app.example.com', 9000, 'abcdef123456')
        self.assertTrue(config.startswith('app.example.com {'))
        self.assertIn('header X-Hosty-App abcdef123456', config)
        self.assertIn('reverse_proxy 127.0.0.1:9000', config)
        self.assertNotIn('tls ', config)

    def test_local_and_ip_sites_use_explicit_http(self):
        for domain in ('hosty.localhost', 'localhost', 'app.local', 'app.test', '127.0.0.1', '::1'):
            with self.subTest(domain=domain):
                self.assertTrue(server.route_config(domain, 8787).startswith('http://' + domain + ' {'))

    def test_reload_failure_restores_previous_route(self):
        with tempfile.TemporaryDirectory() as temporary:
            route = Path(temporary) / 'app.caddy'
            route.write_text('old route')
            with patch.object(server, 'command', side_effect=['', ValueError('reload failed')]):
                with self.assertRaisesRegex(ValueError, 'reload failed'):
                    server.write_route(route, 'new route')
            self.assertEqual(route.read_text(), 'old route')

    @patch.object(server, 'SYSTEM', True)
    @patch.object(server.subprocess, 'run')
    def test_old_ssl_flag_does_not_disable_automatic_https(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, 'active')
        item = {'id': 'abcdef123456', 'name': 'App', 'port': 9000, 'domain': 'app.example.com', 'environment': 'production', 'ssl': False}
        result = server.status(item)
        self.assertTrue(result['ssl'])
        self.assertEqual(result['production_url'], 'https://app.example.com')


class CaddyInstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.main = self.path('/etc/caddy/Caddyfile')
        self.main.parent.mkdir(parents=True)
        self.stock = ':80 {\n root * /usr/share/caddy\n file_server\n}\n'
        self.main.write_text(self.stock)
        self.nginx = False
        self.fail = None
        self.calls = []
        self.fake = Mock()
        self.fake.apps.return_value = [{'id': 'abcdef123456', 'domain': 'app.example.com', 'port': 9000, 'environment': 'production'}]
        self.fake.configure_panel_domain.side_effect = lambda domain, port, reload: self.path('/etc/caddy/hosty.d/hosty-panel.caddy').write_text(server.route_config(domain, port))
        self.fake.write_app_route.side_effect = lambda item, reload: self.path('/etc/caddy/hosty.d/hosty-abcdef123456.caddy').write_text(server.route_config(item['domain'], item['port'], item['id']))

    def tearDown(self):
        self.temp.cleanup()

    def path(self, path):
        return self.root / str(path).lstrip('/')

    def run_command(self, args, **kwargs):
        self.calls.append(args)
        if args == self.fail and kwargs.get('check'):
            raise subprocess.CalledProcessError(1, args)
        if args[0] == 'systemctl' and args[1] in ('is-active', 'is-enabled'):
            return subprocess.CompletedProcess(args, 0 if self.nginx and args[-1] == 'nginx' else 3)
        if args == ['nginx', '-T']:
            return subprocess.CompletedProcess(args, 0, '# configuration file /etc/nginx/conf.d/hosty-panel.conf:\nserver { server_name hosty.localhost; }\n', '')
        return subprocess.CompletedProcess(args, 0, '', '')

    def configure(self):
        with patch.object(cli, 'Path', side_effect=self.path), patch.object(cli.subprocess, 'run', side_effect=self.run_command):
            cli.configure_caddy(self.fake, {'domain': 'hosty.localhost'})

    def legacy(self):
        self.nginx = True
        panel = self.path('/etc/nginx/conf.d/hosty-panel.conf')
        panel.parent.mkdir(parents=True)
        panel.write_text('legacy route')

    def test_fresh_install_and_rerun_preserve_backup_and_records(self):
        self.configure()
        self.configure()
        self.assertEqual(self.main.read_text(), cli.CADDYFILE)
        self.assertEqual(self.main.with_name('Caddyfile.before-hosty').read_text(), self.stock)
        self.fake.configure_panel_domain.assert_called_with('hosty.localhost', 8787, reload=False)
        self.assertIn(['systemctl', 'enable', '--now', 'caddy'], self.calls)
        self.assertNotIn(['systemctl', 'stop', 'nginx'], self.calls)

    def test_migration_validates_before_stopping_nginx(self):
        self.legacy()
        self.configure()
        validate = next(i for i, args in enumerate(self.calls) if args[:4] == ['runuser', '-u', 'caddy', '--'])
        self.assertLess(validate, self.calls.index(['systemctl', 'stop', 'nginx']))
        self.assertLess(self.calls.index(['systemctl', 'reload', 'caddy']), self.calls.index(['systemctl', 'disable', 'nginx']))
        self.assertEqual(self.path('/etc/nginx/conf.d/hosty-panel.conf').read_text(), 'legacy route')

    def test_failed_activation_restores_routes_and_nginx(self):
        self.legacy()
        directory = self.path('/etc/caddy/hosty.d')
        directory.mkdir()
        old = directory / 'hosty-old.caddy'
        old.write_text('old route')
        self.fail = ['systemctl', 'reload', 'caddy']
        with self.assertRaises(subprocess.CalledProcessError):
            self.configure()
        self.assertEqual(self.main.read_text(), self.stock)
        self.assertEqual(old.read_text(), 'old route')
        self.assertFalse((directory / 'hosty-panel.caddy').exists())
        self.assertIn(['systemctl', 'start', 'nginx'], self.calls)
        self.assertIn(['systemctl', 'stop', 'caddy'], self.calls)

    def test_validation_failure_leaves_nginx_running(self):
        self.legacy()
        self.fail = ['runuser', '-u', 'caddy', '--', 'caddy', 'validate', '--config', str(self.main), '--adapter', 'caddyfile']
        with self.assertRaises(subprocess.CalledProcessError):
            self.configure()
        self.assertNotIn(['systemctl', 'stop', 'nginx'], self.calls)
        self.assertEqual(self.main.read_text(), self.stock)

    def test_custom_caddy_configuration_is_not_overwritten(self):
        self.main.write_text('custom.example.com { reverse_proxy localhost:3000 }')
        with self.assertRaisesRegex(RuntimeError, 'custom'):
            self.configure()
        self.assertTrue(self.main.read_text().startswith('custom.example.com'))

    def test_unmanaged_nginx_site_is_not_stopped(self):
        self.legacy()
        normal = self.run_command
        def unmanaged(args, **kwargs):
            result = normal(args, **kwargs)
            if args == ['nginx', '-T']:
                result.stdout += '# configuration file /etc/nginx/sites-enabled/customer:\nserver { server_name customer.example.com; }\n'
            return result
        with patch.object(cli, 'Path', side_effect=self.path), patch.object(cli.subprocess, 'run', side_effect=unmanaged):
            with self.assertRaisesRegex(RuntimeError, 'Unmanaged'):
                cli.configure_caddy(self.fake, {'domain': 'hosty.localhost'})
        self.assertNotIn(['systemctl', 'stop', 'nginx'], self.calls)

class LegacyRenewalTest(unittest.TestCase):
    def test_hosty_only_timer_retired(self):
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / 'app.example.com-0001.conf').touch()
            with patch.object(cli, 'Path', return_value=Path(temporary)), patch.object(cli.subprocess, 'run') as run:
                cli.retire_hosty_certbot({'app.example.com'})
            run.assert_called_once_with(['systemctl', 'disable', '--now', 'certbot.timer'], check=True)

    def test_shared_certbot_jobs_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / 'other.example.com.conf').touch()
            with patch.object(cli, 'Path', return_value=Path(temporary)), patch.object(cli.subprocess, 'run') as run:
                cli.retire_hosty_certbot({'app.example.com'})
            run.assert_not_called()
