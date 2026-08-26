import os
import cv2
import time
import math
import logging
import threading
import numpy as np
from typing import Dict, Optional, Tuple, List, Any

from collections import defaultdict

from backend.config import CameraConfig, Point, TripwireLine, OccupancyZone
from backend.detector import PersonDetector
from backend.analytics import SpatialAnalytics, TrackedObject
from backend.database import db
from backend.face_analyzer import FaceAttributeAnalyzer

logger = logging.getLogger("camcounter.camera")

# Ensure OpenCV uses TCP for RTSP to prevent UDP packet loss/artifacts
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"


def hex_to_bgr(hex_str: str) -> Tuple[int, int, int]:
    """Converts hex color string like #10B981 to OpenCV BGR tuple."""
    hex_str = hex_str.lstrip('#')
    if len(hex_str) == 6:
        r, g, b = tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))
        return (b, g, r)
    return (0, 255, 0)


class SyntheticStreamGenerator:
    """
    Generates a realistic synthetic camera feed with walking people personas
    for immediate testing without physical camera hardware.
    """
    def __init__(self, width: int = 640, height: int = 360, num_people: int = 4):
        self.width = width
        self.height = height
        self.people = []
        for i in range(num_people):
            self.people.append({
                "id": i + 1,
                "x": np.random.uniform(0.15, 0.85),
                "y": np.random.uniform(0.3, 0.85),
                "vx": np.random.uniform(-0.003, 0.003),
                "vy": np.random.uniform(-0.002, 0.002),
                "color": (np.random.randint(80, 220), np.random.randint(80, 220), np.random.randint(120, 255)),
                "height": np.random.uniform(0.22, 0.32),
                "width": np.random.uniform(0.08, 0.12),
                "leg_phase": np.random.uniform(0, math.pi * 2)
            })

    def get_frame(self) -> np.ndarray:
        # Create subtle CCTV background gradient
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        # Floor & Wall perspective
        cv2.rectangle(frame, (0, 0), (self.width, int(self.height * 0.35)), (35, 38, 46), -1)
        cv2.rectangle(frame, (0, int(self.height * 0.35)), (self.width, self.height), (24, 27, 33), -1)
        
        # Room perspective grid lines
        for x in range(0, self.width, 100):
            cv2.line(frame, (x, int(self.height * 0.35)), (int(x * 1.3 - self.width * 0.15), self.height), (32, 36, 44), 1)
        for y in range(int(self.height * 0.35), self.height, 60):
            cv2.line(frame, (0, y), (self.width, y), (30, 34, 42), 1)

        # Entrance door on left
        cv2.rectangle(frame, (40, int(self.height * 0.15)), (180, int(self.height * 0.70)), (45, 50, 60), 2)
        cv2.putText(frame, "ENTRANCE", (55, int(self.height * 0.13)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 120, 140), 1)

        # Exit door on right
        cv2.rectangle(frame, (self.width - 180, int(self.height * 0.15)), (self.width - 40, int(self.height * 0.70)), (45, 50, 60), 2)
        cv2.putText(frame, "EXIT", (self.width - 150, int(self.height * 0.13)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 120, 140), 1)

        # Update and render people
        for p in self.people:
            p["x"] += p["vx"]
            p["y"] += p["vy"]
            p["leg_phase"] += 0.15

            # Bounce off boundary walls
            if p["x"] < 0.1 or p["x"] > 0.9:
                p["vx"] *= -1
            if p["y"] < 0.4 or p["y"] > 0.88:
                p["vy"] *= -1

            # Draw person figure
            px = int(p["x"] * self.width)
            py = int(p["y"] * self.height)
            pw = int(p["width"] * self.width)
            ph = int(p["height"] * self.height)

            # Shadow
            cv2.ellipse(frame, (px, py), (int(pw * 0.6), int(ph * 0.08)), 0, 0, 360, (15, 17, 22), -1)

            # Torso
            torso_top = py - int(ph * 0.7)
            torso_bottom = py - int(ph * 0.35)
            cv2.rectangle(frame, (px - pw//3, torso_top), (px + pw//3, torso_bottom), p["color"], -1)

            # Head
            head_center = (px, py - int(ph * 0.82))
            head_radius = int(ph * 0.11)
            cv2.circle(frame, head_center, head_radius, (190, 205, 220), -1)

            # Legs with walking animation
            leg_swing = int(math.sin(p["leg_phase"]) * (pw * 0.35))
            cv2.line(frame, (px - pw//4, torso_bottom), (px - pw//4 + leg_swing, py), (60, 65, 75), 4)
            cv2.line(frame, (px + pw//4, torso_bottom), (px + pw//4 - leg_swing, py), (60, 65, 75), 4)

        return frame


class CameraStream:
    """
    Manages an individual video stream (Webcam, RTSP, Video File, or Synthetic),
    performs AI detection, spatial line/zone counting, and serves JPEG frames.
    """
    def __init__(self, config: CameraConfig):
        self.config = config
        self.running = False
        self.cap: Optional[cv2.VideoCapture] = None
        self.synthetic_gen: Optional[SyntheticStreamGenerator] = None
        
        # Detector & Analytics
        self.detector = PersonDetector(
            model_name=config.model_name,
            conf_threshold=config.confidence_threshold,
            iou_threshold=config.iou_threshold
        )
        self.analytics = SpatialAnalytics(config.lines, config.zones)

        # Optional face attribute analysis (age/gender/emotion) - opt-in, off by default
        self.face_analyzer = FaceAttributeAnalyzer(refresh_interval=config.face_analysis_interval)

        # Tracking state: track_id -> TrackedObject
        self.tracked_objects: Dict[int, TrackedObject] = {}
        
        # Latest frame buffers (thread safe)
        self.latest_raw_frame: Optional[np.ndarray] = None
        self.latest_annotated_jpeg: Optional[bytes] = None
        self.lock = threading.Lock()
        
        # Performance & Stats
        self.fps = 0.0
        self.frame_count = 0
        self.last_fps_time = time.time()
        self.latest_analytics_summary: Dict[str, Any] = {
            "current_occupancy": 0,
            "total_in": 0,
            "total_out": 0,
            "net_flow": 0,
            "peak_occupancy": 0,
            "lines": {},
            "zones": {},
            "events": []
        }
        
        # Worker Threads
        self.capture_thread: Optional[threading.Thread] = None
        self.db_log_interval = 5.0  # Log counts to DB every 5 seconds
        self.last_db_log_time = time.time()

    def start(self):
        if self.running:
            return
        self.running = True
        if self.config.enable_face_analysis:
            self.face_analyzer.start()
        self.capture_thread = threading.Thread(target=self._run_pipeline, daemon=True)
        self.capture_thread.start()
        logger.info(f"Started camera stream for '{self.config.name}' (ID: {self.config.id})")

    def stop(self):
        self.running = False
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2.0)
        if self.cap:
            self.cap.release()
            self.cap = None
        self.face_analyzer.stop()
        logger.info(f"Stopped camera stream for '{self.config.name}' (ID: {self.config.id})")

    def update_config(self, new_config: CameraConfig):
        with self.lock:
            was_enabled = self.config.enable_face_analysis
            self.config = new_config
            self.detector.conf_threshold = new_config.confidence_threshold
            self.detector.iou_threshold = new_config.iou_threshold
            self.analytics.update_config(new_config.lines, new_config.zones)
            self.face_analyzer.refresh_interval = new_config.face_analysis_interval
            if new_config.enable_face_analysis and not was_enabled:
                self.face_analyzer.start()
            elif not new_config.enable_face_analysis and was_enabled:
                self.face_analyzer.stop()

    def _open_capture(self) -> bool:
        if self.config.source_type == "synthetic":
            self.synthetic_gen = SyntheticStreamGenerator()
            return True

        if self.cap is not None:
            self.cap.release()

        source = self.config.source_url.strip()
        if self.config.source_type == "webcam":
            try:
                device_idx = int(source)
                self.cap = cv2.VideoCapture(device_idx, cv2.CAP_DSHOW if os.name == 'nt' else cv2.CAP_ANY)
            except ValueError:
                self.cap = cv2.VideoCapture(0)
        elif self.config.source_type == "file":
            self.cap = cv2.VideoCapture(source)
        elif self.config.source_type in ("rtsp", "http"):
            self.cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
        else:
            self.cap = cv2.VideoCapture(source)

        if self.cap and self.cap.isOpened():
            # Set buffer size to 1 to reduce lag for live feeds
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            logger.info(f"Successfully opened video capture source: {self.config.source_url}")
            return True
        else:
            logger.warning(f"Could not open capture source {self.config.source_url}. Falling back to synthetic stream.")
            self.synthetic_gen = SyntheticStreamGenerator()
            return True

    def _run_pipeline(self):
        self._open_capture()
        target_frame_time = 1.0 / max(1, self.config.target_fps)

        while self.running:
            start_time = time.time()
            frame = None

            if self.synthetic_gen is not None:
                frame = self.synthetic_gen.get_frame()
            elif self.cap is not None and self.cap.isOpened():
                ret, frame = self.cap.read()
                if not ret or frame is None:
                    if self.config.source_type == "file":
                        # Loop video file
                        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        ret, frame = self.cap.read()
                    else:
                        logger.warning(f"Frame drop or disconnect on camera {self.config.id}. Reconnecting...")
                        time.sleep(1.0)
                        self._open_capture()
                        continue

            if frame is None:
                time.sleep(0.05)
                continue

            # 1. Run AI Detection & Tracking
            detections = self.detector.detect_and_track(frame, persist=True)

            # 2. Update Tracked Objects
            active_ids = set()
            for track_id, norm_bbox, conf in detections:
                active_ids.add(track_id)
                if track_id in self.tracked_objects:
                    self.tracked_objects[track_id].update(norm_bbox, conf)
                else:
                    self.tracked_objects[track_id] = TrackedObject(track_id, norm_bbox, conf)

            # Prune stale tracks (not seen in last 1.5s)
            curr_ts = time.time()
            stale_ids = [tid for tid, obj in self.tracked_objects.items() if curr_ts - obj.last_seen > 1.5]
            for tid in stale_ids:
                del self.tracked_objects[tid]

            # 2b. Feed active tracks to the (background, non-blocking) face attribute analyzer
            if self.config.enable_face_analysis:
                for track_id, track in self.tracked_objects.items():
                    self.face_analyzer.maybe_submit(track_id, frame, track.bbox)
                self.face_analyzer.prune(active_ids)

            # 3. Process Spatial Analytics (Lines & Zones)
            analytics_summary = self.analytics.process_tracks(self.tracked_objects)
            analytics_summary["demographics"] = self._build_demographics_summary()

            # 4. Check for Capacity Alerts
            if self.config.alert_enabled and analytics_summary["current_occupancy"] > self.config.alert_max_occupancy:
                db.log_alert(
                    camera_id=self.config.id,
                    alert_type="OCCUPANCY_EXCEEDED",
                    message=f"Occupancy reached {analytics_summary['current_occupancy']}, exceeding threshold {self.config.alert_max_occupancy}",
                    occupancy=analytics_summary["current_occupancy"],
                    threshold=self.config.alert_max_occupancy
                )

            # 5. Render Visual Annotations
            annotated_frame = self._render_annotations(frame.copy(), analytics_summary)

            # 6. Encode to JPEG
            ret, jpeg_buffer = cv2.imencode('.jpg', annotated_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            
            with self.lock:
                self.latest_raw_frame = frame
                if ret:
                    self.latest_annotated_jpeg = jpeg_buffer.tobytes()
                self.latest_analytics_summary = analytics_summary

            # 7. Calculate FPS
            self.frame_count += 1
            elapsed_fps = time.time() - self.last_fps_time
            if elapsed_fps >= 1.0:
                self.fps = round(self.frame_count / elapsed_fps, 1)
                self.frame_count = 0
                self.last_fps_time = time.time()

            # 8. Periodic Database Logging
            if time.time() - self.last_db_log_time >= self.db_log_interval:
                self.last_db_log_time = time.time()
                db.log_counts(
                    camera_id=self.config.id,
                    current_occ=analytics_summary["current_occupancy"],
                    total_in=analytics_summary["total_in"],
                    total_out=analytics_summary["total_out"],
                    peak_occ=analytics_summary["peak_occupancy"],
                    zone_counts={z: data["current_count"] for z, data in analytics_summary["zones"].items()},
                    line_counts={l: {"in": data["in_count"], "out": data["out_count"]} for l, data in analytics_summary["lines"].items()}
                )

            # Maintain target FPS
            elapsed = time.time() - start_time
            sleep_time = target_frame_time - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _build_demographics_summary(self) -> Dict[str, Any]:
        """Aggregates cached per-track face attributes (age/gender/emotion) into a
        live snapshot for the currently tracked people. Empty when face analysis
        is disabled or no attributes have been resolved yet."""
        if not self.config.enable_face_analysis:
            return {}

        ages: List[int] = []
        genders: Dict[str, int] = defaultdict(int)
        emotions: Dict[str, int] = defaultdict(int)

        for track_id in self.tracked_objects:
            attrs = self.face_analyzer.get(track_id)
            if not attrs:
                continue
            ages.append(attrs["age"])
            genders[attrs["gender"]] += 1
            emotions[attrs["dominant_emotion"]] += 1

        return {
            "analyzed_count": len(ages),
            "avg_age": round(sum(ages) / len(ages), 1) if ages else None,
            "gender_breakdown": dict(genders),
            "emotion_breakdown": dict(emotions),
        }

    def _render_annotations(self, frame: np.ndarray, analytics_summary: Dict[str, Any]) -> np.ndarray:
        h, w = frame.shape[:2]

        # 1. Render Occupancy Zones
        if self.config.show_zones:
            for zone in self.config.zones:
                if not zone.active or len(zone.points) < 3:
                    continue
                pts = np.array([[int(p.x * w), int(p.y * h)] for p in zone.points], np.int32)
                pts = pts.reshape((-1, 1, 2))
                
                bgr = hex_to_bgr(zone.color)
                # Transparent overlay
                overlay = frame.copy()
                cv2.fillPoly(overlay, [pts], bgr)
                cv2.addWeighted(overlay, 0.25, frame, 0.75, 0, frame)
                cv2.polylines(frame, [pts], isClosed=True, color=bgr, thickness=2)

                # Zone Header Tag
                center_x = int(np.mean([p.x for p in zone.points]) * w)
                center_y = int(np.mean([p.y for p in zone.points]) * h)
                zone_info = analytics_summary["zones"].get(zone.id, {})
                occ = zone_info.get("current_count", 0)
                label = f"{zone.name}: {occ}/{zone.max_capacity}"
                cv2.putText(frame, label, (center_x - 40, center_y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

        # 2. Render Tripwire Lines
        if self.config.show_lines:
            for line in self.config.lines:
                if not line.active:
                    continue
                p1 = (int(line.p1.x * w), int(line.p1.y * h))
                p2 = (int(line.p2.x * w), int(line.p2.y * h))
                bgr = hex_to_bgr(line.color)
                
                # Draw main line
                cv2.line(frame, p1, p2, bgr, 3)
                cv2.circle(frame, p1, 5, (255, 255, 255), -1)
                cv2.circle(frame, p2, 5, (255, 255, 255), -1)

                # Draw directional normal arrow in middle
                mid_x = (p1[0] + p2[0]) // 2
                mid_y = (p1[1] + p2[1]) // 2
                dx = p2[0] - p1[0]
                dy = p2[1] - p1[1]
                length = math.sqrt(dx*dx + dy*dy)
                if length > 0:
                    # Unit normal pointing IN
                    nx = int(-dy / length * 25)
                    ny = int(dx / length * 25)
                    cv2.arrowedLine(frame, (mid_x, mid_y), (mid_x + nx, mid_y + ny), (0, 255, 255), 2, tipLength=0.35)

                # Line Label Tag
                line_info = analytics_summary["lines"].get(line.id, {})
                inc = line_info.get("in_count", 0)
                outc = line_info.get("out_count", 0)
                tag = f"{line.name} | IN: {inc}  OUT: {outc}"
                cv2.putText(frame, tag, (mid_x - 30, mid_y - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)

        # 3. Render Tracked People
        for track_id, track in self.tracked_objects.items():
            bx1, by1, bx2, by2 = track.bbox
            x1, y1 = int(bx1 * w), int(by1 * h)
            x2, y2 = int(bx2 * w), int(by2 * h)

            # Motion Trail
            if self.config.show_trails and len(track.history) > 1:
                hist_pts = [(int(p[0] * w), int(p[1] * h)) for p in track.history]
                for i in range(1, len(hist_pts)):
                    alpha = i / len(hist_pts)
                    thickness = max(1, int(3 * alpha))
                    cv2.line(frame, hist_pts[i - 1], hist_pts[i], (0, int(220 * alpha), 255), thickness)

            # Bounding Box
            if self.config.show_boxes:
                # Sleek corner box styling
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 230, 118), 2)

            # Label / ID Tag
            if self.config.show_labels:
                label = f"ID #{track_id} ({int(track.conf * 100)}%)"
                if self.config.enable_face_analysis and self.config.show_face_attributes:
                    attrs = self.face_analyzer.get(track_id)
                    if attrs:
                        label += f" | {attrs['age']}y {attrs['gender']} {attrs['dominant_emotion']}"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
                cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 6, y1), (0, 230, 118), -1)
                cv2.putText(frame, label, (x1 + 3, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)

        # 4. HUD / Status Banner
        # Top-left camera & telemetry HUD
        hud_text = f"{self.config.name} | FPS: {self.fps} | People Detected: {len(self.tracked_objects)}"
        (tw, th), _ = cv2.getTextSize(hud_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(frame, (10, 10), (10 + tw + 14, 10 + th + 14), (20, 24, 30), -1)
        cv2.putText(frame, hud_text, (17, 24 + th // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 230, 180), 1, cv2.LINE_AA)

        return frame

    def get_latest_jpeg(self) -> Optional[bytes]:
        with self.lock:
            return self.latest_annotated_jpeg

    def get_analytics(self) -> Dict[str, Any]:
        with self.lock:
            data = dict(self.latest_analytics_summary)
            data["fps"] = self.fps
            data["camera_id"] = self.config.id
            data["camera_name"] = self.config.name
            return data
