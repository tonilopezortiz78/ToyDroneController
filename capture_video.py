#!/usr/bin/env python3
"""Capture and decode video stream from RC UFO drone.
Drone sends UDP data from port 7099."""

import socket
import struct
import time
import threading
import cv2
import numpy as np
import os
import sys
import signal

DRONE_IP = "192.168.1.1"
DRONE_PORT = 7099
LISTEN_PORT = 60472
SRC_IP = "192.168.1.101"

running = True

def capture_udp_stream():
    """Capture UDP video data from drone on port 7099."""
    global running
    
    # Create UDP socket and bind to a port the drone might send to
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.settimeout(2)
    s.bind(('0.0.0.0', LISTEN_PORT))
    
    # Also try common ports the drone might send to
    alt_socks = []
    for port in [40000, 8888, 50000, 7099]:
        try:
            s2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s2.settimeout(0.5)
            s2.bind(('0.0.0.0', port))
            alt_socks.append(s2)
        except:
            pass
    
    print(f"Listening for UDP video data on port {LISTEN_PORT}...")
    print(f"Also listening on: {[port for port in [40000, 8888, 50000, 7099] if True]} (where bind succeeded)")
    
    frame_count = 0
    bytes_data = b""
    
    while running:
        try:
            data, addr = s.recvfrom(65535)
            if addr[0] == DRONE_IP:
                print(f"\nRX {len(data)} bytes from {addr}", end="")
                # Check if it looks like video data
                if len(data) > 100:
                    # Look for JPEG magic bytes anywhere in the data
                    if b'\xff\xd8' in data:
                        idx = data.index(b'\xff\xd8')
                        print(f" [JPEG FOUND at offset {idx}]")
                        # Check for JPEG end
                        if b'\xff\xd9' in data[idx:]:
                            end_idx = data.rindex(b'\xff\xd9')
                            jpg_data = data[idx:end_idx+2]
                            frame = cv2.imdecode(np.frombuffer(jpg_data, dtype=np.uint8), cv2.IMREAD_COLOR)
                            if frame is not None:
                                frame_count += 1
                                cv2.imshow("Drone Stream", frame)
                                key = cv2.waitKey(1) & 0xFF
                                if key == ord('q'):
                                    running = False
                                    break
                                print(f"  Frame {frame_count}: {frame.shape}")
                                # Save periodically
                                if frame_count % 30 == 0:
                                    cv2.imwrite(f"deepseek_pro/frame_{frame_count}.jpg", frame)
                        else:
                            # Incomplete JPEG - buffer it
                            bytes_data += data
                            print(" [partial JPEG - buffered]")
                    else:
                        print(f" hex: {data[:40].hex()}")
                else:
                    print(f" hex: {data.hex()}")
        except socket.timeout:
            # Check alt sockets
            for s2 in alt_socks:
                try:
                    data, addr = s2.recvfrom(65535)
                    if addr[0] == DRONE_IP:
                        print(f"\nALT RX on port {s2.getsockname()[1]}: {len(data)} bytes from {addr}")
                        if len(data) > 10:
                            print(f"  hex: {data[:40].hex()}")
                except socket.timeout:
                    pass
                except:
                    pass
        except OSError as e:
            if not running:
                break
            print(f"Error: {e}")
    
    cv2.destroyAllWindows()
    s.close()
    for s2 in alt_socks:
        s2.close()

def send_heartbeats():
    """Keep the drone connection alive."""
    global running
    # TCP heartbeats via scapy
    import subprocess
    while running:
        try:
            subprocess.run([
                'sudo', '/home/tony/software/drone/venv/bin/python3', '-c',
                f"""
from scapy.all import *
import random
p = IP(src='{SRC_IP}', dst='{DRONE_IP}', ttl=64) / TCP(sport=random.randint(32768,49152), dport=7060, flags='S')
send(p, verbose=0)
p2 = IP(src='{SRC_IP}', dst='{DRONE_IP}', ttl=64) / UDP(sport=random.randint(32768,49152), dport=40000) / Raw(load=bytes([0x63,0x63,0x01,0x00,0x00,0x00,0x00]))
send(p2, verbose=0)
"""
            ], timeout=5)
        except:
            pass
        time.sleep(1)

def main():
    print("=" * 60)
    print("RC UFO Drone Video Capture")
    print("=" * 60)
    print(f"Drone IP: {DRONE_IP}")
    print(f"Listening on port: {LISTEN_PORT}")
    print("Press 'q' in video window to quit\n")
    
    # Start heartbeat in background
    t = threading.Thread(target=send_heartbeats, daemon=True)
    t.start()
    
    # Capture and display video
    capture_udp_stream()
    
    global running
    running = False
    print("\nDone")

if __name__ == "__main__":
    main()
