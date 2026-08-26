import logging
import time
import cv2
import numpy as np
import threading
from collections import defaultdict
from typing import Dict, List, Optional, Any

from backend.config import CameraConfig
from backend.detector import PersonDetector
from backend.analytics import SpatialAnalytics, TrackedObject
from backend.database import db
from backend.camera_stream import hex_to_bgr
from backend.face_analyzer import FaceAttributeAnalyzer

logger = logging.getLogger("camcounter.browser_stream")


class BrowserCameraProcessor:
    """
    Processes video frames sent from a browser (phone camera via getUserMedia).
    Unlike CameraStream which opens its own capture, this receives frames
    from a WebSocket connection.
    """

    def __init__(self, config: CameraConfig):
        self.config = config
        self.detector = PersonDetector(
            model_name=config.model_name,
            conf_threshold=config.confidence_threshold,
            iou_threshold=config.iou_threshold
        )
        self.analytics = SpatialAnalytics(config.lines, config.zones)
        self.face_analyzer = FaceAttributeAnalyzer(refresh_interval=config.face_analysis_interval)
        if config.enable_face_analysis:
            self.face_analyzer.start()
        self.tracked_objects: Dict[int, TrackedObject] = {}
        self.lock = threading.Lock()

        # Frame buffers
        self.latest_annotated_jpeg: Optional[bytes] = None

        # Performance
        self.fps = 0.0
        self.frame_count = 0
        self.last_fps_time = time.time()

        # Analytics state
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

        # DB logging
        self.db_log_interval = 5.0
        self.last_db_log_time = time.time()

        logger.info(f"BrowserCameraProcessor created for '{config.name}' (ID: {config.id})")

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

    def _build_demographics_summary(self) -> Dict[str, Any]:
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

    def process_frame(self, jpeg_bytes: bytes) -> tuple[Optional[bytes], Dict[str, Any]]:
        """
        Process a single JPEG frame from the browser.
        Returns (annotated_jpeg_bytes, analytics_summary).
        """
        try:
            # Decode JPEG to numpy array
            nparr = np.frombuffer(jpeg_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is None:
                return None, self.latest_analytics_summary

            h, w = frame.shape[:2]

            # 1. AI Detection & Tracking
            detections = self.detector.detect_and_track(frame, persist=True)

            # 2. Update Tracked Objects
            active_ids = set()
            for track_id, norm_bbox, conf in detections:
                active_ids.add(track_id)
                if track_id in self.tracked_objects:
                    self.tracked_objects[track_id].update(norm_bbox, conf)
                else:
                    self.tracked_objects[track_id] = TrackedObject(track_id, norm_bbox, conf)

            # Prune stale tracks
            curr_ts = time.time()
            stale_ids = [tid for tid, obj in self.tracked_objects.items() if curr_ts - obj.last_seen > 1.5]
            for tid in stale_ids:
                del self.tracked_objects[tid]

            # 2b. Feed active tracks to the (background, non-blocking) face attribute analyzer
            if self.config.enable_face_analysis:
                for track_id, track in self.tracked_objects.items():
                    self.face_analyzer.maybe_submit(track_id, frame, track.bbox)
                self.face_analyzer.prune(active_ids)

            # 3. Spatial Analytics
            analytics_summary = self.analytics.process_tracks(self.tracked_objects)
            analytics_summary["demographics"] = self._build_demographics_summary()

            # 4. Alerts
            if self.config.alert_enabled and analytics_summary["current_occupancy"] > self.config.alert_max_occupancy:
                db.log_alert(
                    camera_id=self.config.id,
                    alert_type="OCCUPANCY_EXCEEDED",
                    message=f"Occupancy reached {analytics_summary['current_occupancy']}, exceeding threshold {self.config.alert_max_occupancy}",
                    occupancy=analytics_summary["current_occupancy"],
                    threshold=self.config.alert_max_occupancy
                )

            # 5. Annotate frame
            annotated = self._render_annotations(frame.copy(), analytics_summary)

            # 6. Encode to JPEG
            ret, jpeg_buf = cv2.imencode('.jpg', annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            annotated_jpeg = jpeg_buf.tobytes() if ret else None

            # 7. FPS tracking
            self.frame_count += 1
            elapsed_fps = time.time() - self.last_fps_time
            if elapsed_fps >= 1.0:
                self.fps = round(self.frame_count / elapsed_fps, 1)
                self.frame_count = 0
                self.last_fps_time = time.time()

            # 8. DB logging
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

            with self.lock:
                self.latest_annotated_jpeg = annotated_jpeg
                self.latest_analytics_summary = analytics_summary

            return annotated_jpeg, analytics_summary

        except Exception as e:
            logger.error(f"Error processing browser frame: {e}")
            return None, self.latest_analytics_summary

    def _render_annotations(self, frame: np.ndarray, analytics_summary: Dict[str, Any]) -> np.ndarray:
        """Render bounding boxes, tripwires, zones, and HUD on the frame."""
        import math
        h, w = frame.shape[:2]

        # Zones
        if self.config.show_zones:
            for zone in self.config.zones:
                if not zone.active or len(zone.points) < 3:
                    continue
                pts = np.array([[int(p.x * w), int(p.y * h)] for p in zone.points], np.int32).reshape((-1, 1, 2))
                bgr = hex_to_bgr(zone.color)
                overlay = frame.copy()
                cv2.fillPoly(overlay, [pts], bgr)
                cv2.addWeighted(overlay, 0.25, frame, 0.75, 0, frame)
                cv2.polylines(frame, [pts], isClosed=True, color=bgr, thickness=2)
                center_x = int(np.mean([p.x for p in zone.points]) * w)
                center_y = int(np.mean([p.y for p in zone.points]) * h)
                zone_info = analytics_summary["zones"].get(zone.id, {})
                occ = zone_info.get("current_count", 0)
                label = f"{zone.name}: {occ}/{zone.max_capacity}"
                cv2.putText(frame, label, (center_x - 40, center_y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

        # Tripwire Lines
        if self.config.show_lines:
            for line in self.config.lines:
                if not line.active:
                    continue
                p1 = (int(line.p1.x * w), int(line.p1.y * h))
                p2 = (int(line.p2.x * w), int(line.p2.y * h))
                bgr = hex_to_bgr(line.color)
                cv2.line(frame, p1, p2, bgr, 3)
                cv2.circle(frame, p1, 5, (255, 255, 255), -1)
                cv2.circle(frame, p2, 5, (255, 255, 255), -1)
                mid_x = (p1[0] + p2[0]) // 2
                mid_y = (p1[1] + p2[1]) // 2
                dx = p2[0] - p1[0]
                dy = p2[1] - p1[1]
                length = math.sqrt(dx * dx + dy * dy)
                if length > 0:
                    nx = int(-dy / length * 25)
                    ny = int(dx / length * 25)
                    cv2.arrowedLine(frame, (mid_x, mid_y), (mid_x + nx, mid_y + ny), (0, 255, 255), 2, tipLength=0.35)
                line_info = analytics_summary["lines"].get(line.id, {})
                inc = line_info.get("in_count", 0)
                outc = line_info.get("out_count", 0)
                tag = f"{line.name} | IN: {inc}  OUT: {outc}"
                cv2.putText(frame, tag, (mid_x - 30, mid_y - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)

        # Tracked People
        for track_id, track in self.tracked_objects.items():
            bx1, by1, bx2, by2 = track.bbox
            x1, y1 = int(bx1 * w), int(by1 * h)
            x2, y2 = int(bx2 * w), int(by2 * h)

            if self.config.show_trails and len(track.history) > 1:
                hist_pts = [(int(p[0] * w), int(p[1] * h)) for p in track.history]
                for i in range(1, len(hist_pts)):
                    alpha = i / len(hist_pts)
                    thickness = max(1, int(3 * alpha))
                    cv2.line(frame, hist_pts[i - 1], hist_pts[i], (0, int(220 * alpha), 255), thickness)

            if self.config.show_boxes:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 230, 118), 2)

            if self.config.show_labels:
                label = f"ID #{track_id} ({int(track.conf * 100)}%)"
                if self.config.enable_face_analysis and self.config.show_face_attributes:
                    attrs = self.face_analyzer.get(track_id)
                    if attrs:
                        label += f" | {attrs['age']}y {attrs['gender']} {attrs['dominant_emotion']}"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
                cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 6, y1), (0, 230, 118), -1)
                cv2.putText(frame, label, (x1 + 3, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)

        # HUD
        hud_text = f"{self.config.name} | FPS: {self.fps} | People: {len(self.tracked_objects)}"
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
