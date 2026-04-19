"""Round-trip tests voor het binary TLM-codec (Fase 1)."""

from __future__ import annotations

import unittest


class FrameLayoutTest(unittest.TestCase):
	def test_frame_size_is_60_bytes(self) -> None:
		from cansat_hw.telemetry.codec import FRAME_SIZE

		self.assertEqual(FRAME_SIZE, 60)

	def test_pack_returns_exactly_frame_size_bytes(self) -> None:
		from cansat_hw.telemetry.codec import (
			FRAME_SIZE,
			MODE_TEST,
			STATE_DEPLOYED,
			pack_tlm,
		)

		raw = pack_tlm(
			mode=MODE_TEST,
			state=STATE_DEPLOYED,
			seq=1,
			utc_seconds=1_700_000_000,
			utc_ms=123,
		)
		self.assertEqual(len(raw), FRAME_SIZE)


class ModeStateByteTest(unittest.TestCase):
	def test_pack_unpack_round_trip(self) -> None:
		from cansat_hw.telemetry.codec import pack_mode_state, unpack_mode_state

		for mode in range(0, 16):
			for state in range(0, 16):
				b = pack_mode_state(mode, state)
				self.assertEqual(b & 0xFF, b)
				m, s = unpack_mode_state(b)
				self.assertEqual(m, mode)
				self.assertEqual(s, state)

	def test_high_nibble_is_mode_low_is_state(self) -> None:
		from cansat_hw.telemetry.codec import (
			MODE_TEST,
			STATE_DEPLOYED,
			pack_mode_state,
		)

		# TEST = 0x2 -> high; DEPLOYED = 0x3 -> low. Byte = 0x23 = 35.
		self.assertEqual(pack_mode_state(MODE_TEST, STATE_DEPLOYED), 0x23)

	def test_mode_for_string(self) -> None:
		from cansat_hw.telemetry.codec import (
			MODE_CONFIG,
			MODE_MISSION,
			MODE_TEST,
			MODE_UNKNOWN,
			mode_for_string,
		)

		self.assertEqual(mode_for_string("CONFIG"), MODE_CONFIG)
		self.assertEqual(mode_for_string("MISSION"), MODE_MISSION)
		self.assertEqual(mode_for_string("TEST"), MODE_TEST)
		self.assertEqual(mode_for_string("xyz"), MODE_UNKNOWN)
		self.assertEqual(mode_for_string(""), MODE_UNKNOWN)

	def test_state_for_mode_string_defaults(self) -> None:
		from cansat_hw.telemetry.codec import (
			STATE_DEPLOYED,
			STATE_NONE,
			STATE_PAD_IDLE,
			state_for_mode_string,
		)

		self.assertEqual(state_for_mode_string("CONFIG"), STATE_NONE)
		self.assertEqual(state_for_mode_string("MISSION"), STATE_PAD_IDLE)
		self.assertEqual(state_for_mode_string("TEST"), STATE_DEPLOYED)


class IsBinaryPacketTest(unittest.TestCase):
	def test_record_types_are_binary(self) -> None:
		from cansat_hw.telemetry.codec import (
			RECORD_EVT,
			RECORD_TLM,
			is_binary_packet,
		)

		self.assertTrue(is_binary_packet(RECORD_TLM))
		self.assertTrue(is_binary_packet(RECORD_EVT))
		self.assertTrue(is_binary_packet(0x00))
		self.assertTrue(is_binary_packet(0x1F))

	def test_ascii_letters_are_not_binary(self) -> None:
		from cansat_hw.telemetry.codec import is_binary_packet

		# Tekst-replies starten met 'O' (OK), 'E' (ERR), 'T' (TLM-text), …
		for ch in ("O", "E", "T", " "):
			self.assertFalse(is_binary_packet(ord(ch)))

	def test_invalid_input_is_safe(self) -> None:
		from cansat_hw.telemetry.codec import is_binary_packet

		self.assertFalse(is_binary_packet(-1))
		self.assertFalse(is_binary_packet(256))
		self.assertFalse(is_binary_packet(None))


class CalPackTest(unittest.TestCase):
	def test_round_trip_full(self) -> None:
		from cansat_hw.telemetry.codec import pack_cal, unpack_cal

		# Géén sentinel: alle 256 byte-waarden kunnen voorkomen, ook 0xFF.
		for s in range(4):
			for g in range(4):
				for a in range(4):
					for m in range(4):
						b = pack_cal(s, g, a, m)
						self.assertEqual(unpack_cal(b), (s, g, a, m))

	def test_all_three_packs_to_ff(self) -> None:
		from cansat_hw.telemetry.codec import pack_cal, unpack_cal

		# (3,3,3,3) => 0xFF en moet correct terugkomen (geen sentinel-collision).
		self.assertEqual(pack_cal(3, 3, 3, 3), 0xFF)
		self.assertEqual(unpack_cal(0xFF), (3, 3, 3, 3))

	def test_partial_treats_missing_as_zero(self) -> None:
		from cansat_hw.telemetry.codec import pack_cal, unpack_cal

		b = pack_cal(3)
		self.assertEqual(unpack_cal(b), (3, 0, 0, 0))

	def test_no_bno_decodes_as_none(self) -> None:
		"""Wanneer er géén BNO is, zet de packer Euler op None én cal=0; de
		decoder herkent die combo en rapporteert cal-velden als ``None``."""
		from cansat_hw.telemetry.codec import (
			MODE_TEST,
			STATE_DEPLOYED,
			pack_tlm,
			unpack_tlm,
		)

		raw = pack_tlm(
			mode=MODE_TEST,
			state=STATE_DEPLOYED,
			seq=0,
			utc_seconds=0,
			utc_ms=0,
			heading_deg=None,
			roll_deg=None,
			pitch_deg=None,
			sys_cal=None,
			gyro_cal=None,
			accel_cal=None,
			mag_cal=None,
		)
		f = unpack_tlm(raw)
		self.assertIsNone(f.sys_cal)
		self.assertIsNone(f.gyro_cal)
		self.assertIsNone(f.accel_cal)
		self.assertIsNone(f.mag_cal)

	def test_zero_cal_with_present_euler_is_real_zero(self) -> None:
		"""Als de Euler er WEL is maar cal=0, mag dat niet als None gedecodeerd."""
		from cansat_hw.telemetry.codec import (
			MODE_TEST,
			STATE_DEPLOYED,
			pack_tlm,
			unpack_tlm,
		)

		raw = pack_tlm(
			mode=MODE_TEST,
			state=STATE_DEPLOYED,
			seq=0,
			utc_seconds=0,
			utc_ms=0,
			heading_deg=10.0,
			roll_deg=0.0,
			pitch_deg=0.0,
			sys_cal=0,
			gyro_cal=0,
			accel_cal=0,
			mag_cal=0,
		)
		f = unpack_tlm(raw)
		self.assertEqual(f.sys_cal, 0)
		self.assertEqual(f.gyro_cal, 0)
		self.assertEqual(f.accel_cal, 0)
		self.assertEqual(f.mag_cal, 0)


class PackUnpackRoundTripTest(unittest.TestCase):
	def test_full_frame_round_trips_within_scale(self) -> None:
		from cansat_hw.telemetry.codec import (
			MODE_MISSION,
			STATE_DEPLOYED,
			TagDetection,
			pack_tlm,
			unpack_tlm,
		)

		raw = pack_tlm(
			mode=MODE_MISSION,
			state=STATE_DEPLOYED,
			seq=12345,
			utc_seconds=1_700_000_000,
			utc_ms=789,
			alt_m=12.34,
			pressure_hpa=1019.32,
			temp_c=21.5,
			heading_deg=123.4,
			roll_deg=-5.6,
			pitch_deg=7.8,
			ax_g=0.123,
			ay_g=-0.456,
			az_g=1.001,
			gx_dps=10.0,
			gy_dps=-20.0,
			gz_dps=0.5,
			sys_cal=3,
			gyro_cal=3,
			accel_cal=2,
			mag_cal=1,
			tags=[
				TagDetection(tag_id=7, dx_cm=10, dy_cm=-20, dz_cm=300, size_mm=150)
			],
		)
		f = unpack_tlm(raw)
		self.assertEqual(f.mode, MODE_MISSION)
		self.assertEqual(f.state, STATE_DEPLOYED)
		self.assertEqual(f.seq, 12345)
		self.assertEqual(f.utc_seconds, 1_700_000_000)
		self.assertEqual(f.utc_ms, 789)
		self.assertAlmostEqual(f.alt_m, 12.34, places=2)
		self.assertAlmostEqual(f.pressure_hpa, 1019.3, places=1)
		self.assertAlmostEqual(f.temp_c, 21.5, places=1)
		self.assertAlmostEqual(f.heading_deg, 123.4, places=1)
		self.assertAlmostEqual(f.roll_deg, -5.6, places=1)
		self.assertAlmostEqual(f.pitch_deg, 7.8, places=1)
		self.assertAlmostEqual(f.ax_g, 0.123, places=3)
		self.assertAlmostEqual(f.ay_g, -0.456, places=3)
		self.assertAlmostEqual(f.az_g, 1.001, places=3)
		self.assertAlmostEqual(f.gx_dps, 10.0, places=1)
		self.assertAlmostEqual(f.gy_dps, -20.0, places=1)
		self.assertAlmostEqual(f.gz_dps, 0.5, places=1)
		self.assertEqual(f.sys_cal, 3)
		self.assertEqual(f.gyro_cal, 3)
		self.assertEqual(f.accel_cal, 2)
		self.assertEqual(f.mag_cal, 1)
		self.assertEqual(len(f.tags), 1)
		self.assertEqual(f.tags[0].tag_id, 7)
		self.assertEqual(f.tags[0].dx_cm, 10)
		self.assertEqual(f.tags[0].dy_cm, -20)
		self.assertEqual(f.tags[0].dz_cm, 300)
		self.assertEqual(f.tags[0].size_mm, 150)

	def test_missing_sensors_decode_as_none(self) -> None:
		from cansat_hw.telemetry.codec import (
			MODE_TEST,
			STATE_DEPLOYED,
			pack_tlm,
			unpack_tlm,
		)

		raw = pack_tlm(
			mode=MODE_TEST,
			state=STATE_DEPLOYED,
			seq=1,
			utc_seconds=0,
			utc_ms=0,
		)
		f = unpack_tlm(raw)
		self.assertIsNone(f.alt_m)
		self.assertIsNone(f.pressure_hpa)
		self.assertIsNone(f.temp_c)
		self.assertIsNone(f.heading_deg)
		self.assertIsNone(f.roll_deg)
		self.assertIsNone(f.pitch_deg)
		self.assertIsNone(f.ax_g)
		self.assertIsNone(f.ay_g)
		self.assertIsNone(f.az_g)
		self.assertIsNone(f.gx_dps)
		self.assertIsNone(f.gy_dps)
		self.assertIsNone(f.gz_dps)
		self.assertIsNone(f.sys_cal)
		self.assertEqual(f.tags, [])

	def test_two_tags_round_trip(self) -> None:
		from cansat_hw.telemetry.codec import (
			MODE_MISSION,
			STATE_DEPLOYED,
			TagDetection,
			pack_tlm,
			unpack_tlm,
		)

		raw = pack_tlm(
			mode=MODE_MISSION,
			state=STATE_DEPLOYED,
			seq=2,
			utc_seconds=0,
			utc_ms=0,
			tags=[
				TagDetection(tag_id=1, dx_cm=1, dy_cm=2, dz_cm=3, size_mm=100),
				TagDetection(tag_id=2, dx_cm=-50, dy_cm=0, dz_cm=10, size_mm=200),
			],
		)
		f = unpack_tlm(raw)
		self.assertEqual(len(f.tags), 2)
		self.assertEqual(f.tags[0].tag_id, 1)
		self.assertEqual(f.tags[1].tag_id, 2)
		self.assertEqual(f.tags[1].dx_cm, -50)

	def test_extra_tags_are_truncated_silently(self) -> None:
		from cansat_hw.telemetry.codec import (
			MODE_MISSION,
			NUM_TAGS,
			STATE_DEPLOYED,
			TagDetection,
			pack_tlm,
			unpack_tlm,
		)

		raw = pack_tlm(
			mode=MODE_MISSION,
			state=STATE_DEPLOYED,
			seq=3,
			utc_seconds=0,
			utc_ms=0,
			tags=[
				TagDetection(tag_id=i + 1, size_mm=10 * (i + 1))
				for i in range(NUM_TAGS + 3)
			],
		)
		f = unpack_tlm(raw)
		self.assertEqual(len(f.tags), NUM_TAGS)
		# Eerste twee bewaard, rest weggegooid (geen overflow).
		self.assertEqual(f.tags[0].tag_id, 1)
		self.assertEqual(f.tags[1].tag_id, 2)


class ClampingTest(unittest.TestCase):
	def test_extreme_altitude_is_clamped_not_collided_with_sentinel(self) -> None:
		from cansat_hw.telemetry.codec import (
			MODE_TEST,
			STATE_DEPLOYED,
			pack_tlm,
			unpack_tlm,
		)

		# 10 km zou -> 1_000_000 cm; clip naar +0x7FFF cm = 327.67 m.
		raw = pack_tlm(
			mode=MODE_TEST,
			state=STATE_DEPLOYED,
			seq=0,
			utc_seconds=0,
			utc_ms=0,
			alt_m=10_000.0,
		)
		f = unpack_tlm(raw)
		self.assertIsNotNone(f.alt_m)
		self.assertAlmostEqual(f.alt_m, 327.67, places=2)

	def test_negative_altitude_does_not_alias_sentinel(self) -> None:
		"""Heel diepe negatieve hoogte mag geen ``None`` worden."""
		from cansat_hw.telemetry.codec import (
			MODE_TEST,
			STATE_DEPLOYED,
			pack_tlm,
			unpack_tlm,
		)

		# -400 m -> -40000 cm; ondergrens -32767 cm = -327.67 m.
		raw = pack_tlm(
			mode=MODE_TEST,
			state=STATE_DEPLOYED,
			seq=0,
			utc_seconds=0,
			utc_ms=0,
			alt_m=-400.0,
		)
		f = unpack_tlm(raw)
		self.assertIsNotNone(f.alt_m)
		self.assertLess(f.alt_m, -300.0)

	def test_too_low_pressure_is_clamped_not_sentinel(self) -> None:
		from cansat_hw.telemetry.codec import (
			MODE_TEST,
			STATE_DEPLOYED,
			pack_tlm,
			unpack_tlm,
		)

		raw = pack_tlm(
			mode=MODE_TEST,
			state=STATE_DEPLOYED,
			seq=0,
			utc_seconds=0,
			utc_ms=0,
			pressure_hpa=99999.0,
		)
		f = unpack_tlm(raw)
		# Geclampt op 0xFFFE / 10 = 6553.4 hPa; geen None.
		self.assertIsNotNone(f.pressure_hpa)
		self.assertAlmostEqual(f.pressure_hpa, 6553.4, places=1)


class UnpackErrorTest(unittest.TestCase):
	def test_too_short_raises(self) -> None:
		from cansat_hw.telemetry.codec import unpack_tlm

		with self.assertRaises(ValueError):
			unpack_tlm(b"\x01\x00\x00")

	def test_extra_trailing_bytes_are_ignored(self) -> None:
		"""Voor log-frames met framing-overhead: trailing bytes mogen niet storen."""
		from cansat_hw.telemetry.codec import (
			MODE_TEST,
			STATE_DEPLOYED,
			pack_tlm,
			unpack_tlm,
		)

		raw = pack_tlm(
			mode=MODE_TEST,
			state=STATE_DEPLOYED,
			seq=42,
			utc_seconds=0,
			utc_ms=0,
		)
		framed = raw + b"\xa5\x5a\x12\x34"
		f = unpack_tlm(framed)
		self.assertEqual(f.seq, 42)


if __name__ == "__main__":
	unittest.main()
