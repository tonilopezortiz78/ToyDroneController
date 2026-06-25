# Control Protocol — UDP :7099

Complete specification of the cooingdv RC control protocol.

---

## Overview

Control happens over **UDP port 7099**. The client sends a 2-byte
heartbeat every second and 9-byte (or 21-byte) control packets to
fly the drone. Telemetry comes back on the same socket.

## Heartbeat

Sent once per second to keep the UDP session alive:

```
Offset  Bytes  Value    Description
──────  ─────  ─────    ───────────
0       1      0x01     Heartbeat marker
1       1      0x01     Heartbeat marker

Raw: 01 01
```

Without the heartbeat, the drone stops responding within ~5 seconds
and the RTSP stream may also drop.

## Control Packets

Two packet variants exist: **TC** (short/9-byte) and **GL** (extended/
21-byte). The variant is auto-detected from the telemetry response.

### TC Variant (9 bytes)

```
Byte  Size  Value    Description
────  ────  ─────    ───────────
0     1     0x03     Prefix / sync byte
1     1     0x66     Start marker
2     1     0–255    Roll      (128 = center, 0 = left, 255 = right)
3     1     0–255    Pitch     (128 = center, 0 = forward, 255 = back)
4     1     0–255    Throttle  (0 = min, 255 = max)
5     1     0–255    Yaw       (128 = center, 0 = left, 255 = right)
6     1     bitmask  Flags
7     1              XOR checksum of bytes 2..6
8     1     0x99     End marker
```

#### TC Flags (byte 6)

| Bit | Mask | Flag | Description |
|-----|------|------|-------------|
| 0 | `0x01` | Takeoff | Auto-takeoff |
| 1 | `0x02` | Land | Auto-land |
| 2 | `0x04` | Stop | Emergency stop (drone falls) |
| 3 | `0x08` | Flip | Perform a flip |
| 4 | `0x10` | Headless | Headless mode toggle |
| 7 | `0x80` | Calibrate | Gyro calibration |

#### TC Checksum

```python
checksum = roll ^ pitch ^ throttle ^ yaw ^ flags
```

XOR of bytes 2, 3, 4, 5, 6. Result stored in byte 7.

### GL Variant (21 bytes)

```
Byte  Size  Value    Description
────  ────  ─────    ───────────
0     1     0x03     Prefix / sync byte
1     1     0x66     Extended marker 1
2     1     0x14     Extended marker 2
3     1     0–255    Roll
4     1     0–255    Pitch
5     1     0–255    Throttle
6     1     0–255    Yaw
7     1             Flag group 1
8     1             Flag group 2
9–17  9     0x00    Padding (zeros)
18    1             XOR checksum of bytes 3..8
19    1     0x99    End marker
```

#### GL Flag Groups

**Flags 1 (byte 7):**

| Bit | Mask | Flag |
|-----|------|------|
| 0 | `0x01` | One-key action (takeoff/land) |
| 1 | `0x02` | Stop |
| 2 | `0x04` | Calibrate |
| 3 | `0x08` | Flip |

**Flags 2 (byte 8):**

| Bit | Mask | Flag |
|-----|------|------|
| 0 | `0x01` | Headless mode |

#### GL Checksum

```python
checksum = roll ^ pitch ^ throttle ^ yaw ^ flags1 ^ flags2
```

---

## Telemetry (Drone → Client)

The drone sends status packets back to the client's UDP source port.

```
53 01 00 00 00     — Status heartbeat (5 bytes)
```

The first byte of the telemetry packet is used to auto-detect the
control variant:

| First Byte | Variant | Meaning |
|-----------|---------|---------|
| 82, 85, 90–105 | GL | Extended control |
| Other known IDs | TC | Short control |
| Unknown | — | Wait for next packet |

**Known resolution IDs:** 3, 5, 9, 11, 12, 19, 20, 21, 23, 24, 26,
27, 29, 30, 31, 41, 43, 44, 45, 51, 63, 64, 65, 66, 67, 68, 69, 70,
71, 72, 80, 81, 82, 83, 84, 85, 86, 87, 90, 91, 92, 93, 94, 95, 96,
97, 98, 99, 100, 101, 103, 105

---

## Implementation

### Python Control Example

```python
import socket
import struct
import time

DRONE_IP = "192.168.1.1"
CTRL_PORT = 7099

def build_tc_packet(roll=128, pitch=128, throttle=0, yaw=128,
                    flags=0):
    data = bytes([0x03, 0x66, roll, pitch, throttle, yaw, flags])
    checksum = roll ^ pitch ^ throttle ^ yaw ^ flags
    return data + bytes([checksum, 0x99])

def send_heartbeat(sock):
    sock.sendto(bytes([0x01, 0x01]), (DRONE_IP, CTRL_PORT))

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("", 0))

# Keep-alive
send_heartbeat(sock)

# Arm / takeoff
pkt = build_tc_packet(throttle=0, flags=0x01)  # takeoff
sock.sendto(pkt, (DRONE_IP, CTRL_PORT))

# Fly: roll right
pkt = build_tc_packet(roll=200, throttle=150)
sock.sendto(pkt, (DRONE_IP, CTRL_PORT))
```

### CTP Command Protocol (Jieli variant)

Some cooingdv drones use a JSON-based CTP command protocol over UDP.
See the Jieli video protocol adapter in TurboDrone for details.

Commands sent as CTP packets:

```json
{"topic":"OPEN_RT_STREAM","params":{"format":"0","w":"640","h":"360","fps":"30"}}
{"topic":"CLOSE_RT_STREAM","params":{"status":"1"}}
```

---

## References

- [TurboDrone cooingdv RC adapter](https://github.com/marshallrichards/turbodrone/blob/master/backend/protocols/cooingdv_rc_protocol_adapter.py)
- [cooingdv RC model](https://github.com/marshallrichards/turbodrone/blob/master/backend/models/cooingdv_rc.py)
