import threading
import time

import cv2


class Webcam:
    def __init__(self, camera_index=0):
        self.camera_index = camera_index
        self._lock = threading.Lock()
        self._frame = None
        self._running = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._capture, daemon=True)
        self._thread.start()

    def _capture(self):
        camera = cv2.VideoCapture(self.camera_index)
        try:
            while self._running.is_set():
                success, frame = camera.read()
                if success:
                    encoded, buffer = cv2.imencode(
                        ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75]
                    )
                    if encoded:
                        with self._lock:
                            self._frame = buffer.tobytes()
                else:
                    time.sleep(0.05)
        finally:
            camera.release()

    def get_frame(self):
        self.start()
        with self._lock:
            return self._frame

    def stop(self):
        self._running.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
