"""Resolve app paths independently of the panel service account."""
import os
import pwd


def app_home():
    configured = os.environ.get('HOSTY_APP_HOME')
    if configured:
        return configured.rstrip('/') or '/'
    user = os.environ.get('SUDO_USER')
    if user and user != 'root':
        try:
            return pwd.getpwnam(user).pw_dir
        except KeyError:
            pass
    return os.path.expanduser('~')


def expand_app_path(path):
    if path == '~':
        return app_home()
    if path.startswith('~/'):
        return app_home().rstrip('/') + path[1:]
    return path
