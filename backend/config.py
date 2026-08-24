import os
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
from pydantic import BaseModel, Field

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATABASE_PATH = DATA_DIR / "camcounter.db"
SAMPLE_VIDEOS_DIR = DATA_DIR / "sample_videos"
RECORDINGS_DIR = DATA_DIR / "recordings"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
SAMPLE_VIDEOS_DIR.mkdir(exist_ok=True)
RECORDINGS_DIR.mkdir(exist_ok=True)

# Default AI Settings
DEFAULT_MODEL = "yolov8n.pt"  # Lightweight and fast
DEFAULT_CONFIDENCE = 0.40
DEFAULT_IOU = 0.45
DEFAULT_TRACKER = "bytetrack.yaml"


class Point(BaseModel):
    x: float  # Normalized 0.0 to 1.0 or pixel coordinates
    y: float


class TripwireLine(BaseModel):
    id: str
    name: str = "Entrance Line"
    # p1 and p2 define the line segment
    p1: Point
    p2: Point
    # in_direction vector orientation or label
    in_label: str = "IN"
    out_label: str = "OUT"
    color: str = "#10B981"  # Emerald green hex
    active: bool = True
    in_count: int = 0
    out_count: int = 0


class OccupancyZone(BaseModel):
    id: str
    name: str = "Lobby Area"
    # Polygon points (at least 3 vertices)
    points: List[Point]
    max_capacity: int = 10
    color: str = "#3B82F6"  # Blue hex
    active: bool = True
    current_count: int = 0
    peak_count: int = 0


class CameraConfig(BaseModel):
    id: str
    name: str
    source_type: str = "webcam"  # "webcam", "rtsp", "http", "file", "synthetic"
    source_url: str = "0"        # Device index "0", RTSP URL, or file path
    enabled: bool = True
    target_fps: int = 25
    confidence_threshold: float = DEFAULT_CONFIDENCE
    iou_threshold: float = DEFAULT_IOU
    model_name: str = DEFAULT_MODEL
    show_boxes: bool = True
    show_labels: bool = True
    show_trails: bool = True
    show_zones: bool = True
    show_lines: bool = True
    lines: List[TripwireLine] = Field(default_factory=list)
    zones: List[OccupancyZone] = Field(default_factory=list)
    alert_max_occupancy: int = 20
    alert_enabled: bool = True


class SystemSettings(BaseModel):
    app_port: int = 8000
    app_host: str = "0.0.0.0"
    enable_gpu: bool = True
    retention_days: int = 30
