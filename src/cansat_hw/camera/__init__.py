"""Camera-pijplijn voor de CanSat (Fase 9).

Overview
--------

* :mod:`~cansat_hw.camera.registry` — JSON-registry met fysieke tag-
  afmetingen en lens/sensor-parameters.
* :mod:`~cansat_hw.camera.buffer` — thread-safe ``TagBuffer`` (top-2 grootste
  detecties, met staleness-policy).
* :mod:`~cansat_hw.camera.detector` — AprilTag-wrapper + pinhole-distance-
  math (``d = f_px * size_m / max_side_px``).
* :mod:`~cansat_hw.camera.thread` — ``CameraThread`` die het Picamera2-
  capture-loop draait en detecties in de buffer schrijft. Start/stop
  volgens flight-state (alleen actief in ``DEPLOYED``).
* :mod:`~cansat_hw.camera.hardware` — fabrieksfuncties voor Picamera2 en
  OpenCV; raken ``picamera2``/``cv2`` pas aan bij effectief aanroepen.

De radio-service importeert lazy — zonder camera-hardware blijft de rest
werken.
"""

from .buffer import BufferedDetection, TagBuffer
from .detector import (
	AprilTagDetector,
	CameraUnavailable,
	DetectorMetrics,
	compute_metrics,
	load_apriltag_detector,
	metrics_to_buffered,
)
from .registry import (
	DEFAULT_FOCAL_LENGTH_MM,
	DEFAULT_PIXEL_PITCH_UM,
	DEFAULT_TAG_SIZE_MM,
	TagInfo,
	TagRegistry,
	load_tag_registry,
)
from .thread import (
	DEFAULT_DETECT_WIDTH,
	DEFAULT_TARGET_FPS,
	CameraThread,
)

__all__ = [
	"AprilTagDetector",
	"BufferedDetection",
	"CameraThread",
	"CameraUnavailable",
	"DEFAULT_DETECT_WIDTH",
	"DEFAULT_FOCAL_LENGTH_MM",
	"DEFAULT_PIXEL_PITCH_UM",
	"DEFAULT_TAG_SIZE_MM",
	"DEFAULT_TARGET_FPS",
	"DetectorMetrics",
	"TagBuffer",
	"TagInfo",
	"TagRegistry",
	"compute_metrics",
	"load_apriltag_detector",
	"load_tag_registry",
	"metrics_to_buffered",
]
