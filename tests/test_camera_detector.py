"""Unit tests voor :mod:`cansat_hw.camera.detector` (Fase 9).

De AprilTag-imports zijn niet beschikbaar op een Mac/dev-machine; we testen
daarom de **pure-Python** afstandsmath + clamping en laten de
``apriltag``-wrapper zelf ongetest tot we op de Zero zitten.
"""

from __future__ import annotations

import unittest

from cansat_hw.camera.detector import (
	compute_metrics,
	metrics_to_buffered,
)
from cansat_hw.camera.registry import TagRegistry


def _square_corners(cx: float, cy: float, side: float):
	"""Vier hoekpunten van een as-gebonden vierkant met centrum (cx, cy)."""

	h = side / 2.0
	return [
		(cx - h, cy - h),
		(cx + h, cy - h),
		(cx + h, cy + h),
		(cx - h, cy + h),
	]


class TestComputeMetrics(unittest.TestCase):
	def setUp(self) -> None:
		# Kunstmatig: f_px = 1000 (uit f_mm=1 + pitch=1µm -> 1000 px).
		# Met een tag van 1.0 m en 100 px op beeld: distance = 1000*1.0/100 = 10 m.
		self.reg = TagRegistry(
			focal_length_mm=1.0,
			pixel_pitch_um=1.0,
			tags={},
			default_size_mm=1000,
		)

	def test_distance_formula_tag_centered(self) -> None:
		corners = _square_corners(2028.0, 1520.0, 100.0)
		m = compute_metrics(
			tag_id=7,
			corners_px=corners,
			image_w=4056,
			image_h=3040,
			registry=self.reg,
		)
		assert m is not None
		self.assertAlmostEqual(m.max_side_px, 100.0, places=2)
		self.assertAlmostEqual(m.distance_m, 10.0, places=2)
		self.assertAlmostEqual(m.dx_m, 0.0, places=2)
		self.assertAlmostEqual(m.dy_m, 0.0, places=2)
		self.assertEqual(m.size_mm, 1000)

	def test_distance_scales_with_detection_scale(self) -> None:
		"""Detectie op halve breedte: corners halveren, math schaalt terug."""

		corners = _square_corners(1014.0, 760.0, 50.0)
		m = compute_metrics(
			tag_id=7,
			corners_px=corners,
			image_w=4056,
			image_h=3040,
			registry=self.reg,
			detection_scale=0.5,
		)
		assert m is not None
		self.assertAlmostEqual(m.max_side_px, 100.0, places=2)
		self.assertAlmostEqual(m.distance_m, 10.0, places=2)

	def test_lateral_offset_matches_pinhole(self) -> None:
		corners = _square_corners(3028.0, 1520.0, 100.0)  # 1000 px rechts van center
		m = compute_metrics(
			tag_id=7,
			corners_px=corners,
			image_w=4056,
			image_h=3040,
			registry=self.reg,
		)
		assert m is not None
		# dx_m = (offset_px / f_px) * distance = (1000/1000) * 10 = 10 m
		self.assertAlmostEqual(m.dx_m, 10.0, places=2)
		self.assertAlmostEqual(m.dy_m, 0.0, places=2)

	def test_bad_inputs_return_none(self) -> None:
		self.assertIsNone(
			compute_metrics(
				tag_id=1,
				corners_px=[(0, 0), (1, 1)],  # wrong length
				image_w=100,
				image_h=100,
				registry=self.reg,
			)
		)
		self.assertIsNone(
			compute_metrics(
				tag_id=1,
				corners_px=_square_corners(50.0, 50.0, 10.0),
				image_w=0,  # invalid
				image_h=100,
				registry=self.reg,
			)
		)

	def test_degenerate_corners_return_none(self) -> None:
		self.assertIsNone(
			compute_metrics(
				tag_id=1,
				corners_px=[(10, 10), (10, 10), (10, 10), (10, 10)],
				image_w=100,
				image_h=100,
				registry=self.reg,
			)
		)


class TestMetricsToBuffered(unittest.TestCase):
	def setUp(self) -> None:
		self.reg = TagRegistry(
			focal_length_mm=1.0,
			pixel_pitch_um=1.0,
			tags={},
			default_size_mm=1000,
		)

	def test_buffered_roundtrip(self) -> None:
		corners = _square_corners(2028.0, 1520.0, 100.0)
		m = compute_metrics(
			tag_id=42,
			corners_px=corners,
			image_w=4056,
			image_h=3040,
			registry=self.reg,
		)
		assert m is not None
		buf = metrics_to_buffered(m, captured_at=500.0)
		self.assertEqual(buf.detection.tag_id, 42)
		self.assertEqual(buf.detection.dx_cm, 0)
		self.assertEqual(buf.detection.dy_cm, 0)
		self.assertEqual(buf.detection.dz_cm, 1000)  # 10 m = 1000 cm
		self.assertEqual(buf.detection.size_mm, 1000)
		self.assertEqual(buf.captured_at, 500.0)
		self.assertAlmostEqual(buf.max_side_px, 100.0, places=2)

	def test_clamping_on_large_distance(self) -> None:
		# Zeer kleine max_side_px => zeer grote distance_m => clamp bij +32767.
		corners = _square_corners(2028.0, 1520.0, 1.0)  # 1 px breed
		m = compute_metrics(
			tag_id=1,
			corners_px=corners,
			image_w=4056,
			image_h=3040,
			registry=self.reg,
		)
		assert m is not None
		buf = metrics_to_buffered(m, captured_at=0.0)
		self.assertEqual(buf.detection.dz_cm, 0x7FFF)  # clamped

	def test_tag_id_masked_to_u8(self) -> None:
		corners = _square_corners(2028.0, 1520.0, 100.0)
		m = compute_metrics(
			tag_id=0x1234,
			corners_px=corners,
			image_w=4056,
			image_h=3040,
			registry=self.reg,
		)
		assert m is not None
		buf = metrics_to_buffered(m)
		self.assertEqual(buf.detection.tag_id, 0x34)


if __name__ == "__main__":
	unittest.main()
