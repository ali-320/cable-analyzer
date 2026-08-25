#!/usr/bin/env bash
# hotspot-start.sh — bring up the WiFi hotspot and run the provisioning web app.
#
# Called by wifi-provision.service on boot.
#
# Logic:
#   1. Wait for wlan0, kill interfering processes
#   2. List saved WiFi connections (excluding hotspot)
#   3. Try each saved connection until one works
#   4. If connected → LED on, exit
#   5. If none work → start hotspot + provisioning app
set -euo pipefail

HOTSPOT_NAME="hotspot"
HOTSPOT_SSID="PiHotspot"
HOTSPOT_PASS="raspberry"
WIFI_IF="wlan0"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$APP_DIR/../.." && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python3"
HOTSPOT_IP="192.168.4.1"

# BCM GPIO 21 = physical pin 40 (blue LED)
LED_PIN=21

led_blue_on() {
    $VENV_PYTHON -c "import RPi.GPIO as GPIO; GPIO.setmode(GPIO.BCM); GPIO.setup($LED_PIN, GPIO.OUT); GPIO.output($LED_PIN, GPIO.HIGH)" 2>/dev/null || true
}

led_off() {
    $VENV_PYTHON -c "import RPi.GPIO as GPIO; GPIO.setmode(GPIO.BCM); GPIO.setup($LED_PIN, GPIO.OUT); GPIO.output($LED_PIN, GPIO.LOW)" 2>/dev/null || true
}

kill_panel_processes() {
    pkill -f wfrespawn 2>/dev/null || true
    pkill -f wf-panel-pi 2>/dev/null || true
}

# --- Wait for wlan0 ---
echo "Waiting for $WIFI_IF to be ready ..."
for i in $(seq 1 15); do
    if nmcli device status 2>/dev/null | grep -q "$WIFI_IF"; then
        echo "$WIFI_IF is ready."
        break
    fi
    sleep 1
done

# --- Kill processes that might interfere with nmcli ---
kill_panel_processes
sleep 1

# --- Get saved WiFi connection names (exclude hotspot) ---
echo "Listing saved WiFi connections ..."
SAVED_CONNS=$(nmcli -t -f NAME,TYPE connection show 2>/dev/null \
    | grep ":802-11-wireless$" \
    | cut -d: -f1 \
    | grep -v "^${HOTSPOT_NAME}$" || true)

CONNECTED=false

if [ -n "$SAVED_CONNS" ]; then
    echo "Saved WiFi connections (excluding hotspot):"
    echo "$SAVED_CONNS"

    while IFS= read -r CONN_NAME; do
        [ -z "$CONN_NAME" ] && continue
        echo "Trying to connect to saved connection '$CONN_NAME' ..."

        kill_panel_processes

        if nmcli con up "$CONN_NAME" 2>/dev/null; then
            sleep 2
            CURRENT_IP=$(hostname -I | awk '{print $1}')
            echo "Connected to '$CONN_NAME'. IP: ${CURRENT_IP:-none}"

            if [ -n "$CURRENT_IP" ] && [ "$CURRENT_IP" != "$HOTSPOT_IP" ]; then
                echo "Success! Connected to real WiFi."
                CONNECTED=true
                break
            else
                echo "Got hotspot IP, treating as failure. Disconnecting ..."
                nmcli con down "$CONN_NAME" 2>/dev/null || true
            fi
        else
            echo "Failed to connect to '$CONN_NAME'."
        fi
    done <<< "$SAVED_CONNS"
fi

# --- Check result ---
if [ "$CONNECTED" = true ]; then
    echo "WiFi connected. Turning LED on."
    led_blue_on
    exit 0
fi

# --- No known WiFi — start the hotspot ---
echo "No saved WiFi network available. Starting hotspot provisioning ..."
led_off

# Ensure hotspot profile exists
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

# Bring up hotspot, cycle to fix SSID broadcast
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
