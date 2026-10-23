import hmac
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = Path(os.environ.get('HOSTY_DATA', Path.home() / '.local/share/hosty'))
SYSTEM = os.environ.get('HOSTY_SYSTEM') == '1'
MUTATION_LOCK = threading.Lock()
START = time.time()
TOKEN = os.environ.get('HOSTY_TOKEN', '')


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise ValueError(result.stderr.strip() or result.stdout.strip() or 'Command failed')
    return result.stdout.strip()


def apps():
    path = DATA / 'apps.json'
    return json.loads(path.read_text()) if path.exists() else []


def save(items):
    DATA.mkdir(parents=True, exist_ok=True)
    temp = DATA / 'apps.tmp'
    temp.write_text(json.dumps(items, indent=2))
    temp.replace(DATA / 'apps.json')


def version(executable):
    try:
        return command([executable, '--version']).replace('Python ', '')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 'Not installed'


def metrics():
    disk = shutil.disk_usage('/')
    memory = {'used': 0, 'total': 0}
    if Path('/proc/meminfo').exists():
        values = {line.split(':')[0]: int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines()}
        memory = {'used': values['MemTotal'] - values['MemAvailable'], 'total': values['MemTotal']}
    uptime = float(Path('/proc/uptime').read_text().split()[0]) if Path('/proc/uptime').exists() else time.time() - START
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        ip = '127.0.0.1'
    return {'hostname': socket.gethostname(), 'ip': ip, 'os': platform.system() + ' ' + platform.release(), 'cores': os.cpu_count(), 'timezone': time.tzname[0], 'python': platform.python_version(), 'bun': version('bun'), 'uptime': uptime, 'load': os.getloadavg()[0], 'memory': memory, 'disk': {'used': disk.used, 'total': disk.total}, 'system': SYSTEM, 'firewall': 'Unverified', 'ssh': 'Managed on server'}


def status(item):
    result = dict(item)
    result['status'] = 'configured'
    if SYSTEM:
        running = subprocess.run(['systemctl', 'is-active', f'hosty-app-{item["id"]}'], capture_output=True, text=True)
        result['status'] = 'running' if running.stdout.strip() == 'active' else 'stopped'
    return result


def provision(item):
    ident = item['id']
    runtime = item['runtime']
    path = item['path']
    port = item['port']
    if runtime == 'html':
        executable = f'/usr/bin/python3 -m http.server {port} --bind 127.0.0.1'
    elif runtime == 'python':
        python = str(Path(path) / '.venv/bin/python')
        if not Path(python).is_file():
            raise ValueError('Create a .venv in the app directory and install your dependencies first.')
        executable = f'{python} -m uvicorn main:app --host 127.0.0.1 --port {port}'
    else:
        bun = shutil.which('bun')
        if not bun:
            raise ValueError('Install Bun in a system-wide location before adding a Bun app.')
        executable = f'{bun} run start'
    unit = f'''[Unit]
Description=Hosty app {ident}
After=network.target
[Service]
User=hosty
Group=hosty
WorkingDirectory={path}
Environment=PORT={port}
Environment=HOST=127.0.0.1
ExecStart={executable}
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths={path}
PrivateTmp=true
[Install]
WantedBy=multi-user.target
'''
    Path(f'/etc/systemd/system/hosty-app-{ident}.service').write_text(unit)
    config = f'''server {{
    listen 80;
    server_name {item['domain']};
    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}
'''
    Path(f'/etc/nginx/conf.d/hosty-{ident}.conf').write_text(config)
    command(['nginx', '-t'])
    command(['systemctl', 'daemon-reload'])
    command(['systemctl', 'enable', '--now', f'hosty-app-{ident}'])
    command(['systemctl', 'reload', 'nginx'])


class Handler(BaseHTTPRequestHandler):
    def reply(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        return hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + TOKEN)

    def do_GET(self):
        if self.path.startswith('/api/'):
            if not self.authorized():
                return self.reply({'error': 'Enter your panel token to connect.'}, 401)
            if self.path == '/api/overview':
                return self.reply({'server': metrics(), 'apps': [status(a) for a in apps()]})
            return self.reply({'error': 'Not found'}, 404)
        filename = {'/': 'index.html', '/style.css': 'style.css', '/app.js': 'app.js'}.get(self.path.split('?')[0])
        if not filename:
            return self.reply({'error': 'Not found'}, 404)
        body = (Path(__file__).parent / 'static' / filename).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', {'html': 'text/html', 'css': 'text/css', 'js': 'application/javascript'}[filename.split('.')[-1]])
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        with MUTATION_LOCK:
            self.handle_post()

    def handle_post(self):
        if not self.authorized():
            return self.reply({'error': 'Unauthorized'}, 401)
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length > 16384:
                raise ValueError('Request too large')
            data = json.loads(self.rfile.read(length) or '{}')
            items = apps()
            if self.path == '/api/apps':
                name = str(data.get('name', '')).strip()
                domain = str(data.get('domain', '')).strip().lower()
                path = str(data.get('path', '')).strip()
                runtime = data.get('runtime')
                if not name or len(name) > 64:
                    raise ValueError('App name must be 1–64 characters.')
                if len(domain) > 253 or '.' not in domain or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in domain.split('.')):
                    raise ValueError('Enter a valid domain, such as app.example.com.')
                if runtime not in ('python', 'bun', 'html'):
                    raise ValueError('Choose a supported runtime.')
                if not re.fullmatch(r'/[A-Za-z0-9_./-]+', path) or '..' in path.split('/') or not Path(path).is_dir():
                    raise ValueError('Enter an existing absolute directory using letters, numbers, /, _, - or dots.')
                if any(a['domain'] == domain for a in items):
                    raise ValueError('This domain is already assigned to an app.')
                item = {'id': secrets.token_hex(6), 'name': name, 'domain': domain, 'path': path, 'runtime': runtime, 'port': max([a['port'] for a in items] + [8999]) + 1, 'created': time.time(), 'ssl': False}
                if SYSTEM:
                    try:
                        provision(item)
                    except (ValueError, OSError, subprocess.TimeoutExpired):
                        subprocess.run(['systemctl', 'disable', '--now', f'hosty-app-{item["id"]}'], capture_output=True)
                        Path(f'/etc/systemd/system/hosty-app-{item["id"]}.service').unlink(missing_ok=True)
                        Path(f'/etc/nginx/conf.d/hosty-{item["id"]}.conf').unlink(missing_ok=True)
                        subprocess.run(['systemctl', 'daemon-reload'], capture_output=True)
                        subprocess.run(['systemctl', 'reload', 'nginx'], capture_output=True)
                        raise
                items.append(item)
                save(items)
                return self.reply(status(item), 201)
            match = re.fullmatch(r'/api/apps/([a-f0-9]{12})/(start|stop|restart|ssl|logs)', self.path)
            if not match:
                return self.reply({'error': 'Not found'}, 404)
            ident, action = match.groups()
            item = next((a for a in items if a['id'] == ident), None)
            if not item:
                return self.reply({'error': 'App not found'}, 404)
            if not SYSTEM:
                raise ValueError('Service controls require an Ubuntu installation. Run sudo hosty install on your VPS.')
            if action == 'logs':
                return self.reply({'logs': command(['journalctl', '-u', f'hosty-app-{ident}', '-n', '80', '--no-pager'])})
            if action == 'ssl':
                email = str(data.get('email', ''))
                if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
                    raise ValueError('Enter a valid email for certificate expiry notices.')
                command(['certbot', '--nginx', '-d', item['domain'], '--non-interactive', '--agree-tos', '-m', email, '--redirect'])
                item['ssl'] = True
                save(items)
            else:
                command(['systemctl', action, f'hosty-app-{ident}'])
            self.reply(status(item))
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            self.reply({'error': str(error)}, 400)


def serve(host, port):
    global TOKEN
    DATA.mkdir(parents=True, exist_ok=True)
    if not TOKEN:
        token_file = DATA / 'token'
        if not token_file.exists():
            token_file.write_text(secrets.token_urlsafe(32))
            token_file.chmod(0o600)
        TOKEN = token_file.read_text().strip()
    print(f'Hosty is ready at http://{host}:{port}\nPanel token: {TOKEN}', flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
