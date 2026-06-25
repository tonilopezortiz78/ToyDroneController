#!/usr/bin/env bash
set -euo pipefail

# ── Config ──
DRONE_SSID="WIFI-UFO-600849"
DRONE_IP="192.168.1.1"
PORT=8000
BACKEND="/home/tony/software/drone/deepseek_pro/turbodrone/backend"
VENV_PY="$BACKEND/venv/bin/python3"

# ── Colors ──
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info() { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }

# ── 1. WiFi ──
info "Connecting to drone WiFi..."
nmcli dev wifi connect "$DRONE_SSID" 2>/dev/null || true
sleep 3

if ! ping -c 1 -W 2 "$DRONE_IP" &>/dev/null; then
    warn "Drone not reachable — check WiFi"
fi

# ── 2. Kill old ──
pkill -f "uvicorn web_server" 2>/dev/null || true
sleep 1

# ── 3. Start dashboard ──
info "Dashboard: http://localhost:$PORT"
info "Ctrl+C to stop"
echo ""

cd "$BACKEND"
OPENCV_FFMPEG_READ_TIMEOUT=3000 DRONE_TYPE=cooingdv DRONE_IP="$DRONE_IP" \
    "$VENV_PY" -m uvicorn web_server:app \
    --host 0.0.0.0 --port "$PORT" --log-level error
