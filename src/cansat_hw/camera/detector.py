r"""AprilTag-detectie + distance-math voor Fase 9.

De detectie gebeurt op een **gedownscalede greyscale versie** van het frame
(snelheid), maar alle corner-coördinaten worden teruggeschaald naar full-res
pixels vóór de afstandsberekening. Zo gebruiken we consistent de full-res
``focal_length_px`` uit :class:`~cansat_hw.camera.registry.TagRegistry` en
blijven de resultaten stabiel onafhankelijk van de gekozen ``detect_width``.

Afstand wordt berekend via het klassieke pinhole-model::

	d_m = (f_px * s_m) / max_side_px

met ``f_px`` = focal length in full-res pixels, ``s_m`` = fysieke tag-grootte
(uit de registry), en ``max_side_px`` = grootste gedetecteerde zijde van de
tag in full-res pixels. Lateral offsets (dx_m, dy_m) volgen uit de
pixel-offset van het tag-center t.o.v. het beeldcentrum via kleine-hoek-
benadering (``dx_m = offset_px * d / f_px``).

Deze module **importeert niet hard** ``cv2`` / ``apriltag`` op module-niveau:
op een Mac of dev-machine zonder die pakketten moet de unit-test / import
blijven slagen. :func:`load_apriltag_detector` doet de import lazy en faalt
netjes met :class:`CameraUnavailable` wanneer het daadwerkelijk nodig is.
"""

from __future__ import annotations

import math
import time as time_mod
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from cansat_hw.telemetry.codec import TagDetection

from .buffer import BufferedDetection
from .registry import TagRegistry


class CameraUnavailable(RuntimeError):
	"""Opgeworpen als ``cv2``/``apriltag`` niet importeerbaar zijn.

	De aanroeper (radio-script of camera-thread) vangt dit en draait door
	zonder camera-functionaliteit — zoals we ook bij BNO055/BME280 doen.
	"""


@dataclass
class DetectorMetrics:
	"""Afgeleide geometrie per detectie — vóór clamping op int16-cm.

	Aparte laag tussen ruwe detectie en :class:`TagDetection` zodat de
	afstandsmath geïsoleerd testbaar is. Alle afstanden in meter, pixel-
	maten in (full-res) pixels.
	"""

	tag_id: int
	corners_px_full: List[Tuple[float, float]]
	center_px_full: Tuple[float, float]
	max_side_px: float
	distance_m: float
	dx_m: float
	dy_m: float
	size_mm: int


def _compute_max_side_px(corners: Sequence[Sequence[float]]) -> float:
	"""Langste zijde van de AprilTag-vierhoek, in pixels.

	We nemen expliciet ``max`` i.p.v. een gemiddelde: bij scheve incidentie
	is één zijde korter door perspectief; de langste zijde benadert de
	voor-projectie (orthogonale projectie van de tag op de sensor).
	"""

	pts = [(float(p[0]), float(p[1])) for p in corners]
	if len(pts) != 4:
		return 0.0
	sides = []
	for i in range(4):
		x0, y0 = pts[i]
		x1, y1 = pts[(i + 1) % 4]
		sides.append(math.hypot(x1 - x0, y1 - y0))
	return max(sides)


def compute_metrics(
	*,
	tag_id: int,
	corners_px: Sequence[Sequence[float]],
	image_w: int,
	image_h: int,
	registry: TagRegistry,
	detection_scale: float = 1.0,
) -> Optional[DetectorMetrics]:
	"""Bereken afstand + laterale offset uit vier tag-hoeken.

	Parameters
	----------
	tag_id
		Gedetecteerde AprilTag-ID.
	corners_px
		Vier (x, y)-hoeken **in detectie-ruimte** (dus gedownscaled indien
		``detection_scale != 1``). We schalen intern terug naar full-res.
	image_w, image_h
		Afmetingen van het **full-res** frame. Hieruit bepalen we het
		beeldcentrum (cx, cy) voor de laterale offset-berekening.
	registry
		:class:`TagRegistry` voor ``focal_length_px`` en tag-size.
	detection_scale
		Verhouding detectie-breedte / full-res-breedte (0..1]. Default 1.0
		betekent "geen downscale". ``inv_scale = 1 / detection_scale`` vermenigvuldigt
		alle pixel-coördinaten terug naar full-res.

	Returns
	-------
	DetectorMetrics of ``None`` als ``corners_px`` ongeldig is of ``max_side_px``
	op 0 valt (degenerate detectie).
	"""

	if len(corners_px) != 4 or image_w <= 0 or image_h <= 0:
		return None
	scale = float(detection_scale)
	if scale <= 0:
		return None
	inv = 1.0 / scale if scale != 1.0 else 1.0
	corners_full = [
		(float(p[0]) * inv, float(p[1]) * inv) for p in corners_px
	]
	max_side = _compute_max_side_px(corners_full)
	if max_side <= 0:
		return None

	cx = float(image_w) / 2.0
	cy = float(image_h) / 2.0
	tx = sum(p[0] for p in corners_full) / 4.0
	ty = sum(p[1] for p in corners_full) / 4.0

	size_mm = int(registry.size_mm_for(tag_id))
	size_m = size_mm / 1000.0

	f_px = float(registry.focal_length_px)
	# Pinhole distance: d = f_px * tag_size_m / max_side_px. Bij f_px <= 0
	# (gecorrumpeerde registry) retourneren we toch een metrics-struct
	# zodat de tag nog in het TLM komt met dz=0 — de aanroeper clipt later
	# sowieso naar i16 cm.
	if f_px <= 0 or max_side <= 0:
		distance_m = 0.0
	else:
		distance_m = f_px * size_m / max_side

	# Laterale offsets (meter) via kleine-hoek-benadering op basis van
	# angular offset: ``dx_m = distance_m * (offset_px / f_px)``. Dat is
	# ``distance_m * tan(angle)`` binnen de relevante hoeken (<~20°) tot op
	# het laatste procent nauwkeurig — en het scheelt een ``tan``-call per
	# detectie.
	if f_px > 0:
		dx_m = (tx - cx) * distance_m / f_px
		dy_m = (ty - cy) * distance_m / f_px
	else:
		dx_m = 0.0
		dy_m = 0.0

	return DetectorMetrics(
		tag_id=int(tag_id),
		corners_px_full=corners_full,
		center_px_full=(tx, ty),
		max_side_px=max_side,
		distance_m=float(distance_m),
		dx_m=float(dx_m),
		dy_m=float(dy_m),
		size_mm=size_mm,
	)


def metrics_to_buffered(
	metrics: DetectorMetrics,
	*,
	captured_at: Optional[float] = None,
) -> BufferedDetection:
	"""Zet :class:`DetectorMetrics` om naar een :class:`BufferedDetection`.

	De laterale offsets en afstand worden naar **cm** afgerond (met clamping
	op int16-bereik) zodat het resultaat direct in een TLM-frame past. De
	clamping verliest precisie op zeer grote afstanden (>327 m) — dat is een
	bekende limitatie van het 60-byte codec-formaat en wordt gedocumenteerd
	in ``docs/camera.md``.
	"""

	if captured_at is None:
		captured_at = time_mod.monotonic()
	dx_cm = _clamp_i16(int(round(metrics.dx_m * 100.0)))
	dy_cm = _clamp_i16(int(round(metrics.dy_m * 100.0)))
	dz_cm = _clamp_i16(int(round(metrics.distance_m * 100.0)))
	size_mm = max(0, min(0xFFFF, int(metrics.size_mm)))
	det = TagDetection(
		tag_id=int(metrics.tag_id) & 0xFF,
		dx_cm=dx_cm,
		dy_cm=dy_cm,
		dz_cm=dz_cm,
		size_mm=size_mm,
	)
	return BufferedDetection(
		detection=det,
		max_side_px=float(metrics.max_side_px),
		captured_at=float(captured_at),
	)


def _clamp_i16(v: int) -> int:
	if v < -0x7FFF:
		return -0x7FFF
	if v > 0x7FFF:
		return 0x7FFF
	return int(v)


# --- AprilTag detector wrapper ------------------------------------------------
class AprilTagDetector:
	"""Lazy-imported wrapper rond het ``apriltag``-package.

	We houden één ``Detector``-instance open — maken is duur. Input is een
	**2D greyscale numpy-array** (zoals ``cv2.imread(..., IMREAD_GRAYSCALE)``
	of een Picamera2 ``capture_array()`` die we in greyscale converteren);
	output is een lijst ``(tag_id, corners_px)`` in **detectie-ruimte**.
	"""

	def __init__(
		self,
		*,
		families: str = "tag36h11",
		quad_decimate: float = 2.0,
	) -> None:
		self.families = families
		self.quad_decimate = float(quad_decimate)
		self._detector: Any = None
		self._err: Optional[str] = None

	def _ensure(self) -> None:
		if self._detector is not None or self._err is not None:
			return
		try:
			import apriltag  # type: ignore
		except ImportError as e:
			self._err = str(e)
			raise CameraUnavailable(
				"apriltag package niet beschikbaar (pip install pupil-apriltag)"
			) from e
		opts = apriltag.DetectorOptions(
			families=self.families,
			quad_decimate=self.quad_decimate,
		)
		self._detector = apriltag.Detector(opts)

	def detect(self, grey_img: Any) -> List[Tuple[int, List[Tuple[float, float]]]]:
		"""Run AprilTag-detection op ``grey_img`` (2D uint8 ndarray)."""

		self._ensure()
		assert self._detector is not None
		dets = self._detector.detect(grey_img)
		out: List[Tuple[int, List[Tuple[float, float]]]] = []
		for d in dets:
			tag_id = int(getattr(d, "tag_id", -1))
			corners = getattr(d, "corners", None)
			if corners is None or tag_id < 0:
				continue
			try:
				c_list = [(float(c[0]), float(c[1])) for c in corners]
			except (TypeError, ValueError, IndexError):
				continue
			if len(c_list) != 4:
				continue
			out.append((tag_id, c_list))
		return out


def load_apriltag_detector(
	*,
	families: str = "tag36h11",
	quad_decimate: float = 2.0,
) -> AprilTagDetector:
	"""Fabrieksfunctie voor :class:`AprilTagDetector` (lazy import).

	Roept :meth:`AprilTagDetector._ensure` direct aan zodat een ontbrekende
	``apriltag``/``cv2``-stack vroeg faalt en de aanroeper de camera-thread
	niet start.
	"""

	det = AprilTagDetector(families=families, quad_decimate=quad_decimate)
	det._ensure()  # noqa: SLF001 — expliciet eager import
	return det


__all__ = [
	"AprilTagDetector",
	"CameraUnavailable",
	"DetectorMetrics",
	"compute_metrics",
	"load_apriltag_detector",
	"metrics_to_buffered",
]
