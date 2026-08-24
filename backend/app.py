import os
import io
import csv
import json
import uuid
import asyncio
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Form, Depends
from fastapi.responses import StreamingResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from backend.config import BASE_DIR, DATA_DIR, SAMPLE_VIDEOS_DIR, CameraConfig, TripwireLine, OccupancyZone, Point
from backend.database import db
from backend.stream_manager import stream_manager

# Configure Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("camcounter.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting CamCounter Server...")
    # Background task for broadcasting live stats via WebSocket
    broadcaster = asyncio.create_task(websocket_telemetry_broadcaster())
    yield
    logger.info("Shutting down CamCounter Server...")
    broadcaster.cancel()
    stream_manager.shutdown()


app = FastAPI(title="AI People Counter & Surveillance Analytics", version="1.0.0", lifespan=lifespan)

# Enable CORS for local & remote access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_json(self, data: Dict[str, Any]):
        if not self.active_connections:
            return
        msg = json.dumps(data)
        for conn in list(self.active_connections):
            try:
                await conn.send_text(msg)
            except Exception:
                self.disconnect(conn)

ws_manager = ConnectionManager()


async def websocket_telemetry_broadcaster():
    """Periodically broadcast real-time telemetry from all camera streams."""
    while True:
        try:
            telemetry = stream_manager.get_all_analytics()
            if telemetry and ws_manager.active_connections:
                await ws_manager.broadcast_json({"type": "telemetry", "data": telemetry})
        except Exception as e:
            logger.error(f"Error in telemetry broadcast: {e}")
        await asyncio.sleep(0.2)  # 5 times a second for smooth live dashboard updates


# --- REST API Endpoints ---

@app.get("/api/cameras", response_model=List[CameraConfig])
async def get_cameras():
    """Retrieve all configured cameras."""
    return db.get_all_cameras()


@app.post("/api/cameras", response_model=CameraConfig)
async def create_camera(camera: CameraConfig):
    """Add a new camera feed."""
    if not camera.id:
        camera.id = f"cam_{uuid.uuid4().hex[:8]}"
    saved = stream_manager.add_or_update_camera(camera)
    return saved


@app.put("/api/cameras/{camera_id}", response_model=CameraConfig)
async def update_camera(camera_id: str, camera: CameraConfig):
    """Update camera configuration, lines, zones, or sensitivity."""
    camera.id = camera_id
    saved = stream_manager.add_or_update_camera(camera)
    return saved


@app.delete("/api/cameras/{camera_id}")
async def delete_camera(camera_id: str):
    """Delete a camera configuration and stop its pipeline."""
    stream_manager.delete_camera(camera_id)
    return {"status": "success", "message": f"Camera {camera_id} deleted"}


@app.post("/api/cameras/upload-video")
async def upload_video_camera(file: UploadFile = File(...), name: str = Form("Uploaded Video")):
    """Upload a recorded video file to count people and analyze motion."""
    file_id = uuid.uuid4().hex[:8]
    ext = Path(file.filename or "video.mp4").suffix or ".mp4"
    dest_path = SAMPLE_VIDEOS_DIR / f"{file_id}_{file.filename}"
    
    with open(dest_path, "wb") as f:
        content = await file.read()
        f.write(content)

    cam_id = f"cam_{file_id}"
    cam_cfg = CameraConfig(
        id=cam_id,
        name=name,
        source_type="file",
        source_url=str(dest_path),
        enabled=True,
        lines=[
            TripwireLine(
                id=f"line_{file_id}",
                name="Main Crossing",
                p1=Point(x=0.2, y=0.5),
                p2=Point(x=0.8, y=0.5),
                color="#10B981"
            )
        ],
        zones=[]
    )
    saved = stream_manager.add_or_update_camera(cam_cfg)
    return saved


@app.get("/api/stream/{camera_id}")
async def video_stream(camera_id: str):
    """MJPEG live video stream endpoint with real-time AI bounding boxes and overlays."""
    stream = stream_manager.get_stream(camera_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Camera stream not found or inactive")

    def frame_generator():
        import time as _time
        while stream.running:
            jpeg_bytes = stream.get_latest_jpeg()
            if jpeg_bytes is not None:
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" + jpeg_bytes + b"\r\n")
            _time.sleep(0.04)

    return StreamingResponse(
        frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/api/snapshot/{camera_id}")
async def get_snapshot(camera_id: str):
    """Fetch the latest annotated high-resolution JPEG frame."""
    stream = stream_manager.get_stream(camera_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Camera not active")
    jpeg = stream.get_latest_jpeg()
    if not jpeg:
        raise HTTPException(status_code=503, detail="No frame available yet")
    return Response(content=jpeg, media_type="image/jpeg")


@app.get("/api/stats/{camera_id}/live")
async def get_live_stats(camera_id: str):
    """Get live stats for a specific camera."""
    stream = stream_manager.get_stream(camera_id)
    if stream:
        return stream.get_analytics()
    cam = db.get_camera(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")
    return {"camera_id": camera_id, "status": "offline"}


@app.get("/api/stats/{camera_id}/hourly")
async def get_hourly_stats(camera_id: str, hours: int = 24):
    """Get hourly aggregated stats for charts."""
    return db.get_hourly_stats(camera_id, hours=hours)


@app.get("/api/stats/{camera_id}/logs")
async def get_recent_logs(camera_id: str, limit: int = 100):
    """Get recent time-series count records."""
    return db.get_recent_logs(camera_id, limit=limit)


@app.get("/api/stats/{camera_id}/export-csv")
async def export_csv(camera_id: str, limit: int = 1000):
    """Export camera count logs to CSV."""
    logs = db.get_recent_logs(camera_id, limit=limit)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Timestamp", "Camera_ID", "Current_Occupancy", "Total_IN", "Total_OUT", "Peak_Occupancy", "Zone_Counts", "Line_Counts"])
    
    for row in logs:
        writer.writerow([
            row.get("timestamp"),
            camera_id,
            row.get("current_occupancy"),
            row.get("total_in"),
            row.get("total_out"),
            row.get("peak_occupancy"),
            row.get("zone_counts_json"),
            row.get("line_counts_json")
        ])
    
    output.seek(0)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=cam_{camera_id}_counts.csv"}
    )


@app.get("/api/alerts")
async def get_alerts(camera_id: Optional[str] = None, limit: int = 50):
    """Get list of occupancy and capacity alert events."""
    return db.get_alerts(camera_id, limit=limit)


# --- WebSocket Endpoint ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Client can send control messages or keep-alive pings
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("action") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except Exception:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


@app.websocket("/ws/browser-cam/{camera_id}")
async def browser_camera_ws(websocket: WebSocket, camera_id: str):
    """
    WebSocket endpoint for browser-based camera (phone camera via getUserMedia).
    Receives base64 JPEG frames, runs AI detection, returns annotated frames + analytics.
    """
    await websocket.accept()
    logger.info(f"Browser camera WebSocket connected for camera '{camera_id}'")

    processor = stream_manager.get_or_create_browser_processor(camera_id)
    if not processor:
        await websocket.send_text(json.dumps({"type": "error", "message": "Camera not found or not a browser camera"}))
        await websocket.close()
        return

    import base64
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "frame":
                    # Decode base64 JPEG frame
                    frame_b64 = msg.get("data", "")
                    # Strip data URL prefix if present
                    if "," in frame_b64:
                        frame_b64 = frame_b64.split(",", 1)[1]
                    frame_bytes = base64.b64decode(frame_b64)

                    # Process through AI pipeline (runs in thread to not block event loop)
                    import asyncio
                    annotated_jpeg, analytics = await asyncio.get_event_loop().run_in_executor(
                        None, processor.process_frame, frame_bytes
                    )

                    # Send back annotated frame + analytics
                    response = {"type": "result", "analytics": analytics}
                    if annotated_jpeg:
                        response["frame"] = "data:image/jpeg;base64," + base64.b64encode(annotated_jpeg).decode("ascii")

                    await websocket.send_text(json.dumps(response))

                elif msg.get("action") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))

            except Exception as e:
                logger.error(f"Error processing browser camera frame: {e}")
    except WebSocketDisconnect:
        logger.info(f"Browser camera WebSocket disconnected for camera '{camera_id}'")


# Mount Static Frontend
FRONTEND_DIR = BASE_DIR / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
