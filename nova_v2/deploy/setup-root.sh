#!/usr/bin/env bash
# One-time setup that needs root. Run once on the server:
#     sudo bash setup-root.sh [owner]
# Everything after this runs as the owner (default: the user who ran sudo).
set -euo pipefail

OWNER="${1:-${SUDO_USER:?run with sudo, or pass the owner as an argument}}"
DIR=/srv/nova

[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }

# Docker without sudo, for the owner.
usermod -aG docker "$OWNER"

# `tailscale serve` without sudo, for the owner.
tailscale set --operator="$OWNER"

# The firewall denies incoming by default. Let the tailnet in; the LAN stays shut.
ufw allow in on tailscale0 comment 'tailnet'

# The shared folder. Anyone added to the nova group can read and run it:
#     sudo usermod -aG nova <user>      (then they log in again)
groupadd -f nova
usermod -aG nova "$OWNER"
mkdir -p "$DIR"
chown "$OWNER":nova "$DIR"
chmod 2770 "$DIR"

# compose.yml runs Qwen now, with the same image and volumes, so the hand-run
# container goes. Its weights stay in the qwen38-models volume.
docker rm -f qwen38-dev 2>/dev/null || true

echo
echo "Done. $OWNER is in the docker and nova groups (takes effect on the next login),"
echo "can run tailscale serve, and owns $DIR. The tailnet can reach this machine."
