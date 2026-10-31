import unittest
from unittest.mock import MagicMock, patch
from hosty.server import routing_status


class RoutingTest(unittest.TestCase):
    def setUp(self):
        self.app = {'id': 'abcdef123456', 'domain': 'example.test', 'environment': 'production'}

    @patch('hosty.server.SYSTEM', True)
    @patch('hosty.server.http.client.HTTPConnection')
    def test_matching_route_is_verified(self, connect):
        response = connect.return_value.getresponse.return_value
        response.getheader.return_value = self.app['id']
        response.status = 200
        self.assertEqual(routing_status(self.app)['state'], 'ready')
        connect.return_value.request.assert_called_once_with('HEAD', '/', headers={'Host': 'example.test'})
        connect.return_value.close.assert_called_once()

    @patch('hosty.server.SYSTEM', True)
    @patch('hosty.server.http.client.HTTPConnection')
    def test_nginx_welcome_site_is_not_success(self, connect):
        response = connect.return_value.getresponse.return_value
        response.getheader.return_value = None
        response.status = 200
        self.assertEqual(routing_status(self.app)['state'], 'error')
        self.assertIn('different Caddy site', routing_status(self.app)['message'])

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
