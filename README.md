# GUI_BACKEND: Quantum Key Distribution & Cryptographic Engine
## Python FastAPI, SimulaQron BB84 Simulator & AES-256 Quantum Communications Suite

This repository is the **100% self-contained backend** for the Quantum GUI Platform. It powers live SimulaQron quantum key generation, quantum-encrypted chat, chunked file transfer, and AES-256-GCM video conferencing.

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- Python 3.10, 3.11, or 3.12
- CQC & SimulaQron (`pip install cqc simulaqron`)
- Linux / macOS / Windows

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/Raja-Abrar-Khan/GUI_BACKEND.git
cd GUI_BACKEND

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: .\venv\Scripts\Activate.ps1

# Install requirements
pip install --upgrade pip
pip install -r requirements.txt
pip install cqc simulaqron cryptography python-multipart opencv-python fastapi uvicorn
```

### 3. Start SimulaQron Nodes (Alice & Bob)
```bash
chmod +x manage_simulaqron.sh
./manage_simulaqron.sh start
```

### 4. Launch the Backend Server
```bash
python3 app.py
```
The server will start listening on:
- **Port 8000**: FastAPI HTTP REST API & MJPEG Video Stream
- **Port 6001**: QKD Encrypted TCP File Receiver Service
- **Port 8080**: P2P Encrypted Video Socket (active when hosting call)

---

## 📡 API Reference & Features

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/api/qkd/keys` | `GET` | Returns all available 256-bit keys from `key_database.txt` |
| `/api/qkd/run_simulaqron` | `POST` | Executes live BB84 simulation across 100 qubits and saves new key |
| `/api/qkd/chat/send` | `POST` | Encrypts text using AES-256-CBC with specified QKD Key ID |
| `/api/qkd/file/send` | `POST` | Streams file encrypted with AES-256-GCM to remote TCP port 6001 |
| `/call/host` | `POST` | Starts P2P video host on port 8080 encrypted with specified Key ID |
| `/call/join` | `POST` | Connects to remote P2P video host and decrypts incoming stream |
| `/call/status` | `GET` | Returns video connection status, role, peer, and encryption state |
| `/video` | `GET` | Live OpenCV webcam stream (with synthetic quantum radar fallback) |

---

## 🔒 Cryptographic Engines
- **`crypto_engine.py`**: Clean, self-contained implementation of AES-256-CBC (PKCS7 padding) and AES-256-GCM (12-byte random nonce, GMAC 16-byte authentication tag).
- **`key_database.txt`**: 256-bit hexadecimal cryptographic keys generated from SimulaQron BB84 runs.
- **`simulaqron_runner.py`**: Automated BB84 runner coordinating Alice & Bob across random bases and extracting 256-bit keys.
