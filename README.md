# ToyDroneController

Reverse-engineered control interface for **cooingdv**-family WiFi toy quadcopters
(RC UFO, KY UFO, and OEM variants). Replace the closed-source mobile app with
your own desktop control — keyboard, joystick, or web dashboard.

## Features

- **Live FPV video** — RTSP MJPEG stream with 90° rotation, 3× display
- **Keyboard control** — arrows = pitch/roll, W/S = throttle, A/D = yaw, T = takeoff, L = land, A = arm, D = disarm, Backspace = takeoff, Space = emergency stop
- **Virtual joysticks** — drag-to-fly (mouse + touch), left stick = throttle/yaw, right stick = pitch/roll
- **Two 3D drone views** — top and back with real-time pitch/roll/yaw angle readout
- **Real-time telemetry** — pitch, roll, throttle, yaw, flags, WiFi signal, protocol variant, resolution ID, raw RX packet hex
- **Flight timer** — auto-counter displayed on dashboard
- **Video recording** — one-click record button saves to `~/Videos/` as MKV (H.264)
- **Collapsible help** — organized card layout: Movement, Altitude, Rotation, Takeoff, Land, Emergency
- **CLI tools** — standalone viewer, keyboard-only control, photo/video capture, protocol probe
- **Web dashboard** — all-in-one: video, joysticks, telemetry, controls, recording, flight timer

## Quick Start

```bash
# Connect to drone WiFi
nmcli dev wifi connect WIFI-UFO-XXXXXX

# Start the web dashboard
cd turbodrone/backend
DRONE_TYPE=cooingdv DRONE_IP=192.168.1.1 uvicorn web_server:app --host 0.0.0.0 --port 8000

# Open http://localhost:8000
```

## Architecture

```
┌─────────────────┐         WiFi 2.4 GHz        ┌─────────────────┐
│     Drone       │◄───────────────────────────►│  ToyDroneCtrl   │
│  192.168.1.1    │   UDP :7099 (control)       │  Web dashboard  │
│  MAC: C2:A9:A5  │   RTSP :7070 (video)        │  CLI tools      │
└─────────────────┘                              └─────────────────┘
```

### Protocol

| Layer | Detail |
|-------|--------|
| Control | UDP port 7099, heartbeat `[0x01, 0x01]` every 1s |
| Packets | 9-byte TC (short) or 21-byte GL (extended) |
| Checksum | XOR of axis bytes |
| Video | RTSP `rtsp://192.168.1.1:7070/webcam` |
| Codec | MJPEG 240×320 @ ~18 fps |
| Telemetry | 5-byte status on same UDP socket |
| Chipset | Jieli WiFi module (cooingdv firmware) |

## Project Structure

```
ToyDroneController/
├── drone.sh              # CLI launcher (connect, view, control, web)
├── view.py               # Standalone RTSP stream viewer
├── control.py            # Keyboard-only drone control
├── capture.py            # Photo/video capture tool
├── docs/
│   ├── CONTROL_PROTOCOL.md       # UDP control protocol spec
│   ├── VIDEO_STREAM.md           # RTSP video stream spec
│   └── REVERSE_ENGINEERING.md    # How the protocol was RE'd
├── tools/
│   ├── scan.py           # Network discovery for drones
│   └── probe.py          # Protocol fuzzer
├── turbodrone/           # Web dashboard backend
│   └── backend/
│       ├── web_server.py         # FastAPI server
│       ├── protocols/            # Protocol adapters (cooingdv, s2x, etc.)
│       ├── models/               # RC models
│       ├── services/             # Flight controller, video receiver
│       └── static/index.html     # Dashboard frontend
└── docs/                 # Protocol documentation
```

## Documentation

| Document | Description |
|----------|-------------|
| [docs/CONTROL_PROTOCOL.md](docs/CONTROL_PROTOCOL.md) | Full UDP control packet specification |
| [docs/VIDEO_STREAM.md](docs/VIDEO_STREAM.md) | RTSP stream details and telemetry OSD |
| [docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md) | Methodology for RE these drones |

## Requirements

- Linux with `nmcli`, `iwconfig`, Python 3.10+
- WiFi adapter capable of connecting to drone's 2.4 GHz AP
- Python packages: `opencv-python`, `fastapi`, `uvicorn`

```bash
pip install opencv-python fastapi uvicorn
```

## Drone Compatibility

| App | Package | Status |
|-----|---------|--------|
| RC UFO | `com.cooingdv.rcufo` | Tested |
| KY UFO | `com.cooingdv.kyufo` | Supported |
| RC FPV | `com.cooingdv.rcfpv` | Supported |

## License

MIT

## Acknowledgments

- [TurboDrone](https://github.com/marshallrichards/turbodrone) — Marshall Richards' RE framework
