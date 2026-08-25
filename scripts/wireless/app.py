#!/usr/bin/env python3
"""
WiFi Provisioning Web Server for Raspberry Pi
==============================================
3-page flow:
  1. index.html  — enter SSID + password
  2. correct.html — credentials OK, click OK to connect
  3. wrong.html  — credentials wrong, retry

Flow:
  User submits form → server tries credentials in background → hotspot comes back
  User reconnects to hotspot → reloads → sees correct.html or wrong.html
  correct.html → OK → hotspot down, Pi connects to WiFi
  wrong.html → retry → back to index.html
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
connection_status = None   # None = idle/pending, True = success, False = failure
pending_ssid = None
pending_password = None


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
        log.info("Connected successfully to %s", ssid)
        connection_status = True
        led_on()
    else:
        log.warning("Failed to connect to %s: %s", ssid, result.stderr.strip())
        connection_status = False
        led_blink_twice()
        log.info("Deleting failed connection profile for '%s'", ssid)
        run(["nmcli", "connection", "delete", ssid])

    log.info("Bringing hotspot back up")
    run(["nmcli", "connection", "up", HOTSPOT_CONNECTION_NAME])


def connect_to_wifi(ssid, password):
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


# ── ROUTES ──────────────────────────────────────────────────────

@app.route("/")
def index():
    if connection_status is True:
        return redirect(url_for("correct"))
    if connection_status is False:
        return redirect(url_for("wrong"))
    if connection_status is None and pending_ssid is not None:
        return redirect(url_for("status"))
    return render_template("index.html")


@app.route("/status")
def status():
    if connection_status is True:
        return redirect(url_for("correct"))
    if connection_status is False:
        return redirect(url_for("wrong"))
    # Still pending — auto-reload every 3 seconds
    return render_template("status.html")


@app.route("/correct")
def correct():
    return render_template("correct.html", ssid=pending_ssid or "")


@app.route("/wrong")
def wrong():
    return render_template("wrong.html", ssid=pending_ssid or "")


@app.route("/connect", methods=["POST"])
def connect():
    global connection_status, pending_ssid, pending_password

    ssid = request.form.get("ssid", "").strip()
    password = request.form.get("password", "")

    if not ssid:
        return redirect(url_for("index"))
    if password and not (8 <= len(password) <= 63):
        return redirect(url_for("index"))

    connection_status = None
    pending_ssid = ssid
    pending_password = password

    threading.Thread(target=try_connect, args=(ssid, password), daemon=True).start()

    return redirect(url_for("status"))


@app.route("/confirm", methods=["POST"])
def confirm():
    global connection_status, pending_ssid, pending_password

    ssid = pending_ssid
    password = pending_password

    if not ssid:
        return redirect(url_for("index"))

    connection_status = None
    pending_ssid = None
    pending_password = None

    threading.Thread(target=connect_to_wifi, args=(ssid, password), daemon=True).start()

    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
