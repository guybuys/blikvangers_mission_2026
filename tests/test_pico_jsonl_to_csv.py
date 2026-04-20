"""Tests voor ``scripts/pico_jsonl_to_csv.py`` (Pico JSONL → CSV)."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, List


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "pico_jsonl_to_csv.py"


def _load_tool():
	spec = importlib.util.spec_from_file_location(
		"pico_jsonl_to_csv", str(_SCRIPT_PATH)
	)
	if spec is None or spec.loader is None:
		raise RuntimeError("kan pico_jsonl_to_csv.py niet laden: %s" % _SCRIPT_PATH)
	mod = importlib.util.module_from_spec(spec)
	sys.modules["pico_jsonl_to_csv"] = mod
	spec.loader.exec_module(mod)
	return mod


def _binary_tlm_rx(**overrides: Any) -> dict:
	"""RX-record zoals ``basestation_cli.py`` het schrijft voor een binary TLM."""
	parsed = {
		"kind": "TLM",
		"record_type": 0x01,
		"mode": "MISSION",
		"state": "ASCENT",
		"seq": 42,
		"utc_seconds": 1_776_607_084,
		"utc_ms": 733,
		"utc": 1_776_607_084.733,
		"alt_m": 12.34,
		"pressure_hpa": 1019.48,
		"temp_c": 21.3,
		"heading_deg": 286.6,
		"roll_deg": -34.4,
		"pitch_deg": -54.3,
		"ax_g": 0.123,
		"ay_g": -0.045,
		"az_g": 1.006,
		"gx_dps": 0.0,
		"gy_dps": 0.0,
		"gz_dps": 0.0,
		"bno_sys_cal": 3,
		"bno_gyro_cal": 3,
		"bno_accel_cal": 3,
		"bno_mag_cal": 2,
		"tag_count": 1,
		"tags": [{"id": 23, "dx_cm": 1, "dy_cm": 2, "dz_cm": 3, "size_mm": 46}],
	}
	parsed.update(overrides.pop("parsed_overrides", {}))
	rec = {
		"dt_ms": 1684,
		"dir": "RX",
		"text": "TLM seq=42 MISSION/ASCENT utc=1776607084.733 alt=12.34m ...",
		"rssi": -27.5,
		"parsed": parsed,
		"mode": "MISSION",
	}
	rec.update(overrides)
	return rec


def _write_jsonl(path: Path, records: List[Any]) -> None:
	with open(path, "w", encoding="utf-8") as fh:
		for item in records:
			if isinstance(item, str):
				fh.write(item + "\n")
			else:
				fh.write(json.dumps(item) + "\n")


class RowFromRecordTest(unittest.TestCase):
	def test_tlm_rx_record_produces_row_with_expected_fields(self) -> None:
		tool = _load_tool()
		row = tool._row_from_record(_binary_tlm_rx(), "session.jsonl")
		self.assertIsNotNone(row)
		assert row is not None
		self.assertEqual(row["file"], "session.jsonl")
		self.assertEqual(row["dt_ms"], "1684")
		self.assertEqual(row["rssi"], "-27.5")
		self.assertEqual(row["mode"], "MISSION")
		self.assertEqual(row["state"], "ASCENT")
		self.assertEqual(row["seq"], "42")
		self.assertEqual(row["alt_m"], "12.34")
		self.assertEqual(row["pressure_hpa"], "1019.48")
		self.assertEqual(row["sys_cal"], "3")
		self.assertEqual(row["mag_cal"], "2")
		self.assertEqual(row["tag_count"], "1")
		self.assertEqual(row["tags"], "23@1x2x3:46mm")
		self.assertEqual(row["utc_iso"], "2026-04-19T13:58:04.733Z")
		self.assertNotEqual(row["accel_mag_g"], "")
		self.assertAlmostEqual(float(row["accel_mag_g"]), 1.01477, places=3)
		# Slot 0 = de enige tag (id 23), dz_cm=3 → 0.03 m. Slot 1 moet
		# leeg zijn zodat Power BI er geen spurious 0-punt van maakt.
		self.assertEqual(row["tag0_id"], "23")
		self.assertEqual(row["tag0_dx_m"], "0.01")
		self.assertEqual(row["tag0_dy_m"], "0.02")
		self.assertEqual(row["tag0_dz_m"], "0.03")
		self.assertEqual(row["tag0_size_mm"], "46")
		self.assertEqual(row["tag1_id"], "")
		self.assertEqual(row["tag1_dz_m"], "")

	def test_two_tags_populate_both_slots_in_order(self) -> None:
		tool = _load_tool()
		rec = _binary_tlm_rx(
			parsed_overrides={
				"tag_count": 2,
				# Grootste tag eerst (Zero-side ``TagBuffer`` sorteert al
				# descending op pixel-size; we verifiëren enkel dat onze
				# CSV die volgorde **behoudt** i.p.v. te hersorteren).
				"tags": [
					{"id": 26, "dx_cm": 10, "dy_cm": -5, "dz_cm": 1234, "size_mm": 4500},
					{"id": 4, "dx_cm": 0, "dy_cm": 0, "dz_cm": 4567, "size_mm": 1100},
				],
			}
		)
		row = tool._row_from_record(rec, "f")
		assert row is not None
		self.assertEqual(row["tag_count"], "2")
		self.assertEqual(row["tag0_id"], "26")
		self.assertEqual(row["tag0_dz_m"], "12.34")
		self.assertEqual(row["tag0_size_mm"], "4500")
		self.assertEqual(row["tag1_id"], "4")
		self.assertEqual(row["tag1_dz_m"], "45.67")
		self.assertEqual(row["tag1_size_mm"], "1100")

	def test_no_tags_leaves_all_slot_columns_empty(self) -> None:
		tool = _load_tool()
		rec = _binary_tlm_rx(parsed_overrides={"tag_count": 0, "tags": []})
		row = tool._row_from_record(rec, "f")
		assert row is not None
		self.assertEqual(row["tags"], "")
		self.assertEqual(row["tag0_id"], "")
		self.assertEqual(row["tag0_dx_m"], "")
		self.assertEqual(row["tag0_dz_m"], "")
		self.assertEqual(row["tag0_size_mm"], "")
		self.assertEqual(row["tag1_id"], "")
		self.assertEqual(row["tag1_dz_m"], "")

	def test_non_rx_records_are_skipped(self) -> None:
		tool = _load_tool()
		self.assertIsNone(
			tool._row_from_record(
				{"dir": "TX", "text": "CAL GROUND", "dt_ms": 0}, "f"
			)
		)
		self.assertIsNone(
			tool._row_from_record(
				{"dir": "INFO", "text": "LOG_OPEN"}, "f"
			)
		)

	def test_rx_but_not_tlm_is_skipped(self) -> None:
		tool = _load_tool()
		rec = {
			"dir": "RX",
			"text": "OK ALT -0.29 1019.23",
			"parsed": {"kind": "ALT", "alt_m": -0.29, "pressure_hpa": 1019.23},
		}
		self.assertIsNone(tool._row_from_record(rec, "f"))

	def test_missing_rtc_leaves_t_empty_but_keeps_dt_ms(self) -> None:
		tool = _load_tool()
		rec = _binary_tlm_rx()
		rec.pop("t", None)  # ook zonder RTC
		row = tool._row_from_record(rec, "f")
		assert row is not None
		self.assertEqual(row["t"], "")
		self.assertEqual(row["dt_ms"], "1684")

	def test_text_tlm_from_older_test_mode_is_handled(self) -> None:
		tool = _load_tool()
		rec = {
			"dir": "RX",
			"dt_ms": 500,
			"rssi": -40.0,
			"text": "TLM 123 1.5 1020.0 21.1 180.0 -5.0 2.0 3",
			"parsed": {
				"kind": "TLM",
				"dt_ms": 123.0,
				"alt_m": 1.5,
				"pressure_hpa": 1020.0,
				"temp_c": 21.1,
				"heading_deg": 180.0,
				"roll_deg": -5.0,
				"pitch_deg": 2.0,
				"bno_sys_cal": 3,
			},
		}
		row = tool._row_from_record(rec, "f")
		assert row is not None
		self.assertEqual(row["alt_m"], "1.5")
		self.assertEqual(row["heading_deg"], "180")
		self.assertEqual(row["sys_cal"], "3")
		self.assertEqual(row["ax_g"], "")  # niet aanwezig in text-TLM
		self.assertEqual(row["utc_iso"], "")  # geen utc_seconds
		self.assertEqual(row["seq"], "")  # niet aanwezig
		self.assertEqual(row["accel_mag_g"], "")  # geen accel → leeg


class IterTlmRowsTest(unittest.TestCase):
	def test_malformed_json_line_is_skipped_with_warning(self) -> None:
		tool = _load_tool()
		src = io.StringIO(
			"\n".join(
				[
					json.dumps({"dir": "INFO", "text": "LOG_OPEN"}),
					"this is not JSON",
					json.dumps(_binary_tlm_rx()),
					"",
					json.dumps(
						{
							"dir": "RX",
							"parsed": {"kind": "TLM", "alt_m": 2.0},
						}
					),
				]
			)
		)
		err = io.StringIO()
		rows = list(tool.iter_tlm_rows(src, "stream.jsonl", on_error=err))
		self.assertEqual(len(rows), 2)
		self.assertIn("ongeldige JSON", err.getvalue())
		self.assertIn("stream.jsonl:2", err.getvalue())


class CliTest(unittest.TestCase):
	def test_single_input_defaults_to_sibling_csv(self) -> None:
		tool = _load_tool()
		with tempfile.TemporaryDirectory() as td:
			src = Path(td) / "cansat_20260419.jsonl"
			_write_jsonl(
				src,
				[
					{"dir": "INFO", "text": "LOG_OPEN"},
					{"dir": "TX", "text": "CAL GROUND", "dt_ms": 10},
					_binary_tlm_rx(),
					_binary_tlm_rx(
						parsed_overrides={"seq": 43, "alt_m": 13.45}
					),
				],
			)
			# stderr onderdrukken zodat test-output clean blijft
			old_err = sys.stderr
			sys.stderr = io.StringIO()
			try:
				rc = tool.main([str(src)])
			finally:
				sys.stderr = old_err
			self.assertEqual(rc, 0)
			out = Path(td) / "cansat_20260419.csv"
			self.assertTrue(out.exists())
			lines = out.read_text(encoding="utf-8").splitlines()
			# 1 header + 2 TLM-rijen
			self.assertEqual(len(lines), 3)
			header = lines[0].split(",")
			self.assertIn("seq", header)
			self.assertIn("alt_m", header)
			self.assertIn("tags", header)
			self.assertIn(",42,", lines[1])
			self.assertIn(",43,", lines[2])
			self.assertIn("13.45", lines[2])

	def test_stdout_output_with_dash(self) -> None:
		tool = _load_tool()
		with tempfile.TemporaryDirectory() as td:
			src = Path(td) / "s.jsonl"
			_write_jsonl(src, [_binary_tlm_rx()])

			buf_out = io.StringIO()
			buf_err = io.StringIO()
			old_out, old_err = sys.stdout, sys.stderr
			sys.stdout, sys.stderr = buf_out, buf_err
			try:
				rc = tool.main(["-o", "-", str(src)])
			finally:
				sys.stdout, sys.stderr = old_out, old_err
			self.assertEqual(rc, 0)
			lines = buf_out.getvalue().splitlines()
			self.assertEqual(len(lines), 2)  # header + 1 rij
			self.assertIn("seq", lines[0])
			self.assertIn(",42,", lines[1])
			# Geen "→" status-regel bij stdout-output
			self.assertNotIn("→", buf_err.getvalue())

	def test_multiple_inputs_require_explicit_out(self) -> None:
		tool = _load_tool()
		with tempfile.TemporaryDirectory() as td:
			a = Path(td) / "a.jsonl"
			b = Path(td) / "b.jsonl"
			_write_jsonl(a, [_binary_tlm_rx()])
			_write_jsonl(
				b,
				[_binary_tlm_rx(parsed_overrides={"seq": 99, "alt_m": 99.0})],
			)
			old_err = sys.stderr
			sys.stderr = io.StringIO()
			try:
				with self.assertRaises(SystemExit):
					tool.main([str(a), str(b)])
			finally:
				sys.stderr = old_err

			out = Path(td) / "merged.csv"
			old_err = sys.stderr
			sys.stderr = io.StringIO()
			try:
				rc = tool.main(["-o", str(out), str(a), str(b)])
			finally:
				sys.stderr = old_err
			self.assertEqual(rc, 0)
			lines = out.read_text(encoding="utf-8").splitlines()
			# 1 header + 2 rijen (één per input)
			self.assertEqual(len(lines), 3)
			self.assertIn("a.jsonl", lines[1])
			self.assertIn("b.jsonl", lines[2])
			self.assertIn(",99,", lines[2])


if __name__ == "__main__":
	unittest.main()
