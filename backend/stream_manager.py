import logging
import asyncio
from typing import Dict, List, Optional, Any
from backend.config import CameraConfig, TripwireLine, OccupancyZone, Point
from backend.database import db
from backend.camera_stream import CameraStream

logger = logging.getLogger("camcounter.manager")


class StreamManager:
    def __init__(self):
        self.streams: Dict[str, CameraStream] = {}
        self.active_camera_id: Optional[str] = None
        self._init_cameras()

    def _init_cameras(self):
        saved_cameras = db.get_all_cameras()
        if not saved_cameras:
            # Default 1: Laptop / Integrated Webcam
            webcam_cam = CameraConfig(
                id="laptop_webcam_0",
                name="Laptop / USB Webcam (Dev 0)",
                source_type="webcam",
                source_url="0",
                enabled=True,
                target_fps=25,
                lines=[
                    TripwireLine(
                        id="line_webcam_cross",
                        name="Room Entrance",
                        p1=Point(x=0.15, y=0.5),
                        p2=Point(x=0.85, y=0.5),
                        in_label="IN",
                        out_label="OUT",
                        color="#10B981"
                    )
                ],
                zones=[
                    OccupancyZone(
                        id="zone_room",
                        name="Desk / Room Zone",
                        points=[
                            Point(x=0.1, y=0.2),
                            Point(x=0.9, y=0.2),
                            Point(x=0.9, y=0.9),
                            Point(x=0.1, y=0.9)
                        ],
                        max_capacity=5,
                        color="#3B82F6"
                    )
                ],
                alert_max_occupancy=5,
                alert_enabled=True
            )

            # Default 2: Simulated Demo Stream
            demo_cam = CameraConfig(
                id="demo_cam_1",
                name="Surveillance Corridor (Demo)",
                source_type="synthetic",
                source_url="demo",
                enabled=True,
                target_fps=25,
                lines=[
                    TripwireLine(
                        id="line_entrance",
                        name="Main Gate",
                        p1=Point(x=0.25, y=0.35),
                        p2=Point(x=0.25, y=0.85),
                        in_label="IN",
                        out_label="OUT",
                        color="#10B981"
                    ),
                    TripwireLine(
                        id="line_exit",
                        name="Exit Turnstile",
                        p1=Point(x=0.75, y=0.35),
                        p2=Point(x=0.75, y=0.85),
                        in_label="IN",
                        out_label="OUT",
                        color="#F59E0B"
                    )
                ],
                zones=[
                    OccupancyZone(
                        id="zone_lobby",
                        name="Lobby Lounge",
                        points=[
                            Point(x=0.30, y=0.40),
                            Point(x=0.70, y=0.40),
                            Point(x=0.70, y=0.85),
                            Point(x=0.30, y=0.85)
                        ],
                        max_capacity=5,
                        color="#3B82F6"
                    )
                ],
                alert_max_occupancy=6,
                alert_enabled=True
            )
            db.save_camera(webcam_cam)
            db.save_camera(demo_cam)
            saved_cameras = [webcam_cam, demo_cam]

        for cam_cfg in saved_cameras:
            if cam_cfg.enabled:
                self.start_stream(cam_cfg)

        if saved_cameras:
            self.active_camera_id = saved_cameras[0].id

    def start_stream(self, config: CameraConfig):
        if config.id in self.streams:
            self.streams[config.id].stop()
        stream = CameraStream(config)
        stream.start()
        self.streams[config.id] = stream
        logger.info(f"Stream started for camera: {config.name}")

    def stop_stream(self, camera_id: str):
        if camera_id in self.streams:
            self.streams[camera_id].stop()
            del self.streams[camera_id]

    def add_or_update_camera(self, config: CameraConfig) -> CameraConfig:
        db.save_camera(config)
        if config.enabled:
            if config.id in self.streams:
                self.streams[config.id].update_config(config)
            else:
                self.start_stream(config)
        else:
            self.stop_stream(config.id)
        return config

    def delete_camera(self, camera_id: str):
        self.stop_stream(camera_id)
        db.delete_camera(camera_id)
        if self.active_camera_id == camera_id:
            remaining = list(self.streams.keys())
            self.active_camera_id = remaining[0] if remaining else None

    def get_stream(self, camera_id: str) -> Optional[CameraStream]:
        return self.streams.get(camera_id)

    def get_all_analytics(self) -> Dict[str, Any]:
        result = {}
        for cam_id, stream in self.streams.items():
            result[cam_id] = stream.get_analytics()
        return result

    def shutdown(self):
        for stream in self.streams.values():
            stream.stop()
        self.streams.clear()


# Global Manager Instance
stream_manager = StreamManager()
