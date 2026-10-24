# Hosty

A small hosting panel for your personal tools. A responsive dashboard, live server metrics, Python / Go / Bun / static apps, systemd service controls, Nginx routing, logs, and Let's Encrypt certificates. No Python dependencies are required to run the panel.

## Try locally

```sh
python3 -m hosty.cli serve
```

First run prompts for a username, password, and panel domain. Press Enter for `hostyuser`, `h0styp4ss`, and `hosty.localhost`. Noninteractive first runs use those defaults. Open http://hosty.localhost:8787 (or http://127.0.0.1:8787) and sign in with your account. Apps are saved in `~/.local/share/hosty/apps.json`. The panel runs development apps locally. Public production deployment requires an Ubuntu installation. Linux supplies system memory and server uptime; on macOS memory is unavailable and uptime measures the panel process. CPU is normalized one-minute load average, not instantaneous CPU utilization.

## Per-app environments

```sh
python3 -m hosty.cli serve
```

Choose Development or Production in the app form or the DEV/PROD dropdown on an existing app. Both retain their development hostname and production domain. Development needs no domain; production requires Ubuntu and a domain. `serve --dev` remains a compatibility option, but is no longer required.

Add an app with its local directory; the production domain is optional in Development. Hosty starts the app and displays a clickable `http://my-app.localhost:9000` development address (each app gets its own port). Existing apps can be started using the play button. Start, stop, restart, and logs work without Ubuntu, Nginx, or DNS. Python and Bun use the runtime conventions below; HTML works with just an `index.html`.

Locally, apps run as your current user and bind to loopback (Bun apps must honor `HOST` and `PORT`). Every app keeps its production domain separately from its development hostname. For example, `app.example.com` gets `app.localhost:9000`; you can also enter a custom `name.localhost` in the app form. Existing apps receive an automatic development hostname. The dashboard shows both addresses and uses the development address when that app is set to Development. Local addresses refer to the machine running Hosty. `.localhost` is reserved for loopback, so modern browsers resolve it locally without hosts-file edits; non-browser tools may need `localhost:PORT` or a resolver override. Apps must permit their development hostname in any host validation configuration. The port routes each local address to its own app; production routing still uses Nginx. SSL is available only for Production apps. Switching an installed app to Development removes its public Nginx route; switching back requires enabling SSL again. Installed Development apps use systemd as the hosty user and restart with the server, alongside Production apps. Local apps stop with the panel and restart when you relaunch it, unless explicitly stopped in the panel.

## Install on a fresh Ubuntu VPS

Clone this repository on your VPS, then run:

```sh
sudo sh install.sh
```

The bootstrap installs Python if it is missing, then runs `hosty install`. If Python is already available, `sudo python3 -m hosty.cli install` also works.

Alternatively install this package in a virtual environment with `pip install .`, then run `sudo /path/to/venv/bin/hosty install`.

The installer checks installed packages and installs missing dependencies: Python, pip, venv, Uvicorn, Python build tools, Go, Nginx, Certbot with its Nginx plugin, curl, unzip, CA certificates, Git, OpenSSH/SFTP, ACL permission tools, UFW, and native build tools. It installs Bun system-wide using its official installer when no usable shared installation exists. Existing usable Bun installations are preserved. It verifies the runtimes and configuration, enables the services and certificate renewal timer, and installs a `hosty` command. Rerunning the installer keeps the existing account and app records. First-time installation prompts for username, password, and panel domain, defaulting to `hostyuser`, `h0styp4ss`, and `hosty.localhost`. It configures Nginx to route that hostname to the panel. The default local hostname is mapped to loopback on the installed machine. On that machine, open `http://hosty.localhost` (port 80) or `http://hosty.localhost:8787` directly. A remote VPS cannot configure your laptop’s hosts file; use the SSH tunnel below and `http://localhost:8787` from your laptop. Access it through an SSH tunnel:

```sh
ssh -L 8787:127.0.0.1:8787 your-user@your-server
```

Open http://localhost:8787 and sign in with your configured username and password. Passwords are salted and hashed in `panel.json`, with file permissions restricted to its owner. Login sessions expire after 12 hours or a panel restart. The old environment token cannot bypass account login. The panel service runs as root to configure Nginx, systemd, and certificates; application services run as the unprivileged `hosty` user. The panel process stays on loopback; Nginx exposes its configured domain on HTTP port 80. For remote access, set a DNS A record to the VPS and enable panel HTTPS in Settings before using it over an untrusted network, or use the SSH tunnel. Change the default password in Settings before public use.

A published curl bootstrap URL is not included; use a clone from your own trusted repository. Installation is intended for a fresh server with no conflicting Nginx configuration.

## Add an app

1. Upload your project using your existing SSH/SFTP account, for example to `/var/www/my-app`.
2. Select a dedicated project directory. On installed servers, Hosty automatically grants the `hosty` app user read/write access to that project, traversal access through parent directories, and inherited access for new files. Ownership stays unchanged. Whole home directories and system directories are rejected; project symlinks are not followed when granting access.
3. Create a DNS A record for your domain pointing to the VPS public IP. The dashboard displays the host's resolved address, which may be private; check your VPS provider for the public IP.
4. Select **New application**, enter a name, domain, runtime, and existing directory (an absolute path or `~/my-app`). `~` expands to the home of the user who ran the installer with `sudo`, saved as `HOSTY_APP_HOME` in `/etc/hosty.env`. Installing directly as root uses `/root`. Rerun `sudo sh install.sh` from your normal user to configure this on an existing installation. An existing `HOSTY_APP_HOME` setting is preserved. Local mode uses the current user (or sudo caller).

Runtime conventions:

- **Python:** expose an ASGI `app` in `main.py`. On installed servers, Hosty provides Python and Uvicorn automatically. If your project has `.venv`, Hosty uses that environment instead; install project dependencies including `uvicorn` there. Dev mode requires this project `.venv`.
- **Bun:** the Ubuntu installer supplies Bun automatically (install it yourself for local dev mode). Provide a `dev` script for Development and a `start` script for Production in `package.json`. Hosty checks the script exists, automatically runs `bun install` in the project directory on add/start/restart, then runs `bun run dev` or `bun run start` according to the app’s environment. Installed servers install project packages as the `hosty` user. Install failures stop startup and show the underlying error. The application must honor `HOST=127.0.0.1` and the assigned `PORT`.
- **HTML:** place `index.html` and assets in the directory. The initial implementation serves these using Python's basic HTTP server behind Nginx; use this for small personal sites.

Hosty allocates ports starting at 9000, writes a systemd unit per app, and configures Nginx on port 80. Ensure ports 80 and 443 are reachable. After DNS resolves, select **Enable HTTPS** and supply your email to accept Let's Encrypt's terms. Certbot handles certificate renewal through its Ubuntu timer. The SSL badge records successful issuance; it does not monitor certificate expiration.

To update a project, upload or pull your new code, install changed dependencies, then restart it in Hosty. Hosty installs its own runtime dependencies; Bun project packages are installed automatically; Python packages in `requirements.txt` still need to be installed in the project environment. Automatic Git pulling, Python project dependency installation, custom startup commands and SSH/firewall toggles are not implemented. SSH/SFTP access remains managed by your existing operating system account and keys.

## Checks

```sh
python3 -m unittest discover -s tests -v
```

## Delete an app

Click the trash icon, type `DELETE` exactly, then select **Delete application**. Hosty stops the app, removes its systemd unit and Nginx route on installed servers, and removes its panel entry. Project files, issued certificate files, logs, and permission grants remain on disk. Other apps are kept. If service cleanup fails, Hosty retains the app entry and attempts to restore its previous configuration.

## Edit an app and its port

Use the pencil icon to edit an app’s name, runtime, directory, environment, production domain, development hostname, and app port. Ports may be 1024–65535; leaving the port blank selects one automatically (or keeps the current port when editing). Hosty rejects ports reserved by another app, occupied ports, and the panel’s own port.

Python receives the configured port via Uvicorn’s `--port`; Bun receives `PORT` and `HOST`. If your framework uses a hardcoded port instead, choose that same port in Hosty or update your script to honor `PORT`. Hosty uses this port in its development URL and production Nginx upstream. Saving restarts a running app and preserves stopped apps as stopped. Failed edits attempt to restore the previous service and configuration. Port-only changes preserve an existing HTTPS configuration; changing the production domain or environment requires enabling SSL again.

## Panel account and domain

Open **Settings** to change the panel username, password, or domain. Enter your current password to save; leave the new password blank to keep it. Saving signs out existing sessions and requires a fresh login. A public panel domain needs DNS pointing to this VPS; local aliases refer to the machine where the browser is running. On Ubuntu the Nginx panel route updates automatically. Existing HTTPS configuration is preserved when the domain is unchanged. Changing the hostname creates an HTTP route; enable a certificate for the new hostname in Settings. Local `serve` mode uses `http://DOMAIN:8787` and does not change system DNS or install Nginx.

## Go apps

Select **Go**, a module directory containing `go.mod`, and the port your server uses. Set **Go package** to `.` for a root main package or, for example, `./cmd/server`. Hosty downloads modules, builds `.hosty/app` with `-buildvcs=false` (omitting optional Git metadata so app-user builds do not require repository ownership), and runs that binary in both Development and Production. Add `.hosty/` to your project’s `.gitignore`.

The Ubuntu installer supplies Go automatically (`golang-go`); local development needs a Go toolchain installed. Your server must read `HOST` and `PORT` to listen on the configured loopback address, or use a fixed port matching the app’s setting. Start/restart rebuilds the app; automatic source watching is not implemented. The toolchain may download a newer version when required by your module, so initial builds need network access. Go build commands run as `hosty` on installed servers and as your current user locally, with dependency and build caches inside `.hosty`.

## Firewall

The Ubuntu installer installs and configures UFW. Before activation it adds explicit allow rules for configured SSH ports (including the current SSH connection and active Ubuntu SSH socket ports) and TCP 80/443 for Nginx. If SSH ports cannot be determined, installation stops without enabling the firewall. Existing rules are preserved. For an inactive firewall, Hosty sets deny incoming / allow outgoing and then enables it; an already active firewall retains its default policies.

App ports and panel port 8787 are not opened publicly; access apps through Nginx and the panel through its configured domain or an SSH tunnel. The dashboard reads actual UFW active/inactive status. Additional services require your own firewall rules. Provider-level firewalls remain separate. Local development does not change firewall settings.

## Domain routing checks

New apps default to Production on an installed VPS and Development in local mode. Hosty writes each Production app’s Nginx route automatically. The dashboard probes Nginx on loopback using that app’s domain and checks a per-app response marker, so the default welcome page is not reported as a working route. It distinguishes routing mismatches from an unavailable upstream app. Use **Repair routing** in the panel to reapply a missing or broken managed route; starting/restarting also reapplies it. Installer upgrades refresh existing app routes without rebuilding the apps. DNS still needs to point to this server; the local routing check does not verify external DNS or provider firewall rules.

## Panel SSL and HTTPS route verification

In **Settings → Panel HTTPS**, enter a certificate email and accept the Let’s Encrypt terms to issue or renew the panel certificate. The panel must use a public hostname whose DNS points here; `.localhost` and `.local` cannot receive public certificates. Enabling an app’s HTTPS also attempts panel HTTPS when the panel has a public hostname and is not yet marked secure. Panel issuance failures are shown separately and do not undo a successful app certificate.

HTTPS app routing checks connect to Nginx on loopback port 443 with the app’s hostname as TLS SNI and normal certificate validation. HTTP redirects are not mistaken for a different site. Managed app server blocks, including Certbot redirect blocks, receive the app routing marker when routes are regenerated.
