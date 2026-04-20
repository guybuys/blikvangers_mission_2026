"""CameraServices — synchrone capture+detect+save voor radio-commandos.

De :class:`~cansat_hw.camera.thread.CameraThread` draait alleen in
``DEPLOYED`` en schrijft zijn detecties in de :class:`TagBuffer`. Voor de
**CONFIG**-modus tests (``!shoot`` / ``!detect`` over de radio) hebben we
een eenmalige, synchrone variant nodig:

* één frame capturen (via dezelfde ``capture_fn`` als de thread),
* optioneel als JPEG naar ``photo_dir`` schrijven,
* AprilTags detecteren + in meters omzetten (via dezelfde registry +
  pinhole-math als de thread),
* een beknopt resultaat teruggeven dat de wire-protocol-handler in
  60 bytes kan proppen.

Het ontwerp deelt bewust **dezelfde** ``capture_fn``/``preprocess_fn``/
``detector``/``registry`` als de thread, zodat beide code-paden exact
dezelfde afstandsmath gebruiken (één bug-bron, één testfixture).
``CameraServices`` is dus **niet** thread-safe samen met een actieve
:class:`CameraThread`: de aanroeper (wire-protocol) eist daarom CONFIG-mode.
"""

from __future__ import annotations

import time as time_mod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from .detector import AprilTagDetector, CameraUnavailable, DetectorMetrics, compute_metrics
from .registry import TagRegistry
from .thread import DEFAULT_DETECT_WIDTH, CaptureFn, PreprocessFn

# Callable ``(frame, path) -> None`` die één frame als JPEG naar disk
# schrijft. Productie injecteert :func:`make_opencv_jpeg_save_fn` (cv2);
# tests kunnen een stub gebruiken die een bytes-buffer bijhoudt.
SaveFn = Callable[[Any, Path], None]


@dataclass
class ShootResult:
	"""Resultaat van één synchrone capture+detect (+ optionele save)."""

	path: Optional[Path]
	detections: List[DetectorMetrics]
	image_wh: Tuple[int, int]


@dataclass
class CameraServices:
	"""Lichtgewicht container voor synchrone CONFIG-mode camera-acties.

	Parameters
	----------
	capture_fn
		Dezelfde ``() -> (frame, (w, h))`` callable als de
		:class:`CameraThread`. Productie: wrapper rond Picamera2.
	preprocess_fn
		``(frame, target_w) -> (grey, scale)`` — productie: OpenCV.
	detector
		AprilTag-detector; dummy in tests.
	registry
		:class:`TagRegistry` voor focal length + tag-sizes.
	photo_dir
		Doelmap voor saves. Wordt bij :meth:`shoot_and_detect` opgelegd
		zodat de wire-handler niet telkens een pad hoeft mee te geven.
	detect_width
		Detectie-breedte (px). Default ~1014 (4× downscale van full-res);
		zelfde semantiek als de thread.
	save_fn
		Callable ``(frame, path) -> None`` die het frame als JPEG schrijft.
		``None`` (default) gebruikt :func:`make_opencv_jpeg_save_fn` lazy;
		tests kunnen een stub injecteren.
	filename_prefix
		Prefix voor opgeslagen JPEGs. Default ``cam_`` — past in 60 B reply
		(``cam_HHMMSSZ.jpg`` = 15 chars).
	"""

	capture_fn: CaptureFn
	preprocess_fn: PreprocessFn
	detector: AprilTagDetector
	registry: TagRegistry
	photo_dir: Path
	detect_width: int = DEFAULT_DETECT_WIDTH
	save_fn: Optional[SaveFn] = None
	filename_prefix: str = "cam_"

	# Diagnose-counters voor ``GET CAMSTATS``.
	_shoots: int = field(default=0, init=False)
	_detects: int = field(default=0, init=False)
	_errors: int = field(default=0, init=False)
	_last_error: Optional[str] = field(default=None, init=False)

	def detect_once(self) -> Tuple[List[DetectorMetrics], Tuple[int, int]]:
		"""Capture + detect zonder save. Publiek zodat tests puur de
		detectie-weg kunnen valideren.

		Raises
		------
		RuntimeError
			Bij capture-, preprocess- of detectie-fouten. Aanroeper (wire-
			protocol) vangt dit en vertaalt naar ``ERR CAM …``.
		"""

		self._detects += 1
		try:
			frame, (image_w, image_h) = self.capture_fn()
			grey, scale = self.preprocess_fn(frame, int(self.detect_width))
			dets = self.detector.detect(grey)
		except Exception as e:  # noqa: BLE001 — propagate as RuntimeError
			self._errors += 1
			self._last_error = str(e)[:80]
			raise RuntimeError(str(e)) from e
		metrics: List[DetectorMetrics] = []
		for tag_id, corners in dets:
			m = compute_metrics(
				tag_id=tag_id,
				corners_px=corners,
				image_w=int(image_w),
				image_h=int(image_h),
				registry=self.registry,
				detection_scale=float(scale),
			)
			if m is not None:
				metrics.append(m)
		return metrics, (int(image_w), int(image_h))

	def shoot_and_detect(self, *, save: bool) -> ShootResult:
		"""Capture + detect, met optionele JPEG-save.

		Bij ``save=True`` wordt het **originele** full-res frame (niet de
		grey/downscaled versie) als JPEG geschreven naar ``photo_dir`` met
		een compacte filename ``cam_<HHMMSSZ>.jpg``. Bij botsende namen
		(twee shots binnen dezelfde seconde) komt er ``_N`` achter.

		Raises
		------
		RuntimeError
			Bij capture/detect/save-fouten. Aanroeper vangt + vertaalt.
		"""

		self._shoots += 1
		try:
			frame, (image_w, image_h) = self.capture_fn()
			grey, scale = self.preprocess_fn(frame, int(self.detect_width))
			dets = self.detector.detect(grey)
		except Exception as e:  # noqa: BLE001
			self._errors += 1
			self._last_error = str(e)[:80]
			raise RuntimeError(str(e)) from e

		metrics: List[DetectorMetrics] = []
		for tag_id, corners in dets:
			m = compute_metrics(
				tag_id=tag_id,
				corners_px=corners,
				image_w=int(image_w),
				image_h=int(image_h),
				registry=self.registry,
				detection_scale=float(scale),
			)
			if m is not None:
				metrics.append(m)

		out_path: Optional[Path] = None
		if save:
			out_path = self._reserve_filename()
			save_fn = self.save_fn if self.save_fn is not None else _lazy_opencv_save
			try:
				self.photo_dir.mkdir(parents=True, exist_ok=True)
				save_fn(frame, out_path)
			except Exception as e:  # noqa: BLE001
				self._errors += 1
				self._last_error = ("SAVE " + str(e))[:80]
				raise RuntimeError(str(e)) from e

		return ShootResult(
			path=out_path,
			detections=metrics,
			image_wh=(int(image_w), int(image_h)),
		)

	# -- diagnose ------------------------------------------------------------
	def stats(self) -> dict:
		"""Diagnose-snapshot voor ``GET CAMSTATS``."""

		return {
			"shoots": int(self._shoots),
			"detects": int(self._detects),
			"errors": int(self._errors),
			"last_error": self._last_error,
		}

	# -- intern --------------------------------------------------------------
	def _reserve_filename(self) -> Path:
		"""``cam_HHMMSSZ.jpg`` met ``_N`` suffix bij collision.

		Gebruikt UTC-tijd: bij fetch via rsync blijft de volgorde globaal
		herkenbaar (zelfde conventie als de mission .bin-files).
		"""

		t = time_mod.gmtime()
		base = "%s%02d%02d%02dZ" % (
			self.filename_prefix,
			t.tm_hour,
			t.tm_min,
			t.tm_sec,
		)
		candidate = self.photo_dir / (base + ".jpg")
		n = 1
		while candidate.exists():
			candidate = self.photo_dir / ("%s_%d.jpg" % (base, n))
			n += 1
			if n > 99:
				# Extremely unlikely — fall back on monotonic ns suffix.
				candidate = self.photo_dir / (
					"%s_%d.jpg" % (base, time_mod.monotonic_ns() & 0xFFFF)
				)
				break
		return candidate


def make_opencv_jpeg_save_fn(*, quality: int = 90) -> SaveFn:
	"""Productie-``save_fn`` die via OpenCV een JPEG wegschrijft.

	Lazy-import van ``cv2`` zodat dev-machines zonder het ``[camera]``
	extra de rest van :mod:`cansat_hw.camera` kunnen importeren. Gooit
	:class:`CameraUnavailable` als OpenCV ontbreekt.
	"""

	try:
		import cv2  # type: ignore
	except ImportError as e:
		raise CameraUnavailable(
			"opencv-python-headless niet beschikbaar (pip install ...[camera])"
		) from e
	q = int(max(10, min(100, quality)))

	def save(frame: Any, path: Path) -> None:
		# Picamera2 levert RGB; cv2.imwrite verwacht BGR → converteer zodat
		# de opgeslagen JPEG kleuren kloppen wanneer we later handmatig kijken.
		# (Voor de Arducam OV2311 Mono heeft dit geen effect — grey input.)
		if getattr(frame, "ndim", 0) == 3 and frame.shape[2] == 3:
			frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
		ok = cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), q])
		if not ok:
			raise RuntimeError("cv2.imwrite returned False for %s" % path)

	return save


def _lazy_opencv_save(frame: Any, path: Path) -> None:
	"""Default ``save_fn`` — bouwt de opencv-saver de eerste keer dat hij
	wordt aangeroepen en hergebruikt 'm daarna.
	"""

	global _CACHED_OPENCV_SAVE
	if _CACHED_OPENCV_SAVE is None:
		_CACHED_OPENCV_SAVE = make_opencv_jpeg_save_fn()
	_CACHED_OPENCV_SAVE(frame, path)


_CACHED_OPENCV_SAVE: Optional[SaveFn] = None


__all__ = [
	"CameraServices",
	"SaveFn",
	"ShootResult",
	"make_opencv_jpeg_save_fn",
]
