#!/usr/bin/env python3
"""
Protocol Probe — Send test packets to the drone and observe responses.

Used during reverse engineering to discover packet formats and
verify protocol behavior.
"""

import socket
import time
import sys
import os

DRONE_IP = "192.168.1.1"
CTRL_PORT = 7099


def probe(label, data, wait=0.5):
    """Send raw bytes to the drone and print response."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1)
    s.bind(("", 0))

    print(f"\n[{label}]")
    print(f"  TX: {data.hex()} ({len(data)} bytes)")
    s.sendto(data, (DRONE_IP, CTRL_PORT))

    try:
        resp, addr = s.recvfrom(256)
        print(f"  RX from {addr}: {resp.hex()} ({len(resp)} bytes)")
    except socket.timeout:
        print(f"  RX: (no response)")

    s.close()
    time.sleep(wait)


def main():
    print("=" * 50)
    print("cooingdv Protocol Probe")
    print("=" * 50)
    print(f"Target: {DRONE_IP}:{CTRL_PORT}")

    # 1. Heartbeat
    probe("Heartbeat", bytes([0x01, 0x01]))

    # 2. TC control packet (centered, no flags)
    probe("TC Center", bytes([0x03, 0x66, 0x80, 0x80, 0x00, 0x80, 0x00, 0x80 ^ 0x80 ^ 0x00 ^ 0x80 ^ 0x00, 0x99]))

    # 3. TC with throttle
    probe("TC Throttle 50%", bytes([0x03, 0x66, 0x80, 0x80, 0x80, 0x80, 0x00, 0x80 ^ 0x80 ^ 0x80 ^ 0x80 ^ 0x00, 0x99]))

    # 4. TC takeoff
    probe("TC Takeoff", bytes([0x03, 0x66, 0x80, 0x80, 0x00, 0x80, 0x01, 0x80 ^ 0x80 ^ 0x00 ^ 0x80 ^ 0x01, 0x99]))

    # 5. TC stop
    probe("TC Stop", bytes([0x03, 0x66, 0x80, 0x80, 0x00, 0x80, 0x04, 0x80 ^ 0x80 ^ 0x00 ^ 0x80 ^ 0x04, 0x99]))

    # 6. GL control packet (centered)
    gl_data = bytearray(21)
    gl_data[0] = 0x03
    gl_data[1] = 0x66
    gl_data[2] = 0x14
    gl_data[3] = 0x80  # roll
    gl_data[4] = 0x80  # pitch
    gl_data[5] = 0x00  # throttle
    gl_data[6] = 0x80  # yaw
    gl_data[7] = 0x00  # flags1
    gl_data[8] = 0x00  # flags2
    gl_data[18] = 0x80 ^ 0x80 ^ 0x00 ^ 0x80 ^ 0x00 ^ 0x00  # checksum
    gl_data[19] = 0x99  # end
    probe("GL Center", bytes(gl_data))

    # 7. Empty / ping
    probe("Empty", b"")

    # 8. Random data
    probe("Random", bytes([0xde, 0xad, 0xbe, 0xef]))

    print("\nDone.")


if __name__ == "__main__":
    main()
