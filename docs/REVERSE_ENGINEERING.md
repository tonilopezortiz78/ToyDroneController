# Reverse Engineering Guide — cooingdv WiFi Drones

How the RC UFO / KY UFO / cooingdv drone protocol was discovered and
verified, plus a methodology you can reuse for similar devices.

---

## 1. Reconnaissance

### 1.1 Identify the App

The drone ships with a mandatory mobile app. Note the exact name and
package ID:

| App | Package | Developer |
|-----|---------|-----------|
| RC UFO | `com.cooingdv.rcufo` | cooingdv |
| KY UFO | `com.cooingdv.kyufo` | cooingdv |
| RC FPV | `com.cooingdv.rcfpv` | cooingdv |

### 1.2 Find Existing Work

Search GitHub, forums, and blogs before starting from scratch:

```
github.com/search?q=cooingdv+drone+protocol
github.com/marshallrichards/turbodrone
```

TurboDrone was the key resource — it already had `cooingdv_rc_protocol_adapter.py`
and `cooingdv_video_protocol.py`.

### 1.3 Network Discovery

Power on the drone and connect your computer to its WiFi.

```bash
# Check interface
iwconfig wlp5s0

# Find drone IP
arp -a
ip neigh show

# Ping sweep
nmap -sn 192.168.1.0/24

# Port scan (fast)
sudo nmap -p 80,443,554,7070,7099,8080,8888 192.168.1.1
```

> **Note:** This drone responds to raw SYN packets on all ports with
> RST+ACK (unusual behavior). Standard socket `connect()` calls may
> timeout even when the port is "open" in raw SYN sense.

---

## 2. Traffic Analysis

### 2.1 Capture Live Traffic

```bash
# TCP dump all traffic to/from drone
sudo tcpdump -i wlp5s0 host 192.168.1.1 -w drone.pcap
```

Run the official mobile app on a phone (tethered to same network or
using a separate WiFi adapter) to capture the real protocol interaction.

### 2.2 Identify Control Channel

Look for periodic small UDP packets — these are heartbeats:

```
IP drone.7099 > phone.xxxxx: UDP, length 2
    01 01
```

### 2.3 Identify Video Stream

The video may be:

- **RTSP** — look for `OPTIONS`, `DESCRIBE`, `SETUP`, `PLAY` requests
- **MJPEG over HTTP** — look for `multipart/x-mixed-replace`
- **Raw UDP frames** — look for JPEG magic bytes `ff d8` in UDP payloads
- **RTP/JPEG** — RTP header + JPEG payload

For this drone, RTSP was detected on port 7070:

```bash
ffprobe rtsp://192.168.1.1:7070/webcam
```

---

## 3. APK Reverse Engineering

### 3.1 Download the APK

Use a mirror site or `apkeep`:

```bash
pip install apkeep
apkeep com.cooingdv.rcufo .
```

### 3.2 Decompile with JADX

```bash
jadx -d src/ rcufo.apk
```

### 3.3 Key Artifacts to Find

| Artifact | What to Look For |
|----------|------------------|
| `AndroidManifest.xml` | Permissions (INTERNET, CAMERA, WIFI) |
| Network classes | `DatagramSocket`, `Socket`, `ServerSocket` |
| Hardcoded IPs | `192.168.1.1`, `192.168.8.15`, `192.168.0.1` |
| Port numbers | In hex or decimal: `7070`, `7099`, `2228`, `40000` |
| String constants | `rtsp://`, `/webcam`, `heartbeat`, `cmd` |
| Byte arrays | Packet structures, magic bytes (`\x63\x63`, `\x01\x01`) |
| Native libraries | `lib*.so` files in `lib/` directory |

### 3.4 Analyze Native Libraries

The actual protocol encoding/decoding often lives in native C++ code:

```bash
# Extract native libs
unzip rcufo.apk -d extracted/
find extracted/lib/ -name "*.so"

# Decompile with Ghidra or objdump
objdump -d libcooingdv.so | less
```

---

## 4. Protocol Verification

### 4.1 Test Heartbeat

```python
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.sendto(bytes([0x01, 0x01]), ("192.168.1.1", 7099))
```

If the drone responds with telemetry, the heartbeat is correct.

### 4.2 Test Video Stream

```python
import cv2
cap = cv2.VideoCapture("rtsp://192.168.1.1:7070/webcam")
ret, frame = cap.read()
```

### 4.3 Fuzz Control Parameters

Send control packets with varying axis values and observe behavior.
Use scapy for raw packet crafting if the drone has a non-standard stack.

---

## 5. Tools & References

| Tool | Purpose |
|------|---------|
| Scapy | Raw packet crafting |
| Wireshark / tcpdump | Traffic capture |
| JADX | APK decompiler |
| Ghidra / IDA | Binary reverse engineering |
| FFmpeg/FFprobe | Video stream analysis |
| nmap | Port scanning |
| apkeep | APK downloader |

### Source Code References

- [TurboDrone](https://github.com/marshallrichards/turbodrone)
- [cooingdv RC protocol adapter](https://github.com/marshallrichards/turbodrone/blob/master/backend/protocols/cooingdv_rc_protocol_adapter.py)
- [cooingdv video protocol](https://github.com/marshallrichards/turbodrone/blob/master/backend/protocols/cooingdv_video_protocol.py)
- [cooingdv-jieli video protocol](https://github.com/marshallrichards/turbodrone/blob/master/backend/protocols/cooingdv_jieli_video_protocol.py)
