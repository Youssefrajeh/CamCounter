import logging
import gc
import os
import ctypes
import numpy as np
from typing import Dict, List, Tuple, Optional, Any
import torch

# Limit PyTorch CPU thread pool to save memory and avoid thread stack explosion
torch.set_num_threads(1)
if hasattr(torch, 'set_num_interop_threads'):
    try:
        torch.set_num_interop_threads(1)
    except Exception:
        pass

logger = logging.getLogger("camcounter.detector")

# Global singleton model cache so all cameras share ONE instance in RAM
_SHARED_MODELS: Dict[str, Any] = {}


def _trim_memory():
    """Trigger Python GC and glibc malloc_trim to release unused memory back to the OS."""
    gc.collect()
    try:
        if os.name == 'posix':
            libc = ctypes.CDLL('libc.so.6')
            libc.malloc_trim(0)
    except Exception:
        pass


class PersonDetector:
    def __init__(self, model_name: str = "yolov8n.pt", conf_threshold: float = 0.40, iou_threshold: float = 0.45):
        self.model_name = model_name
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._inference_count = 0

    @property
    def model(self):
        if self.model_name not in _SHARED_MODELS:
            self._load_shared_model()
        return _SHARED_MODELS.get(self.model_name)

    def _load_shared_model(self):
        """Loads and caches the single shared YOLO model instance."""
        global _SHARED_MODELS
        if self.model_name in _SHARED_MODELS:
            return
        try:
            logger.info(f"Loading shared YOLO model '{self.model_name}' on device '{self.device}'...")
            from ultralytics import YOLO
            loaded_model = YOLO(self.model_name)
            # Warmup with small 320x320 frame
            dummy = np.zeros((320, 320, 3), dtype=np.uint8)
            with torch.inference_mode():
                loaded_model(dummy, verbose=False, device=self.device, imgsz=320)
            _SHARED_MODELS[self.model_name] = loaded_model
            _trim_memory()
            logger.info(f"Shared YOLO model '{self.model_name}' loaded successfully into cache.")
        except Exception as e:
            logger.error(f"Failed to load YOLO model: {e}")

    def detect_and_track(self, frame: np.ndarray, persist: bool = True) -> List[Tuple[int, Tuple[float, float, float, float], float]]:
        """
        Runs person detection and tracking on an RGB/BGR image frame using low-memory inference (imgsz=320, inference_mode).
        Returns a list of tuples: (track_id, (norm_x1, norm_y1, norm_x2, norm_y2), confidence)
        """
        m = self.model
        if m is None:
            return []

        h, w = frame.shape[:2]
        if h == 0 or w == 0:
            return []

        try:
            with torch.inference_mode():
                results = m.track(
                    source=frame,
                    persist=persist,
                    classes=[0],  # Person class only
                    conf=self.conf_threshold,
                    iou=self.iou_threshold,
                    imgsz=320,    # 320x320 inference keeps memory <180MB
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
                    
                    if res.boxes.id is not None:
                        ids = res.boxes.id.int().cpu().numpy()
                    else:
                        ids = list(range(len(boxes)))

                    for i, box in enumerate(boxes):
                        track_id = int(ids[i])
                        conf = float(confs[i])
                        nx1 = max(0.0, min(1.0, float(box[0]) / w))
                        ny1 = max(0.0, min(1.0, float(box[1]) / h))
                        nx2 = max(0.0, min(1.0, float(box[2]) / w))
                        ny2 = max(0.0, min(1.0, float(box[3]) / h))

                        detections.append((track_id, (nx1, ny1, nx2, ny2), conf))

            # Periodic memory trimming every 100 frames to prevent RAM creep
            self._inference_count += 1
            if self._inference_count % 100 == 0:
                _trim_memory()

            return detections
        except Exception as e:
            logger.error(f"Error during detection/tracking: {e}")
            return []
