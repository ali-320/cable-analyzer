# Cable-Analyzer — Architecture at a Glance

Minimal, abstract reference. One hardware diagram + one diagram per run mode.
For deep detail see `workflow.md`, `Features-plain.md`, and `dependencies.md`.

---

## 1. Hardware Architecture (current `--voltage` build)

The charger, the cable under test, the measurement board, and the phone form
one series power path; the Pi reads the sensor and drives the display/LED.

```mermaid
flowchart LR
    CHG["USB-C Charger"] --> CBL["Cable under test<br/>(VBUS + GND through the shunt)"]
    CBL --> INA["INA219 sensor<br/>(I²C 0x40, 0.1 Ω shunt)"]
    INA --> PH["Phone<br/>(acts as the load)"]

    INA -- "I²C (GPIO2/3)" --> PI["Raspberry Pi Zero W"]
    PI -- "SPI" --> DISP["ST7735S display"]
    PI -- "GPIO 21" --> LED["Blue LED<br/>(on = internet)"]
    PI -. "Wi-Fi" .-> NET["Battery.ai ingest API"]
```

**Key points**
- CH224K / e-load hardware is **removed**: the charger's own voltage is used
  directly, and the phone's natural charging current is the load.
- Samples are bucketed into real-time **voltage classes** (1 V wide), so a
  charger switching 5 V → 9 V → 12 V simply fills different classes.
- Only the INA219 (I²C), the display (SPI), and one LED (GPIO 21) touch the Pi.

---

## 2. Software Architecture — production mode (`--voltage --continuous`)

The mode the boot service runs. Loop forever: sample → grade → store → sync.

```mermaid
flowchart TB
    subgraph BOOT["radwi-continuous.service (boot)"]
        TMUX["tmux session 'radwi'"] --> LOOP
    end

    LOOP["run_continuous() — main.py"] --> S["sampler.py<br/>(fixed-rate sampling + validation)"]
    S -- "samples" --> ST["session.py<br/>(state machine: NO_PHONE / CHARGING /<br/>CHARGED / NO_SOURCE / FAULT)"]

    S -- "n_busy >= 3000" --> M["features/metrics.py<br/>(22 features per voltage class)"]
    M --> R["analysis/rules.py<br/>(grade A–F, tags, confidence)"]
    R --> U["ui/ (CLI text + ST7735S display)"]
    R --> DB[("storage.py<br/>SQLite sessions.db + CSV")]
    DB --> RM["telemetry/remote.py<br/>(sync to Battery.ai)"]
    RM -- "offline: queue locally" --> DB
    LOOP -- "restart cycle" --> S
```

**Cycle:** ~3,000 busy readings (~2 min @ 25 Hz) → verdict → save → sync → repeat.

---

## 3. Other Run Modes

### 3.1 `--self-check`
Hardware sanity check only, exits with code 0/1.

```mermaid
flowchart LR
    A["main.py"] --> B["build_hardware()"]
    B --> C["run_self_check()"]
    C --> D{"INA219 responds?<br/>bus voltage present?<br/>PWR_OK?"}
    D -- "all pass" --> E["print PASS · exit 0"]
    D -- "any fail" --> F["print FAIL · exit 1"]
```

### 3.2 `--simulate` (and `--demo`)
Synthetic hardware replaces real sensors; the whole pipeline runs on a PC.

```mermaid
flowchart LR
    A["main.py --simulate"] --> B["hardware/sim.py<br/>(synthetic INA219 / phone)"]
    B --> S["same sampler → metrics → rules pipeline"]
    S --> V["verdict + storage (no real I/O)"]
```

### 3.3 `--manual`
CH224K still attached but GPIO-unwired: operator changes voltages by hand.

```mermaid
flowchart LR
    A["main.py --manual --mode probe"] --> B["prompt operator<br/>to set 5/9/12 V"]
    B --> C["VERIFICATION window per voltage<br/>(is the rail supported?)"]
    C --> D["split measurement budget<br/>across supported voltages"]
    D --> E["combined features → verdict"]
```

### 3.4 Default `--mode auto` / `probe` / `charge`
One-shot session (probe, then charge monitoring), then exit.

```mermaid
flowchart TB
    A["main.py (default mode)"] --> B{"--mode?"}
    B -- "auto" --> C["run_probe() then run_charge()"]
    B -- "probe" --> D["run_probe() only"]
    B -- "charge" --> E["run_charge() only"]
    C --> F["features → verdict → save + sync → exit"]
    D --> F
    E --> F
```

### 3.5 WiFi provisioning service (`wifi-provision.service`)
Independent boot service that guarantees the Pi always has connectivity.

```mermaid
flowchart TB
    B["boot"] --> H["hotspot-start.sh"]
    H --> W{"wlan0 up?<br/>saved WiFi connects?"}
    W -- "yes" --> L["LED on — done"]
    W -- "no" --> HS["PiHotspot AP (192.168.4.1)<br/>+ dnsmasq DHCP"]
    HS --> FL["Flask app :5000<br/>(tmux 'wifi-prov')"]
    FL --> F["user enters SSID/password<br/>in browser → nmcli connect"]
    F -- "bad credentials" --> HS
    F -- "success" --> L
```
