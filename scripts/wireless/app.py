#!/usr/bin/env python3
"""
WiFi Provisioning Web Server for Raspberry Pi
==============================================
Flow:
  1. User enters SSID + password on index.html
  2. Hotspot goes down, Pi tries to connect
  3. If CORRECT → LED on, hotspot stays down, display shows new WiFi. Done.
  4. If WRONG → hotspot comes back up, user reconnects, sees wrong.html, retries.
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

from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("wifi-provision")

# --- CONFIGURE THESE FOR YOUR SETUP ---
HOTSPOT_CONNECTION_NAME = "hotspot"
WIFI_INTERFACE = "wlan0"
SWITCH_DELAY_SECONDS = 2

# --- SHARED STATE ---
connection_status = None   # None = idle, True = success, False = failure
pending_ssid = None        # kept on failure so wrong.html can pre-fill


def run(cmd):
    log.info("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True)


def led_on():
    if HAS_GPIO:
        GPIO.output(LED_PIN, GPIO.HIGH)


def led_off():
    if HAS_GPIO:
        GPIO.output(LED_PIN, GPIO.LOW)


def led_blink_twice():
    if not HAS_GPIO:
        return
    for _ in range(2):
        GPIO.output(LED_PIN, GPIO.HIGH)
        time.sleep(0.3)
        GPIO.output(LED_PIN, GPIO.LOW)
        time.sleep(0.3)


def kill_panel_processes():
    log.info("Killing wfrespawn and wf-panel-pi")
    run(["pkill", "-f", "wfrespawn"])
    run(["pkill", "-f", "wf-panel-pi"])


def try_connect(ssid, password):
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
        # SUCCESS — hotspot stays down, LED on, we're done
        log.info("Connected successfully to %s", ssid)
        connection_status = True
        led_on()
        return

    # FAILURE — bring hotspot back up for retry
    log.warning("Failed to connect to %s: %s", ssid, result.stderr.strip())
    connection_status = False
    led_blink_twice()

    log.info("Deleting failed connection profile for '%s'", ssid)
    run(["nmcli", "connection", "delete", ssid])

    log.info("Bringing hotspot back up")
    run(["nmcli", "connection", "up", HOTSPOT_CONNECTION_NAME])


# ── ROUTES ──────────────────────────────────────────────────────

@app.route("/")
def index():
    # If last attempt failed, show retry page
    if connection_status is False:
        return render_template("wrong.html", ssid=pending_ssid or "")
    # Show the form (idle or success — either way, show form)
    return render_template("index.html")


@app.route("/wait", methods=["POST"])
def connect():
    global connection_status, pending_ssid

    ssid = request.form.get("ssid", "").strip()
    password = request.form.get("password", "")

    if not ssid:
        return redirect(url_for("index"))

    connection_status = None
    pending_ssid = ssid

    threading.Thread(target=try_connect, args=(ssid, password), daemon=True).start()

    # Show a brief "connecting" page that auto-reloads
    return render_template("wait.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
