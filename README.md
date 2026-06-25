# RC UFO Drone — Protocol Analysis & Tools

Complete reverse-engineering report for **cooingdv**-family WiFi quadcopters
(RC UFO, KY UFO, etc.).

## Quick Start

```bash
# 1. One-command full startup (connect + web + controls)
./drone.sh all

# 2. Or step by step:
./drone.sh connect     # Connect to drone WiFi
./drone.sh view        # View camera stream
./drone.sh control     # Keyboard control + live video
./drone.sh web         # TurboDrone web dashboard

# 3. Check connection
./drone.sh status
```

## Dual-Network Setup

The drone creates its own WiFi network. To keep **internet access**
while controlling the drone, use two interfaces:

```
┌─────────────────┐     USB Tether     ┌──────────────┐     Internet
│  Phone (USB)    │◄──────────────────►│   Laptop      │◄────────────►
│  10.148.252.x   │     metric 100     │   wlp5s0     │
└─────────────────┘                    │   192.168.1.x │
                                       │   enx...     │
                                       │   10.148.x    │
                                       └──────┬───────┘
                                              │ WiFi Direct
                                              ▼
                                       ┌──────────────┐
                                       │    Drone      │
                                       │  192.168.1.1  │
                                       └──────────────┘
```

**Routing logic:**
- Internet traffic → USB tether `enx...` (default route, metric 100)
- Drone traffic → WiFi `wlp5s0` (direct subnet `192.168.1.0/24`)
- The kernel automatically picks the right interface per destination

## Documentation

| Doc | Description |
|-----|-------------|
| [docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md) | How the protocol was reverse-engineered |
| [docs/CONTROL_PROTOCOL.md](docs/CONTROL_PROTOCOL.md) | Full UDP control protocol specification |
| [docs/VIDEO_STREAM.md](docs/VIDEO_STREAM.md) | RTSP video stream details |

## Tools

| Tool | Description |
|------|-------------|
| `drone.sh` | **Main launcher** — connect, view, control, web app |
| `view.py` | RTSP stream viewer |
| `control.py` | Keyboard control + live video + HUD |
| `capture.py` | Photo/video capture |
| `tools/scan.py` | Network scanner for drone discovery |
| `tools/probe.py` | Protocol fuzzer |

## Protocol Reference

```
Control:  UDP 192.168.1.1:7099
           Heartbeat: [0x01, 0x01] every 1s
           TC packet: 03 66 RR PP TT YY FF CS 99
           GL packet: 03 66 14 RR PP TT YY F1 F2 [9x 00] CS 99

Video:    RTSP rtsp://192.168.1.1:7070/webcam
           MJPEG 240×320 @ ~18 fps

Telemetry: Drone → Client on same UDP socket
           53 01 00 00 00 (5 bytes)
```

## Startup Script

`drone.sh` handles the entire workflow:

```bash
./drone.sh connect         # Auto-detect & connect to drone WiFi
./drone.sh status          # Show connection/routing state
./drone.sh view            # Open camera viewer
./drone.sh control         # Keyboard control (WASD + arrows)
./drone.sh web             # TurboDrone web dashboard
./drone.sh all             # Everything at once
```

## Troubleshooting

**Drone not reachable:**
```bash
./drone.sh status              # Check connection state
iwconfig wlp5s0                # Check WiFi signal
signal level should be > -70 dBm
```

**Web app no video:**
The RTSP stream drops packets below -70 dBm. Move closer to the drone
or use `python3 control.py` which has more robust frame handling.

**Lost drone WiFi:**
```bash
./drone.sh connect             # Reconnect automatically
```

## References

- [TurboDrone](https://github.com/marshallrichards/turbodrone)
- [RC UFO Play Store](https://play.google.com/store/apps/details?id=com.cooingdv.rcufo)
