# AI People Counter & Surveillance Camera Analytics

A real-time Computer Vision system for **USB Webcams**, **RTSP/IP Surveillance Cameras**, **HTTP/MJPEG Streams**, and **Recorded Video Files** with automated person detection, persistent tracking, directional tripwire counting, polygonal zone occupancy monitoring, and an interactive modern web dashboard.

---

## 🌟 Key Features

- **Multi-Source Video Pipeline**:
  - **USB / Integrated Webcams** (e.g. Device index `0`, `1`, `2`)
  - **IP / Surveillance Cameras** via RTSP URLs (`rtsp://admin:pass@ip:port/stream1`)
  - **HTTP / MJPEG Streams**
  - **Video File Uploads** (`.mp4`, `.avi`, `.mkv`)
  - **Built-in Synthetic/Demo Mode** for instant testing without cameras
- **AI Detection & Tracking**:
  - Ultralytics YOLO (Person Class 0)
  - Persistent ID tracking with ByteTrack
  - Hardware acceleration on CUDA GPU with seamless CPU fallback
- **Spatial Analytics**:
  - **Directional Tripwire (Line Crossing)**: Mathematical vector crossing detection for entrance / exit counts
  - **Polygonal Zone Occupancy**: Real-time headcount inside custom polygon zones (e.g. lobby, queues, checkout)
  - **Capacity & Occupancy Alerts**: Instant visual alerts and historical logging when room limit is exceeded
- **Interactive Modern Web Dashboard**:
  - Live video feed with toggleable bounding boxes, person IDs, motion trails, tripwires, and zones
  - **Visual On-Screen Editor**: Click and draw tripwires and zones directly on top of the live video
  - Live KPI cards (Current Occupancy, Total IN, Total OUT, Net Flow, Peak Occupancy, FPS)
  - Real-time Timeline & Hourly Flow charts (Chart.js)
  - CSV report download and snapshot captures

---

## 🚀 Quick Start

### 1. Launch the Application
Simply double-click **`run.bat`** (or run `run.ps1` in PowerShell):
```powershell
.\run.ps1
```
Or run with Python directly:
```bash
py -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
```

### 2. Open the Dashboard
Navigate to:
```
http://localhost:8000
```

---

## 🛠️ Usage Guide

### Adding a Camera
1. Click **"+ Add Camera Feed"** in the sidebar.
2. Select your camera source type:
   - **USB Webcam**: Enter device index (`0` for default webcam, `1` for external USB camera).
   - **RTSP Surveillance Camera**: Enter your IP camera URL (e.g. `rtsp://admin:password@192.168.1.50:554/h264Preview_01_main`).
   - **Video File**: Upload any MP4/AVI recording to analyze people movement.
   - **Synthetic Feed**: Built-in test simulation.
3. Click **"Connect Camera"**.

### Setting Up Tripwires & Zones
- Click **"+ Add Tripwire Line"** on the video toolbar, then click two points on the camera feed across a door or corridor.
- Click **"+ Add Polygon Zone"**, click 3 or 4 points to define a region (like a waiting area), and double-click to finish.

### Exporting Data
- Click **"Export CSV"** in the top navigation bar to download time-series logs and headcount records.
- Click **"Snapshot"** to download an annotated high-resolution capture.

---

## 📁 Project Structure

```
CamCounter/
├── backend/
│   ├── config.py           # Camera schemas & database paths
│   ├── database.py         # SQLite persistence & analytics queries
│   ├── analytics.py        # Line crossing & Point-in-polygon math
│   ├── detector.py         # YOLO detection & ByteTrack wrapper
│   ├── camera_stream.py    # Threaded capture & annotation renderer
│   ├── stream_manager.py   # Multi-camera coordinator
│   └── app.py              # FastAPI server, REST & WebSockets
├── frontend/
│   ├── index.html          # Modern HTML5 dashboard layout
│   ├── css/styles.css      # Dark glassmorphic design system
│   └── js/
│       ├── app.js          # Application state & WebSocket client
│       ├── canvas_editor.js# On-screen drawing tool
│       └── charts.js       # Dynamic Chart.js charts
├── data/                   # Database and video recordings
├── requirements.txt        # Python dependencies
├── run.bat                 # Windows batch launcher
├── run.ps1                 # PowerShell launcher
└── README.md
```
