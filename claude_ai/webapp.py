#!/usr/bin/env python3
"""
RC UFO drone — FLIGHT CONSOLE  (shadcn-style dark dashboard)
============================================================
Built by Claude AI.  One browser page: large FPV view + real telemetry +
keyboard/button flight control + drone scan-and-reconnect.  Self-contained:
Python stdlib + ffmpeg, no Node/build step, no runtime CDN — the UI works even
on the drone's internet-less network.

Features
  * single shared video broker (one RTSP session fans out to all viewers,
    the recorder, and snapshots — never fights the drone's single session)
  * big single live camera (front/bottom switch); 90° default rotation; self-
    healing stream that reconnects on any drop
  * GYRO CALIBRATE pre-flight button (the gyro MUST be calibrated level on the
    ground or the drone flies badly — this was the #1 gotcha)
  * snapshot + H.264 record; rotate/zoom; 3 rate profiles (1/2/3 like the remote)
  * drone scan + connect/reconnect (pick the right WIFI-UFO-* hotspot)
  * ONLY real measured data: WiFi dBm/%, ping RTT, video FPS, bitrate, drops,
    link uptime, model id, control-TX rate, capture counts, raw-packet watcher

TELEMETRY HONESTY: this drone only ever sends `53 01 00 00 00` (status) and
`01 01 00 00 00` (ack). No battery/IMU over WiFi — confirmed by live probe, so
no battery % or attitude is shown (those would be fake).

Controls:
  ↑/↓ = move fwd/back (pitch) · ←/→ = move left/right (roll)
  W/S = throttle up/down · A/D = yaw left/right · 1/2/3 = speed (slow/normal/fast, beeps)
  Space = EMERGENCY STOP · T = takeoff · L = land · R = record · 0 = LED light · C calibrate · H headless · F flip
  In-app "?" menu explains video / controls / data / network. Audio beeps on speed + calibrate.

Run:  python3 ufo.py connect   then   python3 webapp.py   -> http://localhost:8088
SAFETY: DISARMED until ARM/Takeoff; Backspace = E-STOP; 0.5 s input watchdog
auto-centers the sticks on key release. Deps: Python stdlib + ffmpeg.
"""

import argparse, http.server, json, os, queue, re, socket, socketserver
import subprocess, sys, threading, time
from datetime import datetime
from collections import deque
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ufo   # drone wifi scan / connect helpers (shared with the CLI)

DRONE_IP  = "192.168.1.1"
RTSP_URL  = f"rtsp://{DRONE_IP}:7070/webcam"
CTRL_PORT = 7099
CLIENT_IP = "192.168.1.100"
HERE      = os.path.dirname(os.path.abspath(__file__))
CAPDIR    = os.path.join(HERE, "captures")

CENTER, SAFE_LO, SAFE_HI = 128, 0, 255   # full authority, like deepseek_pro
TAKEOFF_THR = 150                          # deepseek forces throttle 150 on takeoff
LOOP_HZ, WATCHDOG = 20, 0.5
CLIENT_GONE = 3.0          # auto-disarm if no cockpit/control has been seen for this long
KNOWN_TEL = "53 01 00 00 00"

F_TAKEOFF, F_LAND, F_ESTOP, F_FLIP = 0x01, 0x02, 0x04, 0x08
F_HEADLESS, F_LIGHT, F_CALIB = 0x10, 0x40, 0x80
GL_IDS = set(range(90, 102)) | {103, 82, 85}

def clamp(v, lo, hi): return lo if v < lo else hi if v > hi else v


# =========================================================== video broker ====
class VideoBroker:
    """One ffmpeg RTSP->MJPEG process; fans frames out to viewers + recorder."""
    def __init__(self):
        self.lock = threading.Lock()
        self.subs = set()           # set[queue.Queue]
        self.proc = None
        self.threads_started = False
        self.last_frame = None
        self.active_cam = "front"          # which camera the single encoder is on
        self.frames = 0
        self.fps = 0.0
        self.bitrate_kbps = 0
        self.drops = 0
        self._win_t = time.time(); self._win_f = 0; self._win_b = 0
        self.rec_proc = None
        self.rec_path = None
        threading.Thread(target=self._watchdog, daemon=True).start()

    @property
    def recording(self): return self.rec_proc is not None

    def _watchdog(self):
        """Footage is the main way to fly — if ffmpeg dies while anyone is
        watching/recording, restart it so the feed self-recovers."""
        while True:
            time.sleep(2)
            with self.lock:
                if (self.subs or self.recording) and (not self.proc or self.proc.poll() is not None):
                    self._ensure()

    def _ensure(self):
        if self.proc and self.proc.poll() is None:
            return
        self.proc = subprocess.Popen(
            ["ffmpeg", "-rtsp_transport", "udp", "-fflags", "nobuffer",
             "-flags", "low_delay", "-i", RTSP_URL, "-f", "mjpeg", "-q:v", "5", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
        threading.Thread(target=self._errwatch, args=(self.proc,), daemon=True).start()

    def _maybe_stop(self):
        if not self.subs and not self.recording and self.proc:
            try: self.proc.kill()
            except OSError: pass
            self.proc = None
            self.last_frame = None
            self.fps = 0.0; self.bitrate_kbps = 0

    def subscribe(self):
        q = queue.Queue(maxsize=4)
        with self.lock:
            self.subs.add(q); self._ensure()
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.subs.discard(q); self._maybe_stop()

    def set_active_cam(self, cam):
        """Switch which physical camera feeds the single encoder. The drone
        re-inits the encoder on switch, so the RTSP session must reconnect —
        we force-restart ffmpeg and the live view reconnects automatically."""
        if cam not in ("front", "bottom"):
            return
        with self.lock:
            self.active_cam = cam
            if self.proc:
                try: self.proc.kill()
                except OSError: pass
                self.proc = None
            self.last_frame = None
            if self.subs or self.recording:
                self._ensure()

    def _errwatch(self, proc):
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("latin1", "replace")
            if "Missing packets" in line or "dropping frame" in line or "corrupt" in line:
                self.drops += 1

    def _reader(self, proc):
        buf = b""
        while proc.poll() is None:
            chunk = proc.stdout.read(4096)
            if not chunk: break
            buf += chunk
            while True:
                a = buf.find(b"\xff\xd8"); b = buf.find(b"\xff\xd9", a + 2)
                if a == -1 or b == -1: break
                frame = buf[a:b + 2]; buf = buf[b + 2:]
                self._on_frame(frame)

    def _on_frame(self, frame):
        self.last_frame = frame
        self.frames += 1; self._win_f += 1; self._win_b += len(frame)
        now = time.time(); el = now - self._win_t
        if el >= 1.0:
            self.fps = round(self._win_f / el, 1)
            self.bitrate_kbps = int(self._win_b * 8 / el / 1000)
            self._win_t = now; self._win_f = 0; self._win_b = 0
        for q in list(self.subs):
            try: q.put_nowait(frame)
            except queue.Full:
                try: q.get_nowait(); q.put_nowait(frame)
                except queue.Empty: pass
        if self.rec_proc and self.rec_proc.poll() is None:
            try: self.rec_proc.stdin.write(frame)
            except (BrokenPipeError, OSError): pass

    # ---- snapshot / record ----
    def snapshot(self):
        f = self.last_frame
        if not f:
            with self.lock: self._ensure()
            for _ in range(40):
                if self.last_frame: f = self.last_frame; break
                time.sleep(0.1)
        if not f: return None
        os.makedirs(CAPDIR, exist_ok=True)
        path = os.path.join(CAPDIR, f"shot_{datetime.now():%Y%m%d_%H%M%S}.jpg")
        with open(path, "wb") as fh: fh.write(f)
        return os.path.basename(path)

    def record_start(self):
        if self.recording: return self.rec_path
        os.makedirs(CAPDIR, exist_ok=True)
        with self.lock: self._ensure()
        self.rec_path = os.path.join(CAPDIR, f"rec_{datetime.now():%Y%m%d_%H%M%S}.mp4")
        self.rec_proc = subprocess.Popen(
            ["ffmpeg", "-y", "-f", "image2pipe", "-framerate", "13", "-i", "pipe:0",
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", self.rec_path],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return os.path.basename(self.rec_path)

    def record_stop(self):
        if not self.recording: return None
        p = self.rec_proc; self.rec_proc = None
        try: p.stdin.close(); p.wait(timeout=8)
        except Exception: p.kill()
        with self.lock: self._maybe_stop()
        return os.path.basename(self.rec_path) if self.rec_path else None


# ============================================================ link monitor ====
class LinkMonitor:
    def __init__(self):
        self.dbm = self.quality = self.ping_ms = None
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()
    def _iface(self):
        """The actual wifi device (per nmcli), not just whatever holds 192.168.1.x."""
        try:
            out = subprocess.check_output(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"], text=True)
            for ln in out.splitlines():
                dev, _, typ = ln.partition(":")
                if typ.strip() == "wifi":
                    return dev
        except Exception: pass
        return "wlp5s0"
    def _loop(self):
        while self.running:
            q = dbm = None
            # nmcli signal% of the associated AP (the drone) — reliable, iface-agnostic
            try:
                out = subprocess.run(["nmcli", "-t", "-f", "ACTIVE,SIGNAL", "device", "wifi"],
                                     capture_output=True, text=True, timeout=3).stdout
                for ln in out.splitlines():
                    act, _, sig = ln.partition(":")
                    if act == "yes":
                        try: q = int(sig)
                        except ValueError: q = None
                        break
            except Exception: pass
            # iw gives a true dBm if present; otherwise approximate from %
            try:
                out = subprocess.run(["iw", "dev", self._iface(), "link"], capture_output=True,
                                     text=True, timeout=3).stdout
                m = re.search(r"signal:\s*(-?\d+)\s*dBm", out)
                if m: dbm = int(m.group(1))
            except Exception: pass
            if dbm is None and q is not None: dbm = int(q / 2 - 100)          # %→dBm (rough)
            if q is None and dbm is not None: q = int(clamp((dbm + 90) * 100 / 60, 0, 100))
            self.dbm, self.quality = dbm, q
            try:
                out = subprocess.run(["ping", "-c", "1", "-W", "1", DRONE_IP],
                                     capture_output=True, text=True, timeout=3).stdout
                m = re.search(r"time=([\d.]+)\s*ms", out)
                self.ping_ms = round(float(m.group(1)), 1) if m else None
            except Exception: self.ping_ms = None
            time.sleep(2.0)


# ======================================================= flight controller ====
class FlightController:
    def __init__(self):
        self.lock = threading.Lock(); self.sock = None; self.bound = False
        self._open_socket()
        self.roll = self.pitch = self.throttle = self.yaw = CENTER
        self.headless = self.armed = self.light = False
        self.oneshot = {}; self.last_input = self.last_hb = 0.0
        self.last_client = time.time()     # last time a cockpit/control was seen (deadman)
        self.tel_raw = ""; self.tel_id = None; self.tel_variant = "?"; self.tel_len = 0
        self.tel_nonstandard = False; self.tel_anomaly = ""; self.tel_last = 0.0
        self.rx_log = deque(maxlen=10)     # recent distinct RX packets (live packet log)
        self.photo_count = self.video_count = None
        self.link_up_since = 0.0; self.tx_rate = 0; self._tx = 0; self._tx_t = time.time()
        self.tx_last = ""          # hex of the exact control packet last sent to the drone
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._maintenance, daemon=True).start()
    def _open_socket(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try: s.bind((CLIENT_IP, 0)); bound = True
        except OSError: s.bind(("0.0.0.0", 0)); bound = False
        s.settimeout(0.0)
        with self.lock:
            old = self.sock; self.sock = s; self.bound = bound
        if old:
            try: old.close()
            except OSError: pass
    def _iface(self):
        try:
            out = subprocess.check_output(["ip", "-o", "-4", "addr"], text=True)
            for ln in out.splitlines():
                if "192.168.1." in ln: return ln.split()[1]
        except Exception: pass
        return "wlp5s0"
    def _maintenance(self):
        while self.running:
            try:
                rc = subprocess.run(["ip", "-4", "addr", "show", "dev", self._iface()],
                                    capture_output=True, text=True)
                if CLIENT_IP not in (rc.stdout or ""):
                    subprocess.run(["sudo", "-n", "ip", "addr", "add", f"{CLIENT_IP}/24",
                                    "dev", self._iface()], capture_output=True)
                    self._open_socket()
                elif not self.bound:
                    self._open_socket()
            except Exception: pass
            time.sleep(4)
    def set_sticks(self, r, p, t, y):
        with self.lock:
            self.roll = clamp(int(r), SAFE_LO, SAFE_HI); self.pitch = clamp(int(p), SAFE_LO, SAFE_HI)
            self.throttle = clamp(int(t), SAFE_LO, SAFE_HI); self.yaw = clamp(int(y), SAFE_LO, SAFE_HI)
            self.last_input = self.last_client = time.time()
    def command(self, name, value=None):
        now = time.time()
        with self.lock:
            if name == "arm": self.armed = True
            elif name == "disarm": self.armed = False
            elif name == "estop":
                self.armed = False; self.oneshot[F_ESTOP] = now + 0.6
                self.roll = self.pitch = self.throttle = self.yaw = CENTER
            elif name == "takeoff":
                self.armed = True; self.oneshot[F_TAKEOFF] = now + 0.6
                self.throttle = TAKEOFF_THR; self.last_input = now   # climb pulse (deepseek), then auto-center to hover
            elif name == "land":
                self.oneshot[F_LAND] = now + 0.8
                self.roll = self.pitch = self.yaw = CENTER; self.throttle = 0   # cut throttle so it descends
                self.last_input = now; self.armed = False                        # (like deepseek's land)
            elif name == "flip": self.oneshot[F_FLIP] = now + 0.4
            elif name == "calib": self.oneshot[F_CALIB] = now + 1.2   # hold gyro-calib ~24 packets
            elif name == "headless": self.headless = not self.headless
            elif name == "light": self.light = not self.light
            elif name in ("cam_front", "cam_rear", "cam_bottom"):
                pkt = b"\x06\x01" if name == "cam_front" else b"\x06\x02"
                try: self.sock.sendto(pkt, (DRONE_IP, CTRL_PORT))
                except OSError: pass
        return self.snapshot()
    def _build(self):
        now = time.time()
        if now - self.last_input > WATCHDOG:
            self.roll = self.pitch = self.throttle = self.yaw = CENTER
        flags = 0
        for bit, exp in list(self.oneshot.items()):
            if exp > now: flags |= bit
            else: del self.oneshot[bit]
        if self.headless: flags |= F_HEADLESS
        if self.light: flags |= F_LIGHT
        b1, b2 = self.roll, self.pitch
        acc = 0 if self.throttle == 1 else self.throttle; turn = self.yaw
        cks = b1 ^ b2 ^ acc ^ turn ^ flags
        return bytes([0x03, 0x66, b1, b2, acc, turn, flags, cks, 0x99])

    def _build_idle(self):
        """Safe ground packet: centered sticks, THROTTLE 0 (motors idle), only
        light/headless flags. Lets the LED stay on while DISARMED without flying."""
        flags = (F_LIGHT if self.light else 0) | (F_HEADLESS if self.headless else 0)
        cks = CENTER ^ CENTER ^ 0 ^ CENTER ^ flags
        return bytes([0x03, 0x66, CENTER, CENTER, 0, CENTER, flags, cks, 0x99])
    def _loop(self):
        period = 1.0 / LOOP_HZ
        while self.running:
            t0 = time.time()
            with self.lock:
                # deadman: if the cockpit/operator vanished while armed, cut motors
                if self.armed and (t0 - self.last_client) > CLIENT_GONE:
                    self.armed = False; self.oneshot[F_ESTOP] = t0 + 0.6
                    self.roll = self.pitch = self.throttle = self.yaw = CENTER
                if t0 - self.last_hb > 1.0:
                    try: self.sock.sendto(b"\x01\x01", (DRONE_IP, CTRL_PORT))
                    except OSError: pass
                    self.last_hb = t0
                pend = any(b in self.oneshot for b in (F_ESTOP, F_LAND, F_TAKEOFF, F_FLIP, F_CALIB))
                # Stream control continuously at 20 Hz like deepseek (drone expects a
                # constant feed). Armed/flag -> real sticks; otherwise a safe idle packet
                # (centered, throttle 0, props off) that still carries light/headless.
                pkt = self._build() if (self.armed or pend) else self._build_idle()
                self.tx_last = pkt.hex(" ")
                try: self.sock.sendto(pkt, (DRONE_IP, CTRL_PORT)); self._tx += 1
                except OSError: pass
            self._drain()
            if t0 - self._tx_t >= 1.0: self.tx_rate = self._tx; self._tx = 0; self._tx_t = t0
            time.sleep(max(0, period - (time.time() - t0)))
    def _drain(self):
        for _ in range(8):
            try: data, _ = self.sock.recvfrom(64)
            except (BlockingIOError, OSError): break
            if not data: break
            with self.lock:
                h = data.hex(" "); self.tel_raw = h; self.tel_id = data[0]
                self.tel_variant = "GL" if data[0] in GL_IDS else "TC"; self.tel_len = len(data)
                self.tel_last = time.time()
                if self.link_up_since == 0.0: self.link_up_since = self.tel_last
                if h != KNOWN_TEL: self.tel_nonstandard = True; self.tel_anomaly = h
                ts = datetime.now().strftime("%H:%M:%S")
                if self.rx_log and self.rx_log[-1]["hex"] == h:
                    self.rx_log[-1]["n"] += 1; self.rx_log[-1]["t"] = ts
                else:
                    self.rx_log.append({"t": ts, "hex": h, "n": 1})
                if len(data) > 3 and data[2] == 0x4D: self.photo_count = data[3]
                elif len(data) > 4 and data[2] == 0x58: self.video_count = data[4]
    def snapshot(self):
        with self.lock:
            now = time.time()
            self.last_client = now          # SSE/state poll or command = a live cockpit (deadman feed)
            online = (now - self.tel_last) < 5.0 if self.tel_last else False
            if not online: self.link_up_since = 0.0
            uptime = int(now - self.link_up_since) if self.link_up_since else 0
            return {
                "armed": self.armed, "headless": self.headless, "light": self.light,
                "sticks": {"roll": self.roll, "pitch": self.pitch, "throttle": self.throttle, "yaw": self.yaw},
                "telemetry": {"online": online, "raw": self.tel_raw, "model_id": self.tel_id,
                              "variant": self.tel_variant, "length": self.tel_len,
                              "nonstandard": self.tel_nonstandard, "anomaly": self.tel_anomaly,
                              "photo_count": self.photo_count, "video_count": self.video_count},
                "link": {"wifi_dbm": LINK.dbm, "wifi_quality": LINK.quality, "ping_ms": LINK.ping_ms,
                         "video_fps": BROKER.fps, "bitrate_kbps": BROKER.bitrate_kbps,
                         "drops": BROKER.drops, "uptime_s": uptime},
                "rec": BROKER.recording, "tx_rate": self.tx_rate, "bound_100": self.bound,
                "tx_packet": self.tx_last,
                "rx_log": list(self.rx_log),
                "active_cam": BROKER.active_cam,
                "drone": DRONE.status() if DRONE else {},
            }


# ============================================================== drone link ====
class DroneLink:
    """Scan for UFO drone hotspots and (re)connect to the right one, reusing the
    ufo.py CLI helpers. Connection runs in a background thread so HTTP never blocks."""
    def __init__(self):
        self.lock = threading.Lock()
        self.ssid = None; self.connecting = False; self.last_result = ""
        self.drones = []
        threading.Thread(target=self._poll, daemon=True).start()
    def _poll(self):
        while True:
            try: self.ssid = ufo.current_ssid()
            except Exception: pass
            time.sleep(3)
    def scan(self):
        try: self.drones = ufo.scan_ufo()
        except Exception: self.drones = []
        return self.drones
    def connect(self, ssid):
        with self.lock:
            if self.connecting: return
            self.connecting = True; self.last_result = "connecting"
        def run():
            try:
                ok = ufo.connect_ufo(ssid=ssid or None, timeout=45, verbose=False)
                self.last_result = "connected" if ok else "failed"
                self.ssid = ufo.current_ssid()
            except Exception:
                self.last_result = "error"
            finally:
                self.connecting = False
        threading.Thread(target=run, daemon=True).start()
    def status(self):
        return {"ssid": self.ssid, "is_ufo": ufo.is_ufo_ssid(self.ssid or ""),
                "connecting": self.connecting, "result": self.last_result}


FC = LINK = BROKER = DRONE = None


# ================================================================== page ======
PAGE = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>RC-UFO Console</title>
<style>
 :root{
   --bg:#09090b; --card:#0c0c0f; --elev:#111114;
   --border:#27272a; --border-soft:#1c1c20;
   --fg:#fafafa; --muted:#a1a1aa; --muted2:#71717a;
   --accent:#18181b; --ring:#3f3f46;
   --green:#22c55e; --amber:#f59e0b; --red:#ef4444; --blue:#3b82f6; --sky:#0ea5e9;
   --r:10px; --r-sm:7px;
 }
 *{box-sizing:border-box}
 html{-webkit-text-size-adjust:100%}
 body{margin:0;background:var(--bg);color:var(--fg);
   font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
   font-size:14px;line-height:1.45;-webkit-font-smoothing:antialiased}
 .mono{font-family:ui-monospace,"SF Mono","DejaVu Sans Mono",Menlo,monospace;font-variant-numeric:tabular-nums}
 /* ---- header ---- */
 header{position:sticky;top:0;z-index:20;display:flex;align-items:center;justify-content:space-between;
   gap:12px;padding:12px 20px;background:rgba(9,9,11,.82);backdrop-filter:blur(8px);
   border-bottom:1px solid var(--border)}
 .brand{display:flex;align-items:center;gap:12px;min-width:0}
 .logo{width:34px;height:34px;border-radius:9px;display:grid;place-items:center;flex:none;
   background:linear-gradient(150deg,#1f1f23,#0d0d10);border:1px solid var(--border);font-size:18px}
 .brand h1{margin:0;font-size:15px;font-weight:600;letter-spacing:-.01em;white-space:nowrap}
 .brand p{margin:0;font-size:11.5px;color:var(--muted2)}
 .hgroup{display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end}
 .clock{font-size:13px;color:var(--muted);padding-left:4px}
 /* ---- app shell: cameras dominate, controls in a compact sidebar ---- */
 .app{display:flex;gap:14px;padding:14px;align-items:stretch;height:calc(100vh - 59px)}
 .stage{flex:1 1 auto;min-width:0;display:flex}
 .stage>.card{flex:1;display:flex;flex-direction:column;min-height:0}
 .stage .card-b{flex:1;display:flex;flex-direction:column;min-height:0}
 .side{width:366px;flex:0 0 366px;overflow-y:auto;overflow-x:hidden;
   display:flex;flex-direction:column;gap:14px;padding-right:6px}
 .side::-webkit-scrollbar{width:8px}.side::-webkit-scrollbar-thumb{background:var(--border);border-radius:8px}
 @media(max-width:880px){.app{flex-direction:column;height:auto}
   .side{width:auto;flex:none;overflow:visible}.stage{height:74vh}}
 /* ---- card ---- */
 .card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);overflow:hidden}
 .card-h{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:13px 16px 11px}
 .card-h h2{margin:0;font-size:13px;font-weight:600;letter-spacing:-.005em}
 .card-h .hint{font-size:11px;color:var(--muted2);font-weight:500}
 .card-b{padding:0 16px 15px}
 .card-h+.card-b{padding-top:1px}
 /* ---- rows / kv ---- */
 .row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:6px 0;
   font-size:13px;border-top:1px solid var(--border-soft)}
 .row:first-child{border-top:none}
 .row .k{color:var(--muted)}
 .row .v{font-weight:500}
 /* ---- badge ---- */
 .badge{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:500;
   padding:3px 9px;border-radius:999px;border:1px solid var(--border);color:var(--muted);
   background:var(--elev);white-space:nowrap}
 .badge .dot{width:7px;height:7px;border-radius:50%;background:var(--muted2)}
 .badge.green{color:#bbf7d0;border-color:rgba(34,197,94,.35);background:rgba(34,197,94,.10)} .badge.green .dot{background:var(--green);box-shadow:0 0 7px var(--green)}
 .badge.amber{color:#fde68a;border-color:rgba(245,158,11,.35);background:rgba(245,158,11,.10)} .badge.amber .dot{background:var(--amber)}
 .badge.red{color:#fecaca;border-color:rgba(239,68,68,.4);background:rgba(239,68,68,.12)} .badge.red .dot{background:var(--red);animation:bl 1s steps(1) infinite}
 @keyframes bl{50%{opacity:.3}}
 /* ---- buttons ---- */
 .btn{appearance:none;cursor:pointer;font:inherit;font-size:12.5px;font-weight:500;
   display:inline-flex;align-items:center;justify-content:center;gap:6px;
   padding:0 10px;height:34px;border-radius:var(--r-sm);border:1px solid var(--border);
   background:var(--elev);color:var(--fg);transition:.12s background,.12s border-color,.12s color;white-space:nowrap}
 .btn:hover{background:#1d1d22;border-color:var(--ring)}
 .btn:active{transform:translateY(1px)}
 .btn.primary{background:var(--fg);color:#09090b;border-color:var(--fg);font-weight:600}
 .btn.primary:hover{background:#e4e4e7}
 .btn.green{border-color:rgba(34,197,94,.4);color:#86efac}
 .btn.green:hover{background:rgba(34,197,94,.12)}
 .btn.danger{border-color:rgba(239,68,68,.45);color:#fca5a5}
 .btn.danger:hover{background:rgba(239,68,68,.14)}
 .btn.on{border-color:var(--green);color:#86efac;background:rgba(34,197,94,.12)}
 .btn.sm{height:30px;font-size:12px;padding:0 9px}
 .grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}
 .grid2{display:grid;grid-template-columns:1fr 1fr;gap:7px}
 .estop{grid-column:1/-1;height:40px;font-weight:600;letter-spacing:.02em;
   border-color:rgba(239,68,68,.55);color:#fee2e2;background:rgba(239,68,68,.14)}
 .estop:hover{background:rgba(239,68,68,.22)}
 .full{grid-column:1/-1}
 .calib{width:100%;height:42px;font:inherit;font-size:13px;font-weight:600;letter-spacing:.02em;cursor:pointer;
   border-radius:var(--r-sm);border:1px solid rgba(245,158,11,.5);color:#fde68a;background:rgba(245,158,11,.12)}
 .calib:hover{background:rgba(245,158,11,.2)}
 /* ---- cameras ---- */
 .cams{flex:1;display:flex;gap:12px;min-height:0;justify-content:center}
 .pane{position:relative;flex:1 1 0;min-width:0;height:100%;border:1px solid var(--border);
   border-radius:10px;overflow:hidden;background:#000;cursor:pointer;transition:border-color .15s,box-shadow .15s}
 .pane:hover{border-color:var(--ring)}
 .pane.live{border-color:rgba(34,197,94,.6);box-shadow:0 0 0 1px rgba(34,197,94,.3),0 0 22px rgba(34,197,94,.10)}
 .pane img{display:block;width:100%;height:100%;object-fit:contain;transition:transform .2s;transform-origin:center}
 .sel{width:100%;background:var(--elev);color:var(--fg);border:1px solid var(--border);border-radius:7px;
   padding:8px 9px;font:inherit;font-size:13px;margin-top:9px}
 .sel:focus{outline:none;border-color:var(--ring)}
 .pane .ptag{position:absolute;top:8px;left:8px;display:flex;align-items:center;gap:6px;
   font-size:11px;font-weight:600;letter-spacing:.04em;color:#e5e7eb;
   background:rgba(0,0,0,.55);border:1px solid rgba(255,255,255,.10);padding:3px 8px;border-radius:999px;backdrop-filter:blur(3px)}
 .pane .ptag .ld{width:7px;height:7px;border-radius:50%;background:var(--muted2)}
 .pane.live .ptag .ld{background:var(--green);box-shadow:0 0 8px var(--green);animation:bl 1.4s steps(1) infinite}
 .pane .pstate{position:absolute;bottom:8px;left:8px;font-size:10.5px;color:var(--muted);
   background:rgba(0,0,0,.5);padding:2px 7px;border-radius:999px}
 .pane.live .pstate{color:#86efac}
 .pane .empty{position:absolute;inset:0;display:none;flex-direction:column;align-items:center;justify-content:center;
   gap:6px;color:var(--muted2);font-size:11.5px;text-align:center;padding:14px;background:#0a0a0c}
 .pane .empty.show{display:flex}
 .pane .empty .ico{font-size:22px;opacity:.6}
 .recdot{position:absolute;top:8px;right:8px;display:none;align-items:center;gap:6px;font-size:11px;font-weight:600;
   color:#fecaca;background:rgba(0,0,0,.55);border:1px solid rgba(239,68,68,.45);padding:3px 8px;border-radius:999px}
 .recdot .d{width:7px;height:7px;border-radius:50%;background:var(--red);animation:bl .8s steps(1) infinite}
 .pane.rec .recdot{display:flex}
 .toolbar{display:flex;gap:7px;flex-wrap:wrap;margin-top:11px}
 .toolbar .btn{flex:1;min-width:64px}
 .note{font-size:11px;color:var(--muted2);margin-top:10px;line-height:1.5}
 /* ---- progress ---- */
 .bar{height:7px;border-radius:999px;background:var(--accent);overflow:hidden;margin-top:7px;border:1px solid var(--border-soft)}
 .bar>i{display:block;height:100%;background:var(--green);border-radius:999px;transition:width .35s,background .35s}
 .seg{display:flex;gap:3px;margin-top:9px}
 .seg>i{flex:1;height:12px;border-radius:3px;background:var(--accent);border:1px solid var(--border-soft)}
 .big{font-size:30px;font-weight:650;letter-spacing:-.02em}
 .est{font-size:11px;color:var(--amber)}
 .muted{color:var(--muted)} .dim{color:var(--muted2)}
 input.cap{width:54px;background:var(--elev);color:var(--fg);border:1px solid var(--border);
   border-radius:6px;padding:5px 7px;font:inherit;font-size:13px}
 input.cap:focus{outline:none;border-color:var(--ring)}
 /* ---- attitude (ADI) ---- */
 .adi{width:150px;height:150px;border-radius:50%;margin:4px auto 2px;overflow:hidden;position:relative;
   border:1px solid var(--border);box-shadow:0 0 0 4px var(--accent) inset}
 .adi .ball{position:absolute;left:-50%;top:-50%;width:200%;height:200%;transition:transform .1s}
 .adi .sky{position:absolute;left:0;top:0;width:100%;height:50%;background:linear-gradient(#0b4a63,#0a2f3f)}
 .adi .gnd{position:absolute;left:0;top:50%;width:100%;height:50%;background:linear-gradient(#3a2a0e,#241806)}
 .adi .hz{position:absolute;left:0;top:50%;width:100%;height:2px;background:var(--sky);box-shadow:0 0 8px var(--sky)}
 .adi .cross{position:absolute;left:50%;top:50%;width:44px;height:2px;background:var(--amber);transform:translate(-50%,-50%);box-shadow:0 0 0 1px rgba(0,0,0,.6)}
 .kbd{display:inline-flex;align-items:center;justify-content:center;min-width:20px;height:20px;padding:0 5px;
   font-family:ui-monospace,monospace;font-size:11px;color:var(--fg);background:var(--elev);
   border:1px solid var(--border);border-bottom-width:2px;border-radius:5px}
 .keys{display:flex;flex-direction:column;gap:7px;margin-top:12px;font-size:12px;color:var(--muted)}
 .keys div{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
 .guide{display:flex;flex-direction:column;gap:7px}
 .guide .grp{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted2);margin-top:7px}
 .guide .grp:first-child{margin-top:0}
 .guide .kr{display:flex;align-items:center;gap:10px;font-size:12.5px;color:var(--muted)}
 .guide .ks{display:flex;gap:4px;flex:0 0 64px}
 .guide .kr .tech{color:var(--muted2);font-size:11px}
 .log{font-family:ui-monospace,monospace;font-size:11px;line-height:1.75;max-height:140px;overflow-y:auto}
 .log div{white-space:nowrap}
 .log .lt{color:var(--muted2)}.log .lx{color:var(--amber)}.log .ln{color:#86efac}
 .seled{display:flex;gap:7px;margin-top:9px}
 .seled .btn{flex:1}
 .toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%) translateY(8px);
   background:var(--elev);border:1px solid var(--border);color:var(--fg);padding:9px 15px;border-radius:9px;
   font-size:12.5px;box-shadow:0 8px 30px rgba(0,0,0,.5);opacity:0;pointer-events:none;transition:.2s;z-index:40}
 .toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
 .lbl{font-size:11px;color:var(--muted2);text-transform:uppercase;letter-spacing:.06em;margin:0 0 6px}
 .foot{max-width:1340px;margin:0 auto;padding:0 20px 30px;font-size:11.5px;color:var(--muted2);line-height:1.6}
 /* ---- help modal ---- */
 .iconbtn{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;cursor:pointer;
   border:1px solid var(--border);background:var(--elev);color:var(--fg);border-radius:8px;font-size:14px;font-weight:700}
 .iconbtn:hover{border-color:var(--ring);background:#1d1d22}
 .modal{position:fixed;inset:0;z-index:60;display:none;align-items:center;justify-content:center;
   background:rgba(0,0,0,.62);backdrop-filter:blur(4px);padding:20px}
 .modal.show{display:flex}
 .sheet{width:min(780px,96vw);max-height:88vh;background:var(--card);border:1px solid var(--border);
   border-radius:13px;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 24px 70px rgba(0,0,0,.6)}
 .sheet-h{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid var(--border)}
 .sheet-h h2{margin:0;font-size:15px;font-weight:600}
 .tabs{display:flex;gap:6px;padding:11px 18px 0;flex-wrap:wrap}
 .sheet-b{padding:14px 18px 20px;overflow-y:auto;font-size:13px;color:var(--muted);line-height:1.62}
 .sheet-b .sec{display:none}.sheet-b .sec.on{display:block}
 .sheet-b h3{color:var(--fg);font-size:13px;margin:15px 0 5px}.sheet-b h3:first-child{margin-top:2px}
 .sheet-b p{margin:0 0 8px}.sheet-b b{color:var(--fg);font-weight:600}
 .sheet-b .mono,.sheet-b code{font-family:ui-monospace,monospace;font-size:12px;color:#86efac;
   background:#0b140f;border:1px solid var(--border-soft);border-radius:5px;padding:1px 5px}
 .sheet-b ul{margin:4px 0 8px;padding-left:18px}.sheet-b li{margin:3px 0}
</style></head><body>

<header>
  <div class=brand>
    <div class=logo>🛸</div>
    <div><h1>RC-UFO Console</h1><p><span id=unitssid>—</span> · cooingdv / Jieli BL60x</p></div>
  </div>
  <div class=hgroup>
    <span id=linkbadge class=badge><span class=dot></span>OFFLINE</span>
    <span id=armbadge class=badge><span class=dot></span>DISARMED</span>
    <span id=clock class="clock mono">--:--:--</span>
    <button class=iconbtn title="How it works" onclick="openHelp()">?</button>
  </div>
</header>

<div class=app>
  <!-- ============ STAGE: cameras dominate the view ============ -->
  <div class=stage>
  <div class=card>
    <div class=card-h><h2>Cameras</h2><span class=hint id=camhint>front · live</span></div>
    <div class=card-b>
      <div class=cams>
        <div class="pane live" id=paneMain>
          <img id=imgMain alt="">
          <div class=ptag><span class=ld></span><span id=camlabel>FRONT</span></div>
          <div class=pstate id=stMain>● LIVE</div>
          <div class=recdot><span class=d></span>REC</div>
          <div class=empty id=emptyMain><div class=ico>⌁</div>NO SIGNAL</div>
        </div>
      </div>
      <div class=toolbar>
        <button class="btn sm" id=camFrontBtn onclick="switchCam('front')">CAM 1 · Front</button>
        <button class="btn sm" id=camBottomBtn onclick="switchCam('bottom')">CAM 2 · Bottom</button>
        <button class="btn sm" onclick="cmd('snapshot')">▣ Snap</button>
        <button class="btn sm" id=recbtn onclick="toggleRec()">● Rec</button>
        <button class="btn sm" onclick="rotate()">⟳ Rotate</button>
        <button class="btn sm" onclick="zoom()">⤢ Zoom</button>
      </div>
    </div>
  </div>
  </div><!-- /stage -->

  <!-- ============ SIDE: compact controls + telemetry ============ -->
  <div class=side>
    <div class=card>
      <div class=card-h><h2>Drone</h2><span class=hint id=dronestat>—</span></div>
      <div class=card-b>
        <div class=row style="border:none"><span class=k>Connected</span><span id=dronessid class="v mono">—</span></div>
        <select id=dronesel class=sel><option value="">— scan for drones —</option></select>
        <div class=toolbar style="margin-top:9px">
          <button class="btn sm" onclick="scanDrones()">⟳ Scan</button>
          <button class="btn sm primary" onclick="connectDrone()" style="flex:1">Connect / reconnect</button>
        </div>
        <p class=note>Each drone is its own WiFi hotspot (all at 192.168.1.1) — one flies at a time.
          Scan, pick the right unit, then connect.</p>
      </div>
    </div>
    <div class=card>
      <div class=card-h><h2>Flight Control</h2><span class=hint>keyboard + buttons</span></div>
      <div class=card-b>
        <button class=calib onclick="calibrate()">◎ GYRO CALIBRATE</button>
        <p class=note style="margin:7px 0 11px">Do this <b>first</b> — drone flat &amp; still on the ground.
          An uncalibrated gyro makes it fly badly (drift/flip). Press <span class=kbd>C</span> any time.</p>
        <div class=grid3>
          <button class="btn green" onclick="cmd('arm')">Arm</button>
          <button class="btn primary" onclick="cmd('takeoff')">Takeoff</button>
          <button class=btn onclick="cmd('land')">Land</button>
          <button class=btn onclick="cmd('flip')">Flip</button>
          <button class=btn id=hlbtn onclick="cmd('headless')">Headless</button>
          <button class=btn id=ltbtn onclick="cmd('light')">◎ Light</button>
          <button class="btn full" onclick="cmd('disarm')">Disarm</button>
          <button class=estop onclick="cmd('estop')">■ EMERGENCY STOP · Space</button>
        </div>
        <p class=lbl style="margin-top:13px">Rate · matches remote (1/2/3 beeps)</p>
        <div class=seled>
          <button class="btn" data-sp=40 onclick="setSpeed(1)">1 · Slow</button>
          <button class="btn on" data-sp=70 onclick="setSpeed(2)">2 · Normal</button>
          <button class="btn" data-sp=110 onclick="setSpeed(3)">3 · Fast</button>
        </div>
      </div>
  </div>
    <div class=card>
      <div class=card-h><h2>Controls</h2><span class=hint>keyboard</span></div>
      <div class=card-b>
        <div class=guide>
          <div class=grp>Move</div>
          <div class=kr><span class=ks><span class=kbd>↑</span><span class=kbd>↓</span></span><span>forward / back <span class=tech>(pitch)</span></span></div>
          <div class=kr><span class=ks><span class=kbd>←</span><span class=kbd>→</span></span><span>left / right <span class=tech>(roll)</span></span></div>
          <div class=grp>Throttle &amp; turn</div>
          <div class=kr><span class=ks><span class=kbd>W</span><span class=kbd>S</span></span><span>up / down <span class=tech>(throttle)</span></span></div>
          <div class=kr><span class=ks><span class=kbd>A</span><span class=kbd>D</span></span><span>turn left / right <span class=tech>(yaw)</span></span></div>
          <div class=grp>Speed <span class=tech>(1/2/3 beeps)</span></div>
          <div class=kr><span class=ks><span class=kbd>1</span><span class=kbd>2</span><span class=kbd>3</span></span><span>slow / normal / fast</span></div>
          <div class=grp>Trim <span class=tech>(level out drift)</span></div>
          <div class=kr><span class=ks style="flex:0 0 auto"><span class=kbd>⇧</span>+<span class=kbd>↑↓←→</span></span><span>nudge center · <span class=tech id=trim style="cursor:pointer" onclick="resetTrim()">roll +0 · pitch +0</span> <span class=dim>(click=reset)</span></span></div>
          <div class=grp>Actions</div>
          <div class=kr><span class=ks><span class=kbd>T</span></span><span>take off</span></div>
          <div class=kr><span class=ks><span class=kbd>L</span></span><span>land</span></div>
          <div class=kr><span class=ks><span class=kbd>C</span></span><span>calibrate gyro <span class=tech>(do first)</span></span></div>
          <div class=kr><span class=ks><span class=kbd>R</span></span><span>record video</span></div>
          <div class=kr><span class=ks><span class=kbd>0</span></span><span>LED light on/off</span></div>
          <div class=kr><span class=ks><span class=kbd>H</span></span><span>headless · <span class=kbd>F</span> flip</span></div>
          <div class=kr><span class=ks><span class=kbd>Space</span></span><span style="color:#fca5a5;font-weight:600">EMERGENCY STOP</span></div>
        </div>
      </div>
    </div>
    <div class=card>
      <div class=card-h><h2>System</h2><span class=hint id=sysstat>standby</span></div>
      <div class=card-b>
        <div class=row><span class=k>Motors</span><span id=armed class="v">Disarmed</span></div>
        <div class=row><span class=k>Drone link · 7099</span><span id=link class=v>Offline</span></div>
        <div class=row><span class=k>Model / variant</span><span id=model class="v mono">—</span></div>
        <div class=row><span class=k>Headless</span><span id=hl class=v>Off</span></div>
        <div class=row><span class=k>LED light</span><span id=lt class=v>Off</span></div>
        <div class=row><span class=k>Control TX</span><span id=tx class="v mono">0 Hz</span></div>
        <div class=row><span class=k>Hold .100</span><span id=ip100 class=v>—</span></div>
        <div class=row><span class=k>Captures · P / V</span><span id=pv class="v mono">— / —</span></div>
      </div>
    </div>
    <div class=card>
      <div class=card-h><h2>Link Quality</h2><span class=hint>RF · video</span></div>
      <div class=card-b>
        <div class=row style="border:none"><span class=k>WiFi signal</span><span id=wifi class="v mono">—</span></div>
        <div class=bar><i id=wifibar style="width:0%"></i></div>
        <div class=row style="margin-top:6px"><span class=k>Ping RTT</span><span id=ping class="v mono">—</span></div>
        <div class=row><span class=k>Video FPS</span><span id=fps class="v mono">—</span></div>
        <div class=row><span class=k>Bitrate</span><span id=br class="v mono">—</span></div>
        <div class=row><span class=k>Frame drops</span><span id=drops class="v mono">—</span></div>
        <div class=row><span class=k>Link uptime</span><span id=uptime class="v mono">—</span></div>
      </div>
    </div>
    <div class=card>
      <div class=card-h><h2>Raw Telemetry</h2><span class=hint>watcher</span></div>
      <div class=card-b>
        <div class=row><span class=k>Packet</span><span id=raw class="v mono">—</span></div>
        <div class=row><span class=k>Length</span><span id=tlen class="v mono">—</span></div>
        <div class=row><span class=k>Status</span><span id=tstat class=v>—</span></div>
        <div id=anom class=est style="margin-top:8px"></div>
      </div>
    </div>
    <div class=card>
      <div class=card-h><h2>Packet Log · RX</h2><span class=hint>live from drone</span></div>
      <div class=card-b>
        <div id=rxlog class=log><div class=dim>— waiting for packets —</div></div>
        <p class=note>Distinct packets the drone sends back (repeats collapsed with a ×count). This drone
          only emits status/ack — anything else is flagged above.</p>
      </div>
    </div>
    <div class=card>
      <div class=card-h><h2>TX · sending to drone</h2><span class=hint>UDP 192.168.1.1:7099</span></div>
      <div class=card-b>
        <div class=row><span class=k>Control packet</span><span id=txpkt class="v mono">—</span></div>
        <div class=row><span class=k>Roll / Pitch</span><span id=txrp class="v mono">—</span></div>
        <div class=row><span class=k>Throttle / Yaw</span><span id=txty class="v mono">—</span></div>
        <div class=row><span class=k>Flags</span><span id=txfl class="v mono">—</span></div>
        <div class=row><span class=k>Checksum</span><span id=txck class="v mono">—</span></div>
        <div class=row><span class=k>Heartbeat</span><span class="v mono">01 01 · 1 Hz</span></div>
        <p class=note>Live 9-byte packet at 20 Hz: <span class=mono>03 66 ROLL PITCH THR YAW FLAGS CKSUM 99</span>.
          Centre = 128. This is exactly what goes on the wire.</p>
      </div>
    </div>
  </div><!-- /side -->
</div><!-- /app -->

<div class=foot>Claude-AI build · only real measured data shown — the airframe sends no battery/IMU over WiFi ·
  keep this tab focused for keyboard control · keys match the deepseek_pro project.</div>
<div class=modal id=help onclick="if(event.target===this)closeHelp()">
  <div class=sheet>
    <div class=sheet-h><h2>How this drone works</h2><button class=iconbtn onclick="closeHelp()">✕</button></div>
    <div class=tabs>
      <button class="btn sm on" data-tab=over onclick="helpTab('over',this)">Overview</button>
      <button class="btn sm" data-tab=video onclick="helpTab('video',this)">Video</button>
      <button class="btn sm" data-tab=ctrl onclick="helpTab('ctrl',this)">Controls</button>
      <button class="btn sm" data-tab=data onclick="helpTab('data',this)">Data / TX</button>
      <button class="btn sm" data-tab=net onclick="helpTab('net',this)">Network</button>
    </div>
    <div class=sheet-b>
      <div class="sec on" id=sec-over>
        <h3>The short version</h3>
        <p>The drone makes its <b>own WiFi</b> (<span class=mono>WIFI-UFO-600849</span>). Your laptop joins it
          and must hold the address <span class=mono>192.168.1.100</span> — the drone <b>only talks to .100</b>.</p>
        <p>Two channels run at once:</p>
        <ul>
          <li><b>Video in</b> — over RTSP on port <span class=mono>7070</span> (a stream of JPEG photos).</li>
          <li><b>Control out</b> — small UDP messages on port <span class=mono>7099</span>, <b>20 times a second</b>.</li>
        </ul>
        <p>The drone is "dumb": it sends almost <b>nothing</b> back — no battery, GPS, or motion sensors over
          WiFi. So this console shows only what's <b>real</b> and measures the rest (signal, ping, fps) itself.</p>
      </div>
      <div class=sec id=sec-video>
        <h3>The camera feed</h3>
        <p>Source: <span class=mono>rtsp://192.168.1.1:7070/webcam</span> — <b>MJPEG</b> (each frame is a full
          JPEG), <b>240×320 portrait</b>, ~13 fps. <span class=mono>ffmpeg</span> pulls it and the page shows it.</p>
        <h3>One camera at a time</h3>
        <p>The airframe has a <b>single video encoder</b>, so front and bottom <b>can't</b> stream together.
          <span class=mono>CAM 1 / CAM 2</span> switch which one feeds the stream (it briefly re-inits and
          reconnects on its own).</p>
        <h3>Why it's rotated</h3>
        <p>The camera is mounted sideways, so the view is <b>rotated 90°</b> by default. <b>Snap</b> saves a JPEG,
          <b>Rec</b> saves an H.264 MP4 — both into <span class=mono>captures/</span>.</p>
      </div>
      <div class=sec id=sec-ctrl>
        <h3>What a keypress actually sends</h3>
        <p>20 times a second we send a <b>9-byte packet</b>:</p>
        <p class=mono>03 66 ROLL PITCH THROTTLE YAW FLAGS CHECKSUM 99</p>
        <p>Each stick is a number <b>0–255</b>, with <b>128 = centre</b> (no movement). Arrows set pitch/roll,
          <span class=mono>W/S</span> throttle, <span class=mono>A/D</span> yaw. You can watch the live bytes in
          the <b>TX</b> panel.</p>
        <h3>Flags &amp; checksum</h3>
        <p><b>FLAGS</b> is one byte of on/off bits: takeoff <span class=mono>0x01</span>, land
          <span class=mono>0x02</span>, e-stop <span class=mono>0x04</span>, flip <span class=mono>0x08</span>,
          headless <span class=mono>0x10</span>, light <span class=mono>0x40</span>, gyro-calibrate
          <span class=mono>0x80</span>. <b>CHECKSUM = ROLL ^ PITCH ^ THROTTLE ^ YAW ^ FLAGS</b> (XOR); the drone
          ignores packets with a bad one.</p>
        <h3>Speed 1 / 2 / 3</h3>
        <p>How far one keypress pushes a stick — gentle → strong. Matches the remote's <b>1 / 2 / 3 beeps</b>
          (you'll hear them).</p>
        <h3>Safety</h3>
        <ul>
          <li>Motors <b>DISARMED</b> until Arm/Takeoff.</li>
          <li>Let go of a key and that stick <b>re-centres</b> within 0.5 s (watchdog).</li>
          <li><b>Space = EMERGENCY STOP</b> — cuts motors instantly.</li>
          <li>Close the tab while flying → it <b>auto-stops</b>.</li>
          <li><b>Calibrate the gyro</b> (flat on the ground) first, or it drifts/flips.</li>
        </ul>
      </div>
      <div class=sec id=sec-data>
        <h3>What we send (TX → drone)</h3>
        <ul>
          <li>The live <b>9-byte control packet</b> at 20 Hz (see the TX panel).</li>
          <li>A <b>heartbeat</b> <span class=mono>01 01</span> once per second.</li>
          <li>Camera switch <span class=mono>06 01</span> / <span class=mono>06 02</span>.</li>
        </ul>
        <h3>What the drone sends back (RX → us)</h3>
        <p>Only two messages, ever: <span class=mono>53 01 00 00 00</span> (status / model id 83 = TC) and
          <span class=mono>01 01 00 00 00</span> (ack). Plus a counter when you take a photo/video.</p>
        <h3>Everything else is measured here</h3>
        <p>WiFi <b>signal</b>, <b>ping</b>, video <b>fps / bitrate / drops</b> — your laptop measures those about
          the link; the drone doesn't report them. There is <b>no battery, IMU, altitude or GPS</b> over WiFi.</p>
      </div>
      <div class=sec id=sec-net>
        <h3>The .100 rule</h3>
        <p>Drone IP is <span class=mono>192.168.1.1</span>; your laptop must be <span class=mono>192.168.1.100</span>
          or the drone stays silent. The toolkit <b>pins</b> .100 and re-adds it automatically if it drops
          (watch the <b>Hold .100</b> row).</p>
        <h3>Internet while flying</h3>
        <p>One WiFi card can't be on your internet and the drone at once — put internet on a <b>second interface</b>
          (phone USB tether) so the WiFi card stays on the drone.</p>
        <h3>Multiple drones</h3>
        <p>Each drone is its own hotspot (all at <span class=mono>192.168.1.1</span>), so you fly <b>one at a
          time</b>. Use the <b>Drone</b> card to scan and pick the right <span class=mono>WIFI-UFO-*</span>.</p>
      </div>
    </div>
  </div>
</div>
<div class=toast id=toast></div>

<script>
const CENTER=128,LO=0,HI=255;let STEP=70;   // full authority, like deepseek
const held=new Set();
// Arrows = MOVE (↑↓ forward/back = pitch, ←→ left/right = roll).
// W/S = throttle up/down, A/D = yaw left/right.
// Space = E-STOP · T = takeoff · L = land · R = record · C calibrate · H headless · F flip.
const AXIS={ArrowUp:['p',+1],ArrowDown:['p',-1],ArrowLeft:['r',-1],ArrowRight:['r',+1],
 KeyW:['t',+1],KeyS:['t',-1],KeyA:['y',-1],KeyD:['y',+1]};   // ↑ = forward (pitch flipped to match the airframe)
const ONE={KeyH:'headless',KeyC:'calib',KeyF:'flip',Digit0:'light',Numpad0:'light'};   // 0 = LED light
let activeCam='front',rot=90,zoomed=false,recording=false;  // 90° default (camera mounted sideways)
const NOIMG=location.search.includes('noimg');  // skip live stream (debug/screenshot)
const $=i=>document.getElementById(i);
function clamp(v){return v<LO?LO:v>HI?HI:v}
// trim = a persistent offset per axis (Shift+arrows) to cancel drift, like RC trim.
const trim={p:0,r:0,t:0,y:0};const TRIM_STEP=3,TRIM_MAX=80;
const TRIMK={ArrowUp:['p',+1],ArrowDown:['p',-1],ArrowLeft:['r',-1],ArrowRight:['r',+1]};  // matches movement dirs
function hasTrim(){return trim.p||trim.r||trim.t||trim.y;}
const YAW_MIN=80;   // yaw needs more authority than pitch/roll (big deadzone on this airframe)
function axes(){let t=CENTER+trim.t,y=CENTER+trim.y,p=CENTER+trim.p,r=CENTER+trim.r;
 for(const c of held){const a=AXIS[c];if(!a)continue;
  const st=a[0]==='y'?Math.max(STEP,YAW_MIN):STEP;   // floor yaw so A/D work even on speed 1
  if(a[0]==='t')t+=a[1]*st;else if(a[0]==='y')y+=a[1]*st;else if(a[0]==='p')p+=a[1]*st;else if(a[0]==='r')r+=a[1]*st;}
 return{throttle:clamp(t),yaw:clamp(y),pitch:clamp(p),roll:clamp(r)};}
function adjustTrim(ax,dir){trim[ax]=Math.max(-TRIM_MAX,Math.min(TRIM_MAX,trim[ax]+dir*TRIM_STEP));showTrim();push();}
function resetTrim(){trim.p=trim.r=trim.t=trim.y=0;showTrim();push();toast('Trim reset');}
function showTrim(){const e=$('trim');if(e)e.textContent=`roll ${trim.r>=0?'+':''}${trim.r} · pitch ${trim.p>=0?'+':''}${trim.p}`;}
async function push(){try{await fetch('/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(axes())});}catch(e){}}
async function cmd(name,value){try{const r=await fetch('/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,value})});const j=await r.json();if(j.toast)toast(j.toast);render(j);}catch(e){}}
function toast(m){const t=$('toast');t.textContent=m;t.classList.add('show');clearTimeout(t._t);t._t=setTimeout(()=>t.classList.remove('show'),2200);}
// ---- sound (speed beeps like the remote, + gyro-calibrate tone) ----
let AC=null;
function actx(){try{AC=AC||new (window.AudioContext||window.webkitAudioContext)();if(AC.state==='suspended')AC.resume();}catch(e){}return AC;}
function beep(n,freq){const c=actx();if(!c)return;let t=c.currentTime;const dur=0.09,gap=0.08;
 for(let i=0;i<n;i++){const o=c.createOscillator(),g=c.createGain();o.type='square';o.frequency.value=freq||880;
  o.connect(g);g.connect(c.destination);g.gain.setValueAtTime(0.0001,t);g.gain.exponentialRampToValueAtTime(0.25,t+0.008);
  g.gain.exponentialRampToValueAtTime(0.0001,t+dur);o.start(t);o.stop(t+dur+0.02);t+=dur+gap;}}
function calibTone(){const c=actx();if(!c)return;const o=c.createOscillator(),g=c.createGain();o.type='sine';
 o.connect(g);g.connect(c.destination);const t=c.currentTime;
 o.frequency.setValueAtTime(520,t);o.frequency.linearRampToValueAtTime(960,t+0.45);
 g.gain.setValueAtTime(0.0001,t);g.gain.exponentialRampToValueAtTime(0.3,t+0.02);g.gain.exponentialRampToValueAtTime(0.0001,t+0.5);
 o.start(t);o.stop(t+0.52);}
const STEPS={1:40,2:70,3:110};
function setSpeed(level){STEP=STEPS[level];
 document.querySelectorAll('.seled .btn').forEach(b=>b.classList.toggle('on',+b.dataset.sp===STEP));
 toast('Rate '+level+' · '+(level===1?'Slow':level===2?'Normal':'Fast'));beep(level);}
function calibrate(){cmd('calib');toast('Calibrating gyro — keep level & still');calibTone();}
function rotate(){rot=(rot+90)%360;applyV();}
function zoom(){zoomed=!zoomed;applyV();}
function applyV(){$('imgMain').style.transform=`rotate(${rot}deg) scale(${zoomed?1.5:1})`;}
function toggleRec(){recording=!recording;cmd(recording?'record_start':'record_stop');syncRec();}
function syncRec(){$('recbtn').classList.toggle('on',recording);$('paneMain').classList.toggle('rec',recording);}
// ---- camera (single live feed; switch which camera feeds it) ----
function switchCam(cam){if(cam===activeCam)return;cmd(cam==='front'?'cam_front':'cam_bottom');}
function liveURL(){return '/stream.mjpg?ts='+Date.now();}
function applyCams(){
 $('imgMain').dataset.live='1';
 if(!NOIMG)$('imgMain').src=liveURL();
 $('camlabel').textContent=activeCam==='front'?'FRONT':'BOTTOM';
 $('camhint').textContent=activeCam+' · live';
 $('camFrontBtn').classList.toggle('on',activeCam==='front');
 $('camBottomBtn').classList.toggle('on',activeCam==='bottom');
 applyV();syncRec();
}
// ---- key handling ----
addEventListener('keydown',e=>{if(e.repeat)return;
 if(e.shiftKey&&TRIMK[e.code]){e.preventDefault();const a=TRIMK[e.code];adjustTrim(a[0],a[1]);return;}  // Shift+arrows = TRIM
 if(e.code in AXIS){held.add(e.code);e.preventDefault();push();}
 else if(e.code==='Space'||e.code==='Backspace'){e.preventDefault();cmd('estop');}   // SPACE = E-STOP
 else if(e.code==='KeyT'){e.preventDefault();cmd('takeoff');}                          // T = takeoff
 else if(e.code==='KeyL'){e.preventDefault();cmd('land');}                             // L = land
 else if(e.code==='KeyR'){e.preventDefault();toggleRec();}                             // R = record
 else if(e.code==='Digit1'||e.code==='Numpad1'){e.preventDefault();setSpeed(1);}       // speed 1/2/3
 else if(e.code==='Digit2'||e.code==='Numpad2'){e.preventDefault();setSpeed(2);}
 else if(e.code==='Digit3'||e.code==='Numpad3'){e.preventDefault();setSpeed(3);}
 else if(e.code in ONE){e.preventDefault();cmd(ONE[e.code]);}});
addEventListener('keyup',e=>{if(e.code in AXIS){held.delete(e.code);e.preventDefault();push();}});
setInterval(()=>{if(held.size||hasTrim())push();},50);
setInterval(()=>{$('clock').textContent=new Date().toTimeString().slice(0,8);},1000);
// ---- drone scan / reconnect ----
async function scanDrones(){toast('Scanning…');
 try{const r=await fetch('/drones');const j=await r.json();const sel=$('dronesel');
  const cur=j.status&&j.status.ssid;sel.innerHTML='';
  if(!j.drones||!j.drones.length){sel.innerHTML='<option value="">no drones found</option>';toast('No drones found');return;}
  for(const d of j.drones){const o=document.createElement('option');o.value=d.ssid;
   o.textContent=d.ssid+'  ('+d.signal+'%)'+(d.ssid===cur?'  ✓':'');
   if(d.ssid===cur)o.selected=true;sel.appendChild(o);}
  toast(j.drones.length+' drone'+(j.drones.length>1?'s':'')+' found');}
 catch(e){toast('Scan failed');}}
function connectDrone(){cmd('connect',$('dronesel').value);}
// ---- help / how-it-works menu ----
function openHelp(){$('help').classList.add('show');}
function closeHelp(){$('help').classList.remove('show');}
function helpTab(id,el){document.querySelectorAll('.sheet-b .sec').forEach(s=>s.classList.remove('on'));
 $('sec-'+id).classList.add('on');
 document.querySelectorAll('.tabs .btn').forEach(b=>b.classList.remove('on'));el.classList.add('on');}
addEventListener('keydown',e=>{if(e.code==='Escape')closeHelp();});
// ---- render helpers ----
function badge(el,cls,txt,dot){el.className='badge '+(cls||'');el.innerHTML=(dot?'<span class=dot></span>':'')+txt;}
function fmt(s){if(s==null)return'—';const m=Math.floor(s/60),x=s%60;return m+':'+String(x).padStart(2,'0');}
function render(s){
 const t=s.telemetry,l=s.link,d=s.drone||{};
 // camera
 if(s.active_cam&&s.active_cam!==activeCam){activeCam=s.active_cam;applyCams();}
 $('emptyMain').classList.toggle('show',!t.online);
 $('stMain').textContent=t.online?'● LIVE':'OFFLINE';
 // header badges
 badge($('armbadge'),s.armed?'red':'',s.armed?'ARMED':'DISARMED',true);
 badge($('linkbadge'),t.online?'green':'',t.online?'ONLINE':'OFFLINE',true);
 $('sysstat').textContent=s.armed?'armed':t.online?'linked':'standby';
 // system
 $('armed').textContent=s.armed?'Armed':'Disarmed';$('armed').style.color=s.armed?'var(--red)':'var(--muted)';
 $('link').textContent=t.online?'Online':'Offline';$('link').style.color=t.online?'var(--green)':'var(--muted)';
 $('model').textContent=t.model_id!=null?t.model_id+' ['+t.variant+']':'—';
 $('hl').textContent=s.headless?'On':'Off';$('hl').style.color=s.headless?'var(--amber)':'var(--muted)';
 $('lt').textContent=s.light?'On':'Off';$('lt').style.color=s.light?'var(--amber)':'var(--muted)';
 $('hlbtn').classList.toggle('on',!!s.headless);$('ltbtn').classList.toggle('on',!!s.light);
 $('tx').textContent=s.tx_rate+' Hz';
 $('ip100').textContent=s.bound_100?'OK':'LOST — run ufo.py net';$('ip100').style.color=s.bound_100?'var(--green)':'var(--red)';
 $('pv').textContent=(t.photo_count??'—')+' / '+(t.video_count??'—');
 // link
 $('wifi').textContent=l.wifi_dbm!=null?(l.wifi_dbm+' dBm · '+l.wifi_quality+'%'):'—';
 const q=l.wifi_quality||0;$('wifibar').style.width=q+'%';$('wifibar').style.background=q>55?'var(--green)':q>30?'var(--amber)':'var(--red)';
 $('ping').textContent=l.ping_ms!=null?l.ping_ms+' ms':'—';
 $('fps').textContent=l.video_fps?l.video_fps+' fps':'—';
 $('br').textContent=l.bitrate_kbps?l.bitrate_kbps+' kbps':'—';
 $('drops').textContent=l.drops!=null?l.drops:'—';
 $('uptime').textContent=t.online?fmt(l.uptime_s):'—';
 // raw telemetry
 $('raw').textContent=t.raw||'—';$('tlen').textContent=t.length?t.length+' B':'—';
 if(t.nonstandard){$('tstat').textContent='⚠ Non-standard';$('tstat').style.color='var(--amber)';
   $('anom').textContent='Unexpected packet: '+t.anomaly+' — possible hidden data.';}
 else{$('tstat').textContent=t.online?'Nominal':'—';$('tstat').style.color=t.online?'var(--green)':'var(--muted)';$('anom').textContent='';}
 // RX packet log (most recent first; repeats collapsed)
 const lg=s.rx_log||[];
 $('rxlog').innerHTML = lg.length ? lg.slice().reverse().map(e=>
   `<div><span class=lt>${e.t}</span> ${e.hex}${e.n>1?' <span class=lx>×'+e.n+'</span>':''}</div>`).join('')
   : '<div class=dim>— waiting for packets —</div>';
 // TX — exact bytes we send to the drone
 const tx=s.tx_packet||'';$('txpkt').textContent=tx||'—';
 const b=tx?tx.split(' ').map(x=>parseInt(x,16)):[];
 if(b.length>=9){
   $('txrp').textContent=b[2]+' / '+b[3];$('txty').textContent=b[4]+' / '+b[5];
   const fn={1:'TAKEOFF',2:'LAND',4:'ESTOP',8:'FLIP',16:'HEADLESS',64:'LIGHT',128:'CALIB'};
   const on=Object.keys(fn).filter(k=>b[6]&Number(k)).map(k=>fn[k]);
   $('txfl').textContent='0x'+b[6].toString(16).padStart(2,'0')+' ('+(on.length?on.join('+'):'none')+')';
   $('txck').textContent='0x'+b[7].toString(16).padStart(2,'0');
 } else {$('txrp').textContent=$('txty').textContent=$('txfl').textContent=$('txck').textContent='—';}
 // drone link
 $('unitssid').textContent=d.ssid||'—';
 $('dronessid').textContent=d.ssid||'—';$('dronessid').style.color=d.is_ufo?'var(--green)':'var(--muted)';
 $('dronestat').textContent=d.connecting?'connecting…':(d.is_ufo?'connected':(d.ssid?'other wifi':'—'));
 // record sync
 if(s.rec!==recording){recording=s.rec;syncRec();}
}
// ---- keep the live feed connected, whatever happens ----
// The MJPEG <img> won't reconnect on its own if the stream drops (server restart,
// network blip, drone power-cycle). So: reconnect on error, and a periodic guard
// re-points the live pane at the stream if it ever loses it.
function reconnectLive(img){
 if(NOIMG||img.dataset.live!=='1')return;
 clearTimeout(img._rt);
 img._rt=setTimeout(()=>{if(img.dataset.live==='1')img.src=liveURL();},1200);
}
$('imgMain').addEventListener('error',()=>reconnectLive($('imgMain')));
setInterval(()=>{if(NOIMG)return;const m=$('imgMain');
 if(!(m.getAttribute('src')||'').includes('/stream.mjpg'))m.src=liveURL();
},5000);
applyCams();
if(location.search.includes('help'))openHelp();
if(NOIMG){fetch('/state').then(r=>r.json()).then(render).catch(()=>{});}
else{new EventSource('/telemetry').onmessage=e=>render(JSON.parse(e.data));}
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    BOUNDARY = "ufoframe"
    def log_message(self, *a): pass
    def do_GET(self):
        route = urlparse(self.path).path
        if route in ("/", "/index.html"): self._send(200, "text/html; charset=utf-8", PAGE.encode())
        elif route == "/stream.mjpg": self._video()
        elif route == "/telemetry": self._sse()
        elif route == "/state": self._send(200, "application/json", json.dumps(FC.snapshot()).encode())
        elif route == "/drones":
            self._send(200, "application/json", json.dumps({"drones": DRONE.scan(), "status": DRONE.status()}).encode())
        else: self.send_error(404)
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try: body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError: body = {}
        if self.path == "/control":
            FC.set_sticks(body.get("roll", CENTER), body.get("pitch", CENTER),
                          body.get("throttle", CENTER), body.get("yaw", CENTER))
            self._send(200, "application/json", b'{"ok":true}')
        elif self.path == "/command":
            name = body.get("name", "")
            if name in ("snapshot", "record_start", "record_stop"):
                if name == "snapshot":
                    p = BROKER.snapshot(); msg = ("Snapshot " + p) if p else "No video feed"
                elif name == "record_start":
                    p = BROKER.record_start(); msg = "Recording " + (p or "")
                else:
                    p = BROKER.record_stop(); msg = ("Saved " + p) if p else "Recording off"
                out = FC.snapshot(); out["toast"] = msg
            elif name == "connect":
                DRONE.connect(body.get("value"))
                out = FC.snapshot()
                out["toast"] = "Connecting to " + (body.get("value") or "strongest drone")
            else:
                out = FC.command(name, body.get("value"))
                if name in ("cam_front", "cam_bottom", "cam_rear"):
                    cam = "front" if name == "cam_front" else "bottom"
                    BROKER.set_active_cam(cam)
                    out["active_cam"] = cam
                    out["toast"] = "Camera: " + cam
            self._send(200, "application/json", json.dumps(out).encode())
        else: self.send_error(404)
    def _send(self, code, ctype, body):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError): pass
    def _sse(self):
        self.send_response(200); self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache"); self.end_headers()
        try:
            while True:
                self.wfile.write(f"data: {json.dumps(FC.snapshot())}\n\n".encode())
                self.wfile.flush(); time.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError): pass
    def _video(self):
        q = BROKER.subscribe()
        self.send_response(200); self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={self.BOUNDARY}")
        self.end_headers()
        try:
            while True:
                frame = q.get()
                self.wfile.write(b"--" + self.BOUNDARY.encode() + b"\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                self.wfile.write(frame); self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError): pass
        finally: BROKER.unsubscribe(q)


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True; allow_reuse_address = True


def main():
    global FC, LINK, BROKER, DRONE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address (default localhost). Use 0.0.0.0 to allow other "
                         "devices — INSECURE on the drone's open wifi (anyone could fly it).")
    args = ap.parse_args()
    BROKER = VideoBroker(); LINK = LinkMonitor(); FC = FlightController(); DRONE = DroneLink()
    if not FC.bound:
        print("WARNING: 192.168.1.100 not bound — run `python3 ufo.py connect` first.")
    srv = ThreadedServer((args.host, args.port), Handler)
    if args.host == "0.0.0.0":
        print("WARNING: bound to 0.0.0.0 — anyone on this network can control the drone.")
    print(f"FLIGHT CONSOLE -> http://localhost:{args.port}")
    print("ARM is off by default. SPACE = EMERGENCY STOP; T takeoff, L land. Ctrl-C to quit.")
    try: srv.serve_forever()
    except KeyboardInterrupt:
        FC.running = False; LINK.running = False; print("\nstopped.")


if __name__ == "__main__":
    main()
