#!/usr/bin/env bash
# =============================================================================
# RC UFO Drone — Startup Script
# =============================================================================
# Usage:
#   ./drone.sh                    # Interactive mode (choose action)
#   ./drone.sh connect            # Connect to drone WiFi
#   ./drone.sh view               # RTSP stream viewer
#   ./drone.sh control            # Keyboard control + video
#   ./drone.sh web                # TurboDrone web app (backend + frontend)
#   ./drone.sh capture            # Photo/video capture
#   ./drone.sh all                # Everything (connect + web + control)
#
# Requirements: nmcli, python3, iwconfig
# =============================================================================

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEEPSEEK_DIR="$PROJECT_DIR/deepseek_pro"
TURBODRONE_DIR="$DEEPSEEK_DIR/turbodrone"
PROJECT_DIR="$DEEPSEEK_DIR"
DRONE_SSID="WIFI-UFO-600849"
DRONE_IP="192.168.1.1"
WIFI_IFACE="wlp5s0"

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
ok()    { echo -e "${GREEN}[OK]${NC}   $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()   { echo -e "${RED}[ERR]${NC}  $1"; }

# ── Help ────────────────────────────────────────────────────────────────────
usage() {
    cat <<EOF
RC UFO Drone — Startup Script

Usage: $(basename "$0") [command]

Commands:
  connect         Scan and connect to drone WiFi
  view            Open RTSP stream viewer
  control         Open keyboard control + live video
  web             Start TurboDrone web dashboard
  capture         Open photo/video capture tool
  all             Full startup: connect → web → control
  status          Show connection status
  help            Show this help

Examples:
  $(basename "$0") connect     # Connect to drone
  $(basename "$0") view        # Just view the camera
  $(basename "$0") all         # Everything at once
EOF
    exit 0
}

# ── Dependency Check ────────────────────────────────────────────────────────
check_deps() {
    local missing=0
    for cmd in nmcli python3 iwconfig ping; do
        if ! command -v "$cmd" &>/dev/null; then
            err "$cmd not found"
            missing=1
        fi
    done
    if [ $missing -eq 1 ]; then
        err "Install missing dependencies and try again"
        exit 1
    fi
}

# ── WiFi Check ──────────────────────────────────────────────────────────────
wifi_connected() {
    local state
    state=$(iwconfig "$WIFI_IFACE" 2>/dev/null | grep -o 'ESSID:"[^"]*"' | grep -c "$DRONE_SSID" || true)
    [ "$state" -ge 1 ]
}

ping_drone() {
    ping -c 1 -W 2 "$DRONE_IP" &>/dev/null
}

# ── Connect to Drone WiFi ───────────────────────────────────────────────────
cmd_connect() {
    info "Scanning for drone WiFi..."

    # Try to find the drone SSID
    local ssid
    ssid=$(nmcli -t -f SSID dev wifi list 2>/dev/null | grep -i "ufo\|drone" | head -1 || true)

    if [ -z "$ssid" ]; then
        err "No drone WiFi network found!"
        info "Make sure the drone is powered on and nearby."
        info "List all networks: nmcli dev wifi list"
        exit 1
    fi

    info "Found: $ssid"
    info "Connecting..."

    if nmcli dev wifi connect "$ssid" iface "$WIFI_IFACE" 2>/dev/null; then
        ok "Connected to $ssid"
        sleep 2
    else
        # Try without password (open network)
        warn "Trying open network (no password)..."
        nmcli dev wifi connect "$ssid" iface "$WIFI_IFACE" --hidden 2>/dev/null || true
        sleep 2
    fi

    # Verify
    if wifi_connected; then
        ok "WiFi: $(iwconfig "$WIFI_IFACE" 2>/dev/null | grep -o 'ESSID:"[^"]*"')"
        local sig
        sig=$(iwconfig "$WIFI_IFACE" 2>/dev/null | grep -o 'Signal level=-[0-9]*' | cut -d= -f2)
        info "Signal: ${sig:-N/A} dBm"

        # Wait for IP and ping
        sleep 3
        if ping_drone; then
            ok "Drone reachable at $DRONE_IP"
        else
            warn "Drone not responding to ping yet, but WiFi is connected"
            info "The drone may be in power-save mode — try sending a heartbeat"
        fi
    else
        err "WiFi connection failed"
        info "Try manually: nmcli dev wifi connect"
        exit 1
    fi
}

# ── Heartbeat (keep drone awake) ────────────────────────────────────────────
start_heartbeat() {
    # Start heartbeat in background (dies when script exits)
    (
        while true; do
            echo -n "01 01" | xxd -r -p 2>/dev/null | nc -u -w 0 "$DRONE_IP" 7099 2>/dev/null || true
            sleep 1
        done
    ) &
    HB_PID=$!
    info "Heartbeat started (PID $HB_PID)"
}

# ── Status ───────────────────────────────────────────────────────────────────
cmd_status() {
    echo ""
    echo "=== Drone Connection Status ==="
    echo ""

    # WiFi
    if wifi_connected; then
        local ssid sig chan
        ssid=$(iwconfig "$WIFI_IFACE" 2>/dev/null | grep -o 'ESSID:"[^"]*"' | tr -d '"')
        sig=$(iwconfig "$WIFI_IFACE" 2>/dev/null | grep -o 'Signal level=-[0-9]*' | cut -d= -f2)
        chan=$(iwconfig "$WIFI_IFACE" 2>/dev/null | grep -o 'Frequency:[0-9.]* GHz' | cut -d: -f2)
        ok "WiFi:     Connected to $ssid"
        echo "  Signal:   ${sig:-N/A} dBm"
        echo "  Channel:  ${chan:-N/A}"
    else
        err "WiFi:     Not connected to drone"
    fi

    # Ping
    if ping_drone; then
        local ms
        ms=$(ping -c 1 -W 2 "$DRONE_IP" 2>/dev/null | grep -o 'time=[0-9.]* ms' | cut -d= -f2)
        ok "Ping:     $DRONE_IP reachable (${ms:-N/A})"
    else
        err "Ping:     $DRONE_IP not reachable"
    fi

    # Internet
    if ping -c 1 -W 2 8.8.8.8 &>/dev/null; then
        ok "Internet: Connected via USB tether"
    else
        warn "Internet: Not available"
    fi

    # Routing
    echo ""
    echo "  Routes:"
    ip route show | grep -v docker | while read -r line; do
        echo "    $line"
    done

    # USB vs WiFi
    echo ""
    local def_route
    def_route=$(ip route show default | head -1 | grep -o 'dev [a-z0-9]*' | cut -d' ' -f2 || echo "none")
    echo "  Default route: $def_route (internet)"
    echo "  Drone subnet:  $(ip route show | grep '192.168.1.0' | head -1 || echo 'no route — reconnect WiFi')"
}

# ── Commands ────────────────────────────────────────────────────────────────

cmd_view() {
    info "Starting stream viewer..."
    python3 "$DEEPSEEK_DIR/view.py"
}

cmd_control() {
    info "Starting keyboard control with live video..."
    python3 "$DEEPSEEK_DIR/control.py"
}

cmd_capture() {
    info "Starting capture tool..."
    python3 "$DEEPSEEK_DIR/capture.py"
}

cmd_web() {
    info "Starting TurboDrone web dashboard..."

    local backend_dir="$TURBODRONE_DIR/backend"
    local venv_python="$backend_dir/venv/bin/python3"

    if [ ! -f "$venv_python" ]; then
        warn "Setting up Python venv..."
        python3 -m venv "$backend_dir/venv"
        "$backend_dir/venv/bin/pip" install -q numpy fastapi uvicorn python-multipart python-dotenv opencv-python 2>/dev/null || true
    fi

    info "Web server: http://localhost:8000"
    info "MJPEG stream: http://localhost:8000/mjpeg"
    info "Press Ctrl+C to stop"

    cd "$backend_dir"
    DRONE_TYPE=cooingdv DRONE_IP="$DRONE_IP" "$venv_python" -m uvicorn web_server:app --host 0.0.0.0 --port 8000
}

cmd_all() {
    info "=== Full Startup ==="

    if ! wifi_connected; then
        cmd_connect
    else
        ok "Already connected to drone WiFi"
    fi

    if ! ping_drone; then
        warn "Drone not responding, sending wake-up..."
        start_heartbeat
        sleep 2
    fi

    # Start web server in background
    info "Starting web dashboard..."
    local backend_dir="$TURBODRONE_DIR/backend"
    local venv_python="$backend_dir/venv/bin/python3"
    if [ ! -f "$venv_python" ]; then
        python3 -m venv "$backend_dir/venv"
        "$backend_dir/venv/bin/pip" install -q numpy fastapi uvicorn python-multipart python-dotenv opencv-python 2>/dev/null || true
        venv_python="$backend_dir/venv/bin/python3"
    fi

    cd "$backend_dir"
    DRONE_TYPE=cooingdv DRONE_IP="$DRONE_IP" "$venv_python" -m uvicorn web_server:app --host 0.0.0.0 --port 8000 &
    WEB_PID=$!
    info "Web: http://localhost:8000 (PID $WEB_PID)"
    sleep 3

    # Open control interface
    info "Starting control interface..."
    cd "$DEEPSEEK_DIR"
    python3 control.py

    # Cleanup web on exit
    kill "$WEB_PID" 2>/dev/null || true
}

# ── Main ────────────────────────────────────────────────────────────────────
main() {
    check_deps

    local cmd="${1:-help}"

    case "$cmd" in
        connect)    cmd_connect ;;
        view)       cmd_view ;;
        control)    cmd_control ;;
        web)        cmd_web ;;
        capture)    cmd_capture ;;
        all)        cmd_all ;;
        status)     cmd_status ;;
        help|--help|-h) usage ;;
        *)
            err "Unknown command: $cmd"
            usage
            ;;
    esac
}

main "$@"
