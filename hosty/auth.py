"""Panel account configuration and password verification."""
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
from pathlib import Path

DEFAULT_DOMAIN = 'hosty.localhost'


def valid_domain(domain):
    return len(domain) <= 253 and '.' in domain and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in domain.split('.'))


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def verify(config, password):
    return hmac.compare_digest(config['password_hash'], password_hash(password, config['salt']))


def account(username, password, domain):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', username):
        raise ValueError('Username must contain 1–64 letters, numbers, dots, underscores, or hyphens.')
    if not password or len(password) > 1024:
        raise ValueError('Password must contain 1–1024 characters.')
    if not valid_domain(domain):
        raise ValueError('Enter a hostname, such as hosty.localhost or panel.example.com.')
    salt = secrets.token_hex(16)
    return {'username': username, 'salt': salt, 'password_hash': password_hash(password, salt), 'domain': domain, 'revision': secrets.token_hex(16)}


def read_config(data):
    path = Path(data) / 'panel.json'
    return json.loads(path.read_text()) if path.exists() else None


def write_config(data, config):
    data = Path(data)
    data.mkdir(parents=True, exist_ok=True)
    temp = data / 'panel.tmp'
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        json.dump(config, output, indent=2)
    temp.chmod(0o600)
    temp.replace(data / 'panel.json')


def ensure_config(data):
    existing = read_config(data)
    if existing:
        return existing
    username, password, domain = 'hostyuser', 'h0styp4ss', DEFAULT_DOMAIN
    if sys.stdin.isatty():
        print('Hosty first-run setup — press Enter to use the defaults.')
        username = input(f'Username [{username}]: ').strip() or username
        password = getpass.getpass('Password [h0styp4ss]: ') or password
        domain = input(f'Panel domain [{domain}]: ').strip().lower() or domain
    config = account(username, password, domain)
    write_config(data, config)
    return config
