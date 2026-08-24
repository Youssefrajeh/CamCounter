import sqlite3
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any

from backend.config import DATABASE_PATH, CameraConfig, TripwireLine, OccupancyZone, Point

logger = logging.getLogger("camcounter.db")


class Database:
    def __init__(self, db_path: Path = DATABASE_PATH):
        self.db_path = str(db_path)
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Cameras table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cameras (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    target_fps INTEGER NOT NULL DEFAULT 25,
                    confidence_threshold REAL NOT NULL DEFAULT 0.40,
                    iou_threshold REAL NOT NULL DEFAULT 0.45,
                    model_name TEXT NOT NULL DEFAULT 'yolov8n.pt',
                    show_boxes INTEGER NOT NULL DEFAULT 1,
                    show_labels INTEGER NOT NULL DEFAULT 1,
                    show_trails INTEGER NOT NULL DEFAULT 1,
                    show_zones INTEGER NOT NULL DEFAULT 1,
                    show_lines INTEGER NOT NULL DEFAULT 1,
                    lines_json TEXT NOT NULL DEFAULT '[]',
                    zones_json TEXT NOT NULL DEFAULT '[]',
                    alert_max_occupancy INTEGER NOT NULL DEFAULT 20,
                    alert_enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Count Events / Snapshots Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS count_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_id TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    current_occupancy INTEGER NOT NULL,
                    total_in INTEGER NOT NULL,
                    total_out INTEGER NOT NULL,
                    peak_occupancy INTEGER NOT NULL,
                    zone_counts_json TEXT NOT NULL DEFAULT '{}',
                    line_counts_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY (camera_id) REFERENCES cameras(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_count_logs_cam_time ON count_logs(camera_id, timestamp)")

            # Hourly Aggregate Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS hourly_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_id TEXT NOT NULL,
                    hour_bucket TEXT NOT NULL, -- YYYY-MM-DD HH:00:00
                    entered_count INTEGER NOT NULL DEFAULT 0,
                    exited_count INTEGER NOT NULL DEFAULT 0,
                    peak_occupancy INTEGER NOT NULL DEFAULT 0,
                    avg_occupancy REAL NOT NULL DEFAULT 0,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(camera_id, hour_bucket),
                    FOREIGN KEY (camera_id) REFERENCES cameras(id) ON DELETE CASCADE
                )
            """)

            # Alerts Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_id TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    alert_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    occupancy_level INTEGER NOT NULL,
                    threshold INTEGER NOT NULL,
                    acknowledged INTEGER NOT NULL DEFAULT 0
                )
            """)

            conn.commit()

    def get_all_cameras(self) -> List[CameraConfig]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM cameras ORDER BY created_at ASC")
            rows = cursor.fetchall()
            cameras = []
            for row in rows:
                lines = [TripwireLine(**item) for item in json.loads(row["lines_json"])]
                zones = [OccupancyZone(**item) for item in json.loads(row["zones_json"])]
                cameras.append(CameraConfig(
                    id=row["id"],
                    name=row["name"],
                    source_type=row["source_type"],
                    source_url=row["source_url"],
                    enabled=bool(row["enabled"]),
                    target_fps=row["target_fps"],
                    confidence_threshold=row["confidence_threshold"],
                    iou_threshold=row["iou_threshold"],
                    model_name=row["model_name"],
                    show_boxes=bool(row["show_boxes"]),
                    show_labels=bool(row["show_labels"]),
                    show_trails=bool(row["show_trails"]),
                    show_zones=bool(row["show_zones"]),
                    show_lines=bool(row["show_lines"]),
                    lines=lines,
                    zones=zones,
                    alert_max_occupancy=row["alert_max_occupancy"],
                    alert_enabled=bool(row["alert_enabled"])
                ))
            return cameras

    def get_camera(self, camera_id: str) -> Optional[CameraConfig]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,))
            row = cursor.fetchone()
            if not row:
                return None
            lines = [TripwireLine(**item) for item in json.loads(row["lines_json"])]
            zones = [OccupancyZone(**item) for item in json.loads(row["zones_json"])]
            return CameraConfig(
                id=row["id"],
                name=row["name"],
                source_type=row["source_type"],
                source_url=row["source_url"],
                enabled=bool(row["enabled"]),
                target_fps=row["target_fps"],
                confidence_threshold=row["confidence_threshold"],
                iou_threshold=row["iou_threshold"],
                model_name=row["model_name"],
                show_boxes=bool(row["show_boxes"]),
                show_labels=bool(row["show_labels"]),
                show_trails=bool(row["show_trails"]),
                show_zones=bool(row["show_zones"]),
                show_lines=bool(row["show_lines"]),
                lines=lines,
                zones=zones,
                alert_max_occupancy=row["alert_max_occupancy"],
                alert_enabled=bool(row["alert_enabled"])
            )

    def save_camera(self, camera: CameraConfig):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            lines_json = json.dumps([line.model_dump() for line in camera.lines])
            zones_json = json.dumps([zone.model_dump() for zone in camera.zones])
            cursor.execute("""
                INSERT INTO cameras (
                    id, name, source_type, source_url, enabled, target_fps,
                    confidence_threshold, iou_threshold, model_name,
                    show_boxes, show_labels, show_trails, show_zones, show_lines,
                    lines_json, zones_json, alert_max_occupancy, alert_enabled,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    source_type=excluded.source_type,
                    source_url=excluded.source_url,
                    enabled=excluded.enabled,
                    target_fps=excluded.target_fps,
                    confidence_threshold=excluded.confidence_threshold,
                    iou_threshold=excluded.iou_threshold,
                    model_name=excluded.model_name,
                    show_boxes=excluded.show_boxes,
                    show_labels=excluded.show_labels,
                    show_trails=excluded.show_trails,
                    show_zones=excluded.show_zones,
                    show_lines=excluded.show_lines,
                    lines_json=excluded.lines_json,
                    zones_json=excluded.zones_json,
                    alert_max_occupancy=excluded.alert_max_occupancy,
                    alert_enabled=excluded.alert_enabled,
                    updated_at=CURRENT_TIMESTAMP
            """, (
                camera.id, camera.name, camera.source_type, camera.source_url,
                int(camera.enabled), camera.target_fps,
                camera.confidence_threshold, camera.iou_threshold, camera.model_name,
                int(camera.show_boxes), int(camera.show_labels), int(camera.show_trails),
                int(camera.show_zones), int(camera.show_lines),
                lines_json, zones_json, camera.alert_max_occupancy, int(camera.alert_enabled)
            ))
            conn.commit()

    def delete_camera(self, camera_id: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
            conn.commit()

    def log_counts(self, camera_id: str, current_occ: int, total_in: int, total_out: int,
                   peak_occ: int, zone_counts: Dict[str, int], line_counts: Dict[str, Dict[str, int]]):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO count_logs (
                        camera_id, current_occupancy, total_in, total_out, peak_occupancy,
                        zone_counts_json, line_counts_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    camera_id, current_occ, total_in, total_out, peak_occ,
                    json.dumps(zone_counts), json.dumps(line_counts)
                ))
                
                # Update hourly roll-up
                now_str = datetime.now().strftime("%Y-%m-%d %H:00:00")
                cursor.execute("""
                    INSERT INTO hourly_stats (camera_id, hour_bucket, peak_occupancy, avg_occupancy, sample_count)
                    VALUES (?, ?, ?, ?, 1)
                    ON CONFLICT(camera_id, hour_bucket) DO UPDATE SET
                        peak_occupancy = MAX(hourly_stats.peak_occupancy, excluded.peak_occupancy),
                        avg_occupancy = (hourly_stats.avg_occupancy * hourly_stats.sample_count + excluded.avg_occupancy) / (hourly_stats.sample_count + 1),
                        sample_count = hourly_stats.sample_count + 1
                """, (camera_id, now_str, current_occ, float(current_occ)))

                conn.commit()
        except Exception as e:
            logger.error(f"Error logging counts: {e}")

    def log_alert(self, camera_id: str, alert_type: str, message: str, occupancy: int, threshold: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO alerts (camera_id, alert_type, message, occupancy_level, threshold)
                VALUES (?, ?, ?, ?, ?)
            """, (camera_id, alert_type, message, occupancy, threshold))
            conn.commit()

    def get_recent_logs(self, camera_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp, current_occupancy, total_in, total_out, peak_occupancy,
                       zone_counts_json, line_counts_json
                FROM count_logs
                WHERE camera_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (camera_id, limit))
            rows = cursor.fetchall()
            return [dict(r) for r in reversed(rows)]

    def get_hourly_stats(self, camera_id: str, hours: int = 24) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            since_time = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:00:00")
            cursor.execute("""
                SELECT hour_bucket, entered_count, exited_count, peak_occupancy, ROUND(avg_occupancy, 1) as avg_occupancy
                FROM hourly_stats
                WHERE camera_id = ? AND hour_bucket >= ?
                ORDER BY hour_bucket ASC
            """, (camera_id, since_time))
            return [dict(r) for r in cursor.fetchall()]

    def get_alerts(self, camera_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if camera_id:
                cursor.execute("""
                    SELECT a.*, c.name as camera_name
                    FROM alerts a
                    LEFT JOIN cameras c ON a.camera_id = c.id
                    WHERE a.camera_id = ?
                    ORDER BY a.timestamp DESC
                    LIMIT ?
                """, (camera_id, limit))
            else:
                cursor.execute("""
                    SELECT a.*, c.name as camera_name
                    FROM alerts a
                    LEFT JOIN cameras c ON a.camera_id = c.id
                    ORDER BY a.timestamp DESC
                    LIMIT ?
                """, (limit,))
            return [dict(r) for r in cursor.fetchall()]


# Global Database Instance
db = Database()
