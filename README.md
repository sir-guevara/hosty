# Hosty

A small hosting panel for your personal tools. A responsive dashboard, live server metrics, Python / Bun / static apps, systemd service controls, Nginx routing, logs, and Let's Encrypt certificates. No Python dependencies are required to run the panel.

## Try locally

```sh
python3 -m hosty.cli serve
```

Open http://127.0.0.1:8787 and enter the token printed in your terminal. Apps are saved in `~/.local/share/hosty/apps.json`. Local mode saves configurations; deployment and service controls require an Ubuntu installation. Linux supplies system memory and server uptime; on macOS memory is unavailable and uptime measures the panel process. CPU is normalized one-minute load average, not instantaneous CPU utilization.

## Development mode

```sh
python3 -m hosty.cli serve --dev
```

Add an app with its local directory; the domain is optional. Hosty starts the app and displays a clickable `http://localhost:9000` address (each app gets its own port). Existing apps can be started using the play button. Start, stop, restart, and logs work without Ubuntu, Nginx, or DNS. Python and Bun use the runtime conventions below; HTML works with just an `index.html`.

Apps run as your current user and bind to loopback (Bun apps must honor `HOST` and `PORT`). Local addresses refer to the machine running Hosty. SSL is disabled in dev mode. Apps stop when you exit the panel; use the play button to start them after relaunching. Do not run dev mode alongside a production Hosty installation.

## Install on a fresh Ubuntu VPS

Clone this repository on your VPS, then run:

```sh
sudo python3 -m hosty.cli install
```

Alternatively install this package in a virtual environment with `pip install .`, then run `sudo /path/to/venv/bin/hosty install`.

The installer installs Nginx, Python venv support, Certbot, and the Hosty systemd service. It binds the panel to localhost. Access it through an SSH tunnel:

```sh
ssh -L 8787:127.0.0.1:8787 your-user@your-server
```

Open http://localhost:8787. Read your token with `sudo cat /etc/hosty.env`. Keep the token private: it authorizes deployment operations on your server. The panel service runs as root to configure Nginx, systemd, and certificates; application services run as the unprivileged `hosty` user. Keep the panel on loopback behind the SSH tunnel.

A published curl bootstrap URL is not included; use a clone from your own trusted repository. Installation is intended for a fresh server with no conflicting Nginx configuration.

## Add an app

1. Upload your project using your existing SSH/SFTP account, for example to `/var/www/my-app`.
2. Give the `hosty` user read and execute access to the project and its parent directories. App data directories must be writable by that user.
3. Create a DNS A record for your domain pointing to the VPS public IP. The dashboard displays the host's resolved address, which may be private; check your VPS provider for the public IP.
4. Select **New application**, enter a name, domain, runtime, and existing directory (an absolute path or `~/my-app`). `~` expands to the home directory of the user running the Hosty panel; the installed root service uses `/root`.

Runtime conventions:

- **Python:** create `.venv` in the project, install your dependencies including `uvicorn`, and expose an ASGI `app` in `main.py`. Hosty runs `.venv/bin/python -m uvicorn main:app`.
- **Bun:** install Bun in a system-wide executable location first. Provide a `start` script in `package.json`. The application must honor `HOST=127.0.0.1` and the assigned `PORT`.
- **HTML:** place `index.html` and assets in the directory. The initial implementation serves these using Python's basic HTTP server behind Nginx; use this for small personal sites.

Hosty allocates ports starting at 9000, writes a systemd unit per app, and configures Nginx on port 80. Ensure ports 80 and 443 are reachable. After DNS resolves, select **Enable HTTPS** and supply your email to accept Let's Encrypt's terms. Certbot handles certificate renewal through its Ubuntu timer. The SSL badge records successful issuance; it does not monitor certificate expiration.

To update a project, upload or pull your new code, install changed dependencies, then restart it in Hosty. Automatic Git pulling, dependency installation, custom startup commands, application deletion, and SSH/firewall toggles are not implemented. SSH/SFTP access remains managed by your existing operating system account and keys.

## Checks

```sh
python3 -m unittest discover -s tests -v
```
