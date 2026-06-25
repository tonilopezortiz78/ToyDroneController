#!/usr/bin/env python3
"""
RC UFO Drone — Photo / Video Capture Tool

Records the RTSP stream to JPEG images or AVI video files.

Usage:
    python3 capture.py --mode photo        # Press 'c' to capture photo
    python3 capture.py --mode video        # Press 'c' to start/stop recording
"""

import cv2
import socket
import threading
import time
import argparse
import os
from datetime import datetime

DRONE_IP = "192.168.1.1"
CTRL_PORT = 7099
RTSP_PORT = 7070

running = True


def heartbeat():
    global running
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", 0))
    beat = bytes([0x01, 0x01])
    while running:
        try:
            s.sendto(beat, (DRONE_IP, CTRL_PORT))
        except OSError:
            pass
        time.sleep(1)
    s.close()


def main():
    global running
    parser = argparse.ArgumentParser(description="RC UFO Drone Capture")
    parser.add_argument("--ip", default=DRONE_IP, help="Drone IP")
    parser.add_argument("--mode", choices=["photo", "video"], default="photo",
                        help="Capture mode")
    parser.add_argument("--output", default="./captures", help="Output directory")
    parser.add_argument("--fps", type=int, default=15, help="Recording FPS")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    print(f"RC UFO Drone — {'Photo' if args.mode == 'photo' else 'Video'} Capture")
    print(f"  Output: {args.output}/")
    print(f"  Press 'c' to capture, 'q' to quit")
    print()

    threading.Thread(target=heartbeat, daemon=True).start()
    time.sleep(0.5)

    rtsp_url = f"rtsp://{args.ip}:{RTSP_PORT}/webcam"
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not cap.isOpened():
        print("ERROR: Could not open video stream")
        return

    recording = False
    out = None
    frame_count = 0
    photo_count = 0

    while running:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue

        frame_count += 1

        # Draw recording indicator
        if recording:
            cv2.circle(frame, (30, 30), 10, (0, 0, 255), -1)
            cv2.putText(frame, "REC", (45, 35),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

        cv2.imshow("RC UFO Drone Capture", frame)

        key = cv2.waitKey(1) & 0xFF

        if key == ord('q'):
            break

        elif key == ord('c'):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")

            if args.mode == "photo":
                path = os.path.join(args.output, f"photo_{ts}.jpg")
                cv2.imwrite(path, frame)
                photo_count += 1
                print(f"  Saved {path}")

            elif args.mode == "video":
                if not recording:
                    path = os.path.join(args.output, f"video_{ts}.avi")
                    h, w = frame.shape[:2]
                    out = cv2.VideoWriter(
                        path, cv2.VideoWriter_fourcc(*"MJPG"),
                        args.fps, (w, h)
                    )
                    recording = True
                    print(f"  Recording to {path} ...")
                else:
                    if out:
                        out.release()
                        out = None
                    recording = False
                    print(f"  Recording stopped")

        if recording and out is not None:
            out.write(frame)

        # Status
        if frame_count % 30 == 0:
            status = f"  Frames: {frame_count}"
            if recording:
                status += " [RECORDING]"
            print(f"\r{status}", end="", flush=True)

    running = False
    if out:
        out.release()
    cap.release()
    cv2.destroyAllWindows()
    print(f"\n\nDone. Captured {photo_count} photos.")


if __name__ == "__main__":
    main()
