# cable-analyzer — Dependencies & Setup (abstract)

What must exist before the code runs on the Pi. Deep detail lives in the
other docs; this is the checklist.

---

## 1. OS packages (apt, once)

```bash
sudo apt update
sudo apt install -y python3-venv python3-smbus i2c-tools network-manager \
                    tmux dnsmasq python3-rpi.gpio python3-spidev \
                    python3-pil fonts-dejavu-core
```

Enable I²C + SPI via `raspi-config`, then verify:
`ls /dev/i2c-1 /dev/spidev0.0` and `i2cdetect -y 1` (must show `40`).

## 2. Virtual environment

Create it **with system-site-packages** — apt-provided `RPi.GPIO`, `spidev`,
and `PIL` are only visible inside the venv this way (also required by the
hotspot LED script, which runs the venv Python):

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install --upgrade pip
pip install pi-ina219 flask adafruit-blinka
```

(`pi-ina219` pulls `smbus2`; `tomllib` is stdlib on Python 3.11+. Do not pip
install Pillow — the apt package already provides it.)

## 3. Runtime secrets & data

| Item | Purpose |
|---|---|
| `.env` (copy from `.env.example`) | `BASE`, `CABLE_INGEST_TOKEN`, `CABLE_INGEST_DEVICE_ID` for remote sync — `chmod 600 .env` |
| `data/calibration.json` | Fixture-resistance baseline; carry over from the old install or regenerate with `scripts/calibrate.py` |

## 4. Service files — edit before installing

Both `.service` files hardcode a user and project path. Fix them to match the
actual install location **before** running the installers:

| File | Lines to fix |
|---|---|
| `scripts/radwi-continuous.service` | `User=`, `Group=`, `WorkingDirectory=`, and the venv path inside `ExecStart=` |
| `scripts/wireless/wifi-provision.service` | `WorkingDirectory=` and the script path in `ExecStart=` (`User=root` stays) |

## 5. Shell scripts to run (once, in order)

```bash
# 1. Analyzer service
bash scripts/install-service.sh
sudo systemctl enable --now radwi-continuous

# 2. WiFi provisioning service
bash scripts/wireless/install-service.sh
sudo systemctl enable --now wifi-provision
```

The installers copy the service files, install `tmux`/`dnsmasq` if missing,
and reload systemd.

## 6. First-time hotspot setup (one-time, from `scripts/wireless/hotspot-setup.txt`)

Run on the Pi before the provisioning service can ever fall back to hotspot
mode:

```bash
sudo nmcli con add con-name hotspot ifname wlan0 type wifi ssid PiHotspot
sudo nmcli con mod hotspot ipv4.addresses 192.168.4.1/24
sudo nmcli con mod hotspot ipv4.method shared
sudo nmcli con mod hotspot wifi-sec.key-mgmt wpa-psk
sudo nmcli con mod hotspot wifi-sec.psk "raspberry"
sudo nmcli con mod hotspot 802-11-wireless.mode ap
sudo nmcli con mod hotspot 802-11-wireless.band bg
sudo nmcli con mod hotspot 802-11-wireless.channel 7
sudo nmcli con mod hotspot 802-11-wireless-security.pmf 1
```

Plus dnsmasq DHCP config (`interface=wlan0`, `dhcp-range=192.168.4.2,…`) and
`sudo systemctl enable --now dnsmasq` — see the full file for the exact blocks.

## 7. Verify

```bash
python -m src.main --voltage --self-check     # hardware sanity, exit 0
python -m src.main --voltage --continuous     # production loop (foreground)
tmux attach -t radwi                          # what the service is doing
```
