import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from hosty.paths import app_home, expand_app_path


class AppPathsTest(unittest.TestCase):
    @patch.dict(os.environ, {'HOME': '/root', 'SUDO_USER': 'ubuntu'}, clear=True)
    @patch('hosty.paths.pwd.getpwnam', return_value=SimpleNamespace(pw_dir='/home/ubuntu'))
    def test_sudo_uses_invoking_users_home(self, lookup):
        self.assertEqual(expand_app_path('~/oka'), '/home/ubuntu/oka')
        self.assertEqual(expand_app_path('~'), '/home/ubuntu')
        self.assertEqual(expand_app_path('/var/www/oka'), '/var/www/oka')

    @patch.dict(os.environ, {'HOME': '/root', 'HOSTY_APP_HOME': '/home/ubuntu/'}, clear=True)
    def test_service_uses_persisted_home_without_sudo_environment(self):
        self.assertEqual(expand_app_path('~/typewriter-savings'), '/home/ubuntu/typewriter-savings')
        self.assertEqual(app_home(), '/home/ubuntu')

    @patch.dict(os.environ, {'HOME': '/Users/me'}, clear=True)
    def test_normal_local_user(self):
        self.assertEqual(expand_app_path('~/oka'), '/Users/me/oka')

    @patch.dict(os.environ, {'HOME': '/root', 'SUDO_USER': 'root'}, clear=True)
    def test_root_install_still_uses_root_home(self):
        self.assertEqual(expand_app_path('~/oka'), '/root/oka')
