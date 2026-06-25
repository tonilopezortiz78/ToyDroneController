#!/usr/bin/env bash
set -euo pipefail

DRONE_SSID="WIFI-UFO-600849"
BACKEND="/home/tony/software/drone/deepseek_pro/turbodrone/backend"
VENV_PY="$BACKEND/venv/bin/python3"

# Connect WiFi
echo "[+] Connecting to $DRONE_SSID ..."
nmcli dev wifi connect "$DRONE_SSID" 2>/dev/null || true
sleep 3

# Kill old server
pkill -f "uvicorn web_server" 2>/dev/null || true
sleep 1

# Start dashboard
echo "[+] Dashboard: http://localhost:8000"
echo "[+] Ctrl+C to stop"
cd "$BACKEND"
OPENCV_FFMPEG_READ_TIMEOUT=3000 DRONE_TYPE=cooingdv DRONE_IP=192.168.1.1 \
    "$VENV_PY" -m uvicorn web_server:app --host 0.0.0.0 --port 8000 --log-level error
