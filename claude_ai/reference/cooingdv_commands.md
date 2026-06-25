# cooingdv — complete command & protocol reference

Every command/packet in the cooingdv app family (RC UFO, KY UFO, RC FPV, KY FPV,
4DRC FPV, HF UFO). **✅ = confirmed on our drone** (RC UFO, TC variant, model id
83). Others are from decompiled-app reverse engineering of the family.

---

## 1. Network constants

| Name | Value |
|---|---|
| Drone IP | `192.168.1.1` ✅ |
| RC / control + telemetry | **UDP `7099`** ✅ |
| Video (RTSP) | `rtsp://192.168.1.1:7070/webcam` ✅ (MJPEG/RTP-UDP) |
| `TCP_SERVER_PORT` | `5000` (defined, **unused**) |
| FTP media | `192.168.1.1:21`, user `ftp` / pass `ftp`, root `/0/` (other models; **not on RC UFO**) |
| HTTP media | `http://192.168.1.1/PHOTO/…`, `/DCIM/…` (other models; **not on RC UFO**) |
| RC loop rate | **20 Hz** (50 ms timer) ✅ |

---

## 2. Discrete UDP commands (port 7099)

| Bytes | Meaning |
|---|---|
| `01 01` | Heartbeat — once per second while preview/control active ✅ |
| `08 01` | Leave flight-control mode (sent when control timer cancels) |
| `06 01` | Select camera 1 (front / default) |
| `06 02` | Select camera 2 (rear / alternate) |
| `09 01` | Gallery sync — photo side |
| `09 02` | Gallery sync — video side |
| `0a d0 d1 d2 d3 d4 d5 d6 d7` | Set 8-digit WiFi password (RC UFO; each `dN` = one digit byte) ⚠️ can lock you out |

**Native-only (KY `UAV` path, not WiFi-7099):**
| Byte | Meaning |
|---|---|
| `64` (0x64) | Ack/activate native streaming after first frame |
| `63` (0x63) | After resolution/capability message |
| `65` (0x65) | Leave control while native `UAV` active |

---

## 3. TC / short flight packet — 9 bytes ✅ (what flies our drone)

```
03 66 B1 B2 ACC TURN FLAGS CKSUM 99
```
| Byte | Field |
|---|---|
| 0 | `0x03` WiFi wrapper prefix |
| 1 | `0x66` start marker |
| 2 | `B1` = roll  (center 128, 1=left, 255=right) |
| 3 | `B2` = pitch (center 128, 1=fwd, 255=back) |
| 4 | `ACC` = throttle (0=idle … 255; value `1` is coerced to `0`) |
| 5 | `TURN` = yaw (center 128) |
| 6 | `FLAGS` (below) |
| 7 | `CKSUM` = `B1 ^ B2 ^ ACC ^ TURN ^ FLAGS` |
| 8 | `0x99` end marker |

**TC FLAGS bits:**
| Bit | Function |
|---|---|
| `0x01` | fast-up / takeoff |
| `0x02` | fast-down / land |
| `0x04` | emergency stop |
| `0x08` | flip / circle-turn |
| `0x10` | headless (no-head) mode |
| `0x20` | fast-return / unlock |
| `0x40` | **LED light on** (KY uses it; RC UFO unconfirmed — testable) |
| `0x80` | gyro calibration |

---

## 4. GL / extended flight packet — 21 bytes (GL-class models, not ours)

```
03 66 14 B1 B2 ACC TURN FLAGS1 FLAGS2 00 00 00 00 00 00 00 00 00 00 CKSUM 99
```
- byte 2 = `0x14` (20) extended marker
- `CKSUM = B1 ^ B2 ^ ACC ^ TURN ^ FLAGS1 ^ FLAGS2`

**GL FLAGS1:** `0x01` fastFly||fastDrop · `0x02` e-stop · `0x04` gyro-calib ·
`0x08` circle/flip · `0x10` LED light (KY) · `0x40` gesture mode
**GL FLAGS2:** `0x01` headless · `0x02` **fixed-height (altitude hold)**

---

## 5. Telemetry (drone → client, ≤20 bytes)

| Pattern | Meaning |
|---|---|
| byte 0 | model / resolution capability ID (selects TC vs GL) |
| `53 01 00 00 00` | our drone's status heartbeat (id `0x53`=83 → TC) ✅ |
| `01 01 00 00 00` | heartbeat ack ✅ |
| `bArr[2] == 0x4D ('M')` | photo taken; count in `bArr[3]` → app replies `09 01` |
| `bArr[2] == 0x58 ('X')` | video recorded; count in `bArr[4]` → app replies `09 02` |
| RC GL status (`deviceType==2`, `bArr[0]==0x66`) | `bArr[1]==0` → state in `bArr[2]`,`bArr[9]`; len 10/15 → state in `bArr[4]` |

**No battery / voltage / IMU / distance fields exist** — confirmed by live probe. ✅

---

## 6. Variant-detection IDs (first telemetry byte)

- **GL ids:** `90–101, 103` (+ RC adds `82, 85`)
- **Password-capable (RC):** `80, 81, 82, 85`
- **Known resolution ids:** `3,5,9,11,12,19,20,21,23,24,26,27,29,30,31,41,43,44,45,
  51,63,64,65,66,67,68,69,70,71,72,80–87,90–101,103,105`
- Override: `COOINGDV_VARIANT=tc|gl`

---

## 7. Other backends in the family (NOT used by RC UFO)

**Native BL60x (KY UFO / 4DRC `libuav_*`):** target `192.168.169.1:8800`,
start packet `ef 00 04 00` (~every 100 ms), incoming marker `0x93`, 12-byte
envelope, JPEG fragment reassembly (1024-byte frags, EOI `ff d9`), ACKs ~every
25 ms. Magics: `0x02ef` ack, `0x04ef` settings, `0x20ef` custom, `0x10ef` query.

**Jieli CTP / JSON (KY FPV `DeviceClient`):** `192.168.8.15:2228`, envelope
`"CTP:" + u16 topic-len + topic + u32 json-len + json`. Flight via topic
`FLYING_CTRL` with `{BYTE0:102, BYTE1..6 = B1/B2/ACC/TURN/FLAGS/CKSUM, BYTE7:153}`.
Topics: `CONTROL_MODE, FLYING_CTRL, SWITCH_CAMERA, RT_PIC_POSITION_CTL,
REQUEST_FAKE_NUMBER`, legacy `0136,0138,0141–0147`.

**RxDrone / HF UFO (`com.xaufohf.rxdrone`):** `Protocol1` 13-byte `66 … 99 … A5`
hex-tunneled via topic `0139`; GPS UDP `192.168.80.1:3456`; CTP topics
`FLIGHT_CONTROL`/`DRONE_DATA` on TCP `3333`; Jieli video RTP `6666`, SDP `6789`,
AVPlayer UDP `2224`.

**4DRC excavator/drive mode (`EXCAVATOR_720=89`):** NOT a quadcopter packet —
```
03 33 TURN ACC BUCKET_FLAGS MODE_FLAGS 88
```
bucket: `0x01` rise · `0x02` drop · `0x04` left · `0x08` right ·
mode: `0x04` auto · `0x08` music · `0x10` light. (No checksum.)

---

## 8. What OUR drone (RC UFO, TC, id 83) actually uses

- ✅ UDP `7099`: `01 01` heartbeat, 9-byte TC control `03 66 … 99`, `06`/`08`/`09`.
- ✅ Video: RTSP `7070/webcam`, MJPEG 240×320.
- ✅ Telemetry: only `53 01 00 00 00` + `01 01 00 00 00`.
- ❓ Untested on ours: LED flag `0x40`, fixed-height (GL-only, likely absent).
- ❌ No HTTP, no FTP, no battery/IMU/distance over WiFi.
