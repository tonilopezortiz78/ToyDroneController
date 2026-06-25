# Video Stream — RTSP :7070

Complete specification of the cooingdv FPV video stream.

---

## Overview

The drone broadcasts a live camera feed via **RTSP** on port **7070**.

### Network Requirements

The drone creates its own WiFi network. To maintain internet access
while viewing the stream, use a **dual-network setup**:

```
USB Tether (internet)     Drone WiFi (video + control)
     │                          │
     │  default route           │  direct subnet
     │  metric 100              │  192.168.1.0/24
     ▼                          ▼
enx8e8099007a7f           wlp5s0
10.148.252.x              192.168.1.101
```

- **Drone traffic** (`192.168.1.x`) → WiFi interface (automatic route)
- **Internet traffic** → USB tether (lower metric default route)
- No manual routing needed — the kernel handles it

**Signal strength** directly affects stream quality:

| Signal | Quality | Behavior |
|--------|---------|----------|
| > -60 dBm | Excellent | Smooth 18 fps |
| -60 to -70 dBm | Good | Occasional packet loss |
| -70 to -80 dBm | Poor | Frequent frame drops |
| < -80 dBm | Lost | Stream disconnects |

Check signal: `iwconfig wlp5s0 | grep Signal`
The stream is Motion JPEG (MJPEG) carried over RTP, typically at
240×320 resolution and ~18 fps.

## Stream URL

```
rtsp://192.168.1.1:7070/webcam
```

## Stream Properties

Measured via `ffprobe`:

| Property | Value |
|----------|-------|
| Codec | Motion JPEG (MJPEG) |
| Resolution | 240 × 320 (portrait) |
| Frame rate | ~18.5 fps (37/2) |
| Pixel format | yuvj420p |
| Color space | BT.470 BG |
| Color range | Full (PC) |
| Time base | 1/90000 |
| Transport | RTP over UDP (default) |

## Telemetry Overlay

The camera feed includes **visual telemetry overlaid on the video**
by the drone's firmware. The OSD typically shows:

```
                    ┌──────────────────────┐
                    │                      │
                    │   [BAT: 3.85V]       │
                    │   [H: 1.2m]          │
                    │   [S: 80%]           │
                    │                      │
                    │   [CAMERA FEED]      │
                    │                      │
                    │                      │
                    │   [MODE: ALT-HOLD]   │
                    └──────────────────────┘
```

Common telemetry elements in the video overlay:

| Element | Description | Format |
|---------|-------------|--------|
| Battery | Voltage level | `XX.X V` or bar icon |
| Altitude | Barometric altitude | `X.X m` |
| Signal | WiFi signal strength | `XX%` or bars |
| Flight mode | Current mode | Text label |
| Headless | Headless mode indicator | Icon/text |
| Recording | Recording status | Red dot / `REC` |

This telemetry is **burned into the video frame** by the drone's
camera chip — it cannot be disabled or separated from the video
stream without image processing.

## Accessing the Stream

### OpenCV (Python)

```python
import cv2

cap = cv2.VideoCapture(
    "rtsp://192.168.1.1:7070/webcam",
    cv2.CAP_FFMPEG
)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

while True:
    ret, frame = cap.read()
    if ret:
        cv2.imshow("Drone Feed", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

cap.release()
```

### FFplay

```bash
ffplay rtsp://192.168.1.1:7070/webcam
```

### FFmpeg (record to file)

```bash
ffmpeg -i rtsp://192.168.1.1:7070/webcam -vcodec copy output.mkv
```

### Browser (WebSocket proxy)

For browser viewing, the stream must be proxied since browsers don't
support RTSP natively. TurboDrone uses a Python WebSocket server that
reads from OpenCV and serves JPEG frames to a web client.

## Known Issues

### RTP Packet Loss

The drone's RTSP implementation is minimal and may drop RTP packets
under poor WiFi conditions. FFmpeg/OpenCV will log:

```
Missing packets; dropping frame.
Received packet without a start chunk; dropping frame.
RTP timestamps don't match.
```

This is normal for this class of drone. The stream recovers
automatically. Mitigations:

- Minimize distance from drone
- Reduce 2.4 GHz interference
- Use a dedicated WiFi adapter

### Unsupported Transport

The drone only supports RTP over UDP transport. Setting
`-rtsp_transport tcp` will fail with:

```
method SETUP failed: 461 Unsupported Transport
```

### No Audio

The RTSP stream contains video only — no audio channel is present.

## Comparison: RTSP vs Raw UDP Video

Some cooingdv-family drones use different video delivery methods:

| Method | Quality | Latency | Complexity |
|--------|---------|---------|------------|
| **RTSP MJPEG** (this drone) | Low (240p) | Medium | Simple |
| RTP/JPEG on UDP :6666 | Variable | Low | Moderate |
| Raw UDP JPEG fragments | Variable | Low | Complex |
| H.265 on UDP :1234 | High (720p) | Low | Complex |

The Jieli variant uses RTP/JPEG on a negotiated UDP port with an SDP
exchange. See TurboDrone's `cooingdv_jieli_video_protocol.py` for that
implementation.

## References

- [TurboDrone cooingdv video protocol](https://github.com/marshallrichards/turbodrone/blob/master/backend/protocols/cooingdv_video_protocol.py)
- [TurboDrone cooingdv-jieli video protocol](https://github.com/marshallrichards/turbodrone/blob/master/backend/protocols/cooingdv_jieli_video_protocol.py)
