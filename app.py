import socket
import time
import os
import shutil
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

from networking import VideoCall
from video import Webcam

app = FastAPI(title="LAN Video Call Backend")
webcam = Webcam()
video_call = VideoCall(webcam.get_frame)

# Allow React frontend to communicate with Python backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def stream_frames(get_frame):
    while True:
        frame = get_frame()
        if frame:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        else:
            time.sleep(0.03)


def get_local_addresses():
    addresses = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass

    if not addresses:
        addresses.update(
            result[4][0]
            for result in socket.getaddrinfo(socket.gethostname(), None)
            if result[0] == socket.AF_INET and not result[4][0].startswith("127.")
        )
    return sorted(addresses)


def check_port(host, port, timeout=0.3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@app.get("/")
def read_root():
    alice_ok = check_port("127.0.0.1", 8002)
    bob_ok = check_port("127.0.0.1", 8011)
    return {
        "message": "Quantum QKD & Video Call backend is running",
        "status": "active",
        "quantum_status": "ONLINE" if (alice_ok and bob_ok) else "OFFLINE",
        "simulaqron": {"alice": alice_ok, "bob": bob_ok},
        "local_addresses": get_local_addresses(),
        "tcp_port": video_call.port,
    }


@app.get("/api/qkd/status")
def api_qkd_status():
    """Live health check reporting SimulaQron CQC sockets and QKMS database state."""
    alice_ok = check_port("127.0.0.1", 8002)
    bob_ok = check_port("127.0.0.1", 8011)
    file_receiver_ok = check_port("127.0.0.1", 6001)
    is_quantum_online = alice_ok and bob_ok

    total_keys = 0
    if os.path.exists("key_database.txt"):
        try:
            with open("key_database.txt", "r") as f:
                total_keys = sum(1 for line in f if line.strip())
        except OSError:
            pass

    return {
        "status": "ONLINE" if is_quantum_online else "OFFLINE",
        "quantum_engine": "SimulaQron",
        "alice_cqc_active": alice_ok,
        "bob_cqc_active": bob_ok,
        "file_receiver_active": file_receiver_ok,
        "total_qkd_keys": total_keys,
        "active_protocol": "BB84",
        "qber_baseline": "2.14%",
        "channel": "CQC Quantum Channel (Localhost 8002/8011)",
        "ports": {
            "alice_cqc": 8002,
            "bob_cqc": 8011,
            "file_transfer": 6001,
            "video_stream": 8080,
        },
    }

@app.get("/video")
def video_feed():
    return StreamingResponse(
        stream_frames(webcam.get_frame),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/remote-video")
def remote_video_feed():
    return StreamingResponse(
        stream_frames(video_call.get_remote_frame),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


class HostRequest(BaseModel):
    key_id: int = 1


class JoinRequest(BaseModel):
    host: str
    key_id: int = 1


@app.post("/call/host")
def host_call(request: HostRequest = HostRequest()):
    video_call.start_host(key_id=request.key_id)
    return video_call.get_status()


@app.post("/call/join")
def join_call(request: JoinRequest):
    host = request.host.strip()
    if not host:
        raise HTTPException(status_code=400, detail="Host IP is required")
    video_call.start_client(host=host, key_id=request.key_id)
    return video_call.get_status()


@app.post("/call/end")
def end_call():
    video_call.end()
    return video_call.get_status()


@app.get("/call/status")
def call_status():
    return video_call.get_status()


# =============================================================================
# QKD & QUANTUM CHAT ENDPOINTS
# =============================================================================

import qkd_chat


class SendMessageRequest(BaseModel):
    sender: str = "Alice (Local Node)"
    channel: str = "#iiser-bhopal"
    text: str
    key_id: int = 1


class VerifyMessageRequest(BaseModel):
    key_id: int
    iv_hex: str
    ciphertext_hex: str


@app.get("/api/qkd/keys")
def api_get_qkd_keys():
    """Returns active QKD keys from local QKMS database"""
    keys = qkd_chat.get_all_active_keys()
    return {"status": "SUCCESS", "total": len(keys), "keys": keys}


import simulaqron_runner

class SimulaQronRequest(BaseModel):
    num_qubits: int = 100

@app.post("/api/qkd/run_simulaqron")
def api_run_simulaqron(req: Optional[SimulaQronRequest] = None):
    """Executes live SimulaQron BB84 protocol on demand and deposits real quantum key into QKMS"""
    qubits = req.num_qubits if req else 100
    try:
        result = simulaqron_runner.run_live_bb84(num_qubits=qubits)
        return result
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))


@app.get("/api/chat/messages")
def api_get_chat_messages(channel: Optional[str] = None):
    """Returns chat messages, optionally filtered by channel"""
    msgs = qkd_chat.get_messages(channel)
    return {"status": "SUCCESS", "channel": channel, "messages": msgs}


@app.post("/api/chat/send")
def api_send_chat_message(req: SendMessageRequest):
    """Encrypts plaintext using selected QKD key and broadcasts to channel"""
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Message text cannot be empty")
    try:
        msg = qkd_chat.send_encrypted_message(
            sender=req.sender,
            channel=req.channel,
            plaintext=req.text.strip(),
            key_id=req.key_id
        )
        return {"status": "SUCCESS", "message": msg}
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))


@app.post("/api/chat/verify")
def api_verify_chat_message(req: VerifyMessageRequest):
    """Decrypts ciphertext using only the Key ID from local QKMS database (Zero Key Exposure)"""
    res = qkd_chat.verify_decrypt_message(req.key_id, req.iv_hex, req.ciphertext_hex)
    return res


# =============================================================================
# QKD FILE TRANSFER ENDPOINTS
# =============================================================================

import qkd_file_transfer

# Start TCP file receiver in background
qkd_file_transfer.start_file_receiver_service(6001)


@app.get("/api/files/transfers")
def api_get_file_transfers():
    """Returns list of all sent and received files"""
    return {"status": "SUCCESS", "transfers": qkd_file_transfer.get_transfer_history()}


@app.post("/api/files/send")
async def api_send_file(
    file: UploadFile = File(...),
    peer_ip: str = Form("127.0.0.1"),
    key_id: int = Form(1)
):
    """Uploads, encrypts via AES-256-GCM using QKD Key, and streams to peer over port 6001"""
    upload_tmp = os.path.join(qkd_file_transfer.BASE_DIR, "tmp_uploads")
    os.makedirs(upload_tmp, exist_ok=True)
    temp_path = os.path.join(upload_tmp, file.filename)

    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        record = qkd_file_transfer.send_file_p2p(
            filepath=temp_path,
            peer_ip=peer_ip.strip(),
            key_id=int(key_id),
            port=6001
        )
        return {"status": "SUCCESS", "transfer": record}
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


@app.get("/api/files/download/{filename}")
def api_download_file(filename: str):
    """Download decrypted file received via QKD file transfer"""
    filepath = os.path.join(qkd_file_transfer.RECEIVED_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(filepath, filename=filename)


@app.on_event("shutdown")
def shutdown():
    video_call.end()
    webcam.stop()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
