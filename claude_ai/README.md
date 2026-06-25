# RC UFO Drone — Claude AI toolkit

Control & view a cheap WiFi folding drone (phone app **"RC UFO"**,
Android package `com.cooingdv.rcufo`) from this Linux laptop — **while keeping
internet access** so I (Claude) can keep working.

> Everything here was discovered and verified live against the actual drone
> (`WIFI-UFO-600849`) on 2026-06-25. Full technical write-up: **[FINDINGS.md](FINDINGS.md)**.
> New here? Start with **[START_HERE.md](START_HERE.md)**.

---

## TL;DR — the two things that make it work

1. **Two network paths at once.** The laptop has one WiFi card, so it can't be
   on your *internet* WiFi and the *drone* WiFi at the same time. We give the
   laptop internet over a **phone USB tether**, leaving the WiFi card free for the
   drone's hotspot. Result: internet **and** drone, together.

2. **The `.100` rule.** The drone only streams to a client at **`192.168.1.100`**.
   DHCP hands you `.101`, so by default the drone sits silent, ARP-ing for `.100`.
   Claim `.100` (now pinned permanently) and the video + telemetry start instantly.

---

## Quick start

**Switch the drone on, then run one command:**

```bash
cd /home/tony/software/drone/claude_ai
./start.sh
```

`start.sh` connects to the drone (retrying while it boots), claims
`192.168.1.100`, opens the cockpit at <http://localhost:8088>, and starts
video + telemetry + keyboard control.

### Or step by step

```bash
python3 ufo.py connect       # robustly join the drone WiFi + hold .100 (retries)
python3 webapp.py            # cockpit -> http://localhost:8088
```

Single-purpose commands also exist:

```bash
python3 ufo.py net           # pin + assert .100, ping the drone
python3 ufo.py live          # video-only web viewer
python3 ufo.py snapshot      # grab a photo -> captures/
python3 ufo.py record -t 20  # record 20s of video
python3 ufo.py telemetry     # decode UDP 7099 in the terminal
```

Requirements: `ffmpeg` and Python 3 stdlib. **No other dependencies** — this
folder is fully self-contained.

## The cockpit (`webapp.py`) — FLIGHT CONSOLE

A clean **shadcn-style dark dashboard** (self-contained — no Node/build step, no
runtime CDN, so it works even on the drone's internet-less network): a **big FPV
view**, **real telemetry**, **keyboard/button flight control**, and a **drone
scan + reconnect** picker. Keep the tab focused for keys to work.

> ### ⚠️ CALIBRATE THE GYRO FIRST
> The **#1 reason a drone flies badly (drift, tip, flip) is an uncalibrated
> gyro** — not the controls. With the drone **flat and still on the ground**,
> press the amber **◎ GYRO CALIBRATE** button (or **`C`**) before every session.
> The controls here are byte-identical to the proven `deepseek_pro` build.

**Camera:** one **big live feed**, **rotated 90° by default** (mount is sideways),
with **CAM 1 · Front** / **CAM 2 · Bottom** to switch. The airframe has **one
encoder**, so only one camera streams at a time (true side-by-side is impossible);
switching re-inits the encoder and the view reconnects on its own.

**Keyboard map** (matches `deepseek_pro`):

| Key | Action | Key | Action |
|---|---|---|---|
| `W`/`S` | pitch fwd/back | `Space` | takeoff / land **toggle** |
| `A`/`D` | roll left/right | **`Backspace`** | **EMERGENCY STOP** |
| `↑`/`↓` | throttle up/down | `H` | headless · `C` calibrate · `F` flip |
| `←`/`→` | yaw left/right | | |

**Buttons:** **◎ GYRO CALIBRATE** · ARM · TAKEOFF · LAND · FLIP · HEADLESS ·
**◎ LIGHT** · DISARM · ■ E-STOP. Camera bar: **▣ SNAP · ● REC · ⟳ ROTATE · ⤢ ZOOM**
and **CAM 1 / CAM 2**. Rate profile **1 · Slow / 2 · Normal / 3 · Fast** (matches
the remote's 1/2/3 beeps).

**Control model (matches `deepseek_pro`, which flies right):** continuous 20 Hz
control stream · `Space` pulses throttle to 150 for takeoff then auto-hovers ·
full 0–255 stick authority. **Plus a safety net:** motors **DISARMED** until
ARM/Takeoff; **`Backspace`** = panic stop; if the browser stops sending input for
>0.5 s the sticks **auto-return to neutral**. Fly in a clear open space.

**Only real data is shown** — no battery % or artificial horizon, because the
airframe sends **no battery or IMU** over WiFi (confirmed by live probe). Faking
them would be worse than omitting them.

### Drone scan / reconnect
Each drone is its own WiFi hotspot (all at `192.168.1.1`), so you fly **one at a
time**. The **Drone** card scans for `WIFI-UFO-*` hotspots and connects to the one
you pick — or use the CLI: `python3 ufo.py scan` then `python3 ufo.py connect --ssid <SSID>`.

### Reliability features
- **`.100` is pinned** to the saved `WIFI-UFO-600849` profile, so NetworkManager
  no longer flushes it on reconnect (this was the cause of stale telemetry).
- **`connect`** retries through power-on, weak signal, and reconnects.
- **The cockpit self-heals** — a background thread re-adds `.100` and rebinds its
  socket if the address drops or the drone power-cycles.
- ⚠️ These toy drones **auto-power-off** when idle / low battery. If video and
  telemetry stop together, check the drone first, then re-run `connect`.

---

## Drone at a glance

| Property | Value |
|---|---|
| App | RC UFO — `com.cooingdv.rcufo` |
| Protocol family | `cooingdv` (chipset: Jieli / BL60x) |
| WiFi SSID | `WIFI-UFO-600849` (open, no password) |
| Drone IP | `192.168.1.1` |
| **Your IP must be** | **`192.168.1.100`** |
| Video | `rtsp://192.168.1.1:7070/webcam` — **MJPEG**, 240×320, ~13 fps |
| Control / telemetry | UDP `192.168.1.1:7099`, 20 Hz |
| Photos / SD card | `http://192.168.1.1/PHOTO/` and `/DCIM/`, or FTP `ftp` / `ftp` |

---

## Files

| File | What it is |
|---|---|
| `start.sh` | **One-command startup** — connect + open the cockpit |
| `webapp.py` | **The cockpit** — video + telemetry + keyboard flight control |
| `ufo.py` | CLI toolkit: `connect`, `net`, `snapshot`, `record`, `live`, `telemetry` |
| `START_HERE.md` | Step-by-step flying procedure from power-on |
| `README.md` | This file |
| `FINDINGS.md` | Full technical reverse-engineering write-up |
| `INFO.txt` | Plain-text cheat sheet |
| `reference/TECHNICAL.md` | **Communications & functions reference** — HTTP/SSE API, CLI, data flow, networking |
| `reference/cooingdv_protocol.md` | Preserved protocol reference (from TurboDrone) |
| `reference/cooingdv_commands.md` | **Complete command/protocol cheat sheet** (every opcode, flag, packet) |
| `reference/electronics.md` | Hardware research — chip (Jieli BL60x, **not ESP32**) + battery-hack probe results |
| `reference/deepseek_audit.md` | Notes from auditing the competing `deepseek_pro` project |
| `captures/` | Saved photos / recordings |

---

## Telemetry — what's real vs. estimated

The cockpit shows **real** metrics and **honestly-labeled estimates**:

| Real (measured) | Estimated / commanded (labeled in UI) |
|---|---|
| Link online, model id (83/TC), control TX rate | **Battery %** — flight-time countdown from arm (no sensor) |
| WiFi signal (dBm + %), ping RTT, video FPS, link uptime | **Attitude** — artificial horizon from *commanded* sticks (no IMU) |
| Photo/video counters, **raw-telemetry anomaly watcher** | |

**Battery / accelerometer are NOT available over WiFi** — confirmed by an
exhaustive live probe (UDP opcode sweep `0x00–0xFF` + HTTP + FTP): the drone only
ever emits `53 01 00 00 00` (status) and `01 01 00 00 00` (heartbeat ack). That
data lives on the flight MCU and is never bridged to the WiFi chip. See
[reference/electronics.md](reference/electronics.md) for the chip teardown
(it's a **Jieli BL60x**, not an ESP32) and the full probe results. The raw-telemetry
watcher will surface any hidden packet the instant the drone ever sends one.

### Cameras (live-audited 2026-06-25)
- The RTSP server answers **any** path (`/webcam`, `/webcam2`, `/webcam_1`…) but
  they all serve the **same single feed** — there is **one encoder**, so two
  **simultaneously-live** feeds are **physically impossible** over WiFi. It's
  **one camera live at a time**.
- The cockpit therefore shows a **dual pane**: the active camera is **live**, the
  other pane shows its **last captured still**. This is the closest you can get to
  "both at once" on this hardware.
- You **switch** which camera feeds that one stream with `06 01` (CAM 1) /
  `06 02` (CAM 2) — the cockpit's **CAM 1 · Front** / **CAM 2 · Bottom** buttons
  (or click a pane).
- **Important:** switching re-inits the encoder, so the RTSP stream briefly drops
  and you must **reconnect** to see the new camera. The cockpit's auto-restart
  video broker handles this automatically; a one-shot `ffmpeg` grab will appear to
  "break" because it doesn't reconnect.
- **CONFIRMED:** CAM 2 is a **genuine bottom/downward camera** (live-verified —
  works, soft focus). So the drone has **two real cameras**, front + bottom, that
  share the one stream. Use `06 01` to return to the front camera.

---

## Credit / sources

The control + video protocol matches the open-source **TurboDrone** project's
`cooingdv` driver (`github.com/marshallrichards/turbodrone`), which
reverse-engineered the RC UFO / KY UFO app family. We confirmed every value
against this physical drone. See [FINDINGS.md](FINDINGS.md) for the evidence trail.
