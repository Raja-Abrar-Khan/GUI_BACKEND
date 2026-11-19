import os
import socket
import struct
import threading
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from crypto_engine import key_from_binary
from qkd_chat import get_key_raw

MAX_FRAME_SIZE = 8 * 1024 * 1024
FRAME_HEADER_SIZE = 4
NONCE_SIZE = 12


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
        self._key_id = 1
        self._aesgcm: Optional[AESGCM] = None

    def _setup_cipher(self, key_id: int):
        """Derive AES-256-GCM cipher from QKD key in database."""
        self._key_id = key_id
        binary_key = get_key_raw(key_id)
        if not binary_key:
            # Fallback to key #1 if selected key not found
            binary_key = get_key_raw(1)
        if binary_key:
            aes_key = key_from_binary(binary_key)
            self._aesgcm = AESGCM(aes_key)
            print(f"[QKD VIDEO CALL] Initialized AES-256-GCM encryption with QKD Key #{key_id}")
        else:
            self._aesgcm = None

    def _set_state(self, status, error=None):
        with self._lock:
            self._status = status
            self._error = error

    def start_host(self, key_id: int = 1):
        self.end()
        self._setup_cipher(key_id)
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

    def start_client(self, host: str, key_id: int = 1):
        self.end()
        self._setup_cipher(key_id)
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
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
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
                (packet_size,) = struct.unpack("!I", header)
                if packet_size <= 0 or packet_size > MAX_FRAME_SIZE:
                    raise ValueError("Received an invalid video packet size")
                packet = _receive_exact(connection, packet_size)
                if packet is None:
                    break

                # Decrypt frame using AES-256-GCM
                if self._aesgcm and len(packet) > NONCE_SIZE:
                    nonce = packet[:NONCE_SIZE]
                    ciphertext = packet[NONCE_SIZE:]
                    try:
                        decrypted_frame = self._aesgcm.decrypt(nonce, ciphertext, b"QKMS-VIDEO")
                        with self._lock:
                            self._remote_frame = decrypted_frame
                    except Exception as dec_err:
                        print(f"[QKD VIDEO DECRYPT ERROR] {dec_err}")
                else:
                    # Unencrypted fallback
                    with self._lock:
                        self._remote_frame = packet
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
                    # Encrypt frame using AES-256-GCM
                    if self._aesgcm:
                        nonce = os.urandom(NONCE_SIZE)
                        ciphertext = self._aesgcm.encrypt(nonce, frame, b"QKMS-VIDEO")
                        packet = nonce + ciphertext
                    else:
                        packet = frame

                    connection.sendall(struct.pack("!I", len(packet)) + packet)
                except OSError:
                    self._stop_event.set()
                    return
            self._stop_event.wait(1 / 25)  # 25 FPS

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
                "key_id": self._key_id,
                "encrypted": self._aesgcm is not None,
                "protocol": "AES-256-GCM (BB84)"
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
