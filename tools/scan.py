#!/usr/bin/env python3
"""
Drone Network Scanner — Discover cooingdv drones on the local network.

Scans for drones by:
  1. Checking common drone IPs (192.168.1.1, 192.168.4.1, 192.168.0.1)
  2. Probing known ports (7070 RTSP, 7099 UDP control)
  3. Sniffing for drone UDP traffic
"""

import socket
import time
import sys
import struct

DRONE_IPS = ["192.168.1.1", "192.168.4.1", "192.168.0.1"]
RTSP_PORT = 7070
CTRL_PORT = 7099


def check_rtsp(ip, port=RTSP_PORT):
    """Check if an RTSP server is running on the given IP:port."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect((ip, port))
        s.sendall(b"OPTIONS rtsp://localhost RTSP/1.0\r\n\r\n")
        resp = s.recv(1024)
        s.close()
        if b"RTSP" in resp:
            return True, resp[:100]
        return False, None
    except:
        return False, None


def check_udp_control(ip, port=CTRL_PORT):
    """Send a heartbeat and check for response."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.bind(("", 0))
        s.sendto(bytes([0x01, 0x01]), (ip, port))
        try:
            data, addr = s.recvfrom(256)
            s.close()
            return True, data.hex()
        except socket.timeout:
            s.close()
            return False, None
    except:
        return False, None


def ping(ip):
    """Simple ICMP ping."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
        s.settimeout(1)
        # Send echo request
        pid = 1
        packet = struct.pack("!BBHHH", 8, 0, 0, pid, 1)
        # Simple checksum
        checksum = 0
        for i in range(0, len(packet), 2):
            if i + 1 < len(packet):
                checksum += (packet[i] << 8) + packet[i + 1]
        checksum = (checksum >> 16) + (checksum & 0xFFFF)
        checksum = ~checksum & 0xFFFF
        packet = struct.pack("!BBHHH", 8, 0, checksum, pid, 1)
        s.sendto(packet, (ip, 0))
        data, addr = s.recvfrom(256)
        s.close()
        return True
    except:
        return False


def scan_wifi():
    """List nearby WiFi networks (Linux only)."""
    import subprocess
    try:
        result = subprocess.run(
            ["nmcli", "-f", "SSID,BSSID,CHAN,SIGNAL", "dev", "wifi", "list"],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.strip().split("\n")
        drones = []
        for line in lines[1:]:  # skip header
            if "UFO" in line.upper() or "DRONE" in line.upper():
                drones.append(line.strip())
        return drones
    except:
        return []


def main():
    print("=" * 50)
    print("Drone Network Scanner")
    print("=" * 50)

    # Scan WiFi for drone SSIDs
    print("\n[1] Scanning WiFi for drone networks...")
    drones = scan_wifi()
    if drones:
        for d in drones:
            print(f"  Found: {d}")
    else:
        print("  (no drone networks detected via nmcli)")

    # Check common IPs
    print("\n[2] Probing common drone IPs...")
    for ip in DRONE_IPS:
        print(f"\n  --- {ip} ---")
        alive = ping(ip)
        print(f"  Ping:    {'YES' if alive else 'no'}")

        if alive:
            rtsp_ok, rtsp_resp = check_rtsp(ip)
            print(f"  RTSP:    {'YES' if rtsp_ok else 'no'}")
            if rtsp_ok:
                print(f"  Response: {rtsp_resp}")

            ctrl_ok, ctrl_resp = check_udp_control(ip)
            print(f"  Ctrl:    {'YES' if ctrl_ok else 'no'}")
            if ctrl_ok:
                print(f"  Telemetry: {ctrl_resp}")

    print("\n[3] Done.")


if __name__ == "__main__":
    main()
