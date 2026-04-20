"""AprilTag-afstand / offset uit hoeken — twee onafhankelijke modi.

Deze module wordt gebruikt door :mod:`scripts.camera.descent_telemetry`
om uit detector-output (vier hoeken in pixels per tag) een fysieke
afstand en laterale offset te schatten. Twee modi staan ter beschikking:

**1. Legacy / single-size calibratie** (``compute_metrics_from_corners``)
   Vereist een vooraf gemeten ``k = pixel_breedte × afstand`` (zie
   :func:`compute_calibration_k`). Werkt alleen correct als alle tags
   die je in beeld krijgt **dezelfde fysieke grootte** hebben als de
   tag waarop ``k`` gecalibreerd is. Dit is de oorspronkelijke
   demo-aanpak uit ``zero_files/camera_project/``.

**2. Pinhole + registry** (``compute_metrics_pinhole``)
   Geeft de focal length expliciet in pixels mee (afgeleid uit
   lens + sensor: ``f_px = focal_mm * 1000 / pixel_pitch_um``) plus
   de fysieke tag-grootte per detectie (uit
   ``config/camera/tag_registry.json`` via
   :class:`cansat_hw.camera.registry.TagRegistry`). Dimensioneel
   correct voor missies met **meerdere tag-groottes** (bv. één 4,5 m
   tag + vier 1,1 m tags). Identiek aan wat de Zero-radio-pijplijn
   gebruikt in :mod:`cansat_hw.camera.detector`.

De twee modi zijn bewust gescheiden zodat oude calibraties (waarvoor
geen lens/sensor-specs bekend zijn) blijven werken.

Implementatie-noot: deze module gebruikt **alleen de stdlib** (geen
numpy). Per frame komen er typisch <5 tags binnen, dus scalair-rekenen
is meer dan snel genoeg en de tool draait ook op een dev-machine zonder
de ``[camera]`` pip-extra geïnstalleerd.
"""

from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple


# --- Calibratie -------------------------------------------------------------


def compute_calibration_k(calibration_data: Iterable[Sequence[float]]) -> float:
	"""Bereken ``k = mean(pixel_breedte × afstand)`` voor de legacy modus.

	``calibration_data`` is een iterable van tuples waarvan minstens de
	eerste twee elementen ``(pixel_breedte_px, afstand_m)`` zijn. Extra
	kolommen worden genegeerd. **Let op**: deze ``k`` veronderstelt één
	vaste tag-grootte; meng nooit metingen van verschillende tag-formaten.
	"""

	products: List[float] = []
	for d in calibration_data:
		products.append(float(d[0]) * float(d[1]))
	if not products:
		return 0.0
	return sum(products) / len(products)


# --- Geometrie helpers ------------------------------------------------------


def _to_corners(corners_px: Sequence[Sequence[float]]) -> List[Tuple[float, float]]:
	"""Normaliseer naar ``[(x, y), (x, y), (x, y), (x, y)]``."""

	out: List[Tuple[float, float]] = []
	for p in corners_px:
		out.append((float(p[0]), float(p[1])))
	if len(out) != 4:
		raise ValueError(f"corners_px must have 4 points, got {len(out)}")
	return out


def _max_side_px(corners: Sequence[Tuple[float, float]]) -> float:
	"""Langste van de vier zijden van een vierhoek."""

	def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
		dx = a[0] - b[0]
		dy = a[1] - b[1]
		return math.hypot(dx, dy)

	side1 = _dist(corners[1], corners[0])
	side2 = _dist(corners[2], corners[1])
	side3 = _dist(corners[3], corners[2])
	side4 = _dist(corners[0], corners[3])
	return max(side1, side2, side3, side4)


def _center(corners: Sequence[Tuple[float, float]]) -> Tuple[float, float]:
	cx = sum(p[0] for p in corners) / 4.0
	cy = sum(p[1] for p in corners) / 4.0
	return (cx, cy)


def calculate_center_distance(
	tag_center_px: Sequence[float],
	camera_center_px: Sequence[float],
	cx: float,
	cy: float,
	fx: float,
	fy: float,
	distance_m: float,
	max_side: float = None,
):
	"""Backward-compatible offset-berekening (zelfde return-tuple als de
	originele numpy-versie). ``cx``/``cy``/``max_side`` zijn ongebruikt
	maar blijven in de signature voor backward compat."""

	offset_x_px = float(tag_center_px[0]) - float(camera_center_px[0])
	offset_y_px = float(tag_center_px[1]) - float(camera_center_px[1])

	distance_px = math.hypot(offset_x_px, offset_y_px)

	angle_x = math.atan2(offset_x_px, float(fx))
	angle_y = math.atan2(offset_y_px, float(fy))

	offset_x_m = float(distance_m) * math.tan(angle_x)
	offset_y_m = float(distance_m) * math.tan(angle_y)

	in_plane_sq = offset_x_m * offset_x_m + offset_y_m * offset_y_m
	d_sq = float(distance_m) * float(distance_m)
	distance_3d_m = math.sqrt(d_sq - in_plane_sq) if d_sq >= in_plane_sq else 0.0

	return distance_px, distance_3d_m, offset_x_px, offset_y_px, offset_x_m, offset_y_m


def _build_metrics(
	*,
	tag_id: int,
	corners: Sequence[Tuple[float, float]],
	max_side: float,
	distance_m: float,
	image_w: int,
	image_h: int,
	fx: float,
	fy: float,
) -> dict:
	"""Gemeenschappelijk metrics-dict (offset + 3D-correctie) voor beide modi."""

	cx_actual = float(image_w) / 2.0
	cy_actual = float(image_h) / 2.0
	tag_center = _center(corners)

	dist_px, _dist_3d_m, off_x_px, off_y_px, off_x_m, off_y_m = calculate_center_distance(
		tag_center,
		(cx_actual, cy_actual),
		cx_actual,
		cy_actual,
		fx,
		fy,
		distance_m,
	)

	in_plane_sq = off_x_m * off_x_m + off_y_m * off_y_m
	d_sq = distance_m * distance_m
	actual_distance_m = math.sqrt(d_sq - in_plane_sq) if d_sq >= in_plane_sq else 0.0

	return {
		"tag_id": int(tag_id),
		"corners_px": [list(p) for p in corners],
		"center_px": list(tag_center),
		"max_side_px": float(max_side),
		"distance_m": float(distance_m),
		"actual_distance_m": float(actual_distance_m),
		"center_distance_px": float(dist_px),
		"offset_px": {"x": float(off_x_px), "y": float(off_y_px)},
		"offset_m": {"x": float(off_x_m), "y": float(off_y_m)},
		"image": {
			"w": int(image_w),
			"h": int(image_h),
			"cx": float(cx_actual),
			"cy": float(cy_actual),
		},
	}


# --- Public API -------------------------------------------------------------


def compute_metrics_from_corners(
	*,
	tag_id: int,
	corners_px: Sequence[Sequence[float]],
	image_w: int,
	image_h: int,
	k: float,
	fx: float = 1000.0,
	fy: float = 1000.0,
) -> dict:
	"""Legacy single-size calibratie: ``distance_m = k / max_side_px``.

	``k`` komt typisch uit :func:`compute_calibration_k`. Werkt alleen
	correct voor tags met dezelfde fysieke grootte als die in de
	calibratie. Voor missies met meerdere tag-groottes: gebruik
	:func:`compute_metrics_pinhole`.
	"""

	corners = _to_corners(corners_px)
	max_side = _max_side_px(corners)
	distance_m = float(k) / max_side if max_side > 0 else 0.0
	return _build_metrics(
		tag_id=tag_id,
		corners=corners,
		max_side=max_side,
		distance_m=distance_m,
		image_w=image_w,
		image_h=image_h,
		fx=float(fx),
		fy=float(fy),
	)


def compute_metrics_pinhole(
	*,
	tag_id: int,
	corners_px: Sequence[Sequence[float]],
	image_w: int,
	image_h: int,
	focal_length_px: float,
	tag_size_m: float,
	fx: float = None,
	fy: float = None,
) -> dict:
	"""Pinhole + per-tag-grootte: ``distance_m = f_px × tag_size_m / max_side_px``.

	``focal_length_px`` is de brandpuntafstand in **full-resolutie
	pixels** (afgeleid uit lens + sensor pitch via
	:attr:`cansat_hw.camera.registry.TagRegistry.focal_length_px`).
	``tag_size_m`` is de fysieke breedte van **deze specifieke tag** in
	meter — typisch via :meth:`TagRegistry.size_mm_for` (en daarna
	delen door 1000).

	``fx``/``fy`` zijn de horizontale/verticale focal-lengths voor de
	offset-berekening. Defaulten naar ``focal_length_px`` (pinhole
	assumption: vierkante pixels, geen lens-asymmetrie).
	"""

	corners = _to_corners(corners_px)
	max_side = _max_side_px(corners)
	f_px = float(focal_length_px)
	size_m = float(tag_size_m)
	distance_m = f_px * size_m / max_side if max_side > 0 and f_px > 0 else 0.0
	return _build_metrics(
		tag_id=tag_id,
		corners=corners,
		max_side=max_side,
		distance_m=distance_m,
		image_w=image_w,
		image_h=image_h,
		fx=float(fx) if fx is not None else f_px,
		fy=float(fy) if fy is not None else f_px,
	)
