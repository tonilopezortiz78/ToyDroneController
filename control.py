#!/usr/bin/env python3
"""
RC UFO Drone — Keyboard Control + Live Video Feed

Controls:
  W/A/S/D     Pitch/Roll
  Arrows      Throttle/Yaw
  Space       Takeoff / Land toggle
  Backspace   Emergency stop
  H           Headless mode toggle
  C           Calibrate
  Q           Quit

Protocol:
  - Heartbeat: [0x01, 0x01] UDP :7099
  - Control:   9-byte TC packet UDP :7099
  - Video:     RTSP :7070/webcam
"""

import cv2
import socket
import threading
import time
import sys
import argparse
from dataclasses import dataclass

DRONE_IP = "192.168.1.1"
CTRL_PORT = 7099
RTSP_PORT = 7070

running = True
arming = False  # Space toggles takeoff/land

# ── Packet Builders ─────────────────────────────────────────────────

def build_tc_packet(roll=128, pitch=128, throttle=0, yaw=128, flags=0):
    roll = max(0, min(255, int(roll)))
    pitch = max(0, min(255, int(pitch)))
    throttle = max(0, min(255, int(throttle)))
    yaw = max(0, min(255, int(yaw)))
    checksum = roll ^ pitch ^ throttle ^ yaw ^ flags
    return bytes([0x03, 0x66, roll, pitch, throttle, yaw, flags, checksum, 0x99])


def send_heartbeat(sock):
    sock.sendto(bytes([0x01, 0x01]), (DRONE_IP, CTRL_PORT))


# ── State ────────────────────────────────────────────────────────────

@dataclass
class ControlState:
    roll: int = 128
    pitch: int = 128
    throttle: int = 0
    yaw: int = 128
    flags: int = 0
    armed: bool = False
    headless: bool = False

    def to_packet(self):
        return build_tc_packet(self.roll, self.pitch, self.throttle, self.yaw, self.flags)


state = ControlState()


# ── Keyboard Listener (non-blocking) ─────────────────────────────────

def handle_key(key):
    global state, arming

    step = 20  # axis step per keypress
    deadzone = 128  # center

    if key == ord('w'):    # pitch forward
        state.pitch = max(0, state.pitch - step)
    elif key == ord('s'):  # pitch backward
        state.pitch = min(255, state.pitch + step)
    elif key == ord('a'):  # roll left
        state.roll = max(0, state.roll - step)
    elif key == ord('d'):  # roll right
        state.roll = min(255, state.roll + step)
    elif key == 82:        # up arrow — throttle up
        state.throttle = min(255, state.throttle + step)
    elif key == 84:        # down arrow — throttle down
        state.throttle = max(0, state.throttle - step)
    elif key == 81:        # left arrow — yaw left
        state.yaw = max(0, state.yaw - step)
    elif key == 83:        # right arrow — yaw right
        state.yaw = min(255, state.yaw + step)
    elif key == ord(' '):  # space — toggle takeoff/land
        arming = not arming
        state.armed = arming
        state.flags = 0x01 if arming else 0x02
        state.throttle = 150 if arming else 0
    elif key == 8 or key == 127:  # backspace — emergency stop
        state.flags = 0x04
        state.throttle = 0
        state.armed = False
        arming = False
    elif key == ord('h'):
        state.headless = not state.headless
        if state.headless:
            state.flags |= 0x10
        else:
            state.flags &= ~0x10
    elif key == ord('c'):
        state.flags = 0x80  # calibrate (one-shot, cleared after send)


def clear_oneshot_flags():
    """Clear flags that should only be sent once (takeoff, land, stop)."""
    state.flags &= ~(0x01 | 0x02 | 0x04 | 0x80)


# ── Threads ───────────────────────────────────────────────────────────

def heartbeat_loop(drone_ip: str):
    """Send heartbeat every 1s."""
    global running
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", 0))
    while running:
        try:
            s.sendto(bytes([0x01, 0x01]), (drone_ip, CTRL_PORT))
        except OSError:
            pass
        time.sleep(1)
    s.close()


def control_loop(drone_ip: str):
    """Send control packets at ~20 Hz."""
    global running
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", 0))
    while running:
        pkt = state.to_packet()
        try:
            s.sendto(pkt, (drone_ip, CTRL_PORT))
        except OSError:
            pass
        clear_oneshot_flags()
        time.sleep(0.05)  # 20 Hz
    s.close()


# ── Main ─────────────────────────────────────────────────────────────

def print_help():
    print()
    print("  W/A/S/D     Pitch / Roll")
    print("  Arrows      Throttle / Yaw")
    print("  Space       Takeoff / Land toggle")
    print("  Backspace   Emergency stop")
    print("  H           Headless mode toggle")
    print("  C           Calibrate gyro")
    print("  Q           Quit")
    print()


def main():
    global running

    parser = argparse.ArgumentParser(description="RC UFO Drone Keyboard Control")
    parser.add_argument("--ip", default=DRONE_IP, help="Drone IP")
    parser.add_argument("--no-video", action="store_true", help="Skip video display")
    args = parser.parse_args()

    print("=" * 50)
    print("RC UFO Drone — Keyboard Control")
    print("=" * 50)
    print(f"  Drone: {args.ip}")
    print(f"  Ctrl:  UDP :{CTRL_PORT}")
    print(f"  Video: RTSP :{RTSP_PORT}/webcam")

    # Start threads
    threading.Thread(target=heartbeat_loop, args=(args.ip,), daemon=True).start()
    threading.Thread(target=control_loop, args=(args.ip,), daemon=True).start()

    time.sleep(0.5)

    # Video stream
    cap = None
    if not args.no_video:
        rtsp_url = f"rtsp://{args.ip}:{RTSP_PORT}/webcam"
        print(f"  Opening video stream...")
        cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not cap.isOpened():
            print("  WARNING: Could not open video stream")
            cap = None

    print_help()

    # Control overlay on video
    while running:
        # Show video frame
        if cap is not None:
            ret, frame = cap.read()
            if ret:
                # Draw HUD overlay
                h, w = frame.shape[:2]
                hud = f"R:{state.roll-128:+d} P:{state.pitch-128:+d} "
                hud += f"T:{state.throttle} Y:{state.yaw-128:+d}"
                armed_str = "ARMED" if state.armed else "DISARMED"
                hl_str = "HL" if state.headless else ""
                color = (0, 255, 0) if state.armed else (0, 0, 255)
                cv2.putText(frame, f"[{armed_str}] {hl_str}", (8, 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
                cv2.putText(frame, hud, (8, h - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

                cv2.imshow("RC UFO Drone", frame)
            else:
                time.sleep(0.05)
                continue
        else:
            time.sleep(0.05)

        key = cv2.waitKey(1) & 0xFF if cap is not None else 0

        if key == ord('q'):
            break

        if key == 255:
            continue

        handle_key(key)

        # Status line
        if key != 255:
            status = f"R:{state.roll:3d} P:{state.pitch:3d} "
            status += f"T:{state.throttle:3d} Y:{state.yaw:3d} "
            status += f"F:0x{state.flags:02x} "
            status += f"{'ARMED' if state.armed else '    '} "
            status += f"{'HL' if state.headless else '  '}"
            print(f"\r  {status}", end="", flush=True)

    running = False
    if cap is not None:
        cap.release()
    cv2.destroyAllWindows()
    print("\n\nStopped.")


if __name__ == "__main__":
    main()
