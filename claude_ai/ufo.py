#!/usr/bin/env python3
"""
RC UFO drone toolkit  —  built by Claude AI
===========================================

Talks to the cheap WiFi folding drone whose phone app is "RC UFO"
(Android package com.cooingdv.rcufo).  Protocol family: "cooingdv".

Discovered facts (verified live on this drone, WIFI-UFO-600849):
  - Drone IP            192.168.1.1
  - Video               rtsp://192.168.1.1:7070/webcam   (MJPEG over RTP/UDP)
  - Resolution          240x320  (portrait sensor)
  - Control / telemetry UDP  192.168.1.1:7099
  - Heartbeat (app->drone)   01 01   every 1000 ms
  - TC stick packet     03 66 B1 B2 ACC TURN FLAGS CKSUM 99   (9 bytes, 20 Hz)
  - Photos on SD card   http://192.168.1.1/PHOTO/  and  /DCIM/  (also FTP ftp/ftp)

IMPORTANT — the .100 rule:
  The drone only streams video/telemetry to a client holding 192.168.1.100.
  DHCP usually hands you .101, so the drone sits there ARP-ing for ".100" and
  never sends anything.  We pin .100 to the wifi profile so it survives reconnects.

Subcommands:
  connect [--timeout N] robustly join the drone wifi + hold .100 (retries)
  net                   pin + ensure 192.168.1.100, ping drone
  snapshot [-o f]       grab one JPEG from the camera
  record [-t s] [-o]    record video to an .mp4
  live [--port 8088]    re-serve the camera as MJPEG -> open in a browser
  telemetry             listen on UDP 7099 and decode what the drone sends

No heavy deps: just Python stdlib + ffmpeg on PATH.
"""

import argparse
import http.server
import os
import re
import socket
import socketserver
import subprocess
import sys
import time

DRONE_IP    = "192.168.1.1"
RTSP_URL    = f"rtsp://{DRONE_IP}:7070/webcam"
CTRL_PORT   = 7099
CLIENT_IP   = "192.168.1.100"         # the address the drone insists on talking to
WIFI_GUESS  = "wlp5s0"
DEFAULT_SSID = "WIFI-UFO-600849"      # historical default; auto-detect overrides it

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- helpers ----
def ffmpeg_input_args():
    """Common ffmpeg flags for pulling this drone's lossy RTSP stream."""
    return [
        "-rtsp_transport", "udp",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-i", RTSP_URL,
    ]


def sh(cmd):
    """Run a command, return (rc, combined output) and never raise."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return 1, str(e)


def wifi_iface():
    """Find the wifi iface: the one on 192.168.1.x, else the first wifi device."""
    rc, out = sh(["ip", "-o", "-4", "addr"])
    for line in out.splitlines():
        if "192.168.1." in line:
            return line.split()[1]
    rc, out = sh(["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"])
    for line in out.splitlines():
        dev, _, typ = line.partition(":")
        if typ.strip() == "wifi":
            return dev
    return WIFI_GUESS


def is_ufo_ssid(ssid):
    """A cooingdv RC-UFO / KY-UFO drone hotspot (e.g. WIFI-UFO-600849)."""
    return bool(ssid) and "UFO" in ssid.upper()


def scan_ufo(rescan=True):
    """Return UFO drone hotspots in range: [{ssid, signal, in_use}], strongest first."""
    args = ["nmcli", "-t", "-f", "IN-USE,SIGNAL,SSID", "device", "wifi", "list"]
    if rescan:
        args += ["--rescan", "yes"]
    rc, out = sh(args)
    seen = {}
    for line in out.splitlines():
        parts = line.split(":", 2)            # SSID is last; keep any colons in it
        if len(parts) < 3:
            continue
        inuse, sig, ssid = parts
        ssid = ssid.strip()
        if not is_ufo_ssid(ssid):
            continue
        try:
            s = int(sig)
        except ValueError:
            s = 0
        if ssid not in seen or s > seen[ssid]["signal"]:
            seen[ssid] = {"ssid": ssid, "signal": s, "in_use": inuse.strip().startswith("*")}
    return sorted(seen.values(), key=lambda d: -d["signal"])


def current_ssid():
    """SSID the wifi card is currently associated with, or None."""
    rc, out = sh(["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi"])
    for line in out.splitlines():
        active, _, ssid = line.partition(":")
        if active == "yes":
            return ssid.strip()
    return None


def ensure_pin(ssid):
    """Pin 192.168.1.100 onto the drone's connection profile so it survives
    reconnects (NetworkManager otherwise flushes a manually-added IP)."""
    rc, out = sh(["nmcli", "-g", "ipv4.addresses", "connection", "show", ssid])
    if CLIENT_IP in out:
        return "already pinned"
    if rc != 0:
        return "no saved profile yet (connect once first)"
    rc, _ = sh(["sudo", "nmcli", "connection", "modify", ssid,
                "+ipv4.addresses", f"{CLIENT_IP}/24"])
    return "pinned" if rc == 0 else "pin FAILED"


def has_100(iface):
    rc, out = sh(["ip", "-4", "addr", "show", iface])
    return CLIENT_IP in out


def ensure_100(iface):
    """Make sure .100 is live on the iface right now (runtime, no reboot)."""
    if has_100(iface):
        return True
    sh(["sudo", "ip", "addr", "add", f"{CLIENT_IP}/24", "dev", iface])
    return has_100(iface)


def connected_to_ufo(ssid=None):
    """True if currently associated with `ssid` (or any UFO drone if ssid=None)."""
    cur = current_ssid()
    if ssid:
        return cur == ssid
    return is_ufo_ssid(cur or "")


def drone_alive():
    rc, _ = sh(["ping", "-c", "1", "-W", "2", DRONE_IP])
    return rc == 0


def connect_ufo(ssid=None, timeout=120, verbose=True):
    """Connect to the right drone and become reachable. If `ssid` is None, the
    strongest UFO hotspot in range is auto-selected. Returns True on success.
    Safe to call repeatedly / while the drone is off."""
    def say(m):
        if verbose:
            print(m, flush=True)

    sh(["nmcli", "radio", "wifi", "on"])
    iface = wifi_iface()
    say(f"wifi iface : {iface}")

    deadline = time.time() + timeout
    attempt = 0
    target = ssid
    while time.time() < deadline:
        attempt += 1
        if target is None:                     # auto-pick the strongest drone
            found = scan_ufo()
            if not found:
                say(f"[{attempt}] no UFO drone hotspot found — is it powered on?")
                time.sleep(3)
                continue
            target = found[0]["ssid"]
            say(f"auto-selected drone: {target}  (signal {found[0]['signal']}%)")
        say(f"pin .100   : {ensure_pin(target)}")
        if not connected_to_ufo(target):
            say(f"[{attempt}] connecting to {target} ...")
            sh(["nmcli", "device", "wifi", "rescan"])
            rc, _ = sh(["nmcli", "connection", "up", target])
            if rc != 0:
                sh(["nmcli", "device", "wifi", "connect", target])
        if connected_to_ufo(target):
            ensure_100(iface)
            if drone_alive():
                say(f"connected  : on {target}, .100 live, drone answers OK")
                return True
            say(f"[{attempt}] associated but drone not answering yet...")
        else:
            say(f"[{attempt}] not on {target} yet — retrying...")
        time.sleep(3)
    say("timed out — is the drone switched on and within range?")
    return False


# -------------------------------------------------------------------- scan ----
def cmd_scan(args):
    found = scan_ufo()
    if not found:
        print("no UFO drone hotspots found — is a drone powered on and in range?")
        sys.exit(1)
    cur = current_ssid()
    print(f"{'':2}{'SSID':<26}{'SIGNAL':>7}")
    for d in found:
        mark = "*" if d["ssid"] == cur else " "
        print(f"{mark:2}{d['ssid']:<26}{str(d['signal'])+'%':>7}")
    print("\nconnect with:  python3 ufo.py connect --ssid <SSID>")


# -------------------------------------------------------------------- net ----
def cmd_net(args):
    iface = wifi_iface()
    ssid = args.ssid or (current_ssid() if connected_to_ufo() else DEFAULT_SSID)
    print(f"wifi iface : {iface}")
    print(f"drone ssid : {ssid}")
    print(f"pin .100   : {ensure_pin(ssid)}")
    print(f"client ip  : {CLIENT_IP} "
          + ("live OK" if ensure_100(iface) else "could not add (sudo?)"))
    print(f"ping drone : {DRONE_IP} ...")
    subprocess.call(["ping", "-c", "2", "-W", "2", DRONE_IP])


def cmd_connect(args):
    ok = connect_ufo(ssid=args.ssid, timeout=args.timeout)
    sys.exit(0 if ok else 1)


# --------------------------------------------------------------- snapshot ----
def cmd_snapshot(args):
    out = args.output or os.path.join(HERE, "captures",
                                      f"shot_{int(time.time())}.jpg")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cmd = ["ffmpeg", "-y", *ffmpeg_input_args(),
           "-frames:v", "1", "-q:v", "2", "-update", "1", out]
    print(f"capturing -> {out}")
    rc = subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if rc == 0 and os.path.exists(out):
        print(f"saved {os.path.getsize(out)} bytes  OK")
    else:
        print("capture failed — is the drone on and is .100 assigned? (run: ufo.py net)")
        sys.exit(1)


# ----------------------------------------------------------------- record ----
def cmd_record(args):
    out = args.output or os.path.join(HERE, "captures",
                                      f"rec_{int(time.time())}.mp4")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cmd = ["ffmpeg", "-y", *ffmpeg_input_args()]
    if args.seconds:
        cmd += ["-t", str(args.seconds)]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", out]
    print(f"recording -> {out}   (Ctrl-C to stop)")
    try:
        subprocess.call(cmd)
    except KeyboardInterrupt:
        pass
    print("done.")


# ------------------------------------------------------------------- live ----
class MjpegHandler(http.server.BaseHTTPRequestHandler):
    BOUNDARY = "ufoframe"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._page()
        elif self.path == "/stream.mjpg":
            self._stream()
        else:
            self.send_error(404)

    def _page(self):
        html = f"""<!doctype html><html><head><meta charset=utf-8>
<title>RC UFO — live</title>
<style>
 body{{background:#0b0d10;color:#e8eef5;font-family:system-ui,sans-serif;
   margin:0;display:flex;flex-direction:column;align-items:center;gap:14px;padding:22px}}
 h1{{font-size:1.1rem;font-weight:600;letter-spacing:.3px;margin:0}}
 .badge{{font-size:.75rem;color:#7d8a99}}
 img{{border:1px solid #243040;border-radius:10px;background:#000;
   width:360px;max-width:92vw}}
 code{{color:#5fd0a0}}
</style></head><body>
<h1>RC UFO &nbsp;·&nbsp; live camera</h1>
<div class=badge>{RTSP_URL} &nbsp;-&gt;&nbsp; MJPEG relay</div>
<img src="/stream.mjpg" alt="connecting to drone...">
<div class=badge>served by <code>ufo.py live</code> · Claude AI</div>
</body></html>"""
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream(self):
        proc = subprocess.Popen(
            ["ffmpeg", *ffmpeg_input_args(), "-f", "mjpeg", "-q:v", "5", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self.send_response(200)
        self.send_header("Age", "0")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Content-Type",
                         f"multipart/x-mixed-replace; boundary={self.BOUNDARY}")
        self.end_headers()
        buf = b""
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                while True:
                    a = buf.find(b"\xff\xd8")
                    b = buf.find(b"\xff\xd9", a + 2)
                    if a == -1 or b == -1:
                        break
                    frame = buf[a:b + 2]
                    buf = buf[b + 2:]
                    self.wfile.write(b"--" + self.BOUNDARY.encode() + b"\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.kill()


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def cmd_live(args):
    srv = ThreadedServer((getattr(args, "host", "127.0.0.1"), args.port), MjpegHandler)
    print(f"live viewer  ->  http://localhost:{args.port}")
    print("open that in a browser. Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


# -------------------------------------------------------------- telemetry ----
GL_IDS = set(range(90, 102)) | {103, 82, 85}


def decode(pkt: bytes) -> str:
    if not pkt:
        return "empty"
    b0 = pkt[0]
    notes = [f"id={b0}", "GL-variant" if b0 in GL_IDS else "TC-variant"]
    if len(pkt) > 2:
        if pkt[2] == 0x4D:
            notes.append(f"PHOTO event (count={pkt[3] if len(pkt)>3 else '?'})")
        elif pkt[2] == 0x58:
            notes.append(f"VIDEO event (count={pkt[4] if len(pkt)>4 else '?'})")
    return "  ".join(notes)


def cmd_telemetry(args):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((CLIENT_IP, 0))
    except OSError:
        print(f"could not bind {CLIENT_IP} — run `python3 ufo.py net` first.")
        sys.exit(1)
    s.settimeout(2.0)
    s.sendto(b"\x01\x01", (DRONE_IP, CTRL_PORT))
    print(f"listening as {s.getsockname()[0]}:{s.getsockname()[1]} "
          f"-> drone {DRONE_IP}:{CTRL_PORT}  (Ctrl-C to stop)", flush=True)
    last = 0
    try:
        while True:
            try:
                data, addr = s.recvfrom(64)
            except socket.timeout:
                s.sendto(b"\x01\x01", (DRONE_IP, CTRL_PORT))
                continue
            now = time.time()
            if now - last > 0.25:
                print(f"{addr[0]}:{addr[1]:<5} {data.hex(' '):<24} | {decode(data)}",
                      flush=True)
                last = now
    except KeyboardInterrupt:
        print("\nstopped.")


# -------------------------------------------------------------------- main ----
def main():
    p = argparse.ArgumentParser(description="RC UFO (cooingdv) drone toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan", help="list UFO drone hotspots in range").set_defaults(fn=cmd_scan)

    sp = sub.add_parser("net", help="pin + ensure .100, ping drone")
    sp.add_argument("--ssid", help="drone SSID to pin .100 onto (default: current/known)")
    sp.set_defaults(fn=cmd_net)

    sp = sub.add_parser("connect", help="robustly connect to the drone (retries)")
    sp.add_argument("--ssid", help="drone SSID to connect to (default: auto-pick strongest)")
    sp.add_argument("--timeout", type=int, default=120, help="seconds to keep trying")
    sp.set_defaults(fn=cmd_connect)

    sp = sub.add_parser("snapshot", help="grab one JPEG")
    sp.add_argument("-o", "--output")
    sp.set_defaults(fn=cmd_snapshot)

    sp = sub.add_parser("record", help="record video to mp4")
    sp.add_argument("-t", "--seconds", type=int)
    sp.add_argument("-o", "--output")
    sp.set_defaults(fn=cmd_record)

    sp = sub.add_parser("live", help="web MJPEG viewer")
    sp.add_argument("--port", type=int, default=8088)
    sp.add_argument("--host", default="127.0.0.1", help="bind address (default localhost)")
    sp.set_defaults(fn=cmd_live)

    sub.add_parser("telemetry", help="decode UDP 7099").set_defaults(fn=cmd_telemetry)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
