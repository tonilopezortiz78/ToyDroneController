#!/usr/bin/env bash
# Persistent web dashboard wrapper — auto-restarts if it dies
set -euo pipefail

VENV_PY="/home/tony/software/drone/deepseek_pro/turbodrone/backend/venv/bin/python3"
BACKEND="/home/tony/software/drone/deepseek_pro/turbodrone/backend"
LOG="/tmp/webapp.log"

cd "$BACKEND"

while true; do
    echo "[$(date)] Starting web server..." >> "$LOG"
    DRONE_TYPE=cooingdv DRONE_IP=192.168.1.1 \
        "$VENV_PY" -m uvicorn web_server:app \
        --host 0.0.0.0 --port 8000 --log-level error \
        >> "$LOG" 2>&1
    EXIT_CODE=$?
    echo "[$(date)] Server exited with code $EXIT_CODE, restarting in 2s..." >> "$LOG"
    sleep 2
done
