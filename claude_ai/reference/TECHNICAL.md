# TECHNICAL — communications & functions reference

How this toolkit talks to the drone and what every interface does. For the raw
on-the-wire byte protocol see [`cooingdv_commands.md`](cooingdv_commands.md) and
[`cooingdv_protocol.md`](cooingdv_protocol.md); this file documents **the
software** (`webapp.py`, `ufo.py`) — its HTTP API, CLI, data flow, and the
networking it depends on.

---

## 1. System overview

```
                 ┌──────────────────────────── your laptop ────────────────────────────┐
   browser ⇄ HTTP/SSE ⇄  webapp.py  ─ UDP 7099 (control + telemetry) ─►  drone 192.168.1.1
  (cockpit UI)            │  ├─ FlightController  (20 Hz control loop, telemetry drain)
                          │  ├─ VideoBroker       (1 ffmpeg RTSP→MJPEG, fans out to viewers)
                          │  ├─ LinkMonitor       (wifi dBm/%, ping)
                          │  └─ DroneLink         (wifi scan / connect, via ufo.py)
                          └─ ffmpeg ◄─ RTSP 7070/webcam (MJPEG/RTP-UDP) ◄── drone
```

* **One process, stdlib + ffmpeg only.** No database, no Node, no build step.
* The laptop **must hold IP `192.168.1.100`** — the drone only streams
  video/telemetry to `.100`. `ufo.py` pins and self-heals this (see §5).
* Control is **fire-and-forget UDP at 20 Hz**; telemetry is whatever the drone
  sends back on the same socket (only status + ack — no battery/IMU).

---

## 2. Drone communications (what crosses the air)

| Channel | Transport | Endpoint | Notes |
|---|---|---|---|
| Control + telemetry | UDP | `192.168.1.1:7099` | 20 Hz; 9-byte TC packet; replies ≤20 B |
| Heartbeat | UDP | `…:7099` | `01 01` once per second |
| Video | RTSP→RTP/UDP | `rtsp://192.168.1.1:7070/webcam` | MJPEG, 240×320 portrait, ~13 fps |
| Camera select | UDP | `…:7099` | `06 01` front · `06 02` bottom |

**Flight packet (9 bytes, sent 20 Hz):**
```
03 66  B1   B2   ACC  TURN FLAGS CKSUM 99
       roll pitch thr  yaw        ^ B1^B2^ACC^TURN^FLAGS
```
centre = 128, range 0–255 (`ACC==1`→`0`). **FLAGS:** `01` takeoff · `02` land ·
`04` e-stop · `08` flip · `10` headless · `40` LED · `80` gyro-calibrate.

**Telemetry seen from our drone:** only `53 01 00 00 00` (status, id 83 = TC) and
`01 01 00 00 00` (ack). Photo/video capture produces a `'M'`/`'X'` marker packet
with a counter. **No battery, voltage, IMU, or distance is ever sent** — verified
by an exhaustive opcode sweep. That is why the UI shows none of those.

---

## 3. webapp.py — HTTP / SSE API

Served on `http://localhost:8088` (override `--port`). All JSON is UTF-8.

### GET endpoints
| Route | Returns | Purpose |
|---|---|---|
| `/` | HTML | the cockpit page (self-contained) |
| `/stream.mjpg` | `multipart/x-mixed-replace` | live MJPEG; one shared ffmpeg fans out to all clients |
| `/telemetry` | `text/event-stream` (SSE) | pushes the full state JSON ~5×/s |
| `/state` | JSON | one-shot snapshot of the same state (good for scripts) |
| `/drones` | JSON `{drones:[…], status:{…}}` | scans for `WIFI-UFO-*` hotspots |

Debug: append `?noimg=1` to `/` to render the UI without opening the video
stream (used for screenshots / headless checks; no drone contact for video).

### POST endpoints
**`/control`** — set the stick axes (sent by the browser as you hold keys):
```json
{ "roll": 128, "pitch": 128, "throttle": 128, "yaw": 128 }
```
Values are clamped server-side. If no `/control` arrives for **0.5 s** the sticks
**auto-center** (watchdog) — releasing a key stops the drone.

**`/command`** — discrete actions: `{ "name": "<cmd>", "value": <optional> }`.

| `name` | Effect | Wire result |
|---|---|---|
| `arm` / `disarm` | enable / disable sending stick packets | — |
| `takeoff` | arm + takeoff pulse (throttle→150) then auto-hover | FLAGS `0x01` |
| `land` | descend | FLAGS `0x02` |
| `estop` | **cut motors**, disarm, center sticks | FLAGS `0x04` |
| `flip` | flip / circle | FLAGS `0x08` |
| `calib` | **gyro calibrate** (held ~1.2 s) | FLAGS `0x80` |
| `headless` | toggle headless | FLAGS `0x10` |
| `light` | toggle LED | FLAGS `0x40` |
| `cam_front` / `cam_bottom` | switch camera (restarts the stream) | `06 01` / `06 02` |
| `snapshot` | save a JPEG to `captures/` | — |
| `record_start` / `record_stop` | H.264 MP4 to `captures/` | — |
| `connect` | (re)connect wifi to drone; `value` = SSID (optional) | runs `ufo.connect_ufo` |

Every `/command` returns the current `/state` JSON (plus a `toast` string).

### `/state` JSON schema (only real, measured data)
```jsonc
{
  "armed": false, "headless": false, "light": false,
  "sticks": { "roll":128,"pitch":128,"throttle":128,"yaw":128 },
  "telemetry": { "online":true, "raw":"53 01 00 00 00", "model_id":83,
                 "variant":"TC", "length":5, "nonstandard":false,
                 "anomaly":"", "photo_count":null, "video_count":null },
  "link": { "wifi_dbm":-60, "wifi_quality":80, "ping_ms":3.0,
            "video_fps":13.0, "bitrate_kbps":900, "drops":0, "uptime_s":42 },
  "rec": false, "tx_rate": 20, "bound_100": true, "active_cam": "front",
  "drone": { "ssid":"WIFI-UFO-600849", "is_ufo":true, "connecting":false, "result":"" }
}
```
There is intentionally **no battery or attitude** field — the drone provides
neither, and faking them would be misleading.

---

## 4. webapp.py — internal components

| Class | Responsibility | Robustness |
|---|---|---|
| `FlightController` | 20 Hz control loop, heartbeat, telemetry drain, command/stick state | binds `.100`; re-binds + re-adds `.100` if it drops; auto-center watchdog |
| `VideoBroker` | one ffmpeg `RTSP→MJPEG`, fans frames to all viewers + recorder + snapshot | watchdog restarts ffmpeg if it dies while anyone is watching |
| `LinkMonitor` | wifi signal (nmcli %/iw dBm) + ping RTT | iface auto-detected via nmcli |
| `DroneLink` | wifi scan + background (re)connect | runs connect in a thread so HTTP never blocks |

Control is streamed **continuously at 20 Hz** (the drone expects a constant
feed): armed → real sticks; disarmed → a safe idle packet (centered, throttle 0,
props off). This matches the proven `deepseek_pro` behavior.

---

## 5. Networking — the `.100` rule

The drone only sends video/telemetry to a client at **`192.168.1.100`**, but DHCP
hands out `.101`. `ufo.py` therefore:

1. **Pins** `192.168.1.100/24` onto the NetworkManager profile so a reconnect
   keeps it (`ensure_pin`).
2. **Asserts** it at runtime if missing (`ensure_100`, needs `sudo`).
3. The cockpit's `FlightController._maintenance` thread re-adds it and rebinds the
   UDP socket if the address ever disappears (drone power-cycle, NM flush).

Keep internet **and** drone at once by putting internet on a *second* interface
(phone USB tether or a USB wifi dongle) so the built-in wifi card is free for the
drone's hotspot.

---

## 6. ufo.py — CLI

```
python3 ufo.py scan                     list UFO drone hotspots in range
python3 ufo.py connect [--ssid S] [--timeout N]   join drone wifi + hold .100 (retries)
python3 ufo.py net [--ssid S]           pin + assert .100, ping the drone
python3 ufo.py snapshot [-o FILE]       grab one JPEG (captures/)
python3 ufo.py record [-t SEC] [-o FILE]   record video to mp4
python3 ufo.py live [--port P]          minimal video-only MJPEG viewer
python3 ufo.py telemetry                decode/print UDP 7099 packets
```
`connect` with no `--ssid` auto-selects the strongest `WIFI-UFO-*` hotspot.

### Importable functions (used by `webapp.py`, reusable in scripts)
| Function | Returns |
|---|---|
| `is_ufo_ssid(ssid)` | `bool` — is this a UFO drone hotspot |
| `scan_ufo()` | `[{ssid, signal, in_use}]` strongest-first |
| `current_ssid()` | SSID the wifi card is associated with, or `None` |
| `connect_ufo(ssid=None, timeout=120, verbose=True)` | `bool` — connect + reach `.100` |
| `ensure_pin(ssid)` / `ensure_100(iface)` | pin / assert `.100` |
| `connected_to_ufo(ssid=None)` / `drone_alive()` | `bool` link checks |

---

## 7. Dependencies & failure modes

* **Requires:** `python3` (stdlib), `ffmpeg` on `PATH`, NetworkManager (`nmcli`),
  `iw` (optional, for dBm), `sudo` (for pinning `.100`).
* **Video stops but telemetry continues** → ffmpeg/RTSP blip; the broker watchdog
  restarts it automatically.
* **Both stop together** → the drone likely auto-powered-off (idle/low battery);
  power-cycle it and re-run `connect`.
* **`bound_100:false` in `/state`** → `.100` isn't held; run `python3 ufo.py net`.
* **Controls feel wrong / drone drifts** → almost always an **uncalibrated gyro**,
  not the software. Calibrate on the ground first (see [`START_HERE.md`](../START_HERE.md)).
