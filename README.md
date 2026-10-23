# Hosty

A small hosting panel for your personal tools. A responsive dashboard, live server metrics, Python / Bun / static apps, systemd service controls, Nginx routing, logs, and Let's Encrypt certificates. No Python dependencies are required to run the panel.

## Try locally

```sh
python3 -m hosty.cli serve
```

Open http://127.0.0.1:8787 and enter the token printed in your terminal. Apps are saved in `~/.local/share/hosty/apps.json`. The panel runs development apps locally. Public production deployment requires an Ubuntu installation. Linux supplies system memory and server uptime; on macOS memory is unavailable and uptime measures the panel process. CPU is normalized one-minute load average, not instantaneous CPU utilization.

## Per-app environments

```sh
python3 -m hosty.cli serve
```

Choose Development or Production in the app form or the DEV/PROD dropdown on an existing app. Both retain their development hostname and production domain. Development needs no domain; production requires Ubuntu and a domain. `serve --dev` remains a compatibility option, but is no longer required.

Add an app with its local directory; the production domain is optional in Development. Hosty starts the app and displays a clickable `http://my-app.localhost:9000` development address (each app gets its own port). Existing apps can be started using the play button. Start, stop, restart, and logs work without Ubuntu, Nginx, or DNS. Python and Bun use the runtime conventions below; HTML works with just an `index.html`.

Apps run as your current user and bind to loopback (Bun apps must honor `HOST` and `PORT`). Every app keeps its production domain separately from its development hostname. For example, `app.example.com` gets `app.localhost:9000`; you can also enter a custom `name.localhost` in the app form. Existing apps receive an automatic development hostname. The dashboard shows both addresses and uses the development address when that app is set to Development. Local addresses refer to the machine running Hosty. `.localhost` is reserved for loopback, so modern browsers resolve it locally without hosts-file edits; non-browser tools may need `localhost:PORT` or a resolver override. Apps must permit their development hostname in any host validation configuration. The port routes each local address to its own app; production routing still uses Nginx. SSL is available only for Production apps. Switching an installed app to Development removes its public Nginx route; switching back requires enabling SSL again. Installed Development apps use systemd as the hosty user and restart with the server, alongside Production apps. Local apps stop with the panel and restart when you relaunch it, unless explicitly stopped in the panel.

## Install on a fresh Ubuntu VPS

Clone this repository on your VPS, then run:

```sh
sudo sh install.sh
```

The bootstrap installs Python if it is missing, then runs `hosty install`. If Python is already available, `sudo python3 -m hosty.cli install` also works.

Alternatively install this package in a virtual environment with `pip install .`, then run `sudo /path/to/venv/bin/hosty install`.

The installer checks installed packages and installs missing dependencies: Python, pip, venv, Uvicorn, Python build tools, Nginx, Certbot with its Nginx plugin, curl, unzip, CA certificates, Git, OpenSSH/SFTP, and native build tools. It installs Bun system-wide using its official installer when no usable shared installation exists. Existing usable Bun installations are preserved. It verifies the runtimes and configuration, enables the services and certificate renewal timer, and installs a `hosty` command. Rerunning the installer keeps the existing panel token and app records. It adds `127.0.0.1 hosty.local` to the installed machine’s hosts file and binds the panel to localhost. On that machine, open `http://hosty.local:8787`. A remote VPS cannot configure your laptop’s hosts file; use the SSH tunnel below and `http://localhost:8787` from your laptop. Access it through an SSH tunnel:

```sh
ssh -L 8787:127.0.0.1:8787 your-user@your-server
```

Open http://localhost:8787. Read your token with `sudo cat /etc/hosty.env`. Keep the token private: it authorizes deployment operations on your server. The panel service runs as root to configure Nginx, systemd, and certificates; application services run as the unprivileged `hosty` user. Keep the panel on loopback behind the SSH tunnel.

A published curl bootstrap URL is not included; use a clone from your own trusted repository. Installation is intended for a fresh server with no conflicting Nginx configuration.

## Add an app

1. Upload your project using your existing SSH/SFTP account, for example to `/var/www/my-app`.
2. Give the `hosty` user read and execute access to the project and its parent directories. App data directories must be writable by that user.
3. Create a DNS A record for your domain pointing to the VPS public IP. The dashboard displays the host's resolved address, which may be private; check your VPS provider for the public IP.
4. Select **New application**, enter a name, domain, runtime, and existing directory (an absolute path or `~/my-app`). `~` expands to the home of the user who ran the installer with `sudo`, saved as `HOSTY_APP_HOME` in `/etc/hosty.env`. Installing directly as root uses `/root`. Rerun `sudo sh install.sh` from your normal user to configure this on an existing installation. An existing `HOSTY_APP_HOME` setting is preserved. Local mode uses the current user (or sudo caller).

Runtime conventions:

- **Python:** expose an ASGI `app` in `main.py`. On installed servers, Hosty provides Python and Uvicorn automatically. If your project has `.venv`, Hosty uses that environment instead; install project dependencies including `uvicorn` there. Dev mode requires this project `.venv`.
- **Bun:** the Ubuntu installer supplies Bun automatically (install it yourself for local dev mode). Provide a `start` script in `package.json`. The application must honor `HOST=127.0.0.1` and the assigned `PORT`.
- **HTML:** place `index.html` and assets in the directory. The initial implementation serves these using Python's basic HTTP server behind Nginx; use this for small personal sites.

Hosty allocates ports starting at 9000, writes a systemd unit per app, and configures Nginx on port 80. Ensure ports 80 and 443 are reachable. After DNS resolves, select **Enable HTTPS** and supply your email to accept Let's Encrypt's terms. Certbot handles certificate renewal through its Ubuntu timer. The SSL badge records successful issuance; it does not monitor certificate expiration.

To update a project, upload or pull your new code, install changed dependencies, then restart it in Hosty. Hosty installs its own runtime dependencies; project-specific packages in `requirements.txt` or `package.json` still need to be installed in each project. Automatic Git pulling, project dependency installation, custom startup commands, application deletion, and SSH/firewall toggles are not implemented. SSH/SFTP access remains managed by your existing operating system account and keys.

## Checks

```sh
python3 -m unittest discover -s tests -v
```
