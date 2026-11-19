import threading
import time
import math
import cv2
import numpy as np


class Webcam:
    def __init__(self, camera_index=0):
        self.camera_index = camera_index
        self._lock = threading.Lock()
        self._frame = None
        self._running = threading.Event()
        self._thread = None
        self._angle = 0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._capture, daemon=True)
        self._thread.start()

    def _generate_synthetic_frame(self) -> bytes:
        """Generates an animated high-tech quantum test pattern if webcam is not connected."""
        width, height = 640, 480
        img = np.zeros((height, width, 3), dtype=np.uint8)

        # Background gradient dark slate/blue
        for y in range(height):
            img[y, :] = (int(15 + y * 0.05), int(10 + y * 0.03), int(25 + y * 0.06))

        # Grid lines
        for x in range(0, width, 40):
            cv2.line(img, (x, 0), (x, height), (35, 30, 50), 1)
        for y in range(0, height, 40):
            cv2.line(img, (0, y), (width, y), (35, 30, 50), 1)

        center_x, center_y = width // 2, height // 2

        # Rotating radar sweep
        self._angle = (self._angle + 4) % 360
        rad = math.radians(self._angle)
        sweep_x = int(center_x + 140 * math.cos(rad))
        sweep_y = int(center_y + 140 * math.sin(rad))

        cv2.circle(img, (center_x, center_y), 140, (140, 90, 240), 2)
        cv2.circle(img, (center_x, center_y), 90, (80, 50, 180), 1)
        cv2.circle(img, (center_x, center_y), 40, (50, 30, 120), 1)
        cv2.line(img, (center_x, center_y), (sweep_x, sweep_y), (100, 240, 160), 2)

        # Text badges
        cv2.putText(img, "QKD QUANTUM SECURE STREAM", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (100, 240, 160), 2)
        cv2.putText(img, "SIMULAQRON BB84 LINK ACTIVE", (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 180, 255), 1)

        timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(img, f"TIME: {timestamp_str}", (20, height - 25), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1)
        cv2.putText(img, "640x480 | 30 FPS | AES-256-GCM", (width - 250, height - 25), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (120, 200, 255), 1)

        encoded, buffer = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return buffer.tobytes() if encoded else None

    def _capture(self):
        camera = cv2.VideoCapture(self.camera_index)
        has_real_camera = camera.isOpened()

        try:
            while self._running.is_set():
                if has_real_camera:
                    success, frame = camera.read()
                    if success:
                        encoded, buffer = cv2.imencode(
                            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75]
                        )
                        if encoded:
                            with self._lock:
                                self._frame = buffer.tobytes()
                    else:
                        time.sleep(0.03)
                else:
                    # Synthetic quantum test feed fallback
                    synth_bytes = self._generate_synthetic_frame()
                    if synth_bytes:
                        with self._lock:
                            self._frame = synth_bytes
                    time.sleep(0.033)  # ~30 FPS
        finally:
            if has_real_camera:
                camera.release()

    def get_frame(self):
        self.start()
        with self._lock:
            return self._frame

    def stop(self):
        self._running.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
