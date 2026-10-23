import hmac
import atexit
import json
import os
import platform
import re
import secrets
import shutil
import socket
import signal
import subprocess
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = Path(os.environ.get('HOSTY_DATA', Path.home() / '.local/share/hosty'))
SYSTEM = os.environ.get('HOSTY_SYSTEM') == '1'
DEV = False
PROCESSES = {}
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
    return {'hostname': socket.gethostname(), 'ip': ip, 'os': platform.system() + ' ' + platform.release(), 'cores': os.cpu_count(), 'timezone': time.tzname[0], 'python': platform.python_version(), 'bun': version('bun'), 'uptime': uptime, 'load': os.getloadavg()[0], 'memory': memory, 'disk': {'used': disk.used, 'total': disk.total}, 'system': SYSTEM, 'dev': DEV, 'firewall': 'Unverified', 'ssh': 'Managed on server'}


def status(item):
    result = dict(item)
    result['status'] = 'configured'
    result['local_url'] = f'http://localhost:{item["port"]}' if DEV else None
    if DEV:
        process = PROCESSES.get(item['id'])
        result['status'] = 'running' if process and process.poll() is None else 'stopped'
    elif SYSTEM:
        running = subprocess.run(['systemctl', 'is-active', f'hosty-app-{item["id"]}'], capture_output=True, text=True)
        result['status'] = 'running' if running.stdout.strip() == 'active' else 'stopped'
    return result


def stop_local(ident):
    process = PROCESSES.pop(ident, None)
    if process:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def stop_all_local():
    for ident in list(PROCESSES):
        stop_local(ident)


atexit.register(stop_all_local)


def start_local(item):
    process = PROCESSES.get(item['id'])
    if process and process.poll() is None:
        return
    port = item['port']
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(('127.0.0.1', port))
        except OSError:
            raise ValueError(f'Port {port} is already in use. Stop the other process first.')
    if item['runtime'] == 'html':
        args = [sys.executable, '-u', '-m', 'http.server', str(port), '--bind', '127.0.0.1']
    elif item['runtime'] == 'python':
        python = Path(item['path']) / '.venv/bin/python'
        if not python.is_file():
            raise ValueError('Create a .venv in the app directory and install uvicorn and your dependencies first.')
        args = [str(python), '-u', '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', str(port)]
    else:
        bun = shutil.which('bun')
        if not bun:
            raise ValueError('Install Bun before starting a Bun application.')
        args = [bun, 'run', 'start']
    logs = DATA / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / f'{item["id"]}.log').open('ab') as output:
        process = subprocess.Popen(args, cwd=item['path'], env={**os.environ, 'HOST': '127.0.0.1', 'PORT': str(port)}, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    PROCESSES[item['id']] = process
    # Catch immediate startup failures instead of reporting a broken link as running.
    for _ in range(30):
        if process.poll() is not None:
            stop_local(item['id'])
            raise ValueError('Application exited during startup. Check its logs and runtime dependencies.')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.1)


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
                if path == '~' or path.startswith('~/'):
                    path = os.path.expanduser(path)
                runtime = data.get('runtime')
                if not name or len(name) > 64:
                    raise ValueError('App name must be 1–64 characters.')
                if not (DEV and not domain) and (len(domain) > 253 or '.' not in domain or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in domain.split('.'))):
                    raise ValueError('Enter a valid domain, such as app.example.com.')
                if runtime not in ('python', 'bun', 'html'):
                    raise ValueError('Choose a supported runtime.')
                if not re.fullmatch(r'/[A-Za-z0-9_./-]+', path) or '..' in path.split('/') or not Path(path).is_dir():
                    raise ValueError('Enter an existing absolute directory or ~/ path using letters, numbers, /, _, - or dots. ~ uses the home directory of the user running Hosty.')
                if domain and any(a['domain'] == domain for a in items):
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
                if DEV:
                    try:
                        start_local(item)
                    except (ValueError, OSError) as error:
                        result = status(item)
                        result['warning'] = str(error)
                        return self.reply(result, 201)
                return self.reply(status(item), 201)
            match = re.fullmatch(r'/api/apps/([a-f0-9]{12})/(start|stop|restart|ssl|logs)', self.path)
            if not match:
                return self.reply({'error': 'Not found'}, 404)
            ident, action = match.groups()
            item = next((a for a in items if a['id'] == ident), None)
            if not item:
                return self.reply({'error': 'App not found'}, 404)
            if DEV:
                if action == 'ssl':
                    raise ValueError('Development apps use local HTTP. SSL requires an Ubuntu installation and domain.')
                if action == 'logs':
                    log = DATA / 'logs' / f'{ident}.log'
                    if not log.exists():
                        return self.reply({'logs': 'No log entries yet.'})
                    with log.open('rb') as source:
                        source.seek(max(0, log.stat().st_size - 65536))
                        return self.reply({'logs': source.read().decode('utf-8', errors='replace')})
                if action in ('stop', 'restart'):
                    stop_local(ident)
                if action in ('start', 'restart'):
                    start_local(item)
                return self.reply(status(item))
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


def serve(host, port, dev=False):
    global TOKEN, DEV
    if dev and SYSTEM:
        raise SystemExit('Dev mode is for local use. Unset HOSTY_SYSTEM before running --dev.')
    DEV = dev
    DATA.mkdir(parents=True, exist_ok=True)
    if not TOKEN:
        token_file = DATA / 'token'
        if not token_file.exists():
            token_file.write_text(secrets.token_urlsafe(32))
            token_file.chmod(0o600)
        TOKEN = token_file.read_text().strip()
    print(f'Hosty is ready at http://{host}:{port}\nPanel token: {TOKEN}', flush=True)
    http = ThreadingHTTPServer((host, port), Handler)
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        http.server_close()
        stop_all_local()
