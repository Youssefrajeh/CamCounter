import logging
import queue
import threading
import time
from typing import Any, Dict, Optional, Set, Tuple

import numpy as np

logger = logging.getLogger("camcounter.face_analyzer")

# Module-level cache so the "is deepface installed" check only ever runs once.
_DEEPFACE_AVAILABLE: Optional[bool] = None


def _try_import_deepface() -> bool:
    """Lazily probe for the optional 'deepface' dependency (see requirements-face.txt).
    Kept out of the default requirements.txt because it pulls in TensorFlow, which
    is far too heavy for the 512MB Render deployment target (see render.yaml)."""
    global _DEEPFACE_AVAILABLE
    if _DEEPFACE_AVAILABLE is None:
        try:
            from deepface import DeepFace  # noqa: F401
            _DEEPFACE_AVAILABLE = True
            logger.info("DeepFace is available; face attribute analysis can be enabled.")
        except Exception as e:
            _DEEPFACE_AVAILABLE = False
            logger.warning(
                f"DeepFace not installed ({e}). Face attribute analysis will stay disabled. "
                f"Install with: pip install -r requirements-face.txt"
            )
    return _DEEPFACE_AVAILABLE


class FaceAttributeAnalyzer:
    """
    Estimates age, gender and dominant emotion for tracked persons using DeepFace.

    DeepFace inference (100-500ms+ on CPU) is far too slow to run inline in the
    per-frame detection loop, so this runs as a background worker thread with a
    bounded queue: the capture pipeline submits a person crop (non-blocking, and
    only once every `refresh_interval` seconds per track), and the render loop
    just reads whatever result is cached for that track_id. Nothing here ever
    blocks frame capture or annotation.
    """

    # Shared across every camera's analyzer instance: DeepFace's underlying
    # TF/Keras models are cached globally by DeepFace itself, so all analyzers
    # reuse the same weights in memory, but concurrent predict() calls from
    # multiple threads are not guaranteed safe -- serialize them.
    _shared_model_lock = threading.Lock()

    def __init__(self, refresh_interval: float = 4.0, min_crop_size: int = 40):
        self.refresh_interval = refresh_interval
        self.min_crop_size = min_crop_size

        self._results: Dict[int, Dict[str, Any]] = {}
        self._results_lock = threading.Lock()

        self._last_submit: Dict[int, float] = {}

        self._queue: "queue.Queue[Tuple[int, np.ndarray]]" = queue.Queue(maxsize=8)
        self._worker_thread: Optional[threading.Thread] = None
        self._running = False
        self._enabled = False  # flips to True only if start() succeeds in importing deepface

    @property
    def available(self) -> bool:
        return self._enabled

    def start(self):
        """Import DeepFace (if needed) and spin up the background worker. No-op if
        already running or if deepface isn't installed."""
        if self._running:
            return
        if not _try_import_deepface():
            return
        self._enabled = True
        self._running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        logger.info("Face attribute analyzer worker started.")

    def stop(self):
        self._running = False
        with self._results_lock:
            self._results.clear()
        self._last_submit.clear()

    def maybe_submit(self, track_id: int, frame: np.ndarray, bbox_norm: Tuple[float, float, float, float]):
        """Non-blocking: submit this track's crop for analysis only if it's due
        for a refresh. Silently drops the frame if the worker queue is backed up."""
        if not self._enabled or not self._running:
            return

        now = time.time()
        if now - self._last_submit.get(track_id, 0.0) < self.refresh_interval:
            return

        h, w = frame.shape[:2]
        x1 = max(0, int(bbox_norm[0] * w))
        y1 = max(0, int(bbox_norm[1] * h))
        x2 = min(w, int(bbox_norm[2] * w))
        y2 = min(h, int(bbox_norm[3] * h))
        if (x2 - x1) < self.min_crop_size or (y2 - y1) < self.min_crop_size:
            return  # too small/far away to get a usable face read

        crop = frame[y1:y2, x1:x2].copy()
        if crop.size == 0:
            return

        self._last_submit[track_id] = now
        try:
            self._queue.put_nowait((track_id, crop))
        except queue.Full:
            pass  # worker is behind; skip this cycle rather than block capture

    def get(self, track_id: int) -> Optional[Dict[str, Any]]:
        with self._results_lock:
            return self._results.get(track_id)

    def prune(self, active_track_ids: Set[int]):
        """Drop cached results/submit timestamps for tracks that are no longer active."""
        with self._results_lock:
            for tid in list(self._results.keys()):
                if tid not in active_track_ids:
                    del self._results[tid]
        for tid in list(self._last_submit.keys()):
            if tid not in active_track_ids:
                del self._last_submit[tid]

    def _worker_loop(self):
        from deepface import DeepFace

        while self._running:
            try:
                track_id, crop = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                with FaceAttributeAnalyzer._shared_model_lock:
                    analysis = DeepFace.analyze(
                        crop,
                        actions=("age", "gender", "emotion"),
                        enforce_detection=False,  # crop is already a person box; don't discard on a soft face miss
                        # "skip" = use the whole crop as-is, no re-detection inside it.
                        # The "opencv" backend needs a Haar cascade XML that only ships
                        # with full opencv-python, not the opencv-python-headless this
                        # project uses -- "skip" also avoids that dependency entirely,
                        # and is a good fit since we already crop to YOLO's person box.
                        detector_backend="skip",
                        silent=True,
                    )
                if isinstance(analysis, list):
                    analysis = analysis[0] if analysis else None

                if analysis:
                    gender_scores = analysis.get("gender", {})
                    if isinstance(gender_scores, dict) and gender_scores:
                        dom_gender = max(gender_scores, key=gender_scores.get)
                        gender_conf = gender_scores[dom_gender]
                    else:
                        dom_gender = str(analysis.get("dominant_gender", "Unknown"))
                        gender_conf = 0.0

                    emotion_scores = analysis.get("emotion", {}) or {}
                    dom_emotion = analysis.get("dominant_emotion", "unknown")

                    result = {
                        "age": int(analysis.get("age", 0)),
                        "gender": dom_gender,
                        "gender_confidence": round(float(gender_conf), 1),
                        "dominant_emotion": dom_emotion,
                        "emotion_confidence": round(float(emotion_scores.get(dom_emotion, 0.0)), 1),
                        "updated_at": time.time(),
                    }
                    with self._results_lock:
                        self._results[track_id] = result
            except Exception as e:
                logger.debug(f"Face analysis failed for track {track_id}: {e}")
            finally:
                self._queue.task_done()
