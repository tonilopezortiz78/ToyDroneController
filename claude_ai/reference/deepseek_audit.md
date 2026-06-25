# Audit of `deepseek_pro` (the competing project)

By Claude AI · 2026-06-25. Notes from auditing the `deepseek_pro` project's
approach vs. `claude_ai/`. (The local code copy has since been removed; the key
takeaway — its control mapping/protocol — is now matched in `webapp.py`.)

## What deepseek_pro built

A "ToyDroneController" for the same cooingdv drone. Two parts:
1. **CLI tools** (`control.py`, `view.py`, `capture.py`, `tools/scan.py`,
   `tools/probe.py`) using **OpenCV**.
2. **Web dashboard** = they **cloned upstream TurboDrone** and run its FastAPI
   `web_server.py` (`DRONE_TYPE=cooingdv … uvicorn web_server:app`). So their
   dashboard is *TurboDrone's*, not original.

Their findings match ours exactly: UDP 7099 control, 9-byte TC / 21-byte GL,
RTSP 7070 MJPEG 240×320, 5-byte status telemetry, Jieli chipset. **No battery,
no IMU** — their telemetry list is pitch/roll/throttle/yaw/flags/WiFi-signal/
variant/resolution-id/raw-hex (README line 12). Same ceiling we hit.

## Where they're ahead (worth adopting)

| Their feature | Adopt into claude_ai? |
|---|---|
| **In-app video recording** (R → MKV/H.264) | ✅ add a Record button to the cockpit |
| **3 speed levels** (slow/normal/fast stick scaling) | ✅ add a speed selector — genuinely useful |
| **Photo/snapshot button** in UI | ✅ add Snapshot button (we only have CLI) |
| **Angle OSD drawn on the video** | ➖ we have a separate artificial-horizon; optional |
| **90° rotate + 3× zoom display** | ✅ add a rotate/zoom toggle (portrait stream) |
| **"Connect to WiFi" button** in dashboard | ✅ we have `ufo.py connect`/`start.sh`; a button is nice |

## Where claude_ai is ahead

| Area | claude_ai | deepseek_pro |
|---|---|---|
| **Dependencies** | pure Python stdlib + ffmpeg, self-contained | needs `opencv-python`, `fastapi`, `uvicorn`, + a cloned TurboDrone tree |
| **Control safety** | keyboard **keyup→auto-center**, 0.5 s **watchdog**, **arm/disarm**, sticks clamped **68–188** | `control.py` has **no auto-center** (cv2 can't see key-release → a stick stays deflected until you press the opposite key — dangerous), clamps full **0–255** |
| **Probe safety/depth** | full opcode sweep `0x00–0xFF`, **skips** control/leave/password; non-destructive | `probe.py` sends only ~8 packets **and actually commands Takeoff + 50% throttle** as "probes" (unsafe), reads one reply each |
| **`.100` persistence** | pinned to NM profile + self-heal thread | not addressed (would hit the stale-telemetry trap) |
| **Telemetry depth** | + ping RTT, video FPS, link uptime, WiFi dBm/%, **raw-anomaly watcher** | WiFi signal + raw hex (via TurboDrone) |
| **Headless/web** | stdlib web cockpit, no display needed | `control.py` needs an OpenCV GUI window |

**Net:** deepseek leaned on TurboDrone's mature dashboard (more buttons out of the
box) but at the cost of heavy deps, an unsafe control model, and an unsafe probe.
claude_ai is lighter, safer, and self-contained — and now has richer real
telemetry. The few nice extras they have (record, speed levels, snapshot, rotate)
are easy to port.

## Do we have more telemetry we're missing?  **No — from the drone.**

Both projects independently mapped the drone protocol to the same floor, and our
exhaustive probe (opcode sweep + HTTP + FTP) confirmed the drone emits only:

- `53 01 00 00 00` — status / model id 83
- `01 01 00 00 00` — heartbeat ack

**Nothing else is obtainable over WiFi.** Battery/voltage/IMU/attitude are read by
the flight MCU and never bridged to the WiFi chip (see `electronics.md`).

What we *can* still surface (real, not "missing", just not yet wired):
1. **Photo/Video event packets** — when a photo/video is captured the drone sends
   a *different* telemetry packet (`bArr[2]=='M'` photo + count, `'X'` video +
   count). We've never triggered it because we never fired the capture command.
   Wiring the Snapshot button will exercise + display this real telemetry.
2. **Video-quality metrics** — actual resolution, bitrate, and **packet-loss %**
   from the RTSP stream (ffmpeg exposes drop stats). Genuinely useful link health.
3. **WiFi signal** — already added (dBm + %).

## Recommended improvement plan for claude_ai
1. Add **Snapshot + Record** buttons (also exercises the M/X photo telemetry).
2. Add **speed levels** (slow/normal/fast stick scaling) + a **rotate/zoom** video toggle.
3. Add **video packet-loss / bitrate** to the Link panel.
4. Keep the safety model (watchdog, arm, clamp) — do **not** copy their open-loop control.
