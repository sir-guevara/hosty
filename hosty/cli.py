import argparse
import os
import secrets
import shutil
import subprocess
import tempfile
import json
from pathlib import Path
from .paths import app_home
from .auth import ensure_config


PACKAGES = (
    'python3', 'python3-venv', 'python3-pip', 'python3-uvicorn', 'python3-dev',
    'nginx', 'certbot', 'python3-certbot-nginx', 'curl', 'unzip',
    'ca-certificates', 'git', 'openssh-server', 'build-essential', 'pkg-config', 'acl', 'golang-go',
)
SYSTEM_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'


def install_dependencies():
    missing = []
    for package in PACKAGES:
        result = subprocess.run(
            ['dpkg-query', '-W', '-f=${Status}', package],
            capture_output=True, text=True,
        )
        if result.returncode or result.stdout.strip() != 'install ok installed':
            missing.append(package)
    if missing:
        print('Installing missing dependencies: ' + ', '.join(missing), flush=True)
        subprocess.run(['apt-get', 'update'], check=True)
        subprocess.run(['apt-get', 'install', '-y', *missing], check=True,
                       env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'})
    else:
        print('System dependencies are already installed.', flush=True)


def install_bun():
    # A root-only ~/.bun installation cannot run applications as the hosty user.
    existing = shutil.which('bun', path=SYSTEM_PATH)
    if existing:
        result = subprocess.run(['runuser', '-u', 'hosty', '--', existing, '--version'],
                                capture_output=True, text=True)
        if result.returncode == 0:
            print(f'Bun {result.stdout.strip()} is already installed.', flush=True)
            return existing
    print('Installing Bun for all Hosty applications...', flush=True)
    with tempfile.TemporaryDirectory(prefix='hosty-install-') as temporary:
        script = Path(temporary) / 'bun-install.sh'
        subprocess.run(['curl', '--fail', '--silent', '--show-error', '--location',
                        '--proto', '=https', '--tlsv1.2', 'https://bun.com/install',
                        '--output', str(script)], check=True)
        subprocess.run(['bash', str(script)], check=True,
                       env={**os.environ, 'BUN_INSTALL': '/opt/hosty/bun'})
    bun = Path('/opt/hosty/bun/bin/bun')
    if not bun.is_file():
        raise RuntimeError('Bun installer completed without creating its executable.')
    # Make the managed installation traversable by application services.
    for directory in (bun.parent.parent, bun.parent):
        directory.chmod(0o755)
    bun.chmod(0o755)
    link = Path('/usr/local/bin/bun')
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        raise RuntimeError('Existing /usr/local/bin/bun is unusable by hosty; move it aside and rerun install.')
    link.symlink_to(bun)
    subprocess.run(['runuser', '-u', 'hosty', '--', str(link), '--version'], check=True)
    return str(link)


def configure_panel_alias(hosts_path=Path('/etc/hosts'), domain='hosty.localhost'):
    text = hosts_path.read_text()
    for line in text.splitlines():
        fields = line.split('#', 1)[0].split()
        if len(fields) > 1 and domain in fields[1:]:
            if fields[0] != '127.0.0.1':
                raise RuntimeError(f'{domain} already points elsewhere in /etc/hosts. Update it to 127.0.0.1 and rerun install.')
            return
    with hosts_path.open('a') as output:
        output.write(('' if text.endswith('\n') else '\n') + f'127.0.0.1 {domain} # Hosty panel\n')


def install():
    if os.geteuid() != 0 or not Path('/etc/debian_version').exists():
        raise SystemExit('Run sudo hosty install on an Ubuntu server.')
    if not Path('/run/systemd/system').is_dir():
        raise SystemExit('Hosty installation requires an Ubuntu server running systemd.')
    os.environ['PATH'] = SYSTEM_PATH
    install_dependencies()
    root = Path('/opt/hosty')
    root.mkdir(exist_ok=True)
    root.chmod(0o755)
    source = Path(__file__).parent.resolve()
    if source != (root / 'hosty').resolve():
        shutil.copytree(source, root / 'hosty', dirs_exist_ok=True)
    if subprocess.run(['id', '-u', 'hosty'], capture_output=True).returncode:
        subprocess.run(['useradd', '--system', '--create-home', '--home-dir', '/var/lib/hosty', '--shell', '/usr/sbin/nologin', 'hosty'], check=True)
    bun = install_bun()
    subprocess.run(['/usr/bin/python3', '-c', 'import venv, pip, uvicorn'], check=True)
    subprocess.run(['go', 'version'], check=True)
    subprocess.run(['certbot', 'plugins', '--init'], check=True)
    subprocess.run(['nginx', '-t'], check=True)
    data = Path('/var/lib/hosty')
    data.mkdir(exist_ok=True)
    os.chown(data, 0, 0)
    data.chmod(0o755)
    cache = Path('/var/cache/hosty')
    cache.mkdir(parents=True, exist_ok=True)
    shutil.chown(cache, user='hosty', group='hosty')
    subprocess.run(['usermod', '--home', str(cache), 'hosty'], check=True)
    config = ensure_config(data)
    if config['domain'].endswith(('.localhost', '.local')):
        configure_panel_alias(domain=config['domain'])
    from . import server
    server.SYSTEM = True
    subprocess.run(['systemctl', 'enable', '--now', 'nginx'], check=True)
    server.configure_panel_domain(config['domain'], 8787)
    token = secrets.token_urlsafe(32)
    env = Path('/etc/hosty.env')
    if not env.exists():
        env.write_text(f'HOSTY_TOKEN={token}\nHOSTY_DATA=/var/lib/hosty\nHOSTY_SYSTEM=1\n')
        env.chmod(0o600)
    # Persist the sudo caller's home: systemd does not inherit SUDO_USER.
    settings = env.read_text()
    if not any(line.startswith('HOSTY_APP_HOME=') for line in settings.splitlines()):
        with env.open('a') as output:
            output.write(('' if settings.endswith('\n') else '\n') + f'HOSTY_APP_HOME={json.dumps(app_home())}\n')
    Path('/etc/systemd/system/hosty.service').write_text('''[Unit]
Description=Hosty panel
After=network.target
[Service]
WorkingDirectory=/opt/hosty
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
EnvironmentFile=/etc/hosty.env
ExecStart=/usr/bin/python3 -m hosty.cli serve --host 127.0.0.1 --port 8787
Restart=on-failure
[Install]
WantedBy=multi-user.target
''')
    launcher = Path('/usr/local/bin/hosty')
    launcher.write_text('#!/bin/sh\ncd /opt/hosty || exit 1\nexec /usr/bin/python3 -m hosty.cli "$@"\n')
    launcher.chmod(0o755)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', 'nginx', 'ssh', 'certbot.timer', 'hosty'], check=True)
    subprocess.run(['systemctl', 'restart', 'hosty'], check=True)
    subprocess.run(['systemctl', 'is-active', '--quiet', 'hosty'], check=True)
    print(f'Dependencies ready. Bun: {bun}. Python ASGI runtime: /usr/bin/python3.', flush=True)
    print('Hosty installed. Connect securely with: ssh -L 8787:127.0.0.1:8787 root@YOUR_SERVER')
    print(f'Open http://{config["domain"]} on this machine. Username: {config["username"]}. Use your configured password.')
    print('For a remote VPS, point the domain DNS to its public IP, or use http://localhost:8787 through the SSH tunnel.')


def main():
    parser = argparse.ArgumentParser(description='Hosty — your little corner of the internet')
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8787)
    serve.add_argument('--dev', action='store_true', help='Run apps locally and show localhost URLs; no domain or Ubuntu required')
    commands.add_parser('install')
    args = parser.parse_args()
    if args.command == 'install':
        install()
    else:
        from .server import serve as run
        run(args.host, args.port, dev=args.dev)

if __name__ == '__main__':
    main()
