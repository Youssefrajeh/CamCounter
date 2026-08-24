import logging
import numpy as np
from typing import Dict, List, Tuple, Optional
import torch

logger = logging.getLogger("camcounter.detector")


class PersonDetector:
    def __init__(self, model_name: str = "yolov8n.pt", conf_threshold: float = 0.40, iou_threshold: float = 0.45):
        self.model_name = model_name
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.model = None
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._load_model()

    def _load_model(self):
        try:
            logger.info(f"Loading YOLO model '{self.model_name}' on device '{self.device}'...")
            from ultralytics import YOLO
            self.model = YOLO(self.model_name)
            # Warmup inference
            dummy = np.zeros((320, 320, 3), dtype=np.uint8)
            self.model(dummy, verbose=False)
            logger.info(f"YOLO model '{self.model_name}' loaded successfully on {self.device}.")
        except Exception as e:
            logger.error(f"Failed to load YOLO model: {e}")
            self.model = None

    def detect_and_track(self, frame: np.ndarray, persist: bool = True) -> List[Tuple[int, Tuple[float, float, float, float], float]]:
        """
        Runs person detection and tracking on an RGB/BGR image frame.
        Returns a list of tuples: (track_id, (norm_x1, norm_y1, norm_x2, norm_y2), confidence)
        """
        if self.model is None:
            self._load_model()
            if self.model is None:
                return []

        h, w = frame.shape[:2]
        if h == 0 or w == 0:
            return []

        try:
            # Run tracking with class 0 (person only)
            results = self.model.track(
                source=frame,
                persist=persist,
                classes=[0],  # Person class only in COCO dataset
                conf=self.conf_threshold,
                iou=self.iou_threshold,
                tracker="bytetrack.yaml",
                verbose=False,
                device=self.device
            )

            detections = []
            if results and len(results) > 0:
                res = results[0]
                if res.boxes is not None and len(res.boxes) > 0:
                    boxes = res.boxes.xyxy.cpu().numpy()
                    confs = res.boxes.conf.cpu().numpy() if res.boxes.conf is not None else [1.0] * len(boxes)
                    
                    # Track IDs (might be None if tracker hasn't assigned yet)
                    if res.boxes.id is not None:
                        ids = res.boxes.id.int().cpu().numpy()
                    else:
                        ids = list(range(len(boxes)))

                    for i, box in enumerate(boxes):
                        track_id = int(ids[i])
                        conf = float(confs[i])
                        # Normalize coordinates to 0.0 - 1.0 range
                        nx1 = max(0.0, min(1.0, float(box[0]) / w))
                        ny1 = max(0.0, min(1.0, float(box[1]) / h))
                        nx2 = max(0.0, min(1.0, float(box[2]) / w))
                        ny2 = max(0.0, min(1.0, float(box[3]) / h))

                        detections.append((track_id, (nx1, ny1, nx2, ny2), conf))

            return detections
        except Exception as e:
            logger.error(f"Error during detection/tracking: {e}")
            return []
