import unittest
from unittest.mock import MagicMock, patch
from hosty.server import routing_status


class RoutingTest(unittest.TestCase):
    def setUp(self):
        self.app = {'id': 'abcdef123456', 'domain': 'example.com', 'environment': 'production'}

    @patch('hosty.server.SYSTEM', True)
    @patch('hosty.server.http.client.HTTPConnection')
    def test_matching_route_is_verified(self, connect):
        response = connect.return_value.getresponse.return_value
        response.getheader.return_value = self.app['id']
        response.status = 200
        self.assertEqual(routing_status(self.app)['state'], 'ready')
        connect.return_value.request.assert_called_once_with('HEAD', '/', headers={'Host': 'example.com'})
        connect.return_value.close.assert_called_once()

    @patch('hosty.server.SYSTEM', True)
    @patch('hosty.server.http.client.HTTPConnection')
    def test_nginx_welcome_site_is_not_success(self, connect):
        response = connect.return_value.getresponse.return_value
        response.getheader.return_value = None
        response.status = 200
        self.assertEqual(routing_status(self.app)['state'], 'error')
        self.assertIn('different Nginx site', routing_status(self.app)['message'])

    @patch('hosty.server.SYSTEM', True)
    @patch('hosty.server.http.client.HTTPConnection')
    def test_upstream_failure_is_explained(self, connect):
        response = connect.return_value.getresponse.return_value
        response.getheader.return_value = self.app['id']
        response.status = 502
        self.assertIn('app is not responding', routing_status(self.app)['message'])

    @patch('hosty.server.http.client.HTTPConnection')
    def test_dev_route_is_explicit_without_nginx_probe(self, connect):
        self.app['environment'] = 'development'
        self.assertEqual(routing_status(self.app)['state'], 'local')
        connect.assert_not_called()

class IPv6RoutingTest(unittest.TestCase):
    def test_http_and_https_get_matching_ipv6_listeners(self):
        from hosty.server import add_ipv6_listeners
        original = 'server {\n    listen 80;\n}\nserver {\n    listen 443 ssl;\n    ssl_certificate /etc/cert.pem;\n}\n'
        updated = add_ipv6_listeners(original)
        self.assertIn('listen [::]:80;', updated)
        self.assertIn('listen [::]:443 ssl;', updated)
        self.assertIn('ssl_certificate /etc/cert.pem;', updated)
        self.assertEqual(add_ipv6_listeners(updated), updated)

    @patch('hosty.server.SYSTEM', True)
    @patch('hosty.server.routing_status', return_value={'state': 'error'})
    @patch('hosty.server.socket.getaddrinfo', return_value=[])
    @patch('hosty.server.subprocess.run')
    def test_loaded_site_conflicts_exposed_in_panel(self, run, dns, status):
        import subprocess
        from hosty.server import routing_details
        app = {'id': 'abcdef123456', 'domain': 'example.com', 'port': 9000, 'environment': 'production'}
        run.return_value = subprocess.CompletedProcess([], 0,
            '# configuration file /etc/nginx/conf.d/hosty-abcdef123456.conf:\nserver { server_name example.com; }\n# configuration file /etc/nginx/sites-enabled/default:\nserver { server_name example.com; }\n',
            'conflicting server name example.com ignored')
        details = routing_details(app)
        self.assertTrue(details['route_loaded'])
        self.assertEqual(len(details['matching_sites']), 2)
        self.assertIn('Multiple loaded', details['explanation'])
        self.assertIn('conflicting server name', details['nginx_messages'])

class TLSRouteCheckTest(unittest.TestCase):
    @patch('hosty.server.SYSTEM', True)
    @patch('hosty.server.ssl.create_default_context')
    @patch('hosty.server.socket.create_connection')
    @patch('hosty.server.http.client.HTTPConnection')
    def test_ssl_app_probes_tls_with_hostname_verification(self, connect, socket, context):
        app = {'id': 'abcdef123456', 'domain': 'app.example.com', 'environment': 'production', 'ssl': True}
        response = connect.return_value.getresponse.return_value
        response.getheader.return_value = app['id']
        response.status = 200
        self.assertEqual(routing_status(app)['state'], 'ready')
        socket.assert_called_once_with(('127.0.0.1', 443), timeout=2)
        context.return_value.wrap_socket.assert_called_once_with(socket.return_value, server_hostname='app.example.com')
