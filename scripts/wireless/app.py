#!/usr/bin/env python3
"""
WiFi Provisioning Web Server for Raspberry Pi
==============================================
Serves a form (over the Pi's own hotspot) that collects WiFi credentials,
then switches the Pi from hotspot (AP) mode to client mode using them.

Flow:
  1. User submits SSID + password
  2. Kill wfrespawn and wf-panel-pi to prevent auth popups
  3. Hotspot goes down, Pi tries to connect
  4. Hotspot comes back up, user reconnects and checks /status
  5. If success → user clicks OK → hotspot goes down, Pi connects to WiFi
  6. If failure → user sees error, can retry

Assumes the hotspot was created with NetworkManager (nmcli).
Requires: pip install flask
"""

import subprocess
import threading
import time
import logging

try:
    import RPi.GPIO as GPIO
    LED_PIN = 21  # BCM GPIO 21 = physical pin 40
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(LED_PIN, GPIO.OUT, initial=GPIO.LOW)
    HAS_GPIO = True
except (ImportError, RuntimeError):
    HAS_GPIO = False

from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("wifi-provision")

# --- CONFIGURE THESE FOR YOUR SETUP ---
HOTSPOT_CONNECTION_NAME = "hotspot"   # check yours with: nmcli connection show
WIFI_INTERFACE = "wlan0"
SWITCH_DELAY_SECONDS = 2              # lets the HTTP response reach the phone first

# --- SHARED STATE ---
connection_status = None   # None = idle, True = success, False = failure
pending_ssid = None        # SSID to connect to after user confirms
pending_password = None    # password to connect to after user confirms


def run(cmd):
    log.info("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True)


def led_on():
    """Turn LED on (solid — internet connected)."""
    if HAS_GPIO:
        GPIO.output(LED_PIN, GPIO.HIGH)


def led_off():
    """Turn LED off."""
    if HAS_GPIO:
        GPIO.output(LED_PIN, GPIO.LOW)


def led_blink_twice():
    """Blink LED twice to indicate failure."""
    if not HAS_GPIO:
        return
    for _ in range(2):
        GPIO.output(LED_PIN, GPIO.HIGH)
        time.sleep(0.3)
        GPIO.output(LED_PIN, GPIO.LOW)
        time.sleep(0.3)


def kill_panel_processes():
    """Kill wfrespawn and wf-panel-pi to prevent auth popups."""
    log.info("Killing wfrespawn and wf-panel-pi")
    run(["pkill", "-f", "wfrespawn"])
    run(["pkill", "-f", "wf-panel-pi"])


def try_connect(ssid: str, password: str):
    """Runs in a background thread. Tries to connect, then brings hotspot back."""
    global connection_status

    time.sleep(SWITCH_DELAY_SECONDS)
    kill_panel_processes()

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
            "nmcli", "device", "wifi", "connect", ssid,
            "ifname", WIFI_INTERFACE
        ])

    if result.returncode == 0:
        log.info("Connected successfully to %s", ssid)
        connection_status = True
        led_on()
    else:
        log.warning("Failed to connect to %s: %s", ssid, result.stderr.strip())
        connection_status = False
        led_blink_twice()

        log.info("Deleting failed connection profile for '%s'", ssid)
        run(["nmcli", "connection", "delete", ssid])

    # Bring hotspot back so the user can check the result
    log.info("Bringing hotspot back up")
    run(["nmcli", "connection", "up", HOTSPOT_CONNECTION_NAME])


def connect_to_wifi(ssid: str, password: str):
    """Runs in a background thread. Connects to the confirmed WiFi and shuts down hotspot."""
    time.sleep(SWITCH_DELAY_SECONDS)
    kill_panel_processes()

    log.info("Bringing down hotspot '%s'", HOTSPOT_CONNECTION_NAME)
    run(["nmcli", "connection", "down", HOTSPOT_CONNECTION_NAME])

    log.info("Connecting to confirmed WiFi '%s'", ssid)
    result = run(["nmcli", "con", "up", ssid])

    if result.returncode == 0:
        log.info("Connected successfully to %s", ssid)
        led_on()
    else:
        log.warning("Failed to connect to %s: %s", ssid, result.stderr.strip())
        led_blink_twice()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/connect", methods=["POST"])
def connect():
    """User submits credentials. We try them, then bring hotspot back."""
    global connection_status, pending_ssid, pending_password

    ssid = request.form.get("ssid", "").strip()
    password = request.form.get("password", "")

    if not ssid:
        return jsonify({"ok": False, "error": "Network name is required."}), 400
    if password and not (8 <= len(password) <= 63):
        return jsonify({
            "ok": False,
            "error": "Password must be 8-63 characters, or left blank for an open network."
        }), 400

    # Reset state and store pending credentials
    connection_status = None
    pending_ssid = ssid
    pending_password = password

    threading.Thread(target=try_connect, args=(ssid, password), daemon=True).start()

    return jsonify({
        "ok": True,
        "message": (
            "Credentials received. The Pi will try to connect and then bring "
            "the hotspot back. Reconnect your phone to the PiHotspot and "
            "check the result in a few seconds."
        )
    })


@app.route("/status", methods=["GET"])
def status():
    """Frontend polls this after reconnecting to the hotspot."""
    global connection_status

    if connection_status is None:
        return jsonify({"ok": True, "status": "pending"})
    elif connection_status is True:
        return jsonify({"ok": True, "status": "success", "ssid": pending_ssid})
    else:
        return jsonify({"ok": True, "status": "error"})


@app.route("/confirm", methods=["POST"])
def confirm():
    """User confirmed. Bring down hotspot and connect to WiFi permanently."""
    global connection_status

    ssid = pending_ssid
    password = pending_password

    if not ssid:
        return jsonify({"ok": False, "error": "No pending connection to confirm."}), 400

    connection_status = None

    threading.Thread(target=connect_to_wifi, args=(ssid, password), daemon=True).start()

    return jsonify({
        "ok": True,
        "message": "Connecting to WiFi. The hotspot will go down shortly."
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
