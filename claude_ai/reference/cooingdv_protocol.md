# CooingDV Protocol Reference

> Distilled and preserved by Claude AI from the open-source **TurboDrone** project
> (`github.com/marshallrichards/turbodrone`, `docs/research/cooingdv.md`) and
> confirmed live against the RC UFO drone (`com.cooingdv.rcufo`). Kept here so
> `claude_ai/` stays self-contained. This is the authoritative protocol summary
> for the cooingdv app family (RC UFO, KY UFO, RC FPV, KY FPV, 4DRC FPV).

## Executive summary

CooingDV WiFi drones use a simple control plane:

- Drone IP: `192.168.1.1`
- RC / command + telemetry: **UDP port `7099`**
- Preview video: **`rtsp://192.168.1.1:7070/webcam`** (MJPEG over RTP/UDP)
- HTTP media: `http://192.168.1.1/PHOTO/…` and `/DCIM/…`
- FTP media: root `/0/`, user `ftp`, pass `ftp`
- RC loop runs at **20 Hz** (app schedules a TimerTask every 50 ms).

Two control packet families, both UDP-wrapped with a leading `0x03`:
- **TC / short**: 9 bytes total. Used when device type == 10.
- **GL / extended**: 21 bytes total. Used for GL-class models (device type != 10).

## Network constants (from `Config.java`)

```
SERVER_IP        = 192.168.1.1
SERVER_PORT      = 7070            (RTSP)
PREVIEW_ADDRESS  = rtsp://192.168.1.1:7070/webcam
RC/CMD UDP PORT  = 7099
TCP_SERVER_PORT  = 5000            (defined but UNUSED for control)
FTP_HOST         = 192.168.1.1   user=ftp pass=ftp  root=/0/
VIDEO_PATH=DCIM  IMAGE_PATH=PHOTO
```

## UDP session lifecycle

- App opens `new DatagramSocket()` (ephemeral local port), sends to `192.168.1.1:7099`,
  receives on the same socket. Drone replies to the source port.
- Receive buffer is 20 bytes in the app, so app-visible telemetry is <= 20 bytes.
- **Heartbeat** `01 01` every 1000 ms while preview/control active.
- **Leave control** `08 01` when the control timer is cancelled (not an init packet).

## Discrete UDP commands (port 7099)

| Bytes | Meaning |
|---|---|
| `01 01` | heartbeat |
| `08 01` | leave flight-control mode |
| `06 01` / `06 02` | select camera (front / rear) |
| `09 01` / `09 02` | photo / video gallery sync |
| `0a d0 d1 … d7` | set 8-digit WiFi password (RC UFO; each dN a digit byte) |

## Flight axes

App stick byte names: `controlByte1` (roll), `controlByte2` (pitch),
`controlAccelerator` (throttle), `controlTurn` (yaw).
Byte-centered: center **128**, min **1**, max **255**. If `ACC == 1` the app
writes `0`. TurboDrone limits normal motion to a safe **50..200** band; this
toolkit uses **68..188**.

## TC / short flight packet (9 bytes)

```
03 66 B1 B2 ACC TURN FLAGS CKSUM 99
```
- byte0 `0x03` WiFi wrapper · byte1 `0x66` start
- B1=roll, B2=pitch, ACC=throttle (1->0), TURN=yaw
- FLAGS · CKSUM = `B1 ^ B2 ^ ACC ^ TURN ^ FLAGS` · byte8 `0x99` end

TC FLAGS bits:
```
0x01 fastFly/up    0x02 fastDrop/down   0x04 emergencyStop   0x08 circle/flip
0x10 noHead/headless                    0x20 fastReturn||unlock
0x40 (KY openLight)                     0x80 gyroCorrection
```

## GL / extended flight packet (21 bytes)

```
03 66 14 B1 B2 ACC TURN FLAGS1 FLAGS2 00 00 00 00 00 00 00 00 00 00 CKSUM 99
```
- byte2 `0x14` (20) extended marker · CKSUM = `B1^B2^ACC^TURN^FLAGS1^FLAGS2`

GL FLAGS1: `0x01 fastFly||fastDrop · 0x02 eStop · 0x04 gyro · 0x08 circle ·
0x10 (KY light) · 0x40 gesture`. GL FLAGS2: `0x01 noHead · 0x02 fixedHeight`.

## Variant detection (which packet to send)

The app picks TC vs GL from the **first byte** of received UDP telemetry (a
model/resolution capability ID):

- **GL ids:** `90..101, 103` (+ RC adds `82, 85`).
- Anything else among the known ids -> **TC**.
- Until a known id is seen, fall back to TC.

This drone reports id **83** -> **TC**. (Telemetry observed: `53 01 00 00 00`,
`0x53` = 83.)

## Telemetry / app messages

- `bArr[2] == 0x4D` (`'M'`) -> photo event; count in `bArr[3]`; app replies `09 01`.
- `bArr[2] == 0x58` (`'X'`) -> video event; count in `bArr[4]`; app replies `09 02`.
- RC UFO GL status: when device type == 2 and `bArr[0] == 0x66`, reads state from
  `bArr[2]`/`bArr[9]`; packet length 10 or 15 reads a state byte from `bArr[4]`.

## Video

- Classic WiFi path: **RTSP** `rtsp://192.168.1.1:7070/webcam`, **MJPEG** frames
  (app decodes with `BitmapFactory.decodeByteArray`). Use **RTP over UDP**.
- App can re-encode displayed frames to local H.264 MP4 for recording — that is
  NOT evidence the wire stream is H.264. Wire format is JPEG.

## Other backends in the app family (not used by RC UFO)

- **BL60x native** (`UAV`/`libuav_gl.so`/`libuav_tc.so`, KY UFO/4DRC): native
  MJPEG transport to `192.168.169.1:8800`, start packet `ef 00 04 00`, incoming
  envelope marker `0x93`, JPEG reassembly from 1024-byte fragments, built-in
  `jpeg_header_640x360_Q{5,10,25,50,75,100}` tables. TurboDrone backend name
  `cooingdv_bl` (research-only).
- **Jieli / CTP JSON** (KY FPV `DeviceClient`): `192.168.8.15:2228`, `CTP:`
  envelope with JSON `FLYING_CTRL` (`BYTE0..BYTE7`). TurboDrone backend
  `cooingdv_jieli`.
- **4DRC excavator** (`com.cooingdv.fpv4drc`): ground-vehicle packet
  `03 33 TURN ACC BUCKET MODE 88` — not a quadcopter packet.

## Compatibility note for RC UFO

RC UFO (`com.cooingdv.rcufo`) is the closest match to TurboDrone's classic
`cooingdv` adapter: UDP `7099`, RTSP `7070`, TC/GL auto-detect, password-capable
ids, GL status telemetry. Our drone is the **TC** path (id 83).
