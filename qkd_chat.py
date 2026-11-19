#!/usr/bin/env python3
"""
Quantum Key Management System (QKMS) Chat & Messaging Engine
Self-contained module integrating local QKMS keys with AES-256-CBC encryption for the React GUI.
"""

import os
import sys
import time
from datetime import datetime
from typing import List, Dict, Optional

# Local key database file inside backend/
KEY_DATABASE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "key_database.txt")

# Import local crypto engine
from crypto_engine import (
    encrypt as aes_encrypt,
    decrypt as aes_decrypt,
    key_from_binary,
)

# In-memory message store (persists while backend server is running)
MESSAGES: List[Dict] = [
    {
        "id": 1,
        "sender": "IISER Bhopal Quantum Node",
        "badge": "QKD NODE",
        "avatar": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=100&auto=format&fit=crop&q=80",
        "channel": "#iiser-bhopal",
        "text": "SimulaQron BB84 key exchange complete with 256-bit secret key. Channel QBER is 2.18%.",
        "key_id": 1,
        "protocol": "BB84",
        "iv_hex": "4a7f678d90bd51a929bfd6a691ae4805",
        "ciphertext_hex": "f200580d89cb6098a40ac98f5744fa2799e9f7193e1113a3f61487b5ca4fb79f",
        "time": "10:32 AM",
        "reactions": ["🔐 Active Key", "✅ Verified"]
    },
    {
        "id": 2,
        "sender": "IIT Roorkee Gateway",
        "badge": "GATEWAY",
        "avatar": "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=100&auto=format&fit=crop&q=80",
        "channel": "#iit-roorkee",
        "text": "Quantum secure channel verified. Ready for QKD-encrypted messaging and telemetry transfer.",
        "key_id": 2,
        "protocol": "BB84",
        "iv_hex": "d59733dd13008aafc2fed50c3994cd59",
        "ciphertext_hex": "3e59930d1b1659f21b8af071e1e81e292ee0d84fa1e43a6baef2df14bf2d1746",
        "time": "11:15 AM",
        "reactions": ["🔐 QKD Ready"]
    }
]


def parse_key_record(raw_record: str) -> Optional[Dict]:
    """Parse raw tab-separated QKMS database line into a dictionary."""
    if not raw_record:
        return None
    fields = raw_record.strip().split("\t")
    if len(fields) < 9:
        return None
    try:
        return {
            "id": int(fields[0]),
            "protocol": fields[1],
            "length": int(fields[2]),
            "status": fields[3],
            "owner": fields[4],
            "timestamp": fields[5],
            "expiry": fields[6],
            "usage": int(fields[7]),
            "preview": fields[8][:16] + "..." + fields[8][-8:]
        }
    except Exception:
        return None


def get_all_active_keys() -> List[Dict]:
    """Retrieve all ACTIVE QKD keys from local key_database.txt."""
    if not os.path.exists(KEY_DATABASE_FILE):
        return []
    
    active = []
    try:
        with open(KEY_DATABASE_FILE, "r") as f:
            for line in f:
                fields = line.strip().split("\t")
                if len(fields) >= 9 and fields[3] == "ACTIVE":
                    record = parse_key_record(line)
                    if record:
                        active.append(record)
        return active
    except Exception as err:
        print(f"[ERROR] Failed to fetch active keys: {err}")
        return []


def get_key_raw(key_id: int) -> Optional[str]:
    """Get the full 256-bit binary key string for a key_id from key_database.txt."""
    if not os.path.exists(KEY_DATABASE_FILE):
        return None

    try:
        with open(KEY_DATABASE_FILE, "r") as f:
            for line in f:
                fields = line.strip().split("\t")
                if len(fields) >= 9 and int(fields[0]) == key_id:
                    return fields[8]  # Secret 256-bit binary string
        return None
    except Exception as err:
        print(f"[ERROR] Could not get raw key for {key_id}: {err}")
        return None


def send_encrypted_message(
    sender: str,
    channel: str,
    plaintext: str,
    key_id: int
) -> Dict:
    """
    Encrypt message using the QKD key from local database and add to the message feed.
    """
    binary_key = get_key_raw(key_id)
    if not binary_key:
        raise ValueError(f"QKD Key #{key_id} not found in local QKMS database.")

    # 1. Derive 32-byte AES key from 256-bit binary QKD key
    aes_key = key_from_binary(binary_key)

    # 2. Encrypt plaintext using AES-256-CBC
    iv, ciphertext = aes_encrypt(plaintext, aes_key)

    # 3. Create secure message object
    msg_id = int(time.time() * 1000)
    current_time = datetime.now().strftime("%I:%M %p")

    message_obj = {
        "id": msg_id,
        "sender": sender or "Local QKD Node (You)",
        "badge": f"QKD KEY #{key_id}",
        "avatar": "https://images.unsplash.com/photo-1535713875002-d1d0cf377fde?w=100&auto=format&fit=crop&q=80",
        "channel": channel or "#iiser-bhopal",
        "text": plaintext,
        "key_id": key_id,
        "protocol": "BB84",
        "iv_hex": iv.hex(),
        "ciphertext_hex": ciphertext.hex(),
        "time": current_time,
        "reactions": [f"🔐 Key #{key_id}", "⚡ Quantum Encrypted"]
    }

    MESSAGES.append(message_obj)
    return message_obj


def get_messages(channel: Optional[str] = None) -> List[Dict]:
    """Get all messages, optionally filtered by channel."""
    if channel:
        return [m for m in MESSAGES if m.get("channel") == channel]
    return list(MESSAGES)


def verify_decrypt_message(key_id: int, iv_hex: str, ciphertext_hex: str) -> Dict:
    """
    Simulate Bob-side reception: Decrypt ciphertext using only the Key ID from local QKMS.
    Confirms zero secret key exposure over the wire.
    """
    binary_key = get_key_raw(key_id)
    if not binary_key:
        return {"status": "ERROR", "detail": f"Key ID #{key_id} not found in local QKMS"}

    try:
        aes_key = key_from_binary(binary_key)
        iv = bytes.fromhex(iv_hex)
        ciphertext = bytes.fromhex(ciphertext_hex)
        plaintext = aes_decrypt(iv, ciphertext, aes_key)
        return {
            "status": "SUCCESS",
            "key_id": key_id,
            "decrypted_text": plaintext,
            "verified": True
        }
    except Exception as err:
        return {"status": "ERROR", "detail": str(err)}
