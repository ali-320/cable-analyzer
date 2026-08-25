#!/usr/bin/env bash
# install-service.sh — install the WiFi Provisioning systemd service.
#
# Usage (on the Pi):
#   cd project-ali/scripts/wireless
#   bash install-service.sh
#   sudo systemctl start wifi-provision
#
# To see live terminal output:
#   tmux attach -t wifi-prov
#
# To detach from tmux without stopping the service:
#   Ctrl+B then D
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/wifi-provision.service"
DEST="/etc/systemd/system/wifi-provision.service"

# ── install dependencies ──────────────────────────────────────────
echo "Installing dependencies ..."
sudo apt-get update -qq
sudo apt-get install -y dnsmasq tmux

# ── install Flask if missing ──────────────────────────────────────
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
if [ -d "$PROJECT_DIR/.venv" ]; then
    "$PROJECT_DIR/.venv/bin/pip" install flask 2>/dev/null || true
else
    echo "No .venv found at $PROJECT_DIR/.venv — make sure Flask is installed."
fi

# ── copy service file ─────────────────────────────────────────────
echo "Copying service file to $DEST"
sudo cp "$SERVICE_FILE" "$DEST"
sudo systemctl daemon-reload

echo ""
echo "Service installed.  To start now:"
echo "  sudo systemctl start wifi-provision"
echo ""
echo "To see live terminal output:"
echo "  tmux attach -t wifi-prov"
echo ""
echo "To detach (service keeps running):"
echo "  Press Ctrl+B then D"
echo ""
echo "To stop:"
echo "  sudo systemctl stop wifi-provision"
echo ""
echo "To enable on boot:"
echo "  sudo systemctl enable wifi-provision"
echo ""
echo "After WiFi is configured, to run the cable analyzer:"
echo "  sudo systemctl start radwi-continuous"
echo "  sudo systemctl stop wifi-provision"
