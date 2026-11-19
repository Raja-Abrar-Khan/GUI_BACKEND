# SYSTEM ARCHITECTURE: PRESENT VS. FUTURE ROADMAP
## Classical Simulation Layer to Live Distributed QKD & KMS Integration

---

## 1. Executive Summary

This document specifies the architectural evolution of the **Quantum GUI Platform & Backend Suite**. 

* **Current Architecture (Present):** A classical desktop and web-based visualization environment simulating Quantum Key Distribution (QKD) workflows, utilizing static mock data (`key_database.txt`) and classical LAN media streaming (OpenCV + FastAPI).
* **Target Architecture (Future):** A production-ready control and application layer directly integrated with a **Distributed Key Management System (KMS)** deployed across real quantum network nodes (e.g., IIT Roorkee, IISER Bhopal, IISc Bangalore). It provides secure key negotiation, automated key lifecycle management (Reserve → Consume), and session-bound encrypted communications (Chat, Video, File Transfer).

---

## 2. Present Architecture: What is Happening Right Now (Simulation Layer)

```
[ Operator / User ]
         │
         ▼
┌────────────────────────────────────────────────────────┐
│ REACT FRONTEND (gui_lab)                              │
│                                                        │
│  • System Interface: Simulates BB84/E91 selection       │
│  • Reads: key_database.txt (Static mock keys)           │
│  • Displays: Mock QBER, Sifted Bits, Synthetic Latency │
└─────────────────────────┬──────────────────────────────┘
                          │
          HTTP Requests (localhost:8000)
                          │
                          ▼
┌────────────────────────────────────────────────────────┐
│ PYTHON FASTAPI BACKEND (backend/app.py)                │
│                                                        │
│  • OpenCV Video Stream: Webcam capture (cv2)           │
│  • Classical LAN Sockets: Raw TCP frame streaming       │
│  • No QKD Key Encryption on Video/Chat Packets          │
└────────────────────────────────────────────────────────┘
```

### Current Characteristics & Limitations:
1. **Mock Key Source (`key_database.txt`):**
   * Keys are statically stored strings inside `key_database.txt`.
   * Random keys are sampled locally in the browser to simulate key generation.
   * No physical quantum channels or quantum hardware nodes are probed.

2. **Isolated Application Modules:**
   * Modules (Quantum Chat, Video Meeting, File Transfer) exist largely as separate interfaces.
   * Video calling streams unencrypted frames over raw TCP/IP sockets (`networking.py`) or HTTP MJPEG (`app.py`).
   * Selecting a quantum link in the System Interface does not automatically encrypt live video or chat streams with a corresponding QKD key.

3. **No Key Lifecycle State Machine:**
   * Keys are visually marked "Active" or "Deactivated" on the frontend, but there is no central authority verifying key consumption or preventing key reuse across multiple nodes.

---

## 3. Future Architecture: What Will Happen (Distributed KMS & Real QKD)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           QUANTUM NETWORK LAYER                         │
│   [IIT Roorkee QKD Node] <==== Quantum Channel (Photons) ====> [IISER Bhopal QKD Node]
│             │                                                          │
│             ▼                                                          ▼
│   [Distributed KMS Node A] <--- Classical Sync Channel ---> [Distributed KMS Node B]
│   (Stores Secret Key Bits)                                  (Stores Secret Key Bits)
└─────────────┬───────────────────────────────────────────────────────────┘
              │ ETSI GS QKD 014 REST APIs
              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ PYTHON BACKEND & KMS ORCHESTRATOR (backend/)                           │
│                                                                         │
│  • kms_client.py: Queries live KMS nodes for Key IDs & metadata         │
│  • app.py: Exposes secure endpoints to GUI for key selection & locking  │
│  • Frame Encryptor: Encrypts OpenCV video frames using active QKD keys  │
└─────────────────────────────┬───────────────────────────────────────────┘
                              │
               JSON REST & WebSocket (Localhost)
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ REACT GUI CONTROL PLANE (gui_lab/)                                      │
│                                                                         │
│  • Link Selection: Queries KMS for live available keys (Node A ↔ Node B)│
│  • Key Selection: Operator picks Key ID (Secret bits never exposed)     │
│  • Session Synchronization: Key ID propagates to Chat, Video, and Files │
│  • Lifecycle Trigger: Signals KMS to mark key RESERVED → CONSUMED       │
└─────────────────────────────────────────────────────────────────────────┘
```

### Key Pillars of the Future System:

### A. Integration with Distributed KMS (ETSI GS QKD 014 / 004 Standard)
* Rather than loading static files, the backend connects to distributed KMS servers located at each quantum node.
* The KMS instances continuously manage key pools generated by physical QKD hardware (single-photon transmitters/receivers, entangled pair sources).

### B. Security of Key Selection via GUI (Zero-Knowledge Architecture)
* **The Core Security Rule:** Raw secret key bits (256-bit symmetric keys) are **never** rendered in plaintext on the GUI and are **never** transmitted over classical channels.
* **Key ID Reference Model:**
  1. Both Node A and Node B already hold the identical secret key in their hardware security modules (HSM) / secure enclaves.
  2. The KMS assigns that key a public identifier: e.g., `Key_ID: "KID-9842-BB84"`.
  3. The GUI only displays and selects the **Key ID** and its metadata (bit length, protocol, creation time, status).
  4. When the operator initiates a session, only the `Key_ID` is referenced. An eavesdropper intercepting network traffic sees only the identifier `KID-9842-BB84`, which provides **zero mathematical information** regarding the actual encryption key bits.

### C. Automated Key Lifecycle (Locking & Consumption)
* **Key Discovery:** GUI requests available keys for link `Node_A ↔ Node_B`.
* **Reservation (Lock):** Once selected, the GUI issues a `RESERVE` command to the KMS. The KMS locks the key so no other application or user can claim it.
* **Consumption (Burn):** When the communication session (Video, Chat, or File Transfer) completes, the GUI sends a `CONSUME` confirmation. The key status transitions to `CONSUMED` and is permanently purged from active pools, strictly adhering to One-Time Pad (OTP) and forward secrecy standards.

### D. End-to-End Application Context Propagation
* Establishing a quantum session in the System Interface binds the active `(Node, Protocol, Key_ID)` tuple globally.
* Opening **Video Meeting**, **Quantum Chat**, or **File Transfer** immediately inherits this context without manual re-entry.
* The backend automatically feeds the negotiated QKD key material from the local KMS into the media encryption pipeline (e.g., AES-256-GCM / ChaCha20-Poly1305 / OTP).

---

## 4. Comprehensive Workflow Comparison: Present vs. Future

| Feature / Dimension | Present State (Current Simulation) | Future State (Live Distributed KMS) |
| :--- | :--- | :--- |
| **Key Generation Source** | Static mock file (`key_database.txt`) and pseudo-random generators in JS/Python. | Hardware QKD optical links generating quantum-random keys (BB84, E91, CV-QKD). |
| **Key Storage & Management** | Static text file loaded into frontend memory. | Distributed KMS nodes running secure database & HSM key storage. |
| **Key Presentation in GUI** | Displays synthetic raw binary streams directly on screen. | Displays **Key IDs** and metadata only; raw secret bits remain inside secure KMS vaults. |
| **Key Selection Mechanism** | Client-side visual selection from static table. | Authenticated API calls to distributed KMS (`/api/v1/keys/{src}/{dst}/reserve`). |
| **Key Reuse Prevention** | Purely visual status tag in local React state. | Enforced at KMS level: atomic lock prevents concurrent access; marked `CONSUMED` after session. |
| **Video Call Security** | Unencrypted OpenCV webcam stream over local TCP sockets/HTTP. | Live frame encryption using symmetric keys retrieved directly from local KMS enclave. |
| **Cross-Module Linkage** | Partial `sessionStorage` synchronization; manually entered connection parameters. | Unified Session Broker: active QKD session automatically secures Chat, Video, and File Transfer. |
| **Network Protocol Standard** | Custom ad-hoc REST / Socket calls. | Industry standard **ETSI GS QKD 014 / 004** compliant REST APIs. |

---

## 5. End-to-End Operational Process: Step-by-Step

### Phase 1: Key Pool Synchronization (KMS Level)
1. Physical QKD devices at Node A (IIT Roorkee) and Node B (IISER Bhopal) execute quantum protocols over dedicated optical fiber.
2. Sifted, error-corrected, and privacy-amplified keys are deposited into the respective local KMS key stores.
3. Both KMS instances synchronize metadata over an authenticated classical channel, confirming that both ends share Key ID `KID-7721`.

### Phase 2: Query & Selection via GUI
1. Operator at Node A opens the **System Interface** on the GUI.
2. Operator selects target: **IISER Bhopal**.
3. GUI calls Backend: `GET /api/kms/keys?peer=IISER_Bhopal`.
4. Backend forwards request to local KMS (ETSI 014 endpoint).
5. KMS returns list of available Key IDs:
   ```json
   [
     {
       "key_id": "KID-7721-BB84",
       "bit_length": 256,
       "protocol": "BB84",
       "created_at": "2026-11-18T17:00:00Z",
       "status": "AVAILABLE"
     }
   ]
   ```
6. Operator selects `KID-7721-BB84` (or enables automatic policy selection).

### Phase 3: Session Reservation & Lock
1. GUI sends `POST /api/kms/reserve` with `{"key_id": "KID-7721-BB84"}`.
2. KMS marks `KID-7721-BB84` as `RESERVED`. Neither Node A nor Node B will serve this key to another session.

### Phase 4: Secure Communication Execution
1. Operator launches **Quantum Video Conference** or **Chat**.
2. Application context binds `KID-7721-BB84`.
3. Node A informs Node B via signaling channel: *"Session secured with Key ID KID-7721-BB84"*.
4. Node B queries its local KMS for `KID-7721-BB84`, loads the secret key bytes into its local decryption engine.
5. Live media packets/frames are encrypted at Node A and decrypted at Node B.

### Phase 5: Key Consumption & Session Teardown
1. Operator ends call / session.
2. GUI triggers `POST /api/kms/consume` with `{"key_id": "KID-7721-BB84"}`.
3. KMS permanently updates status to `CONSUMED`.
4. Key lifecycle is recorded in audit logs for compliance and security verification.

---

## 6. Backend Implementation Plan (Deliverables for `backend/`)

To migrate from the current simulation to the target architecture, the following modules will be introduced in the Python backend:

1. **`kms_client.py`:**
   * Python client library interfacing with ETSI GS QKD 014 KMS endpoints.
   * Handles authentication, key pool polling, key reservation, and key consumption notifications.

2. **`kms_router.py` (FastAPI Endpoints):**
   * `/api/kms/status`: Returns connectivity status of local and peer KMS daemons.
   * `/api/kms/keys`: Fetches available Key IDs for a selected peer node.
   * `/api/kms/reserve`: Atomically locks a Key ID for active session use.
   * `/api/kms/consume`: Marks a Key ID as consumed upon session termination.

3. **`crypto_stream.py`:**
   * Bridges OpenCV video frames (`app.py` / `video.py`) with symmetric key encryption (e.g., AES-GCM-256).
   * Encrypts outgoing video payloads before transmission over TCP/UDP and decrypts incoming remote frames.

---

*Document Author: CYQT Quantum Networking & GUI Team*  
*Location: `/home/student/CYQT/GUI_lab/backend/ARCHITECTURE_PRESENT_VS_FUTURE.md`*
