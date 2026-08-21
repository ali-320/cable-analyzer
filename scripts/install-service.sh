#!/usr/bin/env bash
# Install the RADWI continuous-mode systemd service on a Raspberry Pi.
# Run this once after the first deployment:
#   cd project-ali
#   bash scripts/install-service.sh
#
# To manage the service:
#   sudo systemctl start radwi-continuous
#   sudo systemctl stop radwi-continuous
#   sudo systemctl status radwi-continuous
#   sudo journalctl -u radwi-continuous -f

set -euo pipefail

SERVICE_FILE="$(dirname "$0")/radwi-continuous.service"
TARGET="/etc/systemd/system/radwi-continuous.service"

if [ ! -f "$SERVICE_FILE" ]; then
    echo "ERROR: $SERVICE_FILE not found" >&2
    exit 1
fi

echo "Installing RADWI continuous service ..."
sudo cp "$SERVICE_FILE" "$TARGET"
sudo systemctl daemon-reload
sudo systemctl enable radwi-continuous

echo ""
echo "Done. The service is installed and enabled."
echo ""
echo "  To start now:   sudo systemctl start radwi-continuous"
echo "  To stop:         sudo systemctl stop radwi-continuous"
echo "  To see logs:     sudo journalctl -u radwi-continuous -f"
echo "  Auto-starts on boot."
