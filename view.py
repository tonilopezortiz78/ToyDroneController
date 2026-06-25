#!/usr/bin/env python3
"""
RC UFO Drone (cooingdv) — RTSP Stream Viewer + UDP Heartbeat.

Protocol reverse-engineered from TurboDrone / cooingdv APK analysis.

Usage:
    python3 view.py                        # Default (192.168.1.1)
    python3 view.py --ip 192.168.1.1       # Custom IP
    python3 view.py --rtsp-transport tcp   # Force TCP transport

Controls:
    q       — Quit
    s       — Save snapshot
"""

import cv2
import socket
import threading
import time
import argparse
import os

DRONE_IP = "192.168.1.1"
CTRL_PORT = 7099
RTSP_PORT = 7070

running = True
frame_count = 0


def heartbeat(drone_ip: str):
    """Send UDP heartbeat [0x01, 0x01] every 1s to keep session alive."""
    global running
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", 0))
    beat = bytes([0x01, 0x01])
    while running:
        try:
            s.sendto(beat, (drone_ip, CTRL_PORT))
        except OSError:
            pass
        time.sleep(1)
    s.close()


def view_stream(drone_ip: str, transport: str):
    global running, frame_count
    rtsp_url = f"rtsp://{drone_ip}:{RTSP_PORT}/webcam"
    print(f"  RTSP:  {rtsp_url}")
    print(f"  Ctrl:  UDP {drone_ip}:{CTRL_PORT}")
    print()

    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not cap.isOpened():
        print("ERROR: Could not open RTSP stream.")
        print("  • Is the drone powered on and WiFi connected?")
        print("  • WiFi signal: iwconfig wlp5s0 (look for Signal level)")
        print("  • Verify drone is reachable: ping", drone_ip)
        print("  • FFprobe test: ffprobe", rtsp_url)
        print()
        print("Note: The RTSP stream can be lossy on weak WiFi (-70 dBm or worse).")
        print("Move closer to the drone for better signal.")
        print("If you get '461 Unsupported Transport', the drone needs UDP transport.")
        running = False
        return

    print("Stream open! Press 'q' to quit, 's' to save snapshot.")

    while running:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue

        frame_count += 1
        cv2.imshow("RC UFO Drone", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('s'):
            path = f"snapshot_{int(time.time())}.jpg"
            cv2.imwrite(path, frame)
            print(f"  Saved {path}")

    cap.release()
    cv2.destroyAllWindows()
    running = False


def main():
    parser = argparse.ArgumentParser(description="RC UFO Drone Stream Viewer")
    parser.add_argument("--ip", default=DRONE_IP, help="Drone IP address")
    parser.add_argument("--rtsp-transport", choices=["tcp", "udp"], default="udp",
                        help="RTSP transport protocol (default: udp)")
    args = parser.parse_args()

    print("=" * 50)
    print("RC UFO Drone (cooingdv) — Stream Viewer")
    print("=" * 50)

    t = threading.Thread(target=heartbeat, args=(args.ip,), daemon=True)
    t.start()
    time.sleep(0.5)

    view_stream(args.ip, args.rtsp_transport)

    print(f"\nReceived {frame_count} frames total.")


if __name__ == "__main__":
    main()
