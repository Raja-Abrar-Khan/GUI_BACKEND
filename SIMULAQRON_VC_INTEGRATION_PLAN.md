# SIMULAQRON & SECURE VIDEO CONFERENCE INTEGRATION GUIDE
## Complete Roadmap, Code Modifications, and 2-PC Setup Guide

---

## 1. Executive Summary & Architecture Overview

The goal is to connect the **React GUI (`gui_lab`)** and **FastAPI Backend (`backend`)** with your teammate's **SimulaQron QKMS engine (`simulaquroncode`)** to achieve true **Quantum-Secured Video Conferencing (VC)** across 2 PCs (Alice and Bob).

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           REACT GUI (gui_lab)                           │
│   • Step 1: Click "Generate Key" → Calls /api/qkd/generate               │
│   • Step 2: Live Keys Table updates from SimulaQron                     │
│   • Step 3: Select Key ID (e.g. #25) & Click "Start Secure VC"         │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   PYTHON BACKEND ORCHESTRATOR (backend/)                │
│   • Triggers SimulaQron BB84 Protocol (Alice ↔ Bob Qubits)              │
│   • Stores key into QKMS database (key_database.txt)                   │
│   • Reads Key #25 secret bytes into AES-256-GCM cipher                  │
│   • Encrypts webcam frames & sends over TCP to Peer                     │
│   • Receives encrypted TCP frames from Peer, decrypts & yields MJPEG    │
└──────────────────┬───────────────────────────────────┬──────────────────┘
                   │                                   │
                   ▼                                   ▼
    [ SimulaQron Virtual Network ]       [ Live Encrypted Video Socket ]
      Alice (8001) ↔ Bob (8010)             Alice (7100) ↔ Bob (7101)
```

---

## 2. Master TODO Checklist

- [ ] **Task 1: Environment & Path Alignment**
  - Use SimulaQron virtual environment at `/home/student/simulaqron412/simulaqron-venv`.
  - Fix hardcoded path in teammate's `qkms/config/config.py` (`BB84_PROJECT` must point to `/home/student/CYQT/simulaquroncode/simulaqron/examples/native-mode/bb84_native`).
- [ ] **Task 2: SimulaQron Multi-PC Network Configuration**
  - Update `simulaqron_network.json` with the current WiFi IP addresses of PC 1 (Alice) and PC 2 (Bob).
- [ ] **Task 3: Backend Bridge Creation**
  - Create `qkd_bridge.py` in `GUI_lab/backend/` to wrap QKMS key generation, key retrieval, and AES-GCM encryption.
  - Add API endpoints in `backend/app.py`:
    - `POST /api/qkd/generate` (runs BB84 via SimulaQron)
    - `GET /api/qkd/keys` (fetches active QKMS keys)
    - `POST /api/call/start_qkd` (starts encrypted video streaming using selected Key ID)
- [ ] **Task 4: Headless Frame Streaming for GUI (No OpenCV Popups)**
  - Teammate's script currently calls `cv2.imshow('Remote Video')` which opens a desktop window.
  - Modify the video receiver so decoded frames are stored in memory and streamed directly to the React UI via `http://localhost:8000/remote-video`.
- [ ] **Task 5: Frontend UI Wiring**
  - Connect `SystemInterfaceModule.jsx` to fetch real keys from `/api/qkd/keys` instead of reading static text.
  - Store the selected `Key_ID` in `sessionStorage`.
  - Pass the selected `Key_ID` into `VideoConfModule.jsx` so the user can click "Host Call" or "Join Call" with quantum encryption.
- [ ] **Task 6: End-to-End 2-PC Validation Test**
  - PC 1 (Alice) generates QKD key via SimulaQron.
  - PC 2 (Bob) synchronizes key digest.
  - Alice calls Bob via GUI with Key ID; both screens display encrypted video.

---

## 3. Code Modifications: Exactly What to Change and Where

### A. Modifications in Teammate's Code (`simulaquroncode/`)

#### 1. Fix Directory Path in `qkms/config/config.py`
* **File:** `/home/student/CYQT/simulaquroncode/simulaqron/examples/native-mode/qkms/config/config.py`
* **Issue:** `BB84_PROJECT` is pointing to `~/SimulaQron/examples/...` which does not exist on this machine.
* **Fix:** Change line 38 to:
  ```python
  BB84_PROJECT = os.path.abspath(
      os.path.join(PROJECT_ROOT, "..", "bb84_native")
  )
  ```

#### 2. Dynamic IP and Port Binding
* **File:** `qkms/config/config.py` & `qkms/application/secure_video_conference.py`
* **Issue:** IPs `10.11.80.94` and `10.11.80.93` are hardcoded. If your WiFi changes, it breaks.
* **Fix:** Allow passing `--local-ip`, `--peer-ip`, and `--key-id` as parameters or environment variables instead of hardcoding.

#### 3. Web-Friendly Video Stream (Headless Mode)
* **File:** `qkms/application/secure_video_conference.py`
* **Issue:** Currently opens a native window using `cv2.imshow()`.
* **Fix:** Store the decrypted frame in a global buffer:
  ```python
  remote_frame = decrypted_jpeg_bytes
  ```
  This allows FastAPI to stream it directly to React: `<img src="http://localhost:8000/remote-video" />`.

---

### B. Modifications in Our Backend (`GUI_lab/backend/`)

#### 1. Add QKD & QKMS Bridge (`backend/qkd_bridge.py`)
Import teammate's QKMS functions:
```python
import sys
sys.path.append("/home/student/CYQT/simulaquroncode/simulaqron/examples/native-mode/qkms")

from database.key_manager import list_active_keys, get_key
from protocol_manager import execute_protocol
```

#### 2. Expose REST Endpoints in `backend/app.py`
```python
@app.get("/api/qkd/keys")
def get_qkd_keys():
    """Returns active keys from QKMS database"""
    return {"keys": list_active_keys()}

@app.post("/api/qkd/generate")
def generate_key():
    """Triggers SimulaQron BB84 protocol"""
    success = execute_protocol("BB84")
    return {"status": "SUCCESS" if success else "FAILED"}
```

#### 3. Integrate AES-GCM Encrypted Sockets into Video Call
Replace unencrypted sockets in `networking.py` with AES-GCM encryption:
* When Alice sends a video frame:
  ```python
  nonce = os.urandom(12)
  encrypted_frame = aesgcm.encrypt(nonce, raw_jpeg_frame, b"QKMS-VIDEO")
  sock.sendall(nonce + encrypted_frame)
  ```
* When Bob receives:
  ```python
  decrypted_frame = aesgcm.decrypt(nonce, encrypted_frame, b"QKMS-VIDEO")
  ```

---

### C. Modifications in Our React Frontend (`GUI_lab/gui_lab/`)

#### 1. `src/components/modules/SystemInterfaceModule.jsx`
* Replace `import keyDatabaseRaw from '../../../key_database.txt?raw';` with a live fetch:
  ```javascript
  const fetchLiveKeys = async () => {
    const res = await fetch("http://localhost:8000/api/qkd/keys");
    const data = await res.json();
    setAvailableKeys(data.keys);
  };
  ```
* Connect the **"Generate Key"** button to `POST http://localhost:8000/api/qkd/generate`.
* On key selection, save to session:
  ```javascript
  sessionStorage.setItem("selectedKeyId", key.id);
  ```

#### 2. `src/components/modules/VideoConfModule.jsx`
* Read `sessionStorage.getItem("selectedKeyId")`.
* Show high-tech badge: `🔒 Secured with QKD Key ID: #25 (AES-256-GCM)`.
* When clicking **Host** or **Join**, send the selected `key_id` to the backend.

---

## 4. How to Setup and Start SimulaQron (Step-by-Step)

### Step 1: Activate the SimulaQron Virtual Environment
The virtual environment is already installed at:
```bash
source /home/student/simulaqron412/simulaqron-venv/bin/activate
```

Verify that SimulaQron is available:
```bash
simulaqron version
```

---

### Step 2: Configure Network IPs for 2 PCs
SimulaQron needs to know the IP addresses of both machines.

Open `/home/student/CYQT/simulaquroncode/simulaqron/examples/native-mode/bb84_native/simulaqron_network.json` and update the IP addresses:
```json
[
  {
    "name": "default",
    "nodes": [
      {
        "Alice": {
          "app_socket": ["<ALICE_WIFI_IP>", 8001],
          "qnodeos_socket": ["<ALICE_WIFI_IP>", 8002],
          "vnode_socket": ["<ALICE_WIFI_IP>", 8003]
        }
      },
      {
        "Bob": {
          "app_socket": ["<BOB_WIFI_IP>", 8010],
          "qnodeos_socket": ["<BOB_WIFI_IP>", 8011],
          "vnode_socket": ["<BOB_WIFI_IP>", 8012]
        }
      }
    ]
  }
]
```

*(Note: For local single-machine testing, you can use `"127.0.0.1"` for both).*

---

### Step 3: Start SimulaQron Network
Clean up any old PID files first:
```bash
rm -f ~/.simulaqron_pids/*.pid
```

Start the virtual quantum network:
```bash
simulaqron start --nodes=Alice,Bob --network-config-file /home/student/CYQT/simulaquroncode/simulaqron/examples/native-mode/bb84_native/simulaqron_network.json
```

To verify it is running:
```bash
simulaqron nodes
```

---

### Step 4: Run BB84 Key Generation (Test Run)
To test key generation directly from terminal:
```bash
cd /home/student/CYQT/simulaquroncode/simulaqron/examples/native-mode/bb84_native

# Start Bob receiver
python3 bob_bb84_100.py &

# Run Alice sender
python3 alice_bb84_100.py
```
This will produce a new QKD key and save it into `qkms/database/key_database.txt`.

---

### Step 5: How to Stop SimulaQron
When testing is complete:
```bash
simulaqron stop
# Or use the teammate's script:
/home/student/CYQT/simulaquroncode/simulaqron/examples/native-mode/bb84_native/terminate.sh
```

---

## 5. Execution Order for Integration

1. **Step 1 (Path Fix):** Edit `qkms/config/config.py` to point `BB84_PROJECT` to the actual `bb84_native` directory.
2. **Step 2 (Backend Bridge):** Add `/api/qkd/keys` and `/api/qkd/generate` to `GUI_lab/backend/app.py`.
3. **Step 3 (GUI Live Keys):** Update `SystemInterfaceModule.jsx` to fetch real keys from the backend.
4. **Step 4 (Encrypted VC):** Connect `VideoConfModule.jsx` with the AES-GCM encrypted video pipeline using the selected Key ID.
5. **Step 5 (2-PC Test):** Run on Alice and Bob machines over WiFi!
