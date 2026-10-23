#!/bin/sh
# Bootstrap from a cloned checkout, including on servers without Python installed.
set -eu
if [ "$(id -u)" -ne 0 ]; then
    echo 'Run sudo sh install.sh on your Ubuntu VPS.' >&2
    exit 1
fi
if [ ! -f /etc/debian_version ] || [ ! -d /run/systemd/system ]; then
    echo 'Hosty requires an Ubuntu server running systemd.' >&2
    exit 1
fi
hosty_source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! command -v python3 >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3
fi
cd "$hosty_source_dir"
exec python3 -m hosty.cli install
