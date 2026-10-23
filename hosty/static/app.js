const $ = id => document.getElementById(id);
let token = sessionStorage.getItem('hosty-token') || '', state = {apps: [], server: {}}, page = 'overview';
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const gb = value => (value / 1073741824).toFixed(1);
let toastTimer;
function toast(message) { $('toast').textContent = message; $('toast').style.display = 'block'; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').style.display = 'none', 4500); }
async function api(path, body) {
  const response = await fetch('/api/' + path, {method: body === undefined ? 'GET' : 'POST', headers: {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) { if (response.status === 401 && !$('auth-dialog').open) $('auth-dialog').showModal(); throw new Error(data.error || 'Could not connect to server'); }
  return data;
}
async function refresh() {
  try { state = await api('overview'); render(); $('connection').textContent = 'Server connected'; }
  catch (error) { $('connection').textContent = 'Disconnected'; throw error; }
}
function render() {
  const s = state.server;
  $('hostname').textContent = s.hostname;
  $('side-host').textContent = s.hostname;
  $('os').textContent = s.os;
  $('ip').textContent = s.ip;
  $('server-status').textContent = '●  ' + (s.system ? 'Server online' : 'Local development');
  const hours = Math.floor(s.uptime / 3600);
  $('uptime').textContent = hours >= 24 ? `${Math.floor(hours / 24)}d ${hours % 24}h` : `${hours}h ${Math.floor(s.uptime % 3600 / 60)}m`;
  const load = Math.round(s.load / s.cores * 100);
  $('cpu').textContent = load;
  $('cpu-meter').style.width = Math.min(load, 100) + '%';
  $('cores').textContent = `${s.cores} CPU cores · ${s.load.toFixed(2)} load average`;
  $('memory').textContent = s.memory.total ? gb(s.memory.used) + ' GB' : '—';
  $('memory-total').textContent = s.memory.total ? '/ ' + gb(s.memory.total) + ' GB' : '';
  const mp = s.memory.total ? Math.round(s.memory.used / s.memory.total * 100) : 0;
  $('memory-meter').style.width = mp + '%';
  $('memory-percent').textContent = s.memory.total ? `${mp}% of total memory in use` : 'Memory metrics available on Linux';
  $('disk').textContent = gb(s.disk.used) + ' GB';
  $('disk-total').textContent = '/ ' + gb(s.disk.total) + ' GB';
  const dp = Math.round(s.disk.used / s.disk.total * 100);
  $('disk-meter').style.width = dp + '%';
  $('disk-percent').textContent = gb(s.disk.total - s.disk.used) + ' GB free space';
  for (const id of ['app-count', 'heading-count', 'total-apps']) $(id).textContent = state.apps.length;
  const running = state.apps.filter(a => a.status === 'running').length;
  $('running-apps').textContent = running;
  $('other-apps').textContent = state.apps.length - running;
  $('bun-version').textContent = s.bun;
  $('python-version').textContent = s.python;
  $('timezone').textContent = s.timezone;
  $('ssh-command').textContent = `ssh user@${s.ip}`;
  const details = {'Hostname': s.hostname, 'IP address': s.ip, 'Operating system': s.os, 'CPU cores': s.cores, 'Python': s.python, 'Bun': s.bun, 'Timezone': s.timezone, 'Firewall': s.firewall, 'SSH / SFTP': s.ssh, 'Panel mode': s.system ? 'Ubuntu installation' : 'Local development'};
  $('server-details').innerHTML = Object.entries(details).map(([key, value]) => `<dt>${escape(key)}</dt><dd>${escape(value)}</dd>`).join('');
  renderApps();
  $('cert-list').innerHTML = state.apps.length ? state.apps.map(a => `<div class="cert-row"><div>${escape(a.domain)}<small>${a.ssl ? 'Let’s Encrypt · HTTPS enabled' : 'Certificate not issued'}</small></div><button class="${a.ssl ? 'secondary' : 'primary'}" data-cert="${a.id}">${a.ssl ? 'Renew certificate' : 'Enable HTTPS ↗'}</button></div>`).join('') : '<div class="empty">Add an application to manage its certificate.</div>';
}
function renderApps() {
  const query = $('search').value.toLowerCase(), filter = $('status-filter').value;
  const list = state.apps.filter(a => (a.name + ' ' + a.domain).toLowerCase().includes(query) && (filter === 'all' || a.status === filter));
  $('table-count').textContent = `${list.length} application${list.length === 1 ? '' : 's'}`;
  $('app-list').innerHTML = list.length ? list.map(a => `<div class="app-row"><div class="app-name"><div class="app-icon">${a.runtime === 'html' ? '⌘' : a.runtime === 'python' ? 'Py' : 'B'}</div><div><strong>${escape(a.name)}</strong><a href="${a.ssl ? 'https' : 'http'}://${escape(a.domain)}" target="_blank" rel="noopener">${escape(a.domain)} ↗</a></div></div><span class="app-status ${a.status}"><i></i>${a.status[0].toUpperCase() + a.status.slice(1)}</span><span class="runtime-label"><b>${a.runtime === 'html' ? '〈/〉' : a.runtime === 'python' ? '◈' : '◉'}</b>${{html: 'HTML',python: 'Python',bun: 'Bun'}[a.runtime]}</span><button class="ssl-label ${a.ssl ? '' : 'off'}" data-cert="${a.id}" title="Manage SSL">${a.ssl ? '♧ Secure' : '♧ Enable'}</button><div class="app-actions"><button title="${a.status === 'running' ? 'Stop' : 'Start'} application" data-action="${a.status === 'running' ? 'stop' : 'start'}" data-id="${a.id}">${a.status === 'running' ? 'Ⅱ' : '▷'}</button><button title="Restart application" data-action="restart" data-id="${a.id}">↻</button><button title="View application logs" data-action="logs" data-id="${a.id}">≡</button></div></div>`).join('') : `<div class="empty"><div class="empty-symbol">✳</div><h3>${state.apps.length ? 'No applications match your search.' : 'Big ideas start with a little app.'}</h3><p>${state.apps.length ? 'Try another name or status.' : 'Give your first project a place to call home.'}</p>${state.apps.length ? '' : '<button class="secondary" id="empty-add">＋ Add your first application</button>'}</div>`;
  if ($('empty-add')) $('empty-add').onclick = openApp;
}
function navigate(next) {
  page = next;
  const titles = {overview: ['Make yourself at host.', 'A little space on the internet. Everything running smoothly.'],apps: ['Your corner of the internet.', 'Bring your projects together. Give every idea a home.'], certs: ['A little peace of mind.', 'Free SSL certificates for every application you host.'], server: ['Meet your server.', 'The machine that keeps your ideas online.']};
  $('page-title').textContent = titles[page][0];
  $('page-description').textContent = titles[page][1];
  $('breadcrumb').textContent = {overview:'Overview',apps:'Applications',certs:'SSL certificates',server:'Server details'}[page];
  $('overview-page').hidden = page !== 'overview';
  $('applications-section').hidden = !['overview','apps'].includes(page);
  $('bottom-panels').hidden = page !== 'overview';
  $('certificates-page').hidden = page !== 'certs';
  $('server-page').hidden = page !== 'server';
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('selected', b.dataset.page === page));
}
function openApp() { $('app-form').reset(); $('form-error').textContent = ''; $('app-dialog').showModal(); }
document.querySelectorAll('.add-app').forEach(b => b.onclick = openApp);
document.querySelectorAll('[data-close]').forEach(b => b.onclick = () => $('app-dialog').close());
document.querySelectorAll('nav button').forEach(b => b.onclick = () => navigate(b.dataset.page));
$('search').oninput = renderApps;
$('status-filter').onchange = renderApps;
$('app-form').onchange = () => { $('runtime-help').textContent = {python:'Use a .venv with uvicorn installed and a main.py exporting app.',bun:'Use a package.json with a start script. Your app must read PORT and HOST.',html:'This directory should contain your index.html and static assets.'}[new FormData($('app-form')).get('runtime')]; };
$('app-form').onsubmit = async e => {
  e.preventDefault(); $('create-button').disabled = true; $('form-error').textContent = '';
  try { await api('apps', Object.fromEntries(new FormData(e.target))); await refresh(); $('app-dialog').close(); toast(state.server.system ? 'Application deployed. Welcome home.' : 'Application configured. Deploy on Ubuntu to start it.'); }
  catch(error) { $('form-error').textContent = error.message; }
  finally { $('create-button').disabled = false; }
};
$('auth-form').onsubmit = async e => {
  e.preventDefault(); token = $('token-input').value.trim();
  try { await refresh(); sessionStorage.setItem('hosty-token', token); $('auth-dialog').close(); $('auth-error').textContent = ''; }
  catch(error) { $('auth-error').textContent = error.message; }
};
$('auth-dialog').addEventListener('cancel', e => e.preventDefault());
$('disconnect').onclick = () => { sessionStorage.removeItem('hosty-token'); token = ''; location.reload(); };
function info(title, text) { $('info-title').textContent = title; $('info-content').textContent = text; $('info-dialog').showModal(); }
$('info-close').onclick = () => $('info-dialog').close();
$('access-details').onclick = () => info('SSH & SFTP access', 'Connect using an existing SSH user and key on your VPS.\n\n' + $('ssh-command').textContent + '\n\nFor file transfers, use port 22 in your SFTP client with the same account.\n\nHosty does not store SSH passwords or toggle SSH access. Manage access with sshd and your server firewall.');
async function copy(text) { try { await navigator.clipboard.writeText(text); toast('Copied to clipboard'); } catch { info('Copy connection', text); } }
$('copy-ip').onclick = () => copy(state.server.ip);
$('copy-ssh').onclick = () => copy($('ssh-command').textContent);
document.addEventListener('click', async e => {
  const action = e.target.closest('[data-action]'), cert = e.target.closest('[data-cert]');
  if (!action && !cert) return;
  const button = action || cert;
  const id = button.dataset.id || button.dataset.cert;
  const operation = action ? button.dataset.action : 'ssl';
  let body = {};
  if (operation === 'ssl') {
    const email = prompt('Email for Let’s Encrypt certificate notices (agreeing to Let’s Encrypt terms):');
    if (!email) return;
    body.email = email;
  }
  button.disabled = true;
  try { const result = await api(`apps/${id}/${operation}`, body); if (operation === 'logs') info('Application logs', result.logs || 'No log entries yet.'); else { await refresh(); toast(operation === 'ssl' ? 'HTTPS certificate is ready.' : `Application ${operation === 'stop' ? 'stopped' : operation === 'restart' ? 'restarted' : 'started'}.`); } }
  catch(error) { toast(error.message); }
  finally { button.disabled = false; }
});
if (token) refresh().catch(() => {}); else $('auth-dialog').showModal();
setInterval(() => { if (token && !$('auth-dialog').open) refresh().catch(() => {}); }, 15000);
