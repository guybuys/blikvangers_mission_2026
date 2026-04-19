"""SERVO-commando dispatcher + SVO preflight (Fase 12)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from cansat_hw.radio.wire_protocol import (
	RadioRuntimeState,
	handle_wire_line,
	preflight_checks,
)
from cansat_hw.servos.controller import (
	FakeRailDriver,
	ServoController,
)


def _make_servo(tmp: Path, *, full_cal: bool = True) -> ServoController:
	cal_path = tmp / "servo_calibration.json"
	if full_cal:
		cal_path.write_text(
			json.dumps(
				{
					"servo1": {
						"gpio": 13,
						"min_us": 1000,
						"center_us": 1500,
						"max_us": 2000,
						"stow_us": 1100,
					},
					"servo2": {
						"gpio": 12,
						"min_us": 1100,
						"center_us": 1600,
						"max_us": 2100,
						"stow_us": 2000,
					},
					"saved_at": 0,
				}
			),
			encoding="utf-8",
		)
	# Ruime watchdog zodat tests niet vroegtijdig afkappen.
	return ServoController(FakeRailDriver(), cal_path, tuning_watchdog_s=3600.0)


def _fake_rfm() -> MagicMock:
	r = MagicMock()
	r.frequency_mhz = 433.0
	return r


class WireServoStatusTest(unittest.TestCase):
	def test_status_in_config(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			st = RadioRuntimeState()
			out = handle_wire_line(_fake_rfm(), st, "SERVO STATUS", servo=servo)
			self.assertTrue(out.startswith(b"OK SVO R="))
			self.assertIn(b"CAL=yes", out)

	def test_status_works_in_mission(self) -> None:
		# STATUS staat in de MISSION-allowlist (read-only).
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			st = RadioRuntimeState(mode="MISSION")
			out = handle_wire_line(_fake_rfm(), st, "SERVO STATUS", servo=servo)
			self.assertTrue(out.startswith(b"OK SVO"))

	def test_no_servo_returns_nohw(self) -> None:
		st = RadioRuntimeState()
		out = handle_wire_line(_fake_rfm(), st, "SERVO STATUS")
		self.assertEqual(out, b"ERR SVO NOHW")


class WireServoTuningTest(unittest.TestCase):
	def test_full_tuning_roundtrip(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			st = RadioRuntimeState()
			rfm = _fake_rfm()

			out = handle_wire_line(rfm, st, "SERVO START 1", servo=servo)
			self.assertTrue(out.startswith(b"OK SVO R=on T=on"))

			out = handle_wire_line(rfm, st, "SERVO STEP 50", servo=servo)
			self.assertEqual(out, b"OK SVO STEP 1550")

			out = handle_wire_line(rfm, st, "SERVO SET 1700", servo=servo)
			self.assertEqual(out, b"OK SVO SET 1700")

			out = handle_wire_line(rfm, st, "SERVO STOW_MARK", servo=servo)
			self.assertEqual(out, b"OK SVO STOW 1700")
			self.assertEqual(servo.calibration_for(1).stow_us, 1700)  # type: ignore[union-attr]

			out = handle_wire_line(rfm, st, "SERVO SAVE", servo=servo)
			self.assertTrue(out.startswith(b"OK SVO SAVE "))

			out = handle_wire_line(rfm, st, "SERVO STOP", servo=servo)
			self.assertEqual(out, b"OK SVO STOP")
			self.assertFalse(servo.tuning_active)

	def test_tuning_blocked_in_mission(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			st = RadioRuntimeState(mode="MISSION")
			out = handle_wire_line(_fake_rfm(), st, "SERVO START", servo=servo)
			# MISSION-allowlist filtert eerst (ERR BUSY MISSION).
			self.assertTrue(out.startswith(b"ERR BUSY"))

	def test_step_outside_active_tuning_errors(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			st = RadioRuntimeState()
			out = handle_wire_line(_fake_rfm(), st, "SERVO STEP 10", servo=servo)
			self.assertEqual(out, b"ERR SVO NOTUN")

	def test_step_huge_delta_rejected(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			st = RadioRuntimeState()
			handle_wire_line(_fake_rfm(), st, "SERVO START", servo=servo)
			out = handle_wire_line(_fake_rfm(), st, "SERVO STEP 999", servo=servo)
			self.assertEqual(out, b"ERR SVO BAD")


class WireServoParkTest(unittest.TestCase):
	def test_park_full_sequence_ok(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			st = RadioRuntimeState()
			out = handle_wire_line(_fake_rfm(), st, "SERVO PARK", servo=servo)
			self.assertEqual(out, b"OK SVO PARK")
			self.assertFalse(servo.rail_on)

	def test_park_without_calibration_errors(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td), full_cal=False)
			st = RadioRuntimeState()
			out = handle_wire_line(_fake_rfm(), st, "SERVO PARK", servo=servo)
			self.assertEqual(out, b"ERR SVO NOSTOW")

	def test_stow_requires_rail_on(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			st = RadioRuntimeState()
			out = handle_wire_line(_fake_rfm(), st, "SERVO STOW", servo=servo)
			self.assertEqual(out, b"ERR SVO RAILOFF")

	def test_disable_during_tuning_stops(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			st = RadioRuntimeState()
			handle_wire_line(_fake_rfm(), st, "SERVO START", servo=servo)
			out = handle_wire_line(_fake_rfm(), st, "SERVO DISABLE", servo=servo)
			self.assertEqual(out, b"OK SVO DISABLE TUNING_STOPPED")
			self.assertFalse(servo.tuning_active)


class WireServoHomeTest(unittest.TestCase):
	def test_home_writes_center_and_keeps_rail_on(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			st = RadioRuntimeState()
			out = handle_wire_line(_fake_rfm(), st, "SERVO HOME", servo=servo)
			self.assertEqual(out, b"OK SVO HOME US1=1500 US2=1600")
			self.assertTrue(servo.rail_on)

	def test_home_without_center_errors(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td), full_cal=False)
			st = RadioRuntimeState()
			out = handle_wire_line(_fake_rfm(), st, "SERVO HOME", servo=servo)
			self.assertEqual(out, b"ERR SVO NOCEN")

	def test_home_blocked_during_tuning(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			st = RadioRuntimeState()
			handle_wire_line(_fake_rfm(), st, "SERVO START", servo=servo)
			out = handle_wire_line(_fake_rfm(), st, "SERVO HOME", servo=servo)
			self.assertEqual(out, b"ERR SVO TUNON")

	def test_home_blocked_in_mission(self) -> None:
		# Eerste verdedigingslaag: MISSION-allowlist blokkeert al vóór
		# de SERVO-dispatcher (alleen STATUS staat in de allowlist). Dit
		# bewijst dat HOME nooit autonoom door de mission-state heen kan.
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			st = RadioRuntimeState(mode="MISSION")
			out = handle_wire_line(_fake_rfm(), st, "SERVO HOME", servo=servo)
			self.assertEqual(out, b"ERR BUSY MISSION")


class WireServoStatusTouchesWatchdogTest(unittest.TestCase):
	def test_status_resets_watchdog_during_tuning(self) -> None:
		"""Repro van het veld-probleem: na 60+ s inactief was de watchdog
		afgegaan. Nu mag een ``SERVO STATUS`` (Pico ``p``-toets) hem
		resetten zodat de operator de REPL kan refreshen zonder beweging."""
		clock_t = [0.0]
		with tempfile.TemporaryDirectory() as td:
			cal_path = Path(td) / "servo_calibration.json"
			cal_path.write_text(
				json.dumps(
					{
						"servo1": {"gpio": 13, "min_us": 1000, "center_us": 1500, "max_us": 2000, "stow_us": 1100},
						"servo2": {"gpio": 12, "min_us": 1100, "center_us": 1600, "max_us": 2100, "stow_us": 2000},
					}
				),
				encoding="utf-8",
			)
			servo = ServoController(
				FakeRailDriver(),
				cal_path,
				tuning_watchdog_s=2.0,
				monotonic_func=lambda: clock_t[0],
				sleep_func=lambda s: None,
			)
			st = RadioRuntimeState()
			handle_wire_line(_fake_rfm(), st, "SERVO START", servo=servo)
			# Tel 5 "rondes" van 1 fake-seconde: STATUS moet de watchdog
			# elke keer resetten zodat we ruim voorbij 2 s blijven leven.
			for _ in range(5):
				clock_t[0] += 1.0
				out = handle_wire_line(_fake_rfm(), st, "SERVO STATUS", servo=servo)
				self.assertTrue(out.startswith(b"OK SVO"))
				self.assertIsNone(servo.tick())
			self.assertTrue(servo.tuning_active)

	def test_status_does_not_reset_outside_tuning(self) -> None:
		"""``note_activity`` mag geen tuning-state introduceren als die
		niet draaide (hij is een no-op buiten tuning)."""
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			st = RadioRuntimeState()
			handle_wire_line(_fake_rfm(), st, "SERVO STATUS", servo=servo)
			self.assertFalse(servo.tuning_active)


class WireSvoPreflightTest(unittest.TestCase):
	def _bme(self):
		b = MagicMock()
		b.read.return_value = (20.0, 1013.25, 40.0)
		return b

	def _bno(self):
		b = MagicMock()
		b.calibration_status.return_value = (3, 3, 3, 3)
		return b

	def _state(self):
		st = RadioRuntimeState()
		st.time_synced = True
		st.freq_set = True
		st.ground_hpa = 1013.0
		return st

	def test_complete_calibration_passes(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			missing, _ = preflight_checks(
				self._state(), _fake_rfm(), self._bme(), self._bno(), servo=servo
			)
			self.assertNotIn("SVO", missing)

	def test_incomplete_calibration_flags_svo(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td), full_cal=False)
			missing, _ = preflight_checks(
				self._state(), _fake_rfm(), self._bme(), self._bno(), servo=servo
			)
			self.assertIn("SVO", missing)

	def test_active_tuning_flags_svo(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			servo.start_tuning(1)
			missing, _ = preflight_checks(
				self._state(), _fake_rfm(), self._bme(), self._bno(), servo=servo
			)
			self.assertIn("SVO", missing)

	def test_no_servo_skips_check(self) -> None:
		# Operator draait zonder servo-hardware; ``servo=None`` ⇒ check overslaan.
		missing, _ = preflight_checks(
			self._state(), _fake_rfm(), self._bme(), self._bno(), servo=None
		)
		self.assertNotIn("SVO", missing)


if __name__ == "__main__":
	unittest.main()
