# Elegoo Centauri Carbon — Filament Drying Manager (SDCP v3.0.0)

A robust Python application for real-time control, thermal monitoring, and filament moisture removal (**PETG, PLA, ASA/ABS, TPU, Nylon, and Custom**) on the **Elegoo Centauri Carbon 1** 3D printer (stock firmware or OpenCentauri), communicating directly via the native **Elegoo SDCP V3.0.0** protocol over WebSocket.
<img width="1440" height="1152" alt="Screenshot 2026-09-15 at 01-55-00 Centauri Carbon — Filament Drying Manager" src="https://github.com/user-attachments/assets/a8d1a4e7-7b87-4034-b93b-3793dca21f98" />

---

## 🎯 Key Features

- **Direct SDCP V3.0.0 WebSocket Integration**: No cloud accounts, external bridges, or third-party telemetry servers required. Communicates directly over your local home network (`ws://<PRINTER_IP>:3030/websocket`).
- **Automatic Mainboard ID Discovery**: No need to manually locate or copy 32-character hardware serials. The application auto-detects your printer's MainboardID upon connecting.
- **Premium Web Dashboard**: Modern responsive dark-mode interface with glassmorphism aesthetics, live telemetry gauges, and audio alerts. Accessible on PC, smartphone, or tablet at `http://localhost:8000`.
- **Optimized Filament Drying Presets**:
  - **PETG**: 65 °C | 5 hours | Moisture Exhaust: 15% | Spool flip reminder at 2h30
  - **PLA**: 50 °C | 5 hours | Moisture Exhaust: 10% | Spool flip reminder at 2h30 (prevents heat creep / glass transition deformation)
  - **TPU / Flex**: 55 °C | 6 hours | Moisture Exhaust: 15% | Spool flip reminder at 3h00
  - **ASA / ABS**: 75 °C | 4 hours | Moisture Exhaust: 20% | Spool flip reminder at 2h00
  - **Nylon (PA)**: 80 °C | 8 hours | Moisture Exhaust: 25% | Spool flip reminder at 4h00
  - **Custom**: User-defined temperature (30 °C – 85 °C) and cycle duration (0.5 h – 24 h).
- **Active Moisture Exhaust Control**: Regulates the internal chamber exhaust fan (`BoxFan`) to actively purge moist air released by the spool during heating.
- **Active Thermal Watchdog & Anti-Idle Timeout**:
  - 3D printer firmwares (Klipper / Elegoo OEM) automatically shut down heated beds after 15–30 minutes if no axis movement G-code occurs.
  - The built-in watchdog monitors telemetry once per second.
  - If the firmware reduces or turns off the bed target temperature, the watchdog instantly detects the anomaly, logs the occurrence, and **re-asserts the heating command (`Cmd 403`)**, while sending periodic keepalive packets every 60 seconds to ensure uninterrupted 5-hour drying cycles.
  - Automatic audio-visual alerts trigger if an unexpected temperature drop is detected.
- **Mid-Cycle Spool Flip Reminder**: Audio chime and prominent banner trigger at exactly 50% elapsed time to remind the user to rotate/flip the filament spool on the bed for uniform drying across all layers.
- **Data Export & Thermal Tracking**:
  - Continuous real-time temperature evolution graph with Chart.js.
  - One-click **CSV Download** (`/api/thermal/export`) with timestamped temperature points (`Date_Time`, `Timestamp`, `Bed_Actual_C`, `Bed_Target_C`, `Chamber_C`, `Nozzle_C`).
  - One-click **PNG Image Export** for the thermal chart.
- **Persistent Preferences**: User preferences (preset choice, custom temperature/hours, exhaust fan speed, and sound alerts) are automatically memorized in `localStorage` across page refreshes.
- **Session History & Clean-up**: Historical records saved in JSON with a dedicated **Clear History** button.
- **Safety Interlocks & Emergency Cooldown**:
  - Blocks starting any drying cycle if the printer is actively printing a 3D model (`PrintInfo.Status == 13`).
  - Automatic emergency heater cutoff if the bed exceeds 85 °C.
  - Dedicated **Stop / Turn Off** button active at all times (cools bed down to 0 °C immediately).
- **Interactive Terminal / CLI Mode**: Full-featured curses-style terminal dashboard (`python main.py --cli`) for headless or SSH sessions with terminal beeps (`\a`) and graceful shutdown on `Ctrl+C`.

---

## 🚀 Installation & Quickstart

### 1. Requirements
- Python 3.10 or higher
- Install dependencies:
```bash
pip install -r requirements.txt
```

### 2. Configure Your Printer IP

Find your Centauri Carbon's IP address on the printer's touchscreen under **Settings ➔ Network / WiFi**.

You can configure the IP in any of the following ways:

#### Option A: Command-Line Flag
```bash
python main.py --host 192.168.1.100
```

#### Option B: Configuration File (Recommended)
Copy the example template to `config.json` (which is git-ignored):
```bash
cp config.example.json config.json
```
Edit `config.json`:
```json
{
  "printer_ip": "192.168.1.100",
  "printer_port": 3030,
  "mainboard_id": "auto",
  "web_port": 8000
}
```

#### Option C: Environment Variables
```bash
export CENTAURI_PRINTER_IP="192.168.1.100"
python main.py
```

### 3. Run the Web Dashboard
```bash
python main.py
```
Your browser will open automatically at `http://localhost:8000`.

### 4. Run in Terminal / CLI Mode
```bash
python main.py --cli
```

---

## ⚙️ Command-Line Options

| Option | Default | Description |
|---|---|---|
| `--host` | `config.json` or `192.168.1.100` | Printer IP address |
| `--port` | `3030` | Printer WebSocket port |
| `--mainboard` | `auto` | Printer MainboardID (`auto` for auto-detection) |
| `--web-host` | `0.0.0.0` | Bind host for local web server |
| `--web-port` | `8000` | Port for the Web Dashboard |
| `--preset` | *None* | Auto-start drying with preset (`petg`, `pla`, `asa`, `tpu`, `nylon`) |
| `--cli` | `False` | Run in interactive terminal / CLI mode |
| `--no-browser`| `False` | Do not open the web browser automatically |
| `-v`, `--verbose`| `False` | Enable debug logging |

---

## 📡 SDCP V3.0.0 Protocol Overview

- **WebSocket Endpoint**: `ws://<PRINTER_IP>:3030/websocket`
- **Request Topic**: `sdcp/request/<mainboard_id>`
- **Response Topic**: `sdcp/response/<mainboard_id>`
- **Status Topic**: `sdcp/status/<mainboard_id>`

### Commands Used:
1. **Cmd 0 (`GET_PRINTER_STATUS`)**: Requests live telemetry status push from the printer.
2. **Cmd 403 (`CHANGE_PRINT_PARAMS`)**:
   - **Bed Temperature**:
     ```json
     { "TempTargetHotbed": 65.0, "TempTargetNozzle": 0.0 }
     ```
   - **Exhaust Fan Speed**:
     ```json
     { "TargetFanSpeed": { "BoxFan": 15 } }
     ```
   - **Chamber Light**:
     ```json
     { "LightStatus": { "SecondLight": 1 } }
     ```

---

## 🧪 Automated Unit Tests

Run the unit tests using `pytest`:
```bash
python -m pytest tests/
```

---

## 📄 License

MIT License. Open source for 3D printing enthusiasts and the Elegoo Centauri community.
