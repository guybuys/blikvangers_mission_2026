"""Unit tests voor de binary log writer (Fase 4).

Geen radio of disk-mocking-truken: we werken met ``tempfile.TemporaryDirectory``
zodat elke test een eigen, op-te-ruimen log-directory krijgt.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class CrcAndFramingTest(unittest.TestCase):
	def test_crc_known_vector(self) -> None:
		# CRC-16/CCITT-FALSE op "123456789" (poly 0x1021, init 0xFFFF) = 0x29B1.
		from cansat_hw.telemetry.log_writer import crc16_ccitt

		self.assertEqual(crc16_ccitt(b"123456789"), 0x29B1)

	def test_crc_empty(self) -> None:
		from cansat_hw.telemetry.log_writer import crc16_ccitt

		self.assertEqual(crc16_ccitt(b""), 0xFFFF)

	def test_pack_overhead_is_six_bytes(self) -> None:
		from cansat_hw.telemetry.log_writer import LOG_OVERHEAD, pack_log_record

		payload = b"hello"
		raw = pack_log_record(payload)
		self.assertEqual(len(raw), len(payload) + LOG_OVERHEAD)
		# Magic eerst, daarna little-endian length.
		self.assertEqual(raw[:2], b"\xa5\x5a")
		self.assertEqual(int.from_bytes(raw[2:4], "little"), len(payload))
		self.assertEqual(raw[4 : 4 + len(payload)], payload)

	def test_pack_rejects_oversize_payload(self) -> None:
		from cansat_hw.telemetry.log_writer import LOG_PAYLOAD_MAX, pack_log_record

		with self.assertRaises(ValueError):
			pack_log_record(b"\x00" * (LOG_PAYLOAD_MAX + 1))

	def test_iter_records_round_trip(self) -> None:
		from cansat_hw.telemetry.log_writer import iter_records, pack_log_record

		buf = io.BytesIO()
		buf.write(pack_log_record(b"first"))
		buf.write(pack_log_record(b"second payload"))
		buf.write(pack_log_record(b""))
		buf.seek(0)
		records = list(iter_records(buf))
		self.assertEqual([p for _, p, _ in records], [b"first", b"second payload", b""])
		self.assertTrue(all(ok for _, _, ok in records))

	def test_iter_records_resyncs_after_corruption(self) -> None:
		from cansat_hw.telemetry.log_writer import iter_records, pack_log_record

		# 6 bytes rommel vóór een geldig record; iter moet 'm vinden via magic.
		buf = io.BytesIO()
		buf.write(b"\x11\x22\x33\x44\x55\x66")
		buf.write(pack_log_record(b"survived"))
		buf.seek(0)
		records = list(iter_records(buf))
		self.assertEqual(len(records), 1)
		self.assertEqual(records[0][1], b"survived")
		self.assertTrue(records[0][2])

	def test_iter_records_marks_bad_crc(self) -> None:
		from cansat_hw.telemetry.log_writer import iter_records, pack_log_record

		raw = bytearray(pack_log_record(b"abc"))
		raw[-1] ^= 0xFF  # flip CRC
		buf = io.BytesIO(bytes(raw))
		records = list(iter_records(buf))
		self.assertEqual(len(records), 1)
		self.assertEqual(records[0][1], b"abc")
		self.assertFalse(records[0][2])


class HeaderRecordTest(unittest.TestCase):
	def test_header_payload_is_64_bytes(self) -> None:
		from cansat_hw.telemetry.log_writer import HEADER_PAYLOAD_SIZE, build_header_payload

		payload = build_header_payload(mode="TEST", hostname="rpi", now_wall=1.0)
		self.assertEqual(len(payload), HEADER_PAYLOAD_SIZE)

	def test_header_round_trip_carries_format_and_hostname(self) -> None:
		from cansat_hw.telemetry.codec import FRAME_FORMAT, FRAME_SIZE, RECORD_HDR
		from cansat_hw.telemetry.log_writer import (
			build_header_payload,
			parse_header_payload,
		)

		payload = build_header_payload(
			mode="MISSION", hostname="cansat-pi", now_wall=1_700_000_000.123
		)
		info = parse_header_payload(payload)
		self.assertEqual(info["record_type"], RECORD_HDR)
		self.assertEqual(info["frame_size"], FRAME_SIZE)
		self.assertEqual(info["frame_format"], FRAME_FORMAT)
		self.assertEqual(info["hostname"], "cansat-pi")
		self.assertEqual(info["utc_seconds"], 1_700_000_000)
		self.assertEqual(info["utc_ms"], 123)
		# MISSION/PAD_IDLE = 0x11
		self.assertEqual(info["mode_state"] >> 4, 0x1)
		self.assertEqual(info["mode_state"] & 0x0F, 0x1)


class LogSessionTest(unittest.TestCase):
	def test_session_writes_header_then_payloads(self) -> None:
		from cansat_hw.telemetry.log_writer import (
			LogSession,
			build_header_payload,
			iter_records,
			parse_header_payload,
		)

		with tempfile.TemporaryDirectory() as td:
			path = Path(td) / "x.bin"
			session = LogSession(path, header_payload=build_header_payload(mode="TEST"))
			session.append(b"\x01" * 60)
			session.append(b"\x02" * 60)
			session.close()

			with open(str(path), "rb") as fh:
				records = list(iter_records(fh))
			self.assertEqual(len(records), 3)
			# Eerste record = HEADER
			info = parse_header_payload(records[0][1])
			self.assertEqual(info["frame_size"], 60)
			self.assertEqual(records[1][1], b"\x01" * 60)
			self.assertEqual(records[2][1], b"\x02" * 60)
			self.assertTrue(all(ok for _, _, ok in records))

	def test_session_append_after_close_raises(self) -> None:
		from cansat_hw.telemetry.log_writer import LogSession

		with tempfile.TemporaryDirectory() as td:
			session = LogSession(Path(td) / "y.bin")
			session.close()
			with self.assertRaises(OSError):
				session.append(b"x")


class LogManagerTest(unittest.TestCase):
	def test_continuous_opens_at_init_and_collects_writes(self) -> None:
		from cansat_hw.telemetry.log_writer import LogManager, iter_records

		with tempfile.TemporaryDirectory() as td:
			mgr = LogManager(td, hostname="t")
			self.assertTrue(mgr.enabled)
			cont_path = mgr.continuous_path
			self.assertIsNotNone(cont_path)
			mgr.write_payload(b"\x01" * 60)
			mgr.write_payload(b"\x02" * 60)
			mgr.close()

			with open(str(cont_path), "rb") as fh:
				records = list(iter_records(fh))
			# HEADER + 2 payloads
			self.assertEqual(len(records), 3)

	def test_test_session_opens_and_closes_on_mode_change(self) -> None:
		from cansat_hw.telemetry.log_writer import LogManager, iter_records

		with tempfile.TemporaryDirectory() as td:
			mgr = LogManager(td, hostname="t")
			mgr.on_mode_change("CONFIG", "TEST")
			session_path = mgr.session_path
			self.assertIsNotNone(session_path)
			self.assertTrue(session_path.name.startswith("cansat_test_"))
			mgr.write_payload(b"\xaa" * 60)
			mgr.write_payload(b"\xbb" * 60)
			mgr.on_mode_change("TEST", "CONFIG")
			self.assertIsNone(mgr.session_path)
			mgr.close()

			# Session-file: HEADER + 2 frames
			with open(str(session_path), "rb") as fh:
				records = list(iter_records(fh))
			self.assertEqual(len(records), 3)

			# Continuous-file krijgt dezelfde 2 frames + eigen HEADER
			cont_path = Path(td) / "cansat_continuous.bin"
			with open(str(cont_path), "rb") as fh:
				cont = list(iter_records(fh))
			self.assertEqual(len(cont), 3)

	def test_mission_session_filename(self) -> None:
		from cansat_hw.telemetry.log_writer import LogManager

		with tempfile.TemporaryDirectory() as td:
			mgr = LogManager(td)
			mgr.on_mode_change("CONFIG", "MISSION")
			self.assertTrue(mgr.session_path.name.startswith("cansat_mission_"))
			mgr.close()

	def test_disabled_manager_is_silent(self) -> None:
		from cansat_hw.telemetry.log_writer import LogManager

		with tempfile.TemporaryDirectory() as td:
			mgr = LogManager(td, enabled=False)
			self.assertFalse(mgr.enabled)
			self.assertIsNone(mgr.continuous_path)
			# Geen exception; gewoon no-op.
			self.assertFalse(mgr.write_payload(b"\x01" * 60))
			mgr.on_mode_change("CONFIG", "TEST")
			self.assertIsNone(mgr.session_path)
			mgr.close()

	def test_disk_full_disables_manager_after_warn(self) -> None:
		from cansat_hw.telemetry import log_writer

		with tempfile.TemporaryDirectory() as td:
			mgr = log_writer.LogManager(td)
			self.assertTrue(mgr.enabled)

			# Force een OSError op de continuous-write zodat _disable wordt
			# getriggerd; verifieer dat verdere writes no-ops zijn.
			with mock.patch.object(
				mgr._continuous, "append", side_effect=OSError("no space left")
			):
				with mock.patch("sys.stderr"):
					self.assertFalse(mgr.write_payload(b"\x01" * 60))
			self.assertFalse(mgr.enabled)
			# Stil bij volgende calls.
			self.assertFalse(mgr.write_payload(b"\x02" * 60))
			mgr.close()

	def test_real_tlm_round_trip_through_log(self) -> None:
		"""End-to-end: pack_tlm → LogManager → iter_records → unpack_tlm."""
		from cansat_hw.telemetry.codec import (
			MODE_TEST,
			STATE_DEPLOYED,
			pack_tlm,
			unpack_tlm,
		)
		from cansat_hw.telemetry.log_writer import LogManager, iter_records

		frame = pack_tlm(
			mode=MODE_TEST,
			state=STATE_DEPLOYED,
			seq=42,
			utc_seconds=1_700_000_000,
			utc_ms=500,
			alt_m=1.23,
			pressure_hpa=1019.5,
			temp_c=20.5,
		)

		with tempfile.TemporaryDirectory() as td:
			mgr = LogManager(td)
			mgr.on_mode_change("CONFIG", "TEST")
			mgr.write_payload(frame)
			session_path = mgr.session_path
			mgr.on_mode_change("TEST", "CONFIG")
			mgr.close()

			with open(str(session_path), "rb") as fh:
				records = [p for _, p, ok in iter_records(fh) if ok]
			# HEADER + 1 TLM
			self.assertEqual(len(records), 2)
			tlm = unpack_tlm(records[1])
			self.assertEqual(tlm.seq, 42)
			self.assertAlmostEqual(tlm.alt_m or 0.0, 1.23, places=2)
			self.assertAlmostEqual(tlm.pressure_hpa or 0.0, 1019.5, places=1)
			self.assertAlmostEqual(tlm.temp_c or 0.0, 20.5, places=1)


if __name__ == "__main__":
	unittest.main()
