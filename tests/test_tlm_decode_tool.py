"""Tests voor de laptop-side decoder ``tools/tlm_decode.py`` (Fase 6)."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


def _import_tool():
	"""Import ``tools.tlm_decode`` via expliciet pad (geen package nodig)."""
	root = Path(__file__).resolve().parents[1]
	tools_dir = root / "tools"
	if str(tools_dir) not in sys.path:
		sys.path.insert(0, str(tools_dir))
	import tlm_decode  # noqa: WPS433  (intentional dynamic import)

	return tlm_decode


def _make_test_file(tmp: Path, *, frames: int = 3) -> Path:
	"""Schrijf een ``.bin``-file met HEADER + ``frames`` TLM + 1 ASCII-EVT."""
	from cansat_hw.telemetry.codec import (
		MODE_TEST,
		STATE_DEPLOYED,
		pack_tlm,
	)
	from cansat_hw.telemetry.log_writer import (
		LogSession,
		build_header_payload,
	)

	path = tmp / "session.bin"
	session = LogSession(
		path,
		header_payload=build_header_payload(mode="TEST", hostname="rpi"),
	)
	for i in range(frames):
		frame = pack_tlm(
			mode=MODE_TEST,
			state=STATE_DEPLOYED,
			seq=i + 1,
			utc_seconds=1_700_000_000 + i,
			utc_ms=100 * i,
			alt_m=0.10 + 0.01 * i,
			pressure_hpa=1019.5,
			temp_c=21.0,
			heading_deg=180.0,
			roll_deg=-5.0,
			pitch_deg=2.0,
			ax_g=0.001,
			ay_g=-0.002,
			az_g=0.003,
			sys_cal=2,
			gyro_cal=3,
			accel_cal=1,
			mag_cal=0,
		)
		session.append(frame)
	session.append(b"EVT MODE CONFIG END_TEST")
	session.close()
	return path


class HumanRendererTest(unittest.TestCase):
	def test_human_output_contains_hdr_and_tlm_lines(self) -> None:
		tool = _import_tool()
		buf = io.StringIO()
		renderer = tool.HumanRenderer(buf)
		with tempfile.TemporaryDirectory() as td:
			path = _make_test_file(Path(td), frames=2)
			renderer.header([str(path)])
			for label, off, payload, ok in tool._iter_file_oneshot(path, label=path.name):
				renderer.render(label, off, payload, ok)
			renderer.close()
		out = buf.getvalue().splitlines()
		# 1 HDR + 2 TLM + 1 TXT(EVT) → 4 inhoudsregels (+1 banner)
		self.assertGreaterEqual(len(out), 5)
		self.assertTrue(any(" HDR " in line for line in out))
		self.assertTrue(any(" TLM " in line and "seq=1" in line for line in out))
		self.assertTrue(any(" TLM " in line and "seq=2" in line for line in out))
		self.assertTrue(any(" TXT " in line and "END_TEST" in line for line in out))
		# Mode/state moeten getoond worden voor TLM
		self.assertTrue(any("TEST/DEPLOYED" in line for line in out))


class JsonlRendererTest(unittest.TestCase):
	def test_jsonl_emits_one_object_per_record(self) -> None:
		tool = _import_tool()
		buf = io.StringIO()
		renderer = tool.JsonlRenderer(buf)
		with tempfile.TemporaryDirectory() as td:
			path = _make_test_file(Path(td), frames=3)
			for label, off, payload, ok in tool._iter_file_oneshot(path, label=path.name):
				renderer.render(label, off, payload, ok)
			renderer.close()
		lines = buf.getvalue().splitlines()
		# 1 HDR + 3 TLM + 1 EVT
		self.assertEqual(len(lines), 5)
		objs = [json.loads(line) for line in lines]
		self.assertEqual(objs[0]["kind"], "HDR")
		self.assertEqual(objs[0]["frame_size"], 60)
		self.assertEqual(objs[0]["hostname"], "rpi")
		# TLM-objects bevatten alle ge-decodeerde velden
		self.assertEqual(objs[1]["kind"], "TLM")
		self.assertEqual(objs[1]["seq"], 1)
		self.assertEqual(objs[1]["mode"], "TEST")
		self.assertEqual(objs[1]["state"], "DEPLOYED")
		self.assertAlmostEqual(objs[1]["alt_m"], 0.10, places=2)
		self.assertEqual(objs[-1]["kind"], "ASCII")
		self.assertIn("END_TEST", objs[-1]["text"])


class CsvRendererTest(unittest.TestCase):
	def test_csv_has_header_and_one_row_per_tlm(self) -> None:
		tool = _import_tool()
		buf = io.StringIO()
		renderer = tool.CsvRenderer(buf)
		with tempfile.TemporaryDirectory() as td:
			path = _make_test_file(Path(td), frames=3)
			renderer.header([str(path)])
			for label, off, payload, ok in tool._iter_file_oneshot(path, label=path.name):
				renderer.render(label, off, payload, ok)
			renderer.close()
		lines = buf.getvalue().splitlines()
		# Comments: banner + HDR + EVT-comment ; data: 1 header-rij + 3 TLM-rijen
		comment_lines = [line for line in lines if line.startswith("#")]
		data_lines = [line for line in lines if not line.startswith("#")]
		self.assertGreaterEqual(len(comment_lines), 3)  # banner + HDR + TXT
		self.assertEqual(len(data_lines), 4)  # 1 header + 3 rows
		# CSV-header bevat verplichte kolommen
		self.assertIn("seq", data_lines[0])
		self.assertIn("alt_m", data_lines[0])
		self.assertIn("crc_ok", data_lines[0])
		# Eerste data-rij heeft seq=1
		self.assertIn(",1,", data_lines[1])

	def test_csv_skips_bad_crc_when_requested(self) -> None:
		from cansat_hw.telemetry.codec import (
			MODE_TEST,
			STATE_DEPLOYED,
			pack_tlm,
		)
		from cansat_hw.telemetry.log_writer import LOG_OVERHEAD, pack_log_record

		tool = _import_tool()
		# Bouw 1 goed + 1 corrupt record direct in een buffer.
		good = pack_tlm(
			mode=MODE_TEST,
			state=STATE_DEPLOYED,
			seq=42,
			utc_seconds=1_700_000_000,
			utc_ms=0,
		)
		bad = bytearray(pack_log_record(good))
		bad[-1] ^= 0xFF  # CRC kapot
		with tempfile.TemporaryDirectory() as td:
			path = Path(td) / "mix.bin"
			path.write_bytes(pack_log_record(good) + bytes(bad))
			# Sanity: 2 records met 1 bad CRC
			self.assertEqual(path.stat().st_size, 2 * (LOG_OVERHEAD + len(good)))

			# main() schrijft naar stdout; vang via StringIO + sys.stdout patch.
			out = io.StringIO()
			old_stdout = sys.stdout
			sys.stdout = out
			try:
				rc = tool.main(["--format", "csv", "--skip-bad-crc", str(path)])
			finally:
				sys.stdout = old_stdout
			self.assertEqual(rc, 0)
		text = out.getvalue()
		# Slechts 1 data-rij (de goede); CRC-fout is gefilterd weg.
		data_rows = [
			line for line in text.splitlines() if line and not line.startswith("#")
		]
		# 1 csv-header + 1 data
		self.assertEqual(len(data_rows), 2)
		self.assertIn(",42,", data_rows[1])


class TailModeTest(unittest.TestCase):
	def test_tail_picks_up_new_records_appended_during_iteration(self) -> None:
		from cansat_hw.telemetry.codec import (
			MODE_TEST,
			STATE_DEPLOYED,
			pack_tlm,
		)
		from cansat_hw.telemetry.log_writer import (
			LogSession,
			build_header_payload,
		)

		tool = _import_tool()
		with tempfile.TemporaryDirectory() as td:
			path = Path(td) / "live.bin"
			session = LogSession(
				path, header_payload=build_header_payload(mode="TEST")
			)
			session.append(
				pack_tlm(
					mode=MODE_TEST,
					state=STATE_DEPLOYED,
					seq=1,
					utc_seconds=1_700_000_000,
					utc_ms=0,
				)
			)

			collected = []
			done = threading.Event()

			def writer() -> None:
				time.sleep(0.05)
				session.append(
					pack_tlm(
						mode=MODE_TEST,
						state=STATE_DEPLOYED,
						seq=2,
						utc_seconds=1_700_000_001,
						utc_ms=0,
					)
				)
				time.sleep(0.05)
				session.append(
					pack_tlm(
						mode=MODE_TEST,
						state=STATE_DEPLOYED,
						seq=3,
						utc_seconds=1_700_000_002,
						utc_ms=0,
					)
				)
				time.sleep(0.05)
				session.close()
				done.set()

			t = threading.Thread(target=writer)
			t.start()
			try:
				for label, off, payload, ok in tool._iter_file_tail(
					path,
					label=path.name,
					poll_s=0.02,
					stop_after_idle_s=0.4,
				):
					collected.append((tool._classify(payload), ok))
					# Stop zodra alle 3 TLM binnen zijn (HDR + 3 TLM = 4 entries).
					if sum(1 for k, _ in collected if k == "TLM") >= 3 and done.is_set():
						break
			finally:
				t.join(timeout=2.0)

		kinds = [k for k, _ in collected]
		self.assertEqual(kinds[0], "HDR")
		self.assertEqual(kinds.count("TLM"), 3)
		self.assertTrue(all(ok for _, ok in collected))


class ClassifierTest(unittest.TestCase):
	def test_classify_known_types(self) -> None:
		from cansat_hw.telemetry.codec import RECORD_HDR, RECORD_TLM

		tool = _import_tool()
		self.assertEqual(tool._classify(bytes([RECORD_TLM, 0])), "TLM")
		self.assertEqual(tool._classify(bytes([RECORD_HDR, 0])), "HDR")
		self.assertEqual(tool._classify(b"EVT MODE"), "ASCII")
		self.assertEqual(tool._classify(b"\x05\x06"), "BIN_OTHER")
		self.assertEqual(tool._classify(b""), "EMPTY")


if __name__ == "__main__":
	unittest.main()
