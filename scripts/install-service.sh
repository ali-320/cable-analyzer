#!/usr/bin/env bash
# install-service.sh — install the RADWI continuous-mode systemd service.
#
# Usage (on the Pi):
#   cd project-ali
#   bash scripts/install-service.sh
#   sudo systemctl start radwi-continuous
#
# To see live terminal output:
#   tmux attach -t radwi
#
# To detach from tmux without stopping the service:
#   Ctrl+B then D
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/radwi-continuous.service"
DEST="/etc/systemd/system/radwi-continuous.service"

# ── install tmux if missing ───────────────────────────────────────
if ! command -v tmux &>/dev/null; then
    echo "Installing tmux …"
    sudo apt-get update -qq && sudo apt-get install -y tmux
fi

# ── copy service file ─────────────────────────────────────────────
echo "Copying service file to $DEST"
sudo cp "$SERVICE_FILE" "$DEST"
sudo systemctl daemon-reload

echo ""
echo "Service installed.  To start now:"
echo "  sudo systemctl start radwi-continuous"
echo ""
echo "To see live terminal output:"
echo "  tmux attach -t radwi"
echo ""
echo "To detach (service keeps running):"
echo "  Press Ctrl+B then D"
echo ""
echo "To stop:"
echo "  sudo systemctl stop radwi-continuous"
echo ""
echo "To enable on boot:"
echo "  sudo systemctl enable radwi-continuous"
