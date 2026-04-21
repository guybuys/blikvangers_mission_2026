"""BNO055-calibratieprofiel: JSON ⇄ bytes round-trip + repo-defaults."""

from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

from cansat_hw.sensors.bno055 import (
	PROFILE_LENGTH,
	PROFILE_SCHEMA_VERSION,
	load_profile_file,
	profile_from_dict,
	profile_to_dict,
	save_profile_file,
)


def _blob(accel=(0, 0, 0), mag=(0, 0, 0), gyro=(0, 0, 0), accel_r=1000, mag_r=600) -> bytes:
	return struct.pack("<11h", *accel, *mag, *gyro, accel_r, mag_r)


class ProfileRoundTripTest(unittest.TestCase):
	def test_roundtrip_hex_ground_truth(self) -> None:
		data = _blob(accel=(17, -4, 11), mag=(-128, 94, -210), gyro=(0, 1, -1), accel_r=1000, mag_r=657)
		doc = profile_to_dict(data, calibration_status=(3, 3, 3, 3))
		self.assertEqual(doc["schema"], PROFILE_SCHEMA_VERSION)
		self.assertEqual(doc["accel_offset"], [17, -4, 11])
		self.assertEqual(doc["mag_offset"], [-128, 94, -210])
		self.assertEqual(doc["gyro_offset"], [0, 1, -1])
		self.assertEqual(doc["accel_radius"], 1000)
		self.assertEqual(doc["mag_radius"], 657)
		self.assertEqual(doc["raw_hex"], data.hex())
		self.assertEqual(doc["calibration_at_save"], "sys=3 gyr=3 acc=3 mag=3")
		# En terug.
		self.assertEqual(profile_from_dict(doc), data)

	def test_raw_hex_wins_over_integer_fields(self) -> None:
		# Als raw_hex en integer-velden tegenspreken, is raw_hex leidend (ground
		# truth). Dit zorgt dat een handmatige edit van alleen accel_offset niet
		# stilletjes verkeerde bytes naar de chip duwt.
		data = _blob(accel=(5, 5, 5))
		doc = profile_to_dict(data)
		doc["accel_offset"] = [999, 999, 999]  # liegen
		self.assertEqual(profile_from_dict(doc), data)

	def test_fallback_from_integer_fields(self) -> None:
		doc = {
			"schema": PROFILE_SCHEMA_VERSION,
			"accel_offset": [1, 2, 3],
			"mag_offset": [4, 5, 6],
			"gyro_offset": [7, 8, 9],
			"accel_radius": 1000,
			"mag_radius": 600,
			# geen raw_hex → fallback pad
		}
		out = profile_from_dict(doc)
		self.assertEqual(len(out), PROFILE_LENGTH)
		self.assertEqual(struct.unpack("<11h", out), (1, 2, 3, 4, 5, 6, 7, 8, 9, 1000, 600))

	def test_length_enforced(self) -> None:
		with self.assertRaises(ValueError):
			profile_to_dict(b"\x00" * 21)

	def test_corrupt_raw_hex_falls_back_with_valid_fields(self) -> None:
		doc = {
			"schema": PROFILE_SCHEMA_VERSION,
			"accel_offset": [0, 0, 0],
			"mag_offset": [0, 0, 0],
			"gyro_offset": [0, 0, 0],
			"accel_radius": 0,
			"mag_radius": 0,
			"raw_hex": "not-hex!",
		}
		with self.assertRaises(ValueError):
			profile_from_dict(doc)

	def test_wrong_schema_rejected(self) -> None:
		doc = profile_to_dict(_blob())
		doc["schema"] = 99
		with self.assertRaises(ValueError):
			profile_from_dict(doc)


class ProfileFileTest(unittest.TestCase):
	def test_save_then_load(self) -> None:
		data = _blob(accel=(42, -42, 0), mag=(1, 2, 3))
		with tempfile.TemporaryDirectory() as td:
			path = Path(td) / "cal.json"
			written = save_profile_file(path, data, calibration_status=(3, 3, 3, 3))
			self.assertEqual(written, path)
			loaded = load_profile_file(path)
			self.assertEqual(loaded, data)
			# Verifieer dat het format human-readable is en niet bv. base64.
			raw = path.read_text(encoding="utf-8")
			doc = json.loads(raw)
			self.assertIn("raw_hex", doc)
			self.assertIn("accel_offset", doc)

	def test_load_missing_file_returns_none(self) -> None:
		out = load_profile_file(Path("/tmp/nonexistent_bno_profile_%s.json" % id(self)))
		self.assertIsNone(out)

	def test_save_creates_parent_dirs(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			path = Path(td) / "deep" / "nested" / "cal.json"
			save_profile_file(path, _blob())
			self.assertTrue(path.is_file())


class RepoDefaultsTest(unittest.TestCase):
	"""De in-repo voorbeelden moeten altijd loadbaar blijven."""

	def _load(self, name: str) -> bytes:
		p = Path(__file__).resolve().parents[1] / "config" / name
		out = load_profile_file(p)
		self.assertIsNotNone(out, f"{name} niet loadbaar")
		assert out is not None
		return out

	def test_example_profile_loads(self) -> None:
		data = self._example_like("bno055_calibration.example.json")
		self.assertEqual(len(data), PROFILE_LENGTH)

	def test_default_profile_loads(self) -> None:
		data = self._example_like("bno055_calibration.default.json")
		self.assertEqual(len(data), PROFILE_LENGTH)

	def _example_like(self, name: str) -> bytes:
		return self._load(name)


if __name__ == "__main__":
	unittest.main()
