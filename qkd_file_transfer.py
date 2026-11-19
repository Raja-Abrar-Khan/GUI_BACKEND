#!/usr/bin/env python3
"""
Quantum Key Management System (QKMS) File Transfer Engine
Supports AES-256-GCM chunked file encryption & TCP P2P streaming with SHA-256 verification.
"""

import os
import sys
import json
import time
import socket
import struct
import hashlib
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from crypto_engine import key_from_binary
from qkd_chat import get_key_raw

# File storage directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RECEIVED_DIR = os.path.join(BASE_DIR, "received_files")
os.makedirs(RECEIVED_DIR, exist_ok=True)

FILE_TRANSFER_PORT = 6001
MAX_FRAME_SIZE = 32 * 1024 * 1024  # 32 MB max chunk

# In-memory history of file transfers
TRANSFERS: List[Dict] = [
    {
        "id": 1,
        "name": "Quantum_Network_Topology.bin",
        "size": "2.4 MB",
        "speed": "Completed",
        "progress": 100,
        "peer": "IIT Roorkee Node (192.168.1.101)",
        "direction": "receive",
        "key_id": 1,
        "protocol": "BB84 (AES-256-GCM)",
        "sha256": "a3f5892c90e1...",
        "status": "Verified & Decrypted",
        "timestamp": "10:45 AM"
    }
]

# Background Receiver Server
_receiver_thread: Optional[threading.Thread] = None
_receiver_running = False


def sha256_bytes(data: bytes) -> str:
    """Calculate SHA-256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


def send_frame(sock: socket.socket, header: dict, payload: bytes = b""):
    """
    Frame format:
      4 bytes: total frame body length
      4 bytes: header JSON length
      N bytes: JSON header
      M bytes: payload
    """
    header_bytes = json.dumps(header).encode("utf-8")
    body = struct.pack("!I", len(header_bytes)) + header_bytes + payload
    sock.sendall(struct.pack("!I", len(body)) + body)


def recv_exact(sock: socket.socket, length: int) -> bytes:
    """Read exact number of bytes from socket."""
    buf = bytearray()
    while len(buf) < length:
        chunk = sock.recv(length - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed prematurely")
        buf.extend(chunk)
    return bytes(buf)


def recv_frame(sock: socket.socket):
    """Receive one framed message (header, payload)."""
    raw_len = recv_exact(sock, 4)
    total_len = struct.unpack("!I", raw_len)[0]
    frame_body = recv_exact(sock, total_len)
    
    header_len = struct.unpack("!I", frame_body[:4])[0]
    header = json.loads(frame_body[4:4+header_len].decode("utf-8"))
    payload = frame_body[4+header_len:]
    return header, payload


def send_file_p2p(
    filepath: str,
    peer_ip: str,
    key_id: int,
    port: int = FILE_TRANSFER_PORT
) -> Dict:
    """
    Encrypt file using local QKD key and stream over TCP to peer_ip.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    filename = os.path.basename(filepath)
    with open(filepath, "rb") as f:
        file_bytes = f.read()

    file_size = len(file_bytes)
    sha256_hash = sha256_bytes(file_bytes)

    # 1. Fetch QKD Key from local database
    binary_key = get_key_raw(key_id)
    if not binary_key:
        raise ValueError(f"QKD Key #{key_id} not found in local database")

    aes_key = key_from_binary(binary_key)
    aesgcm = AESGCM(aes_key)

    # 2. Encrypt file using AES-256-GCM
    nonce = os.urandom(12)
    aad = f"QKMS-FILE:{key_id}:{filename}".encode("utf-8")
    ciphertext = aesgcm.encrypt(nonce, file_bytes, aad)

    # 3. Connect to peer
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10.0)
    sock.connect((peer_ip, port))

    try:
        # Send HELLO header with encrypted payload
        header = {
            "type": "FILE_TRANSFER",
            "filename": filename,
            "key_id": key_id,
            "file_size": file_size,
            "sha256": sha256_hash,
            "nonce_hex": nonce.hex()
        }
        send_frame(sock, header, ciphertext)

        # Wait for acknowledgment
        ack_header, _ = recv_frame(sock)
        if ack_header.get("status") != "SUCCESS":
            raise RuntimeError(f"Peer rejected file: {ack_header.get('detail')}")

    finally:
        sock.close()

    transfer_record = {
        "id": int(time.time() * 1000),
        "name": filename,
        "size": f"{(file_size / (1024*1024)):.2f} MB" if file_size >= 1024*1024 else f"{(file_size/1024):.1f} KB",
        "speed": "Completed",
        "progress": 100,
        "peer": f"{peer_ip}:{port}",
        "direction": "send",
        "key_id": key_id,
        "protocol": "BB84 (AES-256-GCM)",
        "sha256": sha256_hash[:16] + "...",
        "status": "Transferred & Verified",
        "timestamp": datetime.now().strftime("%I:%M %p")
    }
    TRANSFERS.insert(0, transfer_record)
    return transfer_record


def _handle_incoming_connection(conn: socket.socket, addr: tuple):
    """Process incoming file transfer from a peer."""
    try:
        header, ciphertext = recv_frame(conn)
        if header.get("type") != "FILE_TRANSFER":
            send_frame(conn, {"status": "ERROR", "detail": "Unknown protocol"})
            return

        filename = os.path.basename(header["filename"])
        key_id = int(header["key_id"])
        nonce = bytes.fromhex(header["nonce_hex"])
        expected_sha256 = header["sha256"]

        # Pull key from local QKMS
        binary_key = get_key_raw(key_id)
        if not binary_key:
            send_frame(conn, {"status": "ERROR", "detail": f"Local Key #{key_id} missing"})
            return

        aes_key = key_from_binary(binary_key)
        aesgcm = AESGCM(aes_key)
        aad = f"QKMS-FILE:{key_id}:{filename}".encode("utf-8")

        # Decrypt payload
        decrypted_bytes = aesgcm.decrypt(nonce, ciphertext, aad)

        # Verify integrity
        actual_sha256 = sha256_bytes(decrypted_bytes)
        if actual_sha256 != expected_sha256:
            send_frame(conn, {"status": "ERROR", "detail": "SHA-256 checksum mismatch"})
            return

        # Save decrypted file to disk
        save_path = os.path.join(RECEIVED_DIR, filename)
        with open(save_path, "wb") as f:
            f.write(decrypted_bytes)

        # Send ACK
        send_frame(conn, {"status": "SUCCESS", "filename": filename, "verified": True})

        # Record transfer
        transfer_record = {
            "id": int(time.time() * 1000),
            "name": filename,
            "size": f"{(len(decrypted_bytes)/(1024*1024)):.2f} MB" if len(decrypted_bytes) >= 1024*1024 else f"{(len(decrypted_bytes)/1024):.1f} KB",
            "speed": "Completed",
            "progress": 100,
            "peer": f"{addr[0]}",
            "direction": "receive",
            "key_id": key_id,
            "protocol": "BB84 (AES-256-GCM)",
            "sha256": actual_sha256[:16] + "...",
            "status": "Saved & Verified",
            "timestamp": datetime.now().strftime("%I:%M %p")
        }
        TRANSFERS.insert(0, transfer_record)
        print(f"[QKMS FILE RECEIVER] Successfully received and decrypted '{filename}' using QKD Key #{key_id}!")

    except Exception as err:
        print(f"[QKMS FILE RECEIVER ERROR] {err}")
        try:
            send_frame(conn, {"status": "ERROR", "detail": str(err)})
        except Exception:
            pass
    finally:
        conn.close()


def start_file_receiver_service(port: int = FILE_TRANSFER_PORT):
    """Start the background TCP receiver server."""
    global _receiver_thread, _receiver_running
    if _receiver_running:
        return

    def _server_loop():
        global _receiver_running
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("0.0.0.0", port))
            server.listen(5)
            _receiver_running = True
            print(f"[QKMS FILE RECEIVER] Listening on port {port} for encrypted transfers...")
            while _receiver_running:
                try:
                    server.settimeout(1.0)
                    conn, addr = server.accept()
                    threading.Thread(target=_handle_incoming_connection, args=(conn, addr), daemon=True).start()
                except socket.timeout:
                    continue
                except Exception as err:
                    if _receiver_running:
                        print(f"[QKMS FILE RECEIVER ACCEPT ERROR] {err}")
                    break
        finally:
            server.close()
            _receiver_running = False

    _receiver_thread = threading.Thread(target=_server_loop, daemon=True)
    _receiver_thread.start()


def get_transfer_history() -> List[Dict]:
    """Return all past and active file transfers."""
    return list(TRANSFERS)
