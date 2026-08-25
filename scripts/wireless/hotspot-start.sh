#!/usr/bin/env bash
# hotspot-start.sh — bring up the WiFi hotspot and run the provisioning web app.
#
# Called by wifi-provision.service on boot.
set -euo pipefail

HOTSPOT_NAME="hotspot"
HOTSPOT_SSID="PiHotspot"
HOTSPOT_PASS="raspberry"
WIFI_IF="wlan0"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$APP_DIR/../.." && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python3"

echo "=== WiFi Provision: checking hotspot profile ==="

# Create the hotspot profile if it doesn't exist
if ! nmcli -t -f NAME connection show | grep -qx "$HOTSPOT_NAME"; then
    echo "Creating hotspot profile '$HOTSPOT_NAME' ..."
    nmcli connection add \
        con-name "$HOTSPOT_NAME" \
        ifname "$WIFI_IF" \
        type wifi \
        ssid "$HOTSPOT_SSID"
    nmcli connection modify "$HOTSPOT_NAME" ipv4.addresses 192.168.4.1/24
    nmcli connection modify "$HOTSPOT_NAME" ipv4.method shared
    nmcli connection modify "$HOTSPOT_NAME" wifi-sec.key-mgmt wpa-psk
    nmcli connection modify "$HOTSPOT_NAME" wifi-sec.psk "$HOTSPOT_PASS"
    nmcli connection modify "$HOTSPOT_NAME" 802-11-wireless.mode ap
    nmcli connection modify "$HOTSPOT_NAME" 802-11-wireless.band bg
    nmcli connection modify "$HOTSPOT_NAME" 802-11-wireless.channel 7
    echo "Hotspot profile created."
else
    echo "Hotspot profile '$HOTSPOT_NAME' already exists."
fi

# Ensure dnsmasq is configured for DHCP on the hotspot
if [ ! -f /etc/dnsmasq.conf.orig ]; then
    echo "Configuring dnsmasq ..."
    sudo mv /etc/dnsmasq.conf /etc/dnsmasq.conf.orig 2>/dev/null || true
    sudo tee /etc/dnsmasq.conf > /dev/null << 'DNSEOF'
interface=wlan0
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h
DNSEOF
    sudo systemctl enable dnsmasq 2>/dev/null || true
    sudo systemctl restart dnsmasq 2>/dev/null || true
fi

# Wait for wlan0 to be ready
echo "Waiting for $WIFI_IF to be ready ..."
for i in $(seq 1 10); do
    if nmcli device status | grep -q "$WIFI_IF"; then
        echo "$WIFI_IF is ready."
        break
    fi
    sleep 1
done

# Bring up the hotspot, cycle it to ensure SSID broadcasts correctly
echo "Bringing up hotspot '$HOTSPOT_NAME' (first time) ..."
nmcli connection up "$HOTSPOT_NAME"
sleep 5
echo "Cycling hotspot to fix SSID broadcast ..."
nmcli connection down "$HOTSPOT_NAME"
sleep 2
nmcli connection up "$HOTSPOT_NAME"
sleep 2
echo "Hotspot is up. SSID: $HOTSPOT_SSID"

# Kill any existing provision app session
tmux kill-session -t wifi-prov 2>/dev/null || true

# Run the Flask provisioning app
echo "Starting WiFi provisioning web app on port 5000 ..."
cd "$APP_DIR"
exec tmux new-session -d -s wifi-prov \
    "$VENV_PYTHON $APP_DIR/app.py --host 0.0.0.0 --port 5000"
