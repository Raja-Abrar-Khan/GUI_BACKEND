import socket
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
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

@app.get("/")
def read_root():
    addresses = sorted(
        {
            result[4][0]
            for result in socket.getaddrinfo(socket.gethostname(), None)
            if result[0] == socket.AF_INET and not result[4][0].startswith("127.")
        }
    )
    return {
        "message": "Video call backend is running",
        "status": "active",
        "local_addresses": addresses,
        "tcp_port": video_call.port,
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


class JoinRequest(BaseModel):
    host: str


@app.post("/call/host")
def host_call():
    video_call.start_host()
    return video_call.get_status()


@app.post("/call/join")
def join_call(request: JoinRequest):
    host = request.host.strip()
    if not host:
        raise HTTPException(status_code=400, detail="Host IP is required")
    video_call.start_client(host)
    return video_call.get_status()


@app.post("/call/end")
def end_call():
    video_call.end()
    return video_call.get_status()


@app.get("/call/status")
def call_status():
    return video_call.get_status()


@app.on_event("shutdown")
def shutdown():
    video_call.end()
    webcam.stop()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
