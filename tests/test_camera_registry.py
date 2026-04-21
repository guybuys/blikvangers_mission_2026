"""Unit tests voor :mod:`cansat_hw.camera.registry` (Fase 9)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cansat_hw.camera.registry import (
	DEFAULT_FOCAL_LENGTH_MM,
	DEFAULT_PIXEL_PITCH_UM,
	DEFAULT_TAG_SIZE_MM,
	TagRegistry,
	load_tag_registry,
)


class TestTagRegistry(unittest.TestCase):
	def test_defaults_when_no_path(self) -> None:
		reg = load_tag_registry(None)
		self.assertEqual(reg.focal_length_mm, DEFAULT_FOCAL_LENGTH_MM)
		self.assertEqual(reg.pixel_pitch_um, DEFAULT_PIXEL_PITCH_UM)
		self.assertEqual(reg.default_size_mm, DEFAULT_TAG_SIZE_MM)
		self.assertEqual(reg.tags, {})

	def test_defaults_when_path_missing(self) -> None:
		reg = load_tag_registry(Path("/nonexistent/tag_registry.json"))
		self.assertEqual(reg.focal_length_mm, DEFAULT_FOCAL_LENGTH_MM)

	def test_focal_length_px_from_pitch_imx477(self) -> None:
		"""Sanity: pinhole-formule werkt ook voor andere sensors (IMX477 = 1.55 µm)."""
		reg = TagRegistry(focal_length_mm=25.0, pixel_pitch_um=1.55)
		# 25 mm * 1000 / 1.55 µm ≈ 16129 px
		self.assertAlmostEqual(reg.focal_length_px, 25000.0 / 1.55, places=2)

	def test_focal_length_px_from_pitch_ov2311(self) -> None:
		"""Missie-config 2026: OV2311 (Arducam B0381 PiVariety) met 3.0 µm pitch."""
		reg = TagRegistry(focal_length_mm=25.0, pixel_pitch_um=3.0)
		# 25 mm * 1000 / 3.0 µm ≈ 8333 px
		self.assertAlmostEqual(reg.focal_length_px, 25000.0 / 3.0, places=2)

	def test_focal_length_px_zero_pitch(self) -> None:
		reg = TagRegistry(focal_length_mm=25.0, pixel_pitch_um=0.0)
		self.assertEqual(reg.focal_length_px, 0.0)

	def test_size_mm_lookup(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			p = Path(td) / "reg.json"
			p.write_text(
				json.dumps(
					{
						"lens": {"focal_length_mm": 25.0},
						"sensor": {"pixel_pitch_um": 1.55, "full_res_px": [4056, 3040]},
						"tags": {
							"26": {"size_mm": 4500, "label": "grote tag"},
							"1": {"size_mm": 1500},
						},
						"default_size_mm": 175,
					}
				),
				encoding="utf-8",
			)
			reg = load_tag_registry(p)
		self.assertEqual(reg.size_mm_for(26), 4500)
		self.assertEqual(reg.size_mm_for(1), 1500)
		self.assertEqual(reg.size_mm_for(42), 175)  # default
		self.assertEqual(reg.label_for(26), "grote tag")
		self.assertEqual(reg.label_for(1), "")
		self.assertEqual(reg.label_for(99), "")

	def test_size_mm_clamped_u16(self) -> None:
		reg = TagRegistry(tags={}, default_size_mm=100_000)
		self.assertEqual(reg.size_mm_for(0), 0xFFFF)

	def test_corrupt_file_falls_back_to_defaults(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			p = Path(td) / "reg.json"
			p.write_text("not json ☹", encoding="utf-8")
			reg = load_tag_registry(p)
		self.assertEqual(reg.focal_length_mm, DEFAULT_FOCAL_LENGTH_MM)
		self.assertEqual(reg.default_size_mm, DEFAULT_TAG_SIZE_MM)

	def test_partial_missing_fields(self) -> None:
		"""Missende lens-velden vallen naar default; tags blijven geldig."""
		with tempfile.TemporaryDirectory() as td:
			p = Path(td) / "reg.json"
			p.write_text(
				json.dumps({"tags": {"7": {"size_mm": 999}}}),
				encoding="utf-8",
			)
			reg = load_tag_registry(p)
		self.assertEqual(reg.focal_length_mm, DEFAULT_FOCAL_LENGTH_MM)
		self.assertEqual(reg.size_mm_for(7), 999)

	def test_bundled_registry_loads(self) -> None:
		"""De in-repo ``config/camera/tag_registry.json`` moet parsebaar zijn."""

		repo_root = Path(__file__).resolve().parents[1]
		bundled = repo_root / "config" / "camera" / "tag_registry.json"
		self.assertTrue(bundled.is_file(), f"missing bundled registry: {bundled}")
		reg = load_tag_registry(bundled)
		self.assertEqual(reg.size_mm_for(26), 4500)
		# Kleine missie-tags zijn opgemeten op 1.1 m (= 1100 mm).
		# IDs 85, 235, 317, 536 (gemeten in het veld — niet de vroegere
		# schatting 1/2/3/4 die toevallig dezelfde afmeting had).
		for tid in (85, 235, 317, 536):
			self.assertEqual(reg.size_mm_for(tid), 1100, f"tag {tid}")
		self.assertEqual(reg.size_mm_for(99), reg.default_size_mm)
		# Sensor: OV2311 (Arducam B0381 PiVariety) — 3.0 µm pitch, 1600x1300 active.
		self.assertEqual(reg.pixel_pitch_um, 3.0)
		self.assertEqual(reg.full_res_px, (1600, 1300))


if __name__ == "__main__":
	unittest.main()
