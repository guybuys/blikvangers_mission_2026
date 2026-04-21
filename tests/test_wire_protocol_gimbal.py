"""Wire-protocol-tests voor GIMBAL-commando's + GET GIMBAL.

Behandelt:

* ``GIMBAL ON/OFF`` in CONFIG én MISSION/TEST (live-toggle toegestaan),
* ``GIMBAL HOME`` CONFIG-only met ``ERR GMB BUSY`` in andere modes,
* ``GET GIMBAL`` altijd toegestaan (alleen-lezen),
* ``ERR GMB NOHW`` wanneer er geen :class:`GimbalLoop` is (bv. geen BNO055
  of geen calibratie).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line
from cansat_hw.servos.controller import FakeRailDriver, ServoController
from cansat_hw.servos.gimbal_loop import GimbalLoop


def _fake_rfm() -> MagicMock:
	r = MagicMock()
	r.frequency_mhz = 433.0
	return r


def _make_servo(tmp: Path) -> ServoController:
	cal_path = tmp / "servo_calibration.json"
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
	return ServoController(FakeRailDriver(), cal_path, tuning_watchdog_s=3600.0)


def _make_loop(servo: ServoController) -> GimbalLoop:
	c1 = servo.calibration_for(1)
	c2 = servo.calibration_for(2)
	assert c1 is not None and c2 is not None
	return GimbalLoop(cal1=c1, cal2=c2)


class GimbalWireTest(unittest.TestCase):
	def test_get_gimbal_no_hw_returns_err(self) -> None:
		state = RadioRuntimeState()
		reply = handle_wire_line(_fake_rfm(), state, "GET GIMBAL")
		self.assertEqual(reply, b"ERR GMB NOHW")

	def test_gimbal_on_and_off_in_config(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			loop = _make_loop(servo)
			state = RadioRuntimeState()
			r = handle_wire_line(
				_fake_rfm(),
				state,
				"GIMBAL ON",
				servo=servo,
				gimbal_loop=loop,
			)
			self.assertEqual(r, b"OK GIMBAL ON")
			self.assertTrue(loop.enabled)
			r = handle_wire_line(
				_fake_rfm(),
				state,
				"GIMBAL OFF",
				servo=servo,
				gimbal_loop=loop,
			)
			self.assertEqual(r, b"OK GIMBAL OFF")
			self.assertFalse(loop.enabled)

	def test_gimbal_on_allowed_in_mission(self) -> None:
		# Operator moet de gimbal live kunnen stilleggen in MISSION.
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			loop = _make_loop(servo)
			state = RadioRuntimeState(mode="MISSION")
			r = handle_wire_line(
				_fake_rfm(),
				state,
				"GIMBAL OFF",
				servo=servo,
				gimbal_loop=loop,
			)
			self.assertEqual(r, b"OK GIMBAL OFF")

	def test_gimbal_home_rejected_in_mission(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			loop = _make_loop(servo)
			state = RadioRuntimeState(mode="MISSION")
			r = handle_wire_line(
				_fake_rfm(),
				state,
				"GIMBAL HOME",
				servo=servo,
				gimbal_loop=loop,
			)
			# MISSION-filter kapt 'GIMBAL HOME' af vóór het naar de handler
			# komt (het zit niet in _MISSION_ALWAYS_CMDS). Dat levert de
			# generieke ERR BUSY MISSION reply op — wat precies de
			# bedoelde safety-barrier is (geen rechtstreekse PWM-writes
			# tijdens vlucht).
			self.assertEqual(r, b"ERR BUSY MISSION")

	def test_gimbal_home_in_config_drives_servos_to_center(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			loop = _make_loop(servo)
			state = RadioRuntimeState()
			r = handle_wire_line(
				_fake_rfm(),
				state,
				"GIMBAL HOME",
				servo=servo,
				gimbal_loop=loop,
			)
			self.assertTrue(r.startswith(b"OK GIMBAL HOME US1=1500 US2=1600"))
			# Rate-limit-vertrekpunt synchroon met de controller:
			self.assertEqual(loop.status().last_us1, 1500)
			self.assertEqual(loop.status().last_us2, 1600)

	def test_get_gimbal_formats_status(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			loop = _make_loop(servo)
			loop.enable()
			loop.tick((0.5, -0.3, 9.7))  # simuleer één tick
			state = RadioRuntimeState()
			r = handle_wire_line(
				_fake_rfm(),
				state,
				"GET GIMBAL",
				servo=servo,
				gimbal_loop=loop,
			)
			text = r.decode("utf-8")
			self.assertTrue(text.startswith("OK GIMBAL "))
			# Compacte format: "OK GIMBAL on prim T=1 R=0 X=+50 Y=-30 U=<u1>/<u2>"
			self.assertIn(" on ", text)
			self.assertIn(" prim ", text)
			self.assertIn("T=1", text)
			self.assertIn("R=0", text)
			self.assertIn("X=+50", text)
			self.assertIn("Y=-30", text)
			self.assertIn("U=", text)
			self.assertIn("/", text)
			self.assertLessEqual(len(r), 60)

	def test_get_gimbal_fits_in_payload_under_stress(self) -> None:
		"""Worst-case format moet onder 60 B blijven.

		Regressietest voor een bug waar 3-digit PWM + 3-digit ticks de regel
		op 61 B bracht, waardoor de laatste digit van ``U2`` wegviel en de
		operator dacht dat de gimbal naar de ondergrens ging. We forceren
		hier grote waardes (caps op T/R/X/Y) via de ``status()``-mock en
		clamp-maxen (U1/U2) om zeker te zijn dat de regel binnen de payload
		past, ook na lange runs.
		"""
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			loop = _make_loop(servo)
			loop.enable()
			# Zet interne tellers en laatste waarden op hun max-display-
			# bereik om de pathologische format te produceren. We gebruiken
			# dunder-access omdat het niet door een echte tick-sequentie
			# haalbaar is om alles gelijktijdig te maxen.
			loop._ticks = 10 ** 6
			loop._rejected_samples = 10 ** 5
			loop._last_err_x = 50.0  # cap bij 1999 cg
			loop._last_err_y = -50.0
			loop._cur_us1 = 2500
			loop._cur_us2 = 2500
			state = RadioRuntimeState()
			r = handle_wire_line(
				_fake_rfm(),
				state,
				"GET GIMBAL",
				servo=servo,
				gimbal_loop=loop,
			)
			self.assertLessEqual(len(r), 60)
			text = r.decode("utf-8")
			self.assertIn("T=99999", text)
			self.assertIn("R=9999", text)
			self.assertIn("U=2500/2500", text)

	def test_gimbal_bad_subcommand(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			servo = _make_servo(Path(td))
			loop = _make_loop(servo)
			state = RadioRuntimeState()
			r = handle_wire_line(
				_fake_rfm(),
				state,
				"GIMBAL FOO",
				servo=servo,
				gimbal_loop=loop,
			)
			self.assertEqual(r, b"ERR GMB BAD")


if __name__ == "__main__":
	unittest.main()
