"""Hardware-wrappers voor Picamera2 + OpenCV (Fase 9).

Deze module isoleert álle imports van ``picamera2``, ``cv2`` en ``numpy``
in fabrieksfuncties, zodat de rest van :mod:`cansat_hw.camera` importeerbaar
blijft op een Mac/dev-machine zonder die packages. De radio-service vangt
:class:`~cansat_hw.camera.detector.CameraUnavailable` op en blijft gewoon
doorlopen zonder camera.
"""

from __future__ import annotations

from typing import Any, Tuple

from .detector import CameraUnavailable


def make_picamera2_capture_fn(
	*,
	resolution: Tuple[int, int] = (1600, 1300),
) -> Tuple[Any, Any]:
	"""Bouw een (picamera2-instance, capture_fn) voor de CameraThread.

	Default resolutie is **1600×1300** — het native actieve array van de
	OV2311-sensor in de Arducam B0381 PiVariety NoIR (zie
	``config/camera/tag_registry.json::sensor``). Voor een Pi HQ-camera
	(IMX477, 4056×3040) moet de caller ``resolution=(4056, 3040)``
	meegeven; libcamera clipt/schaalt de stream anders stil naar de
	actieve sensor-array.

	De caller is verantwoordelijk voor het sluiten van de picamera2-instance
	(``picam2.stop()``). ``capture_fn`` is een callable met signatuur
	``() -> (frame_ndarray, (w, h))``.
	"""

	try:
		from picamera2 import Picamera2  # type: ignore
	except ImportError as e:
		raise CameraUnavailable(
			"picamera2 niet beschikbaar (sudo apt install python3-picamera2)"
		) from e

	picam2 = Picamera2()
	cfg = picam2.create_still_configuration(
		main={"size": tuple(resolution), "format": "RGB888"}
	)
	picam2.configure(cfg)
	picam2.start()

	w, h = int(resolution[0]), int(resolution[1])

	def capture_fn() -> Tuple[Any, Tuple[int, int]]:
		arr = picam2.capture_array("main")
		return arr, (w, h)

	return picam2, capture_fn


def make_opencv_preprocess_fn():
	"""Bouw een ``(frame, target_w) -> (grey, scale)`` op basis van OpenCV.

	``scale`` = ``target_w / orig_w``, geclamped op 1.0 (geen upscaling —
	dat zou alleen ruis toevoegen).
	"""

	try:
		import cv2  # type: ignore
	except ImportError as e:
		raise CameraUnavailable(
			"opencv-python-headless niet beschikbaar (pip install ...[camera])"
		) from e

	def preprocess(frame: Any, target_w: int) -> Tuple[Any, float]:
		h, w = frame.shape[:2]
		if frame.ndim == 3:
			grey = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
		else:
			grey = frame
		if int(target_w) > 0 and int(target_w) < int(w):
			scale = float(target_w) / float(w)
			new_h = max(1, int(round(h * scale)))
			grey = cv2.resize(grey, (int(target_w), new_h), interpolation=cv2.INTER_AREA)
		else:
			scale = 1.0
		return grey, float(scale)

	return preprocess


__all__ = [
	"make_picamera2_capture_fn",
	"make_opencv_preprocess_fn",
]
