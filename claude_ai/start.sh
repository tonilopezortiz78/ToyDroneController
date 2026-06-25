#!/usr/bin/env bash
# =============================================================================
#  RC UFO drone — one-command startup   (Claude AI)
#  Run this AFTER you switch the drone on. It will:
#    1. keep trying to connect to the drone's WiFi (whatever it takes)
#    2. make sure we hold 192.168.1.100 (the address the drone talks to)
#    3. open the cockpit in your browser (video + telemetry + keyboard control)
# =============================================================================
set -u
cd "$(dirname "$0")"

PORT="${1:-8088}"
URL="http://localhost:${PORT}"

echo "--------------------------------------------------"
echo " RC UFO startup — make sure the drone is ON"
echo "--------------------------------------------------"

# 1 + 2: robust connect (retries for up to 2 minutes while you power it on)
python3 ufo.py connect --timeout 120
if [ $? -ne 0 ]; then
  echo
  echo "x Could not reach the drone."
  echo "  - Is it switched on? (the LED should be lit / blinking)"
  echo "  - Wait ~10 s after power-on for its WiFi to appear, then re-run."
  echo "  - Check WiFi 'WIFI-UFO-600849' is in range."
  exit 1
fi

# 3: open the cockpit in a browser (best-effort; ignore if no GUI)
( xdg-open "$URL" >/dev/null 2>&1 \
  || google-chrome --new-window --app="$URL" >/dev/null 2>&1 & )

echo
echo "OK Connected. Opening cockpit at ${URL}"
echo "  (SPACE = EMERGENCY STOP; T = takeoff, L = land, R = record. Calibrate gyro (C) first!)"
echo "  Ctrl-C here to shut the cockpit down."
echo

# 4: run the cockpit (foreground)
exec python3 webapp.py --port "$PORT"
