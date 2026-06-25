# RC UFO Drone — Full Technical Findings

**Investigator:** Claude AI
**Date:** 2026-06-25
**Subject:** Cheap WiFi folding quadcopter, phone app "RC UFO"
(`com.cooingdv.rcufo`), broadcasting WiFi `WIFI-UFO-600849`.

Everything below was **observed live** against the physical drone unless marked
"(from app reversing)". The protocol cross-checks against the open-source
[TurboDrone](https://github.com/marshallrichards/turbodrone) `cooingdv` driver,
which reverse-engineered this same app family. The relevant protocol doc is
preserved at `reference/cooingdv_protocol.md`.

---

## 1. The goal and the two problems

**Goal:** let the laptop (a) keep internet so Claude can work, and (b) connect to
the drone to see its camera/data — *at the same time*.

### Problem A — one WiFi card, two networks
The laptop has a single WiFi radio (`wlp5s0`, Intel). A WiFi card can only join
**one** network at a time, so it can be on the house WiFi (internet) **or** the
drone's hotspot (`WIFI-UFO-600849`) — not both.

**Solution:** give the laptop internet through a **second interface** so the WiFi
card is free for the drone. We used a **phone USB tether** (`enx…`,
`10.148.252.160`). The routing table then looks like:

```
default via 10.148.252.252 dev enx<phone>   metric 100    <- internet (phone)
default via 192.168.1.1     dev wlp5s0       metric 20600  <- drone subnet
192.168.1.0/24              dev wlp5s0                      <- drone
```

Lower metric wins, so general internet traffic goes out the phone, and anything
for `192.168.1.x` goes to the drone over WiFi. Both work simultaneously.
*(A USB WiFi dongle would achieve the same thing if you prefer not to tether.)*

### Problem B — the drone ignores you unless you are `192.168.1.100`
When first connected, DHCP from the drone gave the laptop `192.168.1.101`. The
drone responded to `ping` but **every TCP port was closed** and no video came.

A passive packet capture revealed why — the drone was endlessly ARP-ing for a
client that wasn't there:

```
ARP, Request who-has 192.168.1.100 tell 192.168.1.1
```

The drone hard-expects the controller app at **`192.168.1.100`**. The instant we
added that address to the WiFi interface, the drone began streaming UDP telemetry
from `192.168.1.1:7099`. **This is the single most important gotcha.**

**Persistence caveat (learned the hard way):** a manually-added `ip addr add`
`.100` is **volatile** — NetworkManager flushes it on the next DHCP renew or
reconnect, after which telemetry silently goes stale (you keep the last value but
`online` flips false). Fix: **pin it to the connection profile** so it's
reapplied on every connect, alongside DHCP:

```bash
sudo nmcli connection modify "WIFI-UFO-600849" +ipv4.addresses 192.168.1.100/24
# method stays "auto", so you still get DHCP .101 AND static .100 every time
```

`ufo.py net` / `ufo.py connect` do this automatically. The cockpit also runs a
self-heal thread that re-adds `.100` and rebinds its socket if it ever drops.

**Auto-power-off:** the drone shuts its hotspot down after a few minutes idle and
on low battery (observed live — the WiFi vanished and `wlp5s0` went `DOWN` mid
session). When video + telemetry stop together, suspect the drone, not the code;
`ufo.py connect` will re-acquire it once it's powered back on.

---

## 2. Network map

| Item | Value |
|---|---|
| Drone WiFi SSID | `WIFI-UFO-600849` (open / no encryption) |
| Drone IP (gateway) | `192.168.1.1` |
| Drone MAC | `c2:a9:a5:49:08:60` |
| Required client IP | `192.168.1.100` |
| Drone-as-DHCP | hands out `192.168.1.10x`; also runs a DNS on :53 that **refuses** all queries (so don't rely on it for internet — that's what the phone is for) |
| Only host on the LAN | the drone itself |

---

## 3. Services / ports

Scanned with `nmap` (full TCP 1–65535) and targeted UDP probes.

| Port | Proto | Purpose | State |
|---|---|---|---|
| `7070` | TCP/UDP | **RTSP** video server (`/webcam`) | open when client = .100 |
| `7099` | UDP | **RC control + telemetry** | active when client = .100 |
| `80`  | TCP | HTTP file access to SD card (`/PHOTO/`, `/DCIM/`) (from app reversing) | — |
| `21`  | TCP | FTP media (`ftp`/`ftp`, root `/0/`) (from app reversing) | — |
| `5000`| TCP | defined in app as `TCP_SERVER_PORT` but **unused** | closed |

**Key insight:** when idle (no client at .100) the drone shows **zero open TCP
ports** and only answers ICMP. It "wakes up" its services for the `.100` client.

---

## 4. Video

- **URL:** `rtsp://192.168.1.1:7070/webcam`
- **Transport:** RTSP with **RTP over UDP** (the server returns
  `461 Unsupported Transport` if you ask for RTP-over-TCP — must use UDP).
- **Codec:** **MJPEG** (each frame a standalone baseline JPEG).
- **Resolution:** **240×320** (portrait sensor orientation).
- **Frame rate:** ~13 fps measured; lossy ("Missing packets; dropping frame"
  under weak signal is normal for these toy drones, range < ~30 m).

Verified working capture commands:

```bash
ffmpeg -rtsp_transport udp -i rtsp://192.168.1.1:7070/webcam -frames:v 1 -update 1 out.jpg
ffprobe -rtsp_transport udp rtsp://192.168.1.1:7070/webcam
```

> Note: the app *records* to local H.264 MP4, but that's a re-encode on the
> phone. The **wire format is JPEG**, confirmed by ffprobe.

---

## 5. Control & telemetry protocol (UDP 7099)

The phone app runs a **20 Hz** control loop. One UDP socket bound to an ephemeral
local port on `.100`, sending to `192.168.1.1:7099`; the drone replies to that
same source port.

### Telemetry the drone sends (observed)
Repeating once `.100` is held:

```
53 01 00 00 00
```

- Byte 0 = `0x53` = **83** = the drone's model / resolution capability ID.
- 83 is **not** in the GL id set (`90–101, 103, 82, 85`), so this drone is a
  **TC / "short packet" variant** — it expects the 9-byte control frame below.

App-level meaning of received bytes (from app reversing):
- `bArr[2] == 0x4D` (`'M'`) → a **photo** was taken; new count in `bArr[3]`.
- `bArr[2] == 0x58` (`'X'`) → a **video** was recorded; new count in `bArr[4]`.

### Discrete commands the app sends (from app reversing)
| Bytes | Meaning |
|---|---|
| `01 01` | heartbeat, once per second while preview/control active |
| `08 01` | leave flight-control mode |
| `06 01` / `06 02` | select front / rear camera |
| `09 01` / `09 02` | photo / video gallery sync |
| `0a d0..d7` | set 8-digit WiFi password (RC UFO only) |

### TC / short flight packet (9 bytes) — what flies this drone
```
03 66 B1 B2 ACC TURN FLAGS CKSUM 99
```
| Byte | Field |
|---|---|
| 0 | `0x03` WiFi wrapper prefix |
| 1 | `0x66` start marker |
| 2 | `B1` = roll  (`controlByte1`) |
| 3 | `B2` = pitch (`controlByte2`) |
| 4 | `ACC` = throttle (`controlAccelerator`; value `1` is coerced to `0`) |
| 5 | `TURN` = yaw (`controlTurn`) |
| 6 | `FLAGS` |
| 7 | `CKSUM` = `B1 ^ B2 ^ ACC ^ TURN ^ FLAGS` |
| 8 | `0x99` end marker |

Stick byte semantics: **center 128**, min 1, max 255. (We clamp to 68–188 for
safe control travel.)

`FLAGS` bits:
| Bit | Function |
|---|---|
| `0x01` | fast-up / takeoff |
| `0x02` | fast-down / land |
| `0x04` | emergency stop |
| `0x08` | flip / circle |
| `0x10` | headless mode |
| `0x20` | fast-return / unlock |
| `0x80` | gyro calibration |

> There's also a 21-byte **GL** variant (`03 66 14 …`) for GL-class models, but
> this drone (id 83) is TC, so the 9-byte packet is the one to use.

⚠️ **Safety:** sending these packets spins the motors. `webapp.py` keeps motors
disarmed until you ARM, has a spacebar e-stop, and a 0.5 s input watchdog.

---

## 6. How the tools use all this

| Tool / command | Mechanism |
|---|---|
| `ufo.py connect` | nmcli connect loop + pin `.100` + ping, retries through power-on |
| `ufo.py net` | pins `.100` to the NM profile, asserts it at runtime, pings |
| `ufo.py snapshot` | `ffmpeg … -frames:v 1` against the RTSP URL |
| `ufo.py record` | `ffmpeg` RTSP → H.264 MP4 |
| `ufo.py live` | `ffmpeg … -f mjpeg pipe:1`, split JPEGs, re-serve as multipart |
| `ufo.py telemetry` | binds `.100`:ephemeral, heartbeats, decodes replies |
| `webapp.py` | all of the above in one page + 20 Hz keyboard flight + self-heal |

---

## 7. Reproduction checklist (cold start)

1. Tether phone over USB (internet) — confirm `curl https://google.com` works.
2. `python3 ufo.py connect` → joins `WIFI-UFO-600849`, pins/asserts `.100`, pings.
3. `python3 webapp.py` → open `http://localhost:8088`, see camera + telemetry.
4. Telemetry shows `53 01 00 00 00` decoded as id 83 / TC.

---

## 8. Status / next steps

- **Video + telemetry + keyboard flight: DONE** and verified live (tcpdump shows
  9-byte TC packets to `192.168.1.1:7099` at 20 Hz + `01 01` heartbeat).
- **Persistence: DONE.** `.100` pinned to the NM profile + self-healed.
- **Robust connect: DONE.** `ufo.py connect` / `start.sh` retry through power-on.

### Physical remote control coexistence
- The drone's **FPV WiFi** (camera + telemetry, 2.4 GHz) is independent of the
  **handheld remote's** 2.4 GHz RF link. Video/telemetry on the laptop works fine
  **alongside** flying with the physical remote.
- **Stick control from both at once will conflict** — no multi-master
  arbitration. Recommended: remote = flight, laptop = video/telemetry/capture.

### Possible future work
- Photo/SD browsing via `http://192.168.1.1/PHOTO/`.
- Dual-camera switch (`06 01` / `06 02`).
- GL 21-byte variant support for GL-class models.
