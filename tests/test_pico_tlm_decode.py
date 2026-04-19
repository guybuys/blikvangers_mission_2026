"""Cross-side compat: Zero pakt -> Pico-decoder ontleedt zonder verlies.

Importeert de **Pico**-decoder (``pico_files/.../tlm_decode.py``) rechtstreeks
en feeds er bytes in die door de Zero-codec gegenereerd zijn. Zo merken we
direct als de FRAME_FORMAT-strings tussen beide kanten uit elkaar gaan lopen.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_PICO_DECODER_PATH = (
	Path(__file__).resolve().parent.parent
	/ "pico_files"
	/ "Orginele cansat"
	/ "RadioReceiver"
	/ "tlm_decode.py"
)


def _import_pico_decoder():
	spec = importlib.util.spec_from_file_location(
		"pico_tlm_decode", str(_PICO_DECODER_PATH)
	)
	if spec is None or spec.loader is None:
		raise RuntimeError("kan tlm_decode.py niet laden: %s" % _PICO_DECODER_PATH)
	mod = importlib.util.module_from_spec(spec)
	sys.modules["pico_tlm_decode"] = mod
	spec.loader.exec_module(mod)
	return mod


class CrossSideRoundTripTest(unittest.TestCase):
	def test_frame_size_and_format_match(self) -> None:
		from cansat_hw.telemetry.codec import FRAME_FORMAT, FRAME_SIZE

		pico = _import_pico_decoder()
		self.assertEqual(pico.FRAME_FORMAT, FRAME_FORMAT)
		self.assertEqual(pico.FRAME_SIZE, FRAME_SIZE)
		self.assertEqual(pico.FRAME_SIZE, 60)

	def test_pico_decodes_zero_packed_frame(self) -> None:
		from cansat_hw.telemetry.codec import (
			MODE_TEST,
			STATE_DEPLOYED,
			TagDetection,
			pack_tlm,
		)

		pico = _import_pico_decoder()
		raw = pack_tlm(
			mode=MODE_TEST,
			state=STATE_DEPLOYED,
			seq=42,
			utc_seconds=1_700_000_000,
			utc_ms=500,
			alt_m=12.34,
			pressure_hpa=1019.32,
			temp_c=21.5,
			heading_deg=180.0,
			roll_deg=-30.0,
			pitch_deg=15.0,
			ax_g=0.5,
			ay_g=-0.5,
			az_g=1.0,
			sys_cal=3,
			gyro_cal=2,
			accel_cal=1,
			mag_cal=0,
			tags=[TagDetection(tag_id=7, dx_cm=10, dy_cm=20, dz_cm=30, size_mm=100)],
		)
		d = pico.decode_tlm(raw)
		self.assertEqual(d["kind"], "TLM")
		self.assertEqual(d["mode"], "TEST")
		self.assertEqual(d["state"], "DEPLOYED")
		self.assertEqual(d["seq"], 42)
		self.assertEqual(d["utc_seconds"], 1_700_000_000)
		self.assertEqual(d["utc_ms"], 500)
		self.assertAlmostEqual(d["alt_m"], 12.34, places=2)
		self.assertAlmostEqual(d["pressure_hpa"], 1019.3, places=1)
		self.assertAlmostEqual(d["temp_c"], 21.5, places=1)
		self.assertAlmostEqual(d["heading_deg"], 180.0, places=1)
		self.assertAlmostEqual(d["az_g"], 1.0, places=2)
		self.assertEqual(d["bno_sys_cal"], 3)
		self.assertEqual(d["bno_mag_cal"], 0)
		self.assertEqual(d["tag_count"], 1)
		self.assertEqual(d["tags"][0]["id"], 7)
		self.assertEqual(d["tags"][0]["dz_cm"], 30)

	def test_pico_decodes_missing_sensors_as_none(self) -> None:
		from cansat_hw.telemetry.codec import (
			MODE_CONFIG,
			STATE_NONE,
			pack_tlm,
		)

		pico = _import_pico_decoder()
		raw = pack_tlm(
			mode=MODE_CONFIG,
			state=STATE_NONE,
			seq=1,
			utc_seconds=0,
			utc_ms=0,
		)
		d = pico.decode_tlm(raw)
		self.assertIsNone(d["alt_m"])
		self.assertIsNone(d["pressure_hpa"])
		self.assertIsNone(d["heading_deg"])
		self.assertIsNone(d["bno_sys_cal"])
		self.assertEqual(d["tag_count"], 0)

	def test_format_short_is_one_line(self) -> None:
		from cansat_hw.telemetry.codec import (
			MODE_TEST,
			STATE_DEPLOYED,
			pack_tlm,
		)

		pico = _import_pico_decoder()
		raw = pack_tlm(
			mode=MODE_TEST,
			state=STATE_DEPLOYED,
			seq=3,
			utc_seconds=1_700_000_000,
			utc_ms=0,
			alt_m=1.23,
			pressure_hpa=1013.0,
		)
		line = pico.format_tlm_short(pico.decode_tlm(raw))
		self.assertIsInstance(line, str)
		self.assertNotIn("\n", line)
		self.assertIn("seq=3", line)
		self.assertIn("TEST/DEPLOYED", line)


if __name__ == "__main__":
	unittest.main()
