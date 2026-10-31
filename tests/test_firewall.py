import os
import subprocess
import unittest
from unittest.mock import patch
from hosty.cli import configure_firewall
from hosty.server import firewall_status


class FirewallTest(unittest.TestCase):
    def run_setup(self, active=False, ssh='port 2222\n', connection='', socket_status=''):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            output = ssh if args == ['sshd', '-T'] else socket_status if args[0] == 'systemctl' else ('Status: active' if active else 'Status: inactive') if args == ['ufw', 'status'] else ''
            return subprocess.CompletedProcess(args, 0, output)
        with patch('hosty.cli.subprocess.run', side_effect=run), patch.dict(os.environ, {'SSH_CONNECTION': connection}):
            configure_firewall()
        return calls

    def test_custom_ssh_and_web_open_before_activation(self):
        calls = self.run_setup(connection='192.0.2.1 5555 192.0.2.2 2200')
        enable = calls.index(['ufw', '--force', 'enable'])
        for port in (2222, 2200, 80, 443):
            rule = next(args for args in calls if args[:3] == ['ufw', 'prepend', 'allow'] and args[3] == f'{port}/tcp')
            self.assertLess(calls.index(rule), enable)
        self.assertFalse(any('8787/tcp' in args or '9000/tcp' in args for args in calls))
        self.assertNotIn(['ufw', 'reset'], calls)

    def test_existing_active_firewall_defaults_preserved(self):
        calls = self.run_setup(active=True)
        self.assertFalse(any(args[:2] == ['ufw', 'default'] for args in calls))
        self.assertNotIn(['ufw', '--force', 'enable'], calls)

    def test_socket_activation_ports_allowed(self):
        calls = self.run_setup(socket_status='ActiveState=active\nListen=[::]:2223 (Stream)\n')
        self.assertIn(['ufw', 'prepend', 'allow', '2223/tcp', 'comment', 'Hosty SSH'], calls)

    @patch('hosty.cli.subprocess.run', return_value=subprocess.CompletedProcess([], 1, ''))
    def test_unknown_ssh_port_does_not_enable_firewall(self, run):
        with patch.dict(os.environ, {'SSH_CONNECTION': ''}), self.assertRaisesRegex(RuntimeError, 'SSH port'):
            configure_firewall()
        self.assertFalse(any(call.args[0][0] == 'ufw' for call in run.call_args_list))

    def test_rule_failure_stops_before_enable(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if args[:2] == ['ufw', 'prepend']:
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0, 'port 22\n' if args[0] == 'sshd' else '')
        with patch('hosty.cli.subprocess.run', side_effect=run), self.assertRaises(subprocess.CalledProcessError):
            configure_firewall()
        self.assertNotIn(['ufw', '--force', 'enable'], calls)

    def test_empty_and_existing_rulesets_keep_required_ports_first(self):
        for initial in ([], ['deny all']):
            with self.subTest(initial=initial):
                rules = initial.copy()
                def run(args, **kwargs):
                    if args[:3] == ['ufw', 'insert', '1'] and not rules:
                        raise subprocess.CalledProcessError(1, args, stderr="ERROR: Invalid position '1'")
                    if args[:3] == ['ufw', 'prepend', 'allow']:
                        rules.insert(0, args[3])
                    if args == ['ufw', '--force', 'enable']:
                        self.assertEqual(set(rules[:4]), {'22/tcp', '2222/tcp', '80/tcp', '443/tcp'})
                    output = 'port 2222\n' if args == ['sshd', '-T'] else 'Status: inactive' if args == ['ufw', 'status'] else ''
                    return subprocess.CompletedProcess(args, 0, output)
                with patch('hosty.cli.subprocess.run', side_effect=run), patch.dict(os.environ, {'SSH_CONNECTION': '192.0.2.1 5000 192.0.2.2 22'}):
                    configure_firewall()
                self.assertEqual(set(rules[:4]), {'22/tcp', '2222/tcp', '80/tcp', '443/tcp'})
                self.assertEqual(rules[4:], initial)

    @patch('hosty.server.SYSTEM', True)
    @patch('hosty.server.shutil.which', return_value='/usr/sbin/ufw')
    @patch('hosty.server.command')
    def test_live_status_is_reported(self, command, which):
        command.return_value = 'Status: active\n80/tcp ALLOW Anywhere'
        self.assertEqual(firewall_status(), 'Active (UFW)')
        command.return_value = 'Status: inactive'
        self.assertEqual(firewall_status(), 'Inactive (UFW)')
