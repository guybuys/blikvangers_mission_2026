"""Unit tests voor :mod:`scripts.camera.tag_metrics` (Fase 9 / offline tool).

Het bestand zit niet in een geïmporteerd package — we voegen
``scripts/camera`` ad-hoc aan ``sys.path`` toe, identiek aan wat
``descent_telemetry.py`` zelf doet.
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_CAMERA = _REPO_ROOT / "scripts" / "camera"
if str(_SCRIPTS_CAMERA) not in sys.path:
	sys.path.insert(0, str(_SCRIPTS_CAMERA))

from tag_metrics import (  # noqa: E402  (import na sys.path-mutatie)
	compute_calibration_k,
	compute_metrics_from_corners,
	compute_metrics_pinhole,
)


def _square_corners(cx: float, cy: float, side: float):
	"""Vier hoekpunten van een as-gebonden vierkant met centrum (cx, cy)."""

	h = side / 2.0
	return [
		[cx - h, cy - h],
		[cx + h, cy - h],
		[cx + h, cy + h],
		[cx - h, cy + h],
	]


class TestComputeCalibrationK(unittest.TestCase):
	def test_simple_mean(self) -> None:
		# k = mean(pixel_size * distance) — bij identieke producten is k = product.
		k = compute_calibration_k([(100.0, 2.0), (50.0, 4.0), (200.0, 1.0)])
		self.assertAlmostEqual(k, 200.0, places=6)

	def test_extra_columns_ignored(self) -> None:
		"""Derde kolom (bv. tag_size_m) wordt genegeerd."""
		k = compute_calibration_k([(100.0, 2.0, "ignored"), (200.0, 1.0, 999)])
		self.assertAlmostEqual(k, 200.0, places=6)

	def test_single_point(self) -> None:
		self.assertAlmostEqual(compute_calibration_k([(80.0, 2.0)]), 160.0, places=6)


class TestComputeMetricsFromCorners(unittest.TestCase):
	"""Legacy single-size modus: distance = k / max_side."""

	def test_centered_square_distance(self) -> None:
		# k = 200, max_side = 100 → distance = 2.0
		corners = _square_corners(800.0, 600.0, 100.0)
		out = compute_metrics_from_corners(
			tag_id=7,
			corners_px=corners,
			image_w=1600,
			image_h=1200,
			k=200.0,
			fx=1000.0,
			fy=1000.0,
		)
		self.assertEqual(out["tag_id"], 7)
		self.assertAlmostEqual(out["max_side_px"], 100.0, places=6)
		self.assertAlmostEqual(out["distance_m"], 2.0, places=6)
		# Tag staat in centrum → offset_m ≈ 0
		self.assertAlmostEqual(out["offset_m"]["x"], 0.0, places=6)
		self.assertAlmostEqual(out["offset_m"]["y"], 0.0, places=6)
		self.assertAlmostEqual(out["actual_distance_m"], 2.0, places=6)

	def test_zero_max_side_returns_zero_distance(self) -> None:
		degenerate = [[10.0, 10.0]] * 4
		out = compute_metrics_from_corners(
			tag_id=1,
			corners_px=degenerate,
			image_w=1600,
			image_h=1200,
			k=200.0,
		)
		self.assertEqual(out["distance_m"], 0.0)
		self.assertEqual(out["max_side_px"], 0.0)


class TestComputeMetricsPinhole(unittest.TestCase):
	"""Pinhole + per-tag-grootte modus: distance = f_px × size_m / max_side_px."""

	def test_centered_square_distance(self) -> None:
		# f_px = 8333, size = 1.1 m, max_side = 100 → distance = 8333*1.1/100 = 91.66
		corners = _square_corners(800.0, 650.0, 100.0)
		out = compute_metrics_pinhole(
			tag_id=2,
			corners_px=corners,
			image_w=1600,
			image_h=1300,
			focal_length_px=8333.0,
			tag_size_m=1.1,
		)
		expected = 8333.0 * 1.1 / 100.0
		self.assertAlmostEqual(out["distance_m"], expected, places=4)
		self.assertEqual(out["tag_id"], 2)
		self.assertAlmostEqual(out["max_side_px"], 100.0, places=6)

	def test_distance_scales_inverse_to_pixel_size(self) -> None:
		"""Twee keer kleiner in pixels = twee keer verder weg."""
		near = _square_corners(800.0, 650.0, 200.0)
		far = _square_corners(800.0, 650.0, 100.0)
		kw = dict(focal_length_px=8333.0, tag_size_m=1.1, image_w=1600, image_h=1300)
		d_near = compute_metrics_pinhole(tag_id=1, corners_px=near, **kw)["distance_m"]
		d_far = compute_metrics_pinhole(tag_id=1, corners_px=far, **kw)["distance_m"]
		self.assertAlmostEqual(d_far / d_near, 2.0, places=6)

	def test_lateral_offset(self) -> None:
		"""Tag verschoven in x → offset_m["x"] != 0 met juist teken/grootte.

		f_px = 1000, image 2000x1000 (cx=1000, cy=500). Tag-centrum 100 px
		rechts van cx → angle = atan(100/1000); offset_x_m = distance·tan(angle).
		Met distance = 10 m: offset_x_m = 10 · 100/1000 = 1.0 m.
		"""

		corners = _square_corners(1100.0, 500.0, 100.0)
		out = compute_metrics_pinhole(
			tag_id=42,
			corners_px=corners,
			image_w=2000,
			image_h=1000,
			focal_length_px=1000.0,
			tag_size_m=1.0,  # 1 m tag, 100 px → distance = 10 m
		)
		self.assertAlmostEqual(out["distance_m"], 10.0, places=6)
		self.assertAlmostEqual(out["offset_m"]["x"], 1.0, places=6)
		self.assertAlmostEqual(out["offset_m"]["y"], 0.0, places=6)
		# Pinhole: actual_distance = sqrt(d² - offset²) ≈ sqrt(100 - 1) = 9.9499
		self.assertAlmostEqual(out["actual_distance_m"], math.sqrt(99.0), places=4)

	def test_zero_max_side_returns_zero_distance(self) -> None:
		degenerate = [[10.0, 10.0]] * 4
		out = compute_metrics_pinhole(
			tag_id=1,
			corners_px=degenerate,
			image_w=1600,
			image_h=1300,
			focal_length_px=8333.0,
			tag_size_m=1.1,
		)
		self.assertEqual(out["distance_m"], 0.0)

	def test_zero_focal_length_returns_zero_distance(self) -> None:
		"""Defensief: invalid lens-config mag niet crashen."""
		corners = _square_corners(800.0, 650.0, 100.0)
		out = compute_metrics_pinhole(
			tag_id=1,
			corners_px=corners,
			image_w=1600,
			image_h=1300,
			focal_length_px=0.0,
			tag_size_m=1.1,
		)
		self.assertEqual(out["distance_m"], 0.0)

	def test_default_fx_fy_match_focal_length_px(self) -> None:
		"""Pinhole assumption: fx/fy default naar focal_length_px.

		De offset-formule is ``offset_m = distance · tan(atan2(off_px, fx))
		= distance · off_px / fx``. Met ``fx == focal_length_px`` en
		``distance = f_px · size / max_side`` valt ``f_px`` weg en blijft
		``offset_m = off_px · size / max_side`` over — dus invariant onder
		``focal_length_px``. Dat is precies de pinhole-eigenschap.
		"""

		corners = _square_corners(900.0, 500.0, 100.0)
		kw = dict(corners_px=corners, image_w=1600, image_h=1000, tag_size_m=1.0, tag_id=1)
		out_low = compute_metrics_pinhole(focal_length_px=500.0, **kw)
		out_high = compute_metrics_pinhole(focal_length_px=2000.0, **kw)
		# Distance schaalt lineair met focal_length_px (max_side, size constant).
		self.assertAlmostEqual(out_low["distance_m"], 5.0, places=6)
		self.assertAlmostEqual(out_high["distance_m"], 20.0, places=6)
		# Offset is invariant onder focal_length_px (zie docstring).
		# off_px=100, size_m=1.0, max_side=100 → offset_m = 100*1.0/100 = 1.0
		self.assertAlmostEqual(out_low["offset_m"]["x"], 1.0, places=6)
		self.assertAlmostEqual(out_high["offset_m"]["x"], 1.0, places=6)

	def test_explicit_fx_fy_override_default(self) -> None:
		"""Expliciete fx/fy moeten voorrang hebben op de focal_length_px-default."""

		corners = _square_corners(900.0, 500.0, 100.0)
		out = compute_metrics_pinhole(
			tag_id=1,
			corners_px=corners,
			image_w=1600,
			image_h=1000,
			focal_length_px=2000.0,
			tag_size_m=1.0,
			fx=500.0,
			fy=500.0,
		)
		# Distance gebruikt focal_length_px: 2000*1.0/100 = 20 m.
		self.assertAlmostEqual(out["distance_m"], 20.0, places=6)
		# Offset gebruikt fx=500 (override) ipv 2000:
		#   offset_m = distance · off_px / fx = 20 · 100 / 500 = 4.0 m
		# (vs 20 · 100 / 2000 = 1.0 m als de override niet werkt)
		self.assertAlmostEqual(out["offset_m"]["x"], 4.0, places=6)


class TestPinholeMatchesRegistryFormula(unittest.TestCase):
	"""compute_metrics_pinhole moet identieke distance geven als de
	radio-pijplijn (cansat_hw.camera.detector). Dat houdt offline analyse en
	live TLM consistent."""

	def test_matches_cansat_hw_detector(self) -> None:
		from cansat_hw.camera.detector import compute_metrics
		from cansat_hw.camera.registry import TagInfo, TagRegistry

		reg = TagRegistry(
			focal_length_mm=25.0,
			pixel_pitch_um=3.0,
			tags={1: TagInfo(tag_id=1, size_mm=1100)},
		)
		f_px = reg.focal_length_px

		corners = _square_corners(800.0, 650.0, 200.0)
		out_offline = compute_metrics_pinhole(
			tag_id=1,
			corners_px=corners,
			image_w=1600,
			image_h=1300,
			focal_length_px=f_px,
			tag_size_m=reg.size_mm_for(1) / 1000.0,
		)
		out_live = compute_metrics(
			tag_id=1,
			corners_px=corners,
			image_w=1600,
			image_h=1300,
			registry=reg,
		)
		self.assertIsNotNone(out_live)
		# Beide pijplijnen moeten dezelfde distance_m geven (zelfde formule).
		self.assertAlmostEqual(out_offline["distance_m"], out_live.distance_m, places=4)
		# En dezelfde max_side (geen detection-downscale in deze test).
		self.assertAlmostEqual(out_offline["max_side_px"], out_live.max_side_px, places=4)


if __name__ == "__main__":
	unittest.main()
