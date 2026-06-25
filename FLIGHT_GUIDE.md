# ToyDroneController — Quick Start Guide

## 1. Power On

Turn on the drone. LEDs flash — it's waiting for WiFi.

## 2. Connect Laptop to Drone WiFi

```bash
# Find the drone network
nmcli dev wifi list | grep -i ufo

# Connect
nmcli dev wifi connect WIFI-UFO-600849
```

**Keep internet via USB tether** — works simultaneously.

## 3. Start the Dashboard

```bash
cd ~/software/drone/deepseek_pro
./launch_dashboard.sh
```

Open `http://localhost:8000` in your browser.

## 4. Before Takeoff — Check

- **Signal bar** (top-right): should be > 40% (green)
- **Camera**: video visible in main panel
- If no video → press **I** to reconnect

## 5. Takeoff

Press **T** or **Backspace**. Drone auto-launches and hovers at ~1.5m.

## 6. Fly

| Key | Action |
|-----|--------|
| **↑ ↓ ← →** | Move forward/back/left/right |
| **W / S** | Go up / down (throttle) |
| **A / D** | Rotate left/right (yaw) |
| **1 / 2 / 3** | Slow / Normal / Fast speed |

Hold keys for continuous movement. Release to stop.

## 7. Land

Press **L** or **Backspace** — gradual descent.

## 8. Emergency

**Space** or **X** — cuts motors immediately (drone falls).

## 9. Record

Press **R** to start recording. Press **R** again to stop.
Videos saved to `~/Videos/drone_rec_*.mkv`.

## 10. If Video Drops

Press **I** to force reconnect. Or press **C** to reconnect WiFi.

## Controls Reference

| Key | Action |
|-----|--------|
| **T** | Takeoff |
| **L** | Land |
| **Backspace** | Land |
| **Space** | Emergency stop |
| **X** | Emergency stop |
| **↑ ↓ ← →** | Pitch / Roll (move) |
| **W / S** | Throttle (altitude) |
| **A / D** | Yaw (rotate) |
| **Z** | Center all sticks |
| **R** | Toggle recording |
| **I** | Reconnect video stream |
| **C** | Connect to drone WiFi |
| **1 / 2 / 3** | Slow / Normal / Fast speed |
