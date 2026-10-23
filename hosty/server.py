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
import stat
import subprocess
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .paths import app_home, expand_app_path

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


def service_action(item, action):
    unit = f'hosty-app-{item["id"]}'
    try:
        if action in ('start', 'restart'):
            command(['systemctl', 'reset-failed', unit])
        command(['systemctl', action, unit])
    except (ValueError, subprocess.TimeoutExpired) as error:
        try:
            logs = command(['journalctl', '-u', unit, '-n', '40', '--no-pager'])
        except (ValueError, OSError, subprocess.TimeoutExpired):
            logs = 'Could not read the service journal.'
        raise ValueError(f'{action.capitalize()} failed for {item["name"]}: {error}\n\n{logs}') from error


def prepare_app_permissions(item):
    """Grant the app account access without changing project ownership."""
    path = Path(item['path']).resolve(strict=True)
    reserved = {Path(p).resolve() for p in ('/', '/home', '/root', '/var', '/var/www', '/var/lib', '/var/lib/hosty', '/opt', '/opt/hosty', '/tmp')}
    reserved.update((Path.home().resolve(), Path(app_home()).resolve()))
    protected = ('/etc', '/usr', '/bin', '/sbin', '/lib', '/lib64', '/boot', '/dev', '/proc', '/sys', '/run', '/opt/hosty', '/var/lib/hosty')
    if path in reserved or any(path == Path(p).resolve() or Path(p).resolve() in path.parents for p in protected):
        raise ValueError('Choose a dedicated project directory, not a home directory or system directory, so Hosty can grant app access safely.')
    if not path.is_dir():
        raise ValueError(f'App directory not found: {path}')
    if not shutil.which('setfacl'):
        raise ValueError('Automatic app permissions require acl. Rerun sudo sh install.sh to install it.')
    # Traverse parents without allowing the app to list or write their contents.
    for parent in reversed(path.parents):
        if parent == Path('/'):
            continue
        command(['setfacl', '-m', 'u:hosty:--x', '--', str(parent)])
    # Physical traversal: do not grant access to targets of project symlinks.
    command(['setfacl', '-R', '-P', '-m', 'u:hosty:rwX', '--', str(path)])
    def walk_error(error):
        raise error
    batch = []
    for directory, _, _ in os.walk(path, followlinks=False, onerror=walk_error):
        batch.append(directory)
        if len(batch) == 128:
            command(['setfacl', '-m', 'd:u:hosty:rwx', '--', *batch])
            batch = []
    if batch:
        command(['setfacl', '-m', 'd:u:hosty:rwx', '--', *batch])


def check_app_access(item):
    path = item['path']
    try:
        command(['runuser', '-u', 'hosty', '--', 'test', '-r', path])
        command(['runuser', '-u', 'hosty', '--', 'test', '-x', path])
    except ValueError as error:
        raise ValueError(
            f'The hosty app user cannot read or enter {path}. '
            'Apps run as hosty, even though the panel runs as root. '
            'Use a shared app directory such as /var/www/my-app and grant hosty access '
            'to the project and its parent directories. Do not make /root public.'
        ) from error


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
    return {'hostname': socket.gethostname(), 'ip': ip, 'os': platform.system() + ' ' + platform.release(), 'cores': os.cpu_count(), 'timezone': time.tzname[0], 'python': platform.python_version(), 'bun': version('bun'), 'uptime': uptime, 'load': os.getloadavg()[0], 'memory': memory, 'disk': {'used': disk.used, 'total': disk.total}, 'system': SYSTEM, 'dev': DEV, 'app_home': app_home(), 'firewall': 'Unverified', 'ssh': 'Managed on server'}


def development_domain(item):
    if item.get('dev_domain'):
        return item['dev_domain']
    label = re.sub(r'[^a-z0-9-]+', '-', (item.get('domain') or item['name']).lower().split('.')[0]).strip('-')[:63].rstrip('-')
    return (label or item['id']) + '.localhost'


def environment(item):
    return item.get('environment', 'development' if DEV or not SYSTEM else 'production')


def status(item):
    result = dict(item)
    result['status'] = 'configured'
    result['dev_domain'] = development_domain(item)
    result['dev_url'] = f'http://{result["dev_domain"]}:{item["port"]}'
    result['production_url'] = (('https' if item.get('ssl') else 'http') + '://' + item['domain']) if item.get('domain') else None
    result['environment'] = environment(item)
    result['local_url'] = result['dev_url'] if environment(item) == 'development' else None
    if not SYSTEM and environment(item) == 'development':
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
        process = subprocess.Popen(args, cwd=item['path'], env={**os.environ, 'HOST': '127.0.0.1', 'PORT': str(port), 'NODE_ENV': 'development'}, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
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
    if SYSTEM:
        prepare_app_permissions(item)
    check_app_access(item)
    if runtime == 'html':
        executable = f'/usr/bin/python3 -m http.server {port} --bind 127.0.0.1'
    elif runtime == 'python':
        python = str(Path(path) / '.venv/bin/python')
        if not Path(python).is_file():
            python = '/usr/bin/python3'
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
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=NODE_ENV={'production' if environment(item) == 'production' else 'development'}
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
    nginx_path = Path(f'/etc/nginx/conf.d/hosty-{ident}.conf')
    if environment(item) == 'production':
        nginx_path.write_text(config)
    else:
        nginx_path.unlink(missing_ok=True)
    command(['nginx', '-t'])
    command(['systemctl', 'daemon-reload'])
    command(['systemctl', 'reload', 'nginx'])
    command(['systemctl', 'enable', f'hosty-app-{ident}'])
    service_action(item, 'restart')


def delete_app(items, item):
    remaining = [app for app in items if app['id'] != item['id']]
    if not SYSTEM:
        stop_local(item['id'])
        save(remaining)
        return
    unit = f'hosty-app-{item["id"]}'
    unit_path = Path(f'/etc/systemd/system/{unit}.service')
    nginx_path = Path(f'/etc/nginx/conf.d/hosty-{item["id"]}.conf')
    backups = {path: path.read_text() if path.exists() else None for path in (unit_path, nginx_path)}
    running = status(item)['status'] == 'running'
    enabled = subprocess.run(['systemctl', 'is-enabled', '--quiet', unit], capture_output=True).returncode == 0
    try:
        if backups[unit_path] is not None:
            command(['systemctl', 'disable', '--now', unit])
        unit_path.unlink(missing_ok=True)
        nginx_path.unlink(missing_ok=True)
        command(['nginx', '-t'])
        command(['systemctl', 'daemon-reload'])
        command(['systemctl', 'reload', 'nginx'])
        save(remaining)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        for path, content in backups.items():
            if content is not None:
                path.write_text(content)
        command(['systemctl', 'daemon-reload'])
        command(['systemctl', 'reload', 'nginx'])
        if backups[unit_path] is not None:
            if enabled:
                command(['systemctl', 'enable', unit])
            if running:
                service_action(item, 'start')
        raise


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
        filename = {'/': 'index.html', '/style.css': 'style.css', '/app.js': 'app.js', '/lineicons.css': 'lineicons.css', '/lineicons.woff2': 'lineicons.woff2'}.get(self.path.split('?')[0])
        if not filename:
            return self.reply({'error': 'Not found'}, 404)
        body = (Path(__file__).parent / 'static' / filename).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', {'html': 'text/html', 'css': 'text/css', 'js': 'application/javascript', 'woff2': 'font/woff2'}[filename.split('.')[-1]])
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
                dev_domain = str(data.get('dev_domain', '')).strip().lower()
                path = str(data.get('path', '')).strip()
                path = expand_app_path(path)
                runtime = data.get('runtime')
                mode = data.get('environment', 'production' if SYSTEM and not DEV else 'development')
                if mode not in ('development', 'production'):
                    raise ValueError('Choose Development or Production.')
                if mode == 'production' and not SYSTEM:
                    raise ValueError('Production requires an Ubuntu installation. Choose Development to run locally.')
                if not name or len(name) > 64:
                    raise ValueError('App name must be 1–64 characters.')
                if not (mode == 'development' and not domain) and (len(domain) > 253 or '.' not in domain or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in domain.split('.'))):
                    raise ValueError('Enter a valid domain, such as app.example.com.')
                if dev_domain and not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.localhost', dev_domain):
                    raise ValueError('Use a development hostname such as my-app.localhost, or leave it blank for an automatic address.')
                if runtime not in ('python', 'bun', 'html'):
                    raise ValueError('Choose a supported runtime.')
                if not re.fullmatch(r'/[A-Za-z0-9_./-]+', path) or '..' in path.split('/'):
                    raise ValueError(f'Invalid directory path: {path!r}. Use an absolute path or ~/ with letters, numbers, /, _, - or dots.')
                try:
                    directory_mode = Path(path).stat().st_mode
                except FileNotFoundError:
                    raise ValueError(f'Directory not found on the Hosty server: {path}. ~ expands to {app_home()}. Enter the full path to your project on this machine.')
                except PermissionError:
                    raise ValueError(f'Hosty cannot access {path}. Give the user running Hosty permission to traverse this directory and its parent directories.')
                if not stat.S_ISDIR(directory_mode):
                    raise ValueError(f'App path is a file, not a directory: {path}. Select the folder containing your app.')
                if domain and any(a['domain'] == domain for a in items):
                    raise ValueError('This domain is already assigned to an app.')
                item = {'id': secrets.token_hex(6), 'name': name, 'domain': domain, 'dev_domain': dev_domain, 'path': path, 'runtime': runtime, 'port': max([a['port'] for a in items] + [8999]) + 1, 'created': time.time(), 'ssl': False, 'environment': mode}
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
                if not SYSTEM and mode == 'development':
                    try:
                        start_local(item)
                    except (ValueError, OSError) as error:
                        result = status(item)
                        result['warning'] = str(error)
                        return self.reply(result, 201)
                return self.reply(status(item), 201)
            match = re.fullmatch(r'/api/apps/([a-f0-9]{12})/(start|stop|restart|ssl|logs|environment|delete)', self.path)
            if not match:
                return self.reply({'error': 'Not found'}, 404)
            ident, action = match.groups()
            item = next((a for a in items if a['id'] == ident), None)
            if not item:
                return self.reply({'error': 'App not found'}, 404)
            if action == 'delete':
                if data.get('confirmation') != 'DELETE':
                    raise ValueError('Type DELETE exactly to confirm app deletion.')
                delete_app(items, item)
                return self.reply({'deleted': ident})
            if action == 'environment':
                mode = data.get('environment')
                if mode not in ('development', 'production'):
                    raise ValueError('Choose Development or Production.')
                domain = str(data.get('domain', item.get('domain', ''))).strip().lower()
                if mode == 'production':
                    if not SYSTEM:
                        raise ValueError('Production requires an Ubuntu installation.')
                    if len(domain) > 253 or '.' not in domain or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in domain.split('.')):
                        raise ValueError('Enter a valid production domain, such as app.example.com.')
                    if any(a['id'] != ident and a['domain'] == domain for a in items):
                        raise ValueError('This domain is already assigned to an app.')
                if mode == environment(item):
                    return self.reply(status(item))
                candidate = {**item, 'domain': domain, 'environment': mode, 'ssl': False}
                if SYSTEM:
                    paths = [Path(f'/etc/systemd/system/hosty-app-{ident}.service'), Path(f'/etc/nginx/conf.d/hosty-{ident}.conf')]
                    backups = {path: path.read_text() if path.exists() else None for path in paths}
                    was_running = status(item)['status'] == 'running'
                    try:
                        provision(candidate)
                    except (ValueError, OSError, subprocess.TimeoutExpired):
                        for path, content in backups.items():
                            if content is None:
                                path.unlink(missing_ok=True)
                            else:
                                path.write_text(content)
                        command(['systemctl', 'daemon-reload'])
                        command(['systemctl', 'reload', 'nginx'])
                        command(['systemctl', 'restart' if was_running else 'stop', f'hosty-app-{ident}'])
                        raise
                else:
                    stop_local(ident)
                    start_local(candidate)
                item.update(candidate)
                save(items)
                return self.reply(status(item))
            if action == 'ssl' and environment(item) != 'production':
                raise ValueError('Switch this app to Production to enable SSL.')
            if not SYSTEM and environment(item) == 'development':
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
                item['autostart'] = action != 'stop'
                save(items)
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
                if action in ('start', 'restart'):
                    prepare_app_permissions(item)
                    check_app_access(item)
                service_action(item, action)
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
    if not SYSTEM:
        for item in apps():
            if environment(item) == 'development' and item.get('autostart', True):
                try:
                    start_local(item)
                except (ValueError, OSError) as error:
                    print(f'Could not start {item["name"]}: {error}', flush=True)
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        http.server_close()
        stop_all_local()
