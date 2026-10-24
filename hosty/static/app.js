const $ = id => document.getElementById(id);
let state = {apps: [], server: {}}, page = 'overview';
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = name => `<i class="lni lni-${name}" aria-hidden="true"></i>`;
const runtimeIcon = runtime => icon({html: 'code-1', python: 'python', bun: 'javascript'}[runtime]);
const gb = value => (value / 1073741824).toFixed(1);
let toastTimer;
function toast(message) { $('toast').textContent = message; $('toast').style.display = 'block'; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').style.display = 'none', 4500); }
async function api(path, body) {
  const response = await fetch('/api/' + path, {method: body === undefined ? 'GET' : 'POST', headers: {'X-Hosty-Request': '1', 'Content-Type': 'application/json'}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
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
  $('server-status').innerHTML = icon('check-circle-1') + ' ' + (s.system ? 'Server online' : 'Local development');
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
  updateFormEnvironment();
  $('path-help').textContent = `Directory on ${s.hostname}. ~/ expands to ${s.app_home}/. Use a dedicated project folder; installed Hosty configures app access automatically.`;
  const details = {'Hostname': s.hostname, 'IP address': s.ip, 'Operating system': s.os, 'CPU cores': s.cores, 'Python': s.python, 'Bun': s.bun, 'Timezone': s.timezone, 'Firewall': s.firewall, 'SSH / SFTP': s.ssh, 'Panel mode': s.system ? 'Ubuntu · per-app environments' : 'Local development'};
  $('server-details').innerHTML = Object.entries(details).map(([key, value]) => `<dt>${escape(key)}</dt><dd>${escape(value)}</dd>`).join('');
  renderApps();
  const productionApps = state.apps.filter(a => a.environment === 'production');
  $('cert-list').innerHTML = productionApps.length ? productionApps.map(a => `<div class="cert-row"><div>${escape(a.domain)}<small>${a.ssl ? 'Let’s Encrypt · HTTPS enabled' : 'Certificate not issued'}</small></div><button class="${a.ssl ? 'secondary' : 'primary'}" data-cert="${a.id}">${a.ssl ? 'Renew certificate' : 'Enable HTTPS ' + icon('arrow-angular-top-right')}</button></div>`).join('') : '<div class="empty">Switch an app to Production to configure SSL.</div>';
}
function renderApps() {
  const query = $('search').value.toLowerCase(), filter = $('status-filter').value;
  const list = state.apps.filter(a => (a.name + ' ' + a.domain + ' ' + (a.local_url || '')).toLowerCase().includes(query) && (filter === 'all' || a.status === filter));
  $('table-count').textContent = `${list.length} application${list.length === 1 ? '' : 's'}`;
  $('app-list').innerHTML = list.length ? list.map(a => `<div class="app-row"><div class="app-name"><div class="app-icon">${runtimeIcon(a.runtime)}</div><div><strong>${escape(a.name)}</strong><a href="${escape(a.local_url || (a.domain ? (a.ssl ? 'https' : 'http') + '://' + a.domain : '#'))}" target="_blank" rel="noopener">${escape(a.local_url || a.domain || 'No domain')} ${icon('arrow-angular-top-right')}</a><div class="environment-links"><span class="app-port">PORT ${a.port}</span><select class="environment-select" aria-label="Environment for ${escape(a.name)}" data-environment="${a.id}"><option value="development" ${a.environment === 'development' ? 'selected' : ''}>DEV</option><option value="production" ${a.environment === 'production' ? 'selected' : ''}>PROD</option></select>${a.environment === 'development' ? (a.production_url ? `<span>Prod: <a href="${escape(a.production_url)}" target="_blank" rel="noopener">${escape(a.domain)} ${icon('arrow-angular-top-right')}</a></span>` : '<span>Production domain not configured</span>') : `<span>Dev: <a href="${escape(a.dev_url)}" target="_blank" rel="noopener">${escape(a.dev_url)} ${icon('arrow-angular-top-right')}</a></span>`}</div></div></div><span class="app-status ${a.status}"><i></i>${a.status[0].toUpperCase() + a.status.slice(1)}</span><span class="runtime-label"><b>${runtimeIcon(a.runtime)}</b>${{html: 'HTML',python: 'Python',bun: 'Bun'}[a.runtime]}</span><button class="ssl-label ${a.ssl ? '' : 'off'}" data-cert="${a.id}" title="Manage SSL" ${a.environment === 'development' ? 'disabled' : ''}>${icon(a.ssl ? 'shield-2-check' : 'unlocked-2')} ${a.environment === 'development' ? 'Local HTTP' : a.ssl ? 'Secure' : 'Enable'}</button><div class="app-actions"><button title="Edit application" aria-label="Edit ${escape(a.name)}" data-edit="${a.id}">${icon('file-pencil')}</button><button title="${a.status === 'running' ? 'Stop' : 'Start'} application" aria-label="${a.status === 'running' ? 'Stop' : 'Start'} application" data-action="${a.status === 'running' ? 'stop' : 'start'}" data-id="${a.id}">${icon(a.status === 'running' ? 'pause' : 'play')}</button><button title="Restart application" aria-label="Restart application" data-action="restart" data-id="${a.id}">${icon('refresh-circle-1-clockwise')}</button><button title="View application logs" aria-label="View application logs" data-action="logs" data-id="${a.id}">${icon('file-pencil')}</button><button class="delete-app-button" title="Delete application" aria-label="Delete ${escape(a.name)}" data-delete="${a.id}">${icon('trash-3')}</button></div></div>`).join('') : `<div class="empty"><div class="empty-symbol">${icon('flower-2')}</div><h3>${state.apps.length ? 'No applications match your search.' : 'Big ideas start with a little app.'}</h3><p>${state.apps.length ? 'Try another name or status.' : 'Give your first project a place to call home.'}</p>${state.apps.length ? '' : `<button class="secondary" id="empty-add">${icon('plus')} Add your first application</button>`}</div>`;
  if ($('empty-add')) $('empty-add').onclick = openApp;
}
function navigate(next) {
  page = next;
  const titles = {overview: ['Make yourself at host.', 'A little space on the internet. Everything running smoothly.'],apps: ['Your corner of the internet.', 'Bring your projects together. Give every idea a home.'], certs: ['A little peace of mind.', 'Free SSL certificates for every application you host.'], server: ['Meet your server.', 'The machine that keeps your ideas online.'], settings: ['Make Hosty yours.', 'Manage your panel account and browser address.']};
  $('page-title').textContent = titles[page][0];
  $('page-description').textContent = titles[page][1];
  $('breadcrumb').textContent = {overview:'Overview',apps:'Applications',certs:'SSL certificates',server:'Server details',settings:'Settings'}[page];
  $('overview-page').hidden = page !== 'overview';
  $('applications-section').hidden = !['overview','apps'].includes(page);
  $('bottom-panels').hidden = page !== 'overview';
  $('certificates-page').hidden = page !== 'certs';
  $('server-page').hidden = page !== 'server';
  $('settings-page').hidden = page !== 'settings';
  if (page === 'settings') loadSettings().catch(error => toast(error.message));
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('selected', b.dataset.page === page));
}
function updateFormEnvironment() {
  const production = $('app-environment').value === 'production';
  $('app-form').querySelector('input[name="domain"]').required = production;
  $('domain-help').textContent = production ? 'Required. Point your DNS A record to this server.' : 'Optional production domain. Development uses its own local address.';
}
let editingAppId = null;
function openApp() { editingAppId = null; $('app-dialog-title').textContent = 'A new home for your app.'; $('create-button').innerHTML = 'Create application ' + icon('arrow-angular-top-right'); $('app-form').reset(); updateFormEnvironment(); $('form-error').textContent = ''; $('app-dialog').showModal(); }
document.querySelectorAll('.add-app').forEach(b => b.onclick = openApp);
document.querySelectorAll('[data-close]').forEach(b => b.onclick = () => $('app-dialog').close());
document.querySelectorAll('nav button').forEach(b => b.onclick = () => navigate(b.dataset.page));
$('search').oninput = renderApps;
$('status-filter').onchange = renderApps;
$('app-form').onchange = () => { updateFormEnvironment(); $('runtime-help').textContent = {python:!state.server.system ? 'Use a .venv with uvicorn installed and a main.py exporting app.' : 'Expose app in main.py. Hosty supplies Uvicorn; a project .venv is optional.',bun:`Runs bun run ${$('app-environment').value === 'development' ? 'dev' : 'start'}. Hosty runs bun install first. Define that script in package.json and honor PORT and HOST.`,html:'This directory should contain your index.html and static assets.'}[new FormData($('app-form')).get('runtime')]; };
$('app-form').onsubmit = async e => {
  e.preventDefault(); $('create-button').disabled = true; $('form-error').textContent = '';
  try { const wasEditing = Boolean(editingAppId); const created = await api(editingAppId ? `apps/${editingAppId}/edit` : 'apps', Object.fromEntries(new FormData(e.target))); await refresh(); $('app-dialog').close(); toast(created.warning || (wasEditing ? 'Application updated.' : created.environment === 'development' ? `Application running at ${created.dev_url}` : state.server.system ? 'Application deployed. Welcome home.' : 'Application configured. Use --dev to run locally.')); }
  catch(error) { $('form-error').textContent = error.message; }
  finally { $('create-button').disabled = false; }
};
$('auth-form').onsubmit = async e => {
  e.preventDefault(); $('login-submit').disabled = true;
  try {
    await api('login', {username: $('login-username').value.trim(), password: $('login-password').value});
    $('login-password').value = '';
    await refresh();
    $('auth-dialog').close();
    $('auth-error').textContent = '';
  } catch (error) { $('auth-error').textContent = error.message; }
  finally { $('login-submit').disabled = false; }
};
$('auth-dialog').addEventListener('cancel', e => e.preventDefault());
$('disconnect').onclick = async () => { try { await api('logout', {}); location.reload(); } catch (error) { toast(error.message); } };
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
  catch(error) { if (error.message.includes('\n')) info('Application could not start', error.message); else toast(error.message); }
  finally { button.disabled = false; }
});
refresh().catch(() => {});
setInterval(() => { if (!$('auth-dialog').open) refresh().catch(() => {}); }, 15000);

document.addEventListener('change', async e => {
  const select = e.target.closest('[data-environment]');
  if (!select) return;
  const app = state.apps.find(a => a.id === select.dataset.environment);
  const body = {environment: select.value};
  if (select.value === 'production' && !app.domain) {
    const domain = prompt('Production domain for this application (for example, app.example.com):');
    if (!domain) { renderApps(); return; }
    body.domain = domain;
  }
  select.disabled = true;
  try {
    await api(`apps/${select.dataset.environment}/environment`, body);
    await refresh();
    toast('Application environment updated.');
  } catch (error) {
    toast(error.message);
    await refresh().catch(() => {});
  } finally { select.disabled = false; }
});

let deletingAppId = null, deletionBusy = false;
function closeDelete() {
  if (deletionBusy) return;
  $('delete-dialog').close();
  deletingAppId = null;
}
document.addEventListener('click', e => {
  const button = e.target.closest('[data-delete]');
  if (!button) return;
  const app = state.apps.find(a => a.id === button.dataset.delete);
  if (!app) return;
  deletingAppId = app.id;
  $('delete-app-name').textContent = app.name;
  $('delete-form').reset();
  $('delete-error').textContent = '';
  $('delete-submit').disabled = true;
  $('delete-dialog').showModal();
  $('delete-confirmation').focus();
});
$('delete-confirmation').oninput = () => { $('delete-submit').disabled = deletionBusy || $('delete-confirmation').value !== 'DELETE'; };
$('delete-cancel').onclick = closeDelete;
$('delete-close').onclick = closeDelete;
$('delete-dialog').addEventListener('cancel', e => { e.preventDefault(); closeDelete(); });
$('delete-form').onsubmit = async e => {
  e.preventDefault();
  if (deletionBusy || !deletingAppId || $('delete-confirmation').value !== 'DELETE') return;
  deletionBusy = true;
  $('delete-submit').disabled = true;
  $('delete-cancel').disabled = true;
  $('delete-close').disabled = true;
  $('delete-error').textContent = '';
  try {
    await api(`apps/${deletingAppId}/delete`, {confirmation: $('delete-confirmation').value});
    $('delete-dialog').close();
    deletingAppId = null;
    toast('Application deleted. Project files kept.');
    await refresh();
  } catch (error) {
    if ($('delete-dialog').open) $('delete-error').textContent = error.message;
    else toast(error.message);
  } finally {
    deletionBusy = false;
    $('delete-cancel').disabled = false;
    $('delete-close').disabled = false;
    $('delete-submit').disabled = $('delete-confirmation').value !== 'DELETE';
  }
};

document.addEventListener('click', e => {
  const button = e.target.closest('[data-edit]');
  if (!button) return;
  const app = state.apps.find(a => a.id === button.dataset.edit);
  if (!app) return;
  openApp();
  editingAppId = app.id;
  $('app-dialog-title').textContent = 'Edit application';
  $('create-button').innerHTML = 'Save changes ' + icon('arrow-angular-top-right');
  for (const field of ['name', 'domain', 'dev_domain', 'environment', 'runtime', 'path', 'port']) {
    $('app-form').elements.namedItem(field).value = app[field] || '';
  }
  $('app-form').onchange();
});

async function loadSettings() {
  const settings = await api('settings');
  $('settings-username').value = settings.username;
  $('settings-domain').value = settings.domain;
  $('settings-address').textContent = settings.system ? `Panel address: http://${settings.domain}. Configure DNS for access from other machines.` : `Panel address: http://${settings.domain}:${settings.panel_port}. This hostname must resolve to the machine running Hosty.`;
}
$('settings-form').onsubmit = async e => {
  e.preventDefault(); $('settings-submit').disabled = true; $('settings-error').textContent = '';
  try {
    await api('settings', {username: $('settings-username').value.trim(), domain: $('settings-domain').value.trim(), password: $('settings-password').value, current_password: $('settings-current-password').value});
    $('settings-password').value = ''; $('settings-current-password').value = '';
    $('login-username').value = $('settings-username').value;
    $('auth-dialog').showModal();
    toast('Settings saved. Sign in again with your account.');
  } catch (error) { $('settings-error').textContent = error.message; }
  finally { $('settings-submit').disabled = false; }
};
