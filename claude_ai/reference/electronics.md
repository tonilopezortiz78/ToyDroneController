# RC UFO drone — electronics research

**Question:** is the drone an ESP32?
**Short answer: No — almost certainly not.** The evidence points to a **Jieli
"BL60x"-class WiFi camera SoC** for video/comms, plus a **separate flight-control
MCU with an onboard gyro/accelerometer**. Researched 2026-06-25 by Claude AI.

> Confidence: high on "not an ESP32" and "Jieli BL60x WiFi-cam family"; the exact
> silicon part number can only be 100% confirmed by opening the drone and reading
> the chip markings (or its FCC ID). See "How to confirm" below.

---

## The strongest evidence — the drone's own app

The decompiled **RC UFO** app (`com.cooingdv.rcufo`) and its siblings ship a
native video/control engine in a package literally named:

```
com.cooingdv.bl60xmjpeg      ->  libuav_gl.so , libuav_tc.so
```

and a secondary **"Jieli CTP"** backend (`DeviceClient`, `CTP:` packets). The
native libraries contain a hardware-style **MJPEG fragment-reassembly engine**
with built-in `jpeg_header_640x360_Q{5,10,25,50,75,100}` tables and a native
transport to `192.168.169.1:8800`.

Two fingerprints there:
1. **"BL60x"** — the WiFi camera SoC family the firmware drives.
2. **"Jieli"** (Zhuhai JieLi Technology) — the silicon vendor.

That is device-specific, authoritative evidence: it's this drone's own software
naming its own chip. It is **not** Espressif/ESP naming.

## Why it's not an ESP32

- **Wrong market/architecture.** Mass-market ~$20 cooingdv toy FPV drones use
  cheap, dedicated **WiFi-camera SoCs** (Jieli BL60x, HiSilicon Hi3518, Beken
  BK7231, Anyka AK39xx…). ESP32 shows up in *DIY/hobby* drones, not these.
- **ESP32 has no matching pipeline.** ESP32-CAM streams by reading an external
  OV2640 over software — it has no built-in hardware MJPEG fragment engine like
  the one in this drone's `libuav_*` native code.
- **Naming.** The app names the chip `bl60x` + `Jieli`, never Espressif/ESP.

(General ESP32 background: it's an Espressif WiFi+BT MCU popular in DIY drones —
[ESP32 wiki](https://en.wikipedia.org/wiki/ESP32),
[DIY ESP32 drone](https://circuitdigest.com/microcontroller-projects/DIY-wifi-controlled-drone).
Jieli is a separate vendor — [JieLi GitHub](https://github.com/Jieli-Tech),
[chip-mark catalog](https://kagaimiq.github.io/jielie/chips/chip-marks.html),
which publicly documents their BT/audio chips; their WiFi-cam line is sparsely
documented but is what the app drives.)

## Likely board architecture (typical for this class, consistent with our data)

```
                 2.4 GHz RF (XN297 / nRF24-class)
   handheld RC  <─────────────────────────────────►  ┌─────────────────┐
                                                       │  Flight MCU     │
   phone / laptop                                      │  (ARM Cortex-M0 │
   (WiFi-UFO AP)   WiFi 2.4 GHz                        │   or dedicated  │
        │   ┌───────────────────────┐   stick cmds     │   drone IC)     │
        └──►│  Jieli BL60x WiFi-cam  │─────────────────►│  + GYRO/ACCEL   │
            │  SoC  (192.168.1.1)    │                  │  (MPU6050-class)│
            │  • AP + DHCP           │◄─── status ──────│  • motor mixing │
            │  • RTSP/MJPEG :7070    │                  │  • altitude hold│
            │  • UDP control :7099   │                  └─────────────────┘
            │  • camera (640x360→     │                          │
            │    240x320 stream)     │                     4× motor ESC/FET
            └───────────────────────┘
```

- **Jieli BL60x WiFi-cam SoC**: runs the `WIFI-UFO-600849` access point, the
  RTSP/MJPEG video, and the UDP control socket (7099). It is the chip our cockpit
  talks to. It forwards stick packets to the flight MCU and sends back the tiny
  `53 01 00 00 00` status heartbeat.
- **Separate flight MCU + IMU**: a small MCU with an **MPU6050-class gyro +
  accelerometer** does the actual stabilization and altitude hold. (Cheap toy
  drones routinely keep the IMU on a separate flight board over I²C —
  [MPU6050 overview](https://www.amazon.com/MPU-6050-Accelerometer-Sensor-Module-Arduino/dp/B09NCFHHYG).)
- **Physical remote** talks to the flight MCU over a 2.4 GHz RF transceiver
  (XN297/nRF24-class), independent of WiFi — which is why you can fly with the
  remote while the laptop watches video. (Toy-drone teardowns commonly find an
  STM32F0 + XN297 — [AllAboutCircuits micro-drone teardown](https://www.allaboutcircuits.com/news/teardown-tuesday-micro-drone/).)

## Why this explains the battery / accelerometer question

The drone **does physically have a gyro + accelerometer** (it needs them for
altitude-hold and stable flight) — but that data lives on the **flight MCU** and
is **never bridged to the WiFi SoC's telemetry**. The WiFi SoC only emits the
5-byte status heartbeat. So:

- **Battery, voltage, accelerometer, attitude → not available over WiFi.** Not a
  bug in our code or deepseek_pro's — the firmware simply doesn't expose it.
- That's why the cockpit shows battery/attitude as **estimates**, and why the
  only real low-battery signal is the drone's **LED blink + auto-land/power-off**.

## How to confirm the exact chip (when the drone is free)

1. **Open it and read the markings.** Photograph the main PCB. Jieli parts have a
   distinctive marking format (decode via
   [kagaimiq's chip-marks guide](https://kagaimiq.github.io/jielie/chips/chip-marks.html)).
   Look for a large QFN/LGA SoC near the camera ribbon (the BL60x) and a smaller
   MCU + a 3×3 mm IMU (MPU6050-class) near the motor connectors.
2. **FCC ID.** If there's an FCC ID on the battery bay / label, the FCC OET
   database has internal photos and often a parts list.
3. **Firmware probe (non-destructive).** While connected, probe the SoC's HTTP
   server for version strings: `http://192.168.1.1/` , `/version`, `/cgi-bin/`,
   and the FTP banner (`ftp`/`ftp`) — Jieli/IPC firmwares often leak a build id.
   (Do this when you're not flying.)

## Hidden-battery probe results (2026-06-25, drone live)

Route 1 ("can we find battery over WiFi without opening it") was run end-to-end:

- **HTTP**: scanned `192.168.1.1` ports 80/8080/7070/81/8000 across ~18 paths
  (`/version`, `/status`, `/battery`, `/cgi-bin/...getbatteryinfo`, etc.) →
  **no HTTP server at all**. (The `/PHOTO/`,`/DCIM/` HTTP + FTP features belong to
  *other* cooingdv models, not RC UFO.)
- **FTP**: port 21 **connection refused**.
- **UDP 7099 opcode sweep**: sent opcodes `0x00–0xFF` (skipping the unsafe
  `0x03` control, `0x08` leave-control, `0x0a` password) in 1- and 2-byte
  framings, watching for any reply that was longer or different.
  → The drone **only ever returns two 5-byte packets**: `53 01 00 00 00`
  (status / model id 83) and `01 01 00 00 00` (heartbeat ack). **No opcode
  produced extended data, voltage, or anything battery-shaped.**

**Verdict: battery is NOT exposed over WiFi on this drone — confirmed by probe,
not just inference.** Remaining routes are invasive (UART sniff + firmware patch)
or additive (own voltage sensor). See table in the chat / route list above.

## Sources
- [RC UFO app — Google Play](https://play.google.com/store/apps/details?id=com.cooingdv.rcufo)
- [Zhuhai JieLi Technology — GitHub](https://github.com/Jieli-Tech) ·
  [JieLi chip markings](https://kagaimiq.github.io/jielie/chips/chip-marks.html)
- [IP-camera SoC list (Hi3518, etc.)](https://www.unifore.net/ip-video-surveillance/list-of-popular-soc-processors-for-ip-security-cameras.html)
- [Teardown Tuesday: micro drone (STM32F0 + XN297)](https://www.allaboutcircuits.com/news/teardown-tuesday-micro-drone/)
- [ESP32 — Wikipedia](https://en.wikipedia.org/wiki/ESP32) ·
  [MPU6050 IMU](https://www.amazon.com/MPU-6050-Accelerometer-Sensor-Module-Arduino/dp/B09NCFHHYG)
- Primary: TurboDrone cooingdv reverse-engineering (preserved in
  `reference/cooingdv_protocol.md`).
