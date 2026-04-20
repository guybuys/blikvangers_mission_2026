"""Tag-registry + lens-parameters voor de AprilTag-pipeline (Fase 9).

Een JSON-bestand (default ``config/camera/tag_registry.json``) bevat:

* ``lens.focal_length_mm`` — effectieve brandpuntafstand van de telelens.
* ``sensor.pixel_pitch_um`` — pitch per sensor-pixel. Voor de IMX477 (Pi HQ
  camera) is dat 1.55 µm. Samen met de brandpuntafstand rekenen we hieruit
  ``focal_length_px`` op **volle resolutie** uit — onafhankelijk van hoe
  sterk we de frames voor de detector downscalen.
* ``sensor.full_res_px`` — [W, H] van de native sensor, ter info en voor
  sanity-checks tegen ``Picamera2.create_still_configuration``.
* ``tags`` — dict ``"<id>" -> {"size_mm": int, "label": str?}``. IDs worden
  als string bewaard omdat JSON-keys strings moeten zijn; bij lookup
  accepteren we zowel ``"26"`` als ``26``.
* ``default_size_mm`` — fallback voor onbekende IDs (bv. 175 mm voor de
  papierprints die leerlingen gebruiken).

De klasse :class:`TagRegistry` is **read-only** na load en bewust minimaal:
één fysische afmeting + een focal-length — genoeg om op de Zero 2 W de
afstand per detectie te berekenen zonder een volledige pinhole-calibratie.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple


# Default-waardes als het JSON-bestand ontbreekt of een veld mist. De main
# loop kan dan nog steeds functioneren; de gebruiker ziet alleen een WARN.
DEFAULT_FOCAL_LENGTH_MM = 25.0
DEFAULT_PIXEL_PITCH_UM = 1.55
DEFAULT_FULL_RES_PX: Tuple[int, int] = (4056, 3040)
DEFAULT_TAG_SIZE_MM = 175  # 17.5 cm papierprints uit de voorbereidende tests


@dataclass(frozen=True)
class TagInfo:
	"""Één tag-definitie uit de registry."""

	tag_id: int
	size_mm: int
	label: str = ""


@dataclass
class TagRegistry:
	"""In-memory view van ``config/camera/tag_registry.json``.

	Attributes
	----------
	focal_length_mm
		Brandpuntafstand van de lens (mm).
	pixel_pitch_um
		Sensor-pixel pitch (µm). ``focal_length_px = focal_length_mm * 1000 /
		pixel_pitch_um`` op volle resolutie.
	full_res_px
		(W, H) van de native sensor-afmeting, ter info.
	tags
		Dict ``tag_id -> TagInfo``.
	default_size_mm
		Fallback-afmeting voor niet-geregistreerde IDs.
	"""

	focal_length_mm: float = DEFAULT_FOCAL_LENGTH_MM
	pixel_pitch_um: float = DEFAULT_PIXEL_PITCH_UM
	full_res_px: Tuple[int, int] = DEFAULT_FULL_RES_PX
	tags: Dict[int, TagInfo] = field(default_factory=dict)
	default_size_mm: int = DEFAULT_TAG_SIZE_MM

	# -- Berekende properties -------------------------------------------------
	@property
	def focal_length_px(self) -> float:
		"""Brandpuntafstand uitgedrukt in **full-res sensor-pixels**.

		Afleiding: 1 mm = 1000 µm, en een pixel beslaat ``pixel_pitch_um`` µm.
		Dus ``f_px = f_mm * 1000 / pixel_pitch_um``. Voor 25 mm / 1.55 µm =
		~16129 px.
		"""

		pp = float(self.pixel_pitch_um)
		if pp <= 0:
			return 0.0
		return float(self.focal_length_mm) * 1000.0 / pp

	# -- Lookup ---------------------------------------------------------------
	def size_mm_for(self, tag_id: int) -> int:
		"""Fysieke afmeting voor ``tag_id``; ``default_size_mm`` als fallback.

		``size_mm`` wordt geclipped op ``0..65535`` zodat het direct in het
		``TagDetection.size_mm``-veld (u16) past.
		"""

		info = self.tags.get(int(tag_id))
		size = int(info.size_mm) if info is not None else int(self.default_size_mm)
		if size < 0:
			size = 0
		if size > 0xFFFF:
			size = 0xFFFF
		return size

	def label_for(self, tag_id: int) -> str:
		info = self.tags.get(int(tag_id))
		return info.label if info else ""


# --- Loader -----------------------------------------------------------------
def load_tag_registry(path: Optional[Path] = None) -> TagRegistry:
	"""Laad de registry uit een JSON-file; val terug op defaults bij fouten.

	Missende velden worden vervangen door de default; een onleesbaar bestand
	of parse-fout retourneert een volledig default-object. Nooit exceptions
	— de camera-pijplijn mag bij een lege/stuk registry nog starten (met
	waarschuwing op de aanroeper z'n kant).
	"""

	if path is None:
		return TagRegistry()

	p = Path(path)
	if not p.is_file():
		return TagRegistry()

	try:
		raw = json.loads(p.read_text(encoding="utf-8"))
	except (OSError, ValueError):
		return TagRegistry()

	return _from_mapping(raw if isinstance(raw, Mapping) else {})


def _from_mapping(data: Mapping) -> TagRegistry:
	lens = data.get("lens") if isinstance(data.get("lens"), Mapping) else {}
	sensor = data.get("sensor") if isinstance(data.get("sensor"), Mapping) else {}
	tags_raw = data.get("tags") if isinstance(data.get("tags"), Mapping) else {}

	focal = _coerce_float(lens.get("focal_length_mm"), DEFAULT_FOCAL_LENGTH_MM)
	pitch = _coerce_float(sensor.get("pixel_pitch_um"), DEFAULT_PIXEL_PITCH_UM)
	full_res = _coerce_xy(sensor.get("full_res_px"), DEFAULT_FULL_RES_PX)
	default_size = _coerce_int(data.get("default_size_mm"), DEFAULT_TAG_SIZE_MM)

	tags: Dict[int, TagInfo] = {}
	for key, entry in tags_raw.items():
		try:
			tag_id = int(key)
		except (TypeError, ValueError):
			continue
		if not isinstance(entry, Mapping):
			continue
		size = _coerce_int(entry.get("size_mm"), default_size)
		if size <= 0:
			continue
		label = str(entry.get("label") or "")
		tags[tag_id] = TagInfo(tag_id=tag_id, size_mm=size, label=label)

	return TagRegistry(
		focal_length_mm=focal,
		pixel_pitch_um=pitch,
		full_res_px=full_res,
		tags=tags,
		default_size_mm=int(default_size),
	)


def _coerce_float(v: object, default: float) -> float:
	try:
		x = float(v)  # type: ignore[arg-type]
	except (TypeError, ValueError):
		return float(default)
	if not (x == x) or x <= 0:  # NaN or non-positive
		return float(default)
	return x


def _coerce_int(v: object, default: int) -> int:
	try:
		return int(v)  # type: ignore[arg-type]
	except (TypeError, ValueError):
		return int(default)


def _coerce_xy(v: object, default: Tuple[int, int]) -> Tuple[int, int]:
	if not isinstance(v, (list, tuple)) or len(v) != 2:
		return tuple(default)  # type: ignore[return-value]
	try:
		w = int(v[0])
		h = int(v[1])
	except (TypeError, ValueError):
		return tuple(default)  # type: ignore[return-value]
	if w <= 0 or h <= 0:
		return tuple(default)  # type: ignore[return-value]
	return (w, h)


__all__ = [
	"DEFAULT_FOCAL_LENGTH_MM",
	"DEFAULT_PIXEL_PITCH_UM",
	"DEFAULT_FULL_RES_PX",
	"DEFAULT_TAG_SIZE_MM",
	"TagInfo",
	"TagRegistry",
	"load_tag_registry",
]
