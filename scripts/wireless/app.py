#!/usr/bin/env python3
"""
WiFi Provisioning Web Server for Raspberry Pi
==============================================
Serves a form (over the Pi's own hotspot) that collects WiFi credentials,
then switches the Pi from hotspot (AP) mode to client mode using them.

Assumes the hotspot was created with NetworkManager (nmcli), which is the
default network stack on Raspberry Pi OS Bookworm and later. If you set
your hotspot up the classic way with hostapd + dnsmasq instead, the
switch_to_client() function needs to be rewritten (see notes at the bottom).

Requires: pip install flask   (or: sudo apt install python3-flask)
"""

import subprocess
import threading
import time
import logging

from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("wifi-provision")

# --- CONFIGURE THESE FOR YOUR SETUP ---
HOTSPOT_CONNECTION_NAME = "Hotspot"   # check yours with: nmcli connection show
WIFI_INTERFACE = "wlan0"
SWITCH_DELAY_SECONDS = 2              # lets the HTTP response reach the phone first


def run(cmd):
    log.info("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True)


def switch_to_client(ssid: str, password: str):
    """Runs in a background thread so the HTTP response can be sent first."""
    time.sleep(SWITCH_DELAY_SECONDS)

    log.info("Bringing down hotspot '%s'", HOTSPOT_CONNECTION_NAME)
    run(["nmcli", "connection", "down", HOTSPOT_CONNECTION_NAME])

    log.info("Attempting to connect to '%s'", ssid)
    if password:
        result = run([
            "nmcli", "device", "wifi", "connect", ssid,
            "password", password, "ifname", WIFI_INTERFACE
        ])
    else:
        result = run([
            "nmcli", "device", "wifi", "connect", ssid, "ifname", WIFI_INTERFACE
        ])

    if result.returncode == 0:
        log.info("Connected successfully to %s", ssid)
        return

    log.warning("Failed to connect to %s: %s", ssid, result.stderr.strip())
    log.info("Reverting to hotspot mode so the user can retry")
    run(["nmcli", "connection", "up", HOTSPOT_CONNECTION_NAME])


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/connect", methods=["POST"])
def connect():
    ssid = request.form.get("ssid", "").strip()
    password = request.form.get("password", "")

    if not ssid:
        return jsonify({"ok": False, "error": "Network name is required."}), 400
    if password and not (8 <= len(password) <= 63):
        return jsonify({
            "ok": False,
            "error": "Password must be 8-63 characters, or left blank for an open network."
        }), 400

    threading.Thread(target=switch_to_client, args=(ssid, password), daemon=True).start()

    return jsonify({
        "ok": True,
        "message": (
            "Got it. The Pi will switch to that network in a couple of seconds "
            "and the hotspot will disappear. Reconnect your phone to your normal "
            "WiFi, then check that the Pi has joined the network."
        )
    })


if __name__ == "__main__":
    # host="0.0.0.0" so it's reachable at the Pi's hotspot IP, not just localhost.
    app.run(host="0.0.0.0", port=5000)


# -----------------------------------------------------------------------
# If your hotspot was built with hostapd + dnsmasq instead of NetworkManager,
# switch_to_client() needs to instead:
#   1. sudo systemctl stop hostapd dnsmasq
#   2. Write a network={ ssid=... psk=... } block into
#      /etc/wpa_supplicant/wpa_supplicant.conf
#   3. sudo wpa_cli -i wlan0 reconfigure   (or restart dhcpcd/wpa_supplicant)
#   4. On failure, restore the AP-mode config for wlan0 and restart
#      hostapd + dnsmasq.
# This is more fragile because you're juggling the same interface between
# two different services - nmcli's approach is a lot less error-prone.
# -----------------------------------------------------------------------
