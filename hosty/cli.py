import argparse
import os
import secrets
import shutil
import subprocess
from pathlib import Path


def install():
    if os.geteuid() != 0 or not Path('/etc/debian_version').exists():
        raise SystemExit('Run sudo hosty install on an Ubuntu server.')
    subprocess.run(['apt-get', 'update'], check=True)
    subprocess.run(['apt-get', 'install', '-y', 'nginx', 'python3-venv', 'certbot', 'python3-certbot-nginx'], check=True)
    root = Path('/opt/hosty')
    root.mkdir(exist_ok=True)
    shutil.copytree(Path(__file__).parent, root / 'hosty', dirs_exist_ok=True)
    subprocess.run(['useradd', '--system', '--create-home', '--home-dir', '/var/lib/hosty', '--shell', '/usr/sbin/nologin', 'hosty'], check=False)
    Path('/var/lib/hosty').mkdir(exist_ok=True)
    token = secrets.token_urlsafe(32)
    env = Path('/etc/hosty.env')
    if not env.exists():
        env.write_text(f'HOSTY_TOKEN={token}\nHOSTY_DATA=/var/lib/hosty\nHOSTY_SYSTEM=1\n')
        env.chmod(0o600)
    Path('/etc/systemd/system/hosty.service').write_text('''[Unit]
Description=Hosty panel
After=network.target
[Service]
WorkingDirectory=/opt/hosty
EnvironmentFile=/etc/hosty.env
ExecStart=/usr/bin/python3 -m hosty.cli serve --host 127.0.0.1 --port 8787
Restart=on-failure
[Install]
WantedBy=multi-user.target
''')
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', 'hosty'], check=True)
    print('Hosty installed. Connect securely with: ssh -L 8787:127.0.0.1:8787 root@YOUR_SERVER')
    print('Open http://localhost:8787. Your panel token is in /etc/hosty.env.')


def main():
    parser = argparse.ArgumentParser(description='Hosty — your little corner of the internet')
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8787)
    commands.add_parser('install')
    args = parser.parse_args()
    if args.command == 'install':
        install()
    else:
        from .server import serve as run
        run(args.host, args.port)

if __name__ == '__main__':
    main()
