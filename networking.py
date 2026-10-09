import socket
import struct
import threading


MAX_FRAME_SIZE = 4 * 1024 * 1024
FRAME_HEADER_SIZE = 4


def _receive_exact(connection, size):
    data = bytearray()
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


class VideoCall:
    def __init__(self, get_local_frame, port=8765):
        self.get_local_frame = get_local_frame
        self.port = port
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._listener = None
        self._connection = None
        self._remote_frame = None
        self._role = None
        self._status = "idle"
        self._peer = None
        self._error = None

    def _set_state(self, status, error=None):
        with self._lock:
            self._status = status
            self._error = error

    def start_host(self):
        self.end()
        self._stop_event.clear()
        self._role = "host"
        self._set_state("starting")
        threading.Thread(target=self._host, daemon=True).start()

    def _host(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("0.0.0.0", self.port))
            listener.listen(1)
            listener.settimeout(0.5)
            self._listener = listener
            self._set_state("waiting")
            while not self._stop_event.is_set():
                try:
                    connection, address = listener.accept()
                    self._peer = address[0]
                    self._run_connection(connection)
                    return
                except socket.timeout:
                    continue
        except OSError as error:
            if not self._stop_event.is_set():
                self._set_state("error", str(error))
        finally:
            listener.close()
            if self._listener is listener:
                self._listener = None

    def start_client(self, host):
        self.end()
        self._stop_event.clear()
        self._role = "client"
        self._peer = host
        self._set_state("connecting")
        threading.Thread(target=self._connect, args=(host,), daemon=True).start()

    def _connect(self, host):
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connection.settimeout(5)
        try:
            connection.connect((host, self.port))
            connection.settimeout(None)
            self._run_connection(connection)
        except OSError as error:
            connection.close()
            if not self._stop_event.is_set():
                self._set_state("error", str(error))

    def _run_connection(self, connection):
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 512 * 1024)
        connection.settimeout(2)
        with self._lock:
            self._connection = connection
            self._status = "connected"
            self._error = None

        sender = threading.Thread(
            target=self._send_frames, args=(connection,), daemon=True
        )
        sender.start()
        try:
            while not self._stop_event.is_set():
                header = _receive_exact(connection, FRAME_HEADER_SIZE)
                if header is None:
                    break
                (frame_size,) = struct.unpack("!I", header)
                if frame_size <= 0 or frame_size > MAX_FRAME_SIZE:
                    raise ValueError("Received an invalid video frame size")
                frame = _receive_exact(connection, frame_size)
                if frame is None:
                    break
                with self._lock:
                    self._remote_frame = frame
        except (OSError, ValueError) as error:
            if not self._stop_event.is_set():
                self._set_state("error", str(error))
        finally:
            self._stop_event.set()
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
            with self._lock:
                self._connection = None
                if self._status == "connected":
                    self._status = "disconnected"

    def _send_frames(self, connection):
        while not self._stop_event.is_set():
            frame = self.get_local_frame()
            if frame:
                try:
                    connection.sendall(struct.pack("!I", len(frame)) + frame)
                except OSError:
                    self._stop_event.set()
                    return
            self._stop_event.wait(1 / 15)

    def get_remote_frame(self):
        with self._lock:
            return self._remote_frame

    def get_status(self):
        with self._lock:
            return {
                "status": self._status,
                "role": self._role,
                "peer": self._peer,
                "error": self._error,
            }

    def end(self):
        self._stop_event.set()
        for resource in (self._connection, self._listener):
            if resource:
                try:
                    resource.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    resource.close()
                except OSError:
                    pass
        with self._lock:
            self._connection = None
            self._listener = None
            self._remote_frame = None
            self._role = None
            self._peer = None
            self._status = "idle"
            self._error = None
