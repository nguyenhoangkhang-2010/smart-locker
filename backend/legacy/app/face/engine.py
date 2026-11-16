"""Face detection + embedding with OpenCV's YuNet and SFace models.

Both run on CPU through OpenCV, no extra framework. Models live in
backend/models/face/ (see README for where they come from).
"""
from pathlib import Path
from threading import Lock

import cv2
import numpy as np

MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models" / "face"
DETECTOR_FILE = MODEL_DIR / "face_detection_yunet_2023mar.onnx"
RECOGNIZER_FILE = MODEL_DIR / "face_recognition_sface_2021dec.onnx"

# OpenCV 5 prints a harmless "Targets are not supported" warning per model load
try:
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
except AttributeError:
    pass

MODEL_NAME = "SFace"
MAX_SIDE = 1280  # larger photos are shrunk before detection


class NoFace(Exception):
    """No face could be found in the image."""


class ModelsMissing(RuntimeError):
    pass


def decode(data: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("not a readable image")
    return image


def cosine(a, b) -> float:
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


class FaceEngine:
    def __init__(self):
        self._detector = None
        self._recognizer = None
        # the OpenCV objects keep per-call state, so one call at a time
        self._lock = Lock()

    def _load(self):
        if self._detector is not None:
            return
        for path in (DETECTOR_FILE, RECOGNIZER_FILE):
            if not path.exists():
                raise ModelsMissing(f"model file missing: {path}")
        self._detector = cv2.FaceDetectorYN.create(
            str(DETECTOR_FILE), "", (320, 320), 0.8, 0.3, 5000
        )
        self._recognizer = cv2.FaceRecognizerSF.create(str(RECOGNIZER_FILE), "")

    def embed(self, image: np.ndarray) -> tuple[np.ndarray, int]:
        """Embedding of the largest face, and how many faces were seen."""
        with self._lock:
            self._load()

            height, width = image.shape[:2]
            scale = min(1.0, MAX_SIDE / max(height, width))
            if scale < 1.0:
                image = cv2.resize(image, (int(width * scale), int(height * scale)))
                height, width = image.shape[:2]

            self._detector.setInputSize((width, height))
            _, faces = self._detector.detect(image)
            if faces is None or len(faces) == 0:
                raise NoFace()

            largest = max(faces, key=lambda f: f[2] * f[3])
            aligned = self._recognizer.alignCrop(image, largest)
            vector = self._recognizer.feature(aligned).flatten()
            return vector.astype(np.float64), len(faces)


engine = FaceEngine()


def best_match(embedding, gallery: dict[int, list]) -> tuple[int | None, float]:
    """1:N. gallery maps user id -> enrolled vectors. Returns (user, score)."""
    best_user, best_score = None, -1.0
    for user_id, vectors in gallery.items():
        for vector in vectors:
            score = cosine(embedding, vector)
            if score > best_score:
                best_user, best_score = user_id, score
    return best_user, best_score
