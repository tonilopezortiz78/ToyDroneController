"""
Video Protocol Adapter for Cooingdv drones.

Uses RTSP streaming instead of custom UDP protocol - much simpler than 
the S2x and WiFi UAV implementations.

The drone broadcasts video at: rtsp://192.168.1.1:7070/webcam

This adapter uses OpenCV's VideoCapture to connect to the RTSP stream
and provides frames through the standard turbodrone interface.
"""

import io
import logging
import subprocess
import cv2
import numpy as np
import queue
import threading
import time
from typing import Final, Optional, List

from models.cooingdv_video_model import CooingdvVideoModel
from models.video_frame import VideoFrame
from protocols.base_video_protocol import BaseVideoProtocolAdapter

logger = logging.getLogger(__name__)


class CooingdvVideoProtocolAdapter(BaseVideoProtocolAdapter):
    """
    Video protocol adapter for cooingdv drones using RTSP streaming.
    
    Unlike the UDP-based adapters for S2x and WiFi UAV drones, this adapter
    connects to a standard RTSP stream, making it much simpler and more
    reliable.
    
    Features:
    - Automatic reconnection on stream failure
    - Frame rate limiting to prevent buffer overflow
    - Thread-safe frame queue
    """

    DEFAULT_DRONE_IP: Final = "192.168.1.1"
    DEFAULT_RTSP_PORT: Final = 7070
    DEFAULT_CONTROL_PORT: Final = 7099
    
    RTSP_PATH: Final = "/webcam"
    
    # Reconnection settings
    RECONNECT_DELAY: Final = 1.0  # seconds
    MAX_RECONNECT_ATTEMPTS: Final = 100
    
    # Frame capture settings
    FRAME_TIMEOUT: Final = 5.0  # seconds without frame triggers reconnect
    READ_FAILURE_BACKOFF: Final = 0.05  # seconds between failed reads

    def __init__(
        self,
        drone_ip: str = DEFAULT_DRONE_IP,
        control_port: int = DEFAULT_CONTROL_PORT,
        video_port: int = DEFAULT_RTSP_PORT,
        *,
        debug: bool = False,
    ):
        super().__init__(drone_ip, control_port, video_port)
        self.model = CooingdvVideoModel()
        
        self.debug = debug or logger.isEnabledFor(logging.DEBUG)
        self._dbg = logger.debug if self.debug else (lambda *a, **k: None)
        
        # Build RTSP URL
        self.rtsp_url = f"rtsp://{drone_ip}:{video_port}{self.RTSP_PATH}"
        self._dbg(f"[cooingdv-video] RTSP URL: {self.rtsp_url}")
        
        # ffmpeg process handle
        self._proc: Optional[subprocess.Popen] = None
        self._pipe: Optional[io.BufferedReader] = None
        self._stream_lock = threading.Lock()
        
        # Threading
        self._running = False
        self._rx_thread: Optional[threading.Thread] = None
        self._frame_q: "queue.Queue[VideoFrame]" = queue.Queue(maxsize=2)
        
        # Packet buffer for compatibility with existing interface
        self._pkt_lock = threading.Lock()
        self._pkt_buffer: List[bytes] = []
        
        # Statistics
        self.frames_ok = 0
        self.frames_dropped = 0
        self.reconnect_count = 0
        self._last_frame_time = time.time()
        self._fps_frames = 0
        self._fps_time = time.monotonic()
        self.current_fps = 0
        self.video_width = 0
        self.video_height = 0

    # ------------------------------------------------------------------ #
    # RTSP Connection Management (via ffmpeg subprocess)
    # ------------------------------------------------------------------ #
    def _open_stream(self) -> bool:
        """
        Open the RTSP stream via ffmpeg subprocess (UDP transport).
        ffmpeg pipes MJPEG frames to stdout for reading.
        """
        with self._stream_lock:
            self._close_stream()
            
            self._dbg(f"[cooingdv-video] Opening RTSP stream: {self.rtsp_url}")
            
            # Probe stream with ffprobe first
            r = subprocess.run(
                ["timeout", "3", "ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_streams", self.rtsp_url],
                capture_output=True, timeout=4
            )
            if r.returncode != 0 or b'"codec_type": "video"' not in r.stdout:
                self._dbg("[cooingdv-video] ffprobe check failed, skipping stream")
                return False
            
            # Start ffmpeg with UDP transport (drone rejects TCP)
            cmd = [
                "ffmpeg", "-rtsp_transport", "udp",
                "-i", self.rtsp_url,
                "-f", "image2pipe",
                "-vcodec", "mjpeg",
                "-q:v", "2",
                "-an",
                "-"
            ]
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0
                )
                self._pipe = io.BufferedReader(self._proc.stdout)
                self._dbg("[cooingdv-video] ffmpeg process started")
                self._last_frame_time = time.time()
                return True
            except Exception as e:
                self._dbg(f"[cooingdv-video] Failed to start ffmpeg: {e}")
                self._close_stream()
                return False

    def _close_stream(self) -> None:
        """Kill the ffmpeg subprocess."""
        with self._stream_lock:
            if self._proc:
                try:
                    self._proc.kill()
                    self._proc.wait(timeout=2)
                except Exception:
                    pass
                self._proc = None
                self._pipe = None
                self._dbg("[cooingdv-video] Stream closed")

    def _reconnect(self) -> bool:
        """Attempt to reconnect to the stream."""
        self.reconnect_count += 1
        self._dbg(f"[cooingdv-video] Reconnection attempt #{self.reconnect_count}")
        
        self._close_stream()
        time.sleep(self.RECONNECT_DELAY)
        
        for attempt in range(self.MAX_RECONNECT_ATTEMPTS):
            if not self._running:
                return False
            
            if self._open_stream():
                return True
            
            self._dbg(f"[cooingdv-video] Reconnect attempt {attempt + 1}/{self.MAX_RECONNECT_ATTEMPTS} failed")
            time.sleep(self.RECONNECT_DELAY)
        
        return False

    # ------------------------------------------------------------------ #
    # BaseVideoProtocolAdapter interface
    # ------------------------------------------------------------------ #
    def send_start_command(self) -> None:
        """
        For RTSP, we don't need to send a start command.
        The stream starts when we connect.
        """
        pass

    def create_receiver_socket(self):
        """
        Not used for RTSP - OpenCV handles the connection.
        Returns None for compatibility.
        """
        return None

    def handle_payload(self, payload: bytes) -> Optional[VideoFrame]:
        """
        Wrap one encoded RTSP frame using the shared video-model interface.
        """
        return self.model.ingest_chunk(payload=payload)

    # ------------------------------------------------------------------ #
    # Receiver Thread API (matches existing interface)
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Start the video receiver thread. Non-blocking - doesn't wait for stream."""
        if self._rx_thread and self._rx_thread.is_alive():
            return
        
        self._running = True
        self._frame_q = queue.Queue(maxsize=2)
        
        # Start receiver thread immediately (it handles reconnection)
        self._rx_thread = threading.Thread(
            target=self._rx_loop,
            daemon=True,
            name="CooingdvVideoRx",
        )
        self._rx_thread.start()
        self._dbg("[cooingdv-video] Receiver thread started")

    def _rx_loop(self) -> None:
        """
        Main receiver loop - reads frames from ffmpeg pipe.
        """
        time.sleep(1.0)

        read_buf = b""
        while self._running:
            with self._stream_lock:
                proc_ok = self._proc is not None and self._proc.poll() is None
                pipe = self._pipe

            if not proc_ok or not pipe:
                self._dbg("[cooingdv-video] ffmpeg not running, reconnecting...")
                if not self._reconnect():
                    time.sleep(self.RECONNECT_DELAY)
                continue

            try:
                # Read raw bytes from pipe
                chunk = pipe.read(65536)
                if not chunk:
                    if time.time() - self._last_frame_time > self.FRAME_TIMEOUT:
                        self._dbg("[cooingdv-video] Frame timeout, reconnecting...")
                        self._reconnect()
                    else:
                        time.sleep(self.READ_FAILURE_BACKOFF)
                    continue

                read_buf += chunk

                # Extract JPEGs from the byte stream
                soi = 0
                while self._running:
                    # Find start marker
                    soi = read_buf.find(b'\xff\xd8', soi)
                    if soi < 0:
                        read_buf = read_buf[-4096:] if len(read_buf) > 4096 else b""
                        break

                    # Find end marker
                    eoi = read_buf.find(b'\xff\xd9', soi)
                    if eoi < 0:
                        # Incomplete frame, keep buffer
                        if soi > 0:
                            read_buf = read_buf[soi:]
                        break

                    jpeg_bytes = read_buf[soi:eoi + 2]
                    soi = eoi + 2

                    # Decode JPEG to OpenCV frame
                    frame = cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if frame is None:
                        continue

                    self._last_frame_time = time.time()

                    if self.video_width == 0:
                        h, w = frame.shape[:2]
                        self.video_width = w
                        self.video_height = h

                    # Rotate 90° clockwise (camera is mounted 90° off)
                    frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)

                    # Re-encode as JPEG at consistent quality
                    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 85]
                    _, jpeg_data = cv2.imencode('.jpg', frame, encode_param)

                    video_frame = self.handle_payload(jpeg_data.tobytes())
                    if video_frame is None:
                        continue

                    self.frames_ok += 1

                    # FPS counter
                    self._fps_frames += 1
                    now = time.monotonic()
                    if now - self._fps_time >= 1.0:
                        self.current_fps = round(self._fps_frames / (now - self._fps_time))
                        self._fps_frames = 0
                        self._fps_time = now

                    # Add to queue (drop if full)
                    try:
                        self._frame_q.put(video_frame, timeout=0.1)
                    except queue.Full:
                        self.frames_dropped += 1
                        try:
                            self._frame_q.get_nowait()
                            self._frame_q.put(video_frame, timeout=0.1)
                        except (queue.Empty, queue.Full):
                            pass

                # Trim processed data from buffer
                read_buf = read_buf[soi:] if soi > 0 else b""

            except Exception as e:
                self._dbg(f"[cooingdv-video] Error: {e}")
                self._reconnect()

    def get_stats(self) -> dict:
        return {
            "frames_ok": self.frames_ok,
            "frames_dropped": self.frames_dropped,
            "reconnects": self.reconnect_count,
            "fps": self.current_fps,
            "width": self.video_width,
            "height": self.video_height,
        }

    def stop(self) -> None:
        """Stop the video receiver and clean up."""
        self._dbg("[cooingdv-video] Stopping...")
        self._running = False
        
        # Wait for thread to finish
        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=2.0)
        
        # Close stream
        self._close_stream()
        
        self._dbg(
            f"[cooingdv-video] Stopped. Stats: "
            f"frames_ok={self.frames_ok}, "
            f"frames_dropped={self.frames_dropped}, "
            f"reconnects={self.reconnect_count}"
        )

    def is_running(self) -> bool:
        """Check if the receiver is running."""
        return self._running and self._rx_thread is not None and self._rx_thread.is_alive()

    def get_frame(self, timeout: float = 1.0) -> Optional[VideoFrame]:
        """Get the next available frame from the queue."""
        try:
            return self._frame_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def get_packets(self) -> List[bytes]:
        """
        Get raw packets - not really applicable for RTSP.
        Returns empty list for compatibility.
        """
        return []

    # ------------------------------------------------------------------ #
    # Keep-alive (not needed for RTSP, but required by base class)
    # ------------------------------------------------------------------ #
    def start_keepalive(self, interval: float = 1.0) -> None:
        """RTSP doesn't need keep-alive packets."""
        pass

    def stop_keepalive(self) -> None:
        """RTSP doesn't need keep-alive packets."""
        pass

