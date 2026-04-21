"""Preflight: BNO055-kalibratie check op sys+acc+mag (≥2)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from cansat_hw.radio.wire_protocol import (
	RadioRuntimeState,
	preflight_checks,
)


def _fake_rfm() -> MagicMock:
	r = MagicMock()
	r.frequency_mhz = 433.0
	return r


def _bme() -> MagicMock:
	b = MagicMock()
	b.read.return_value = (20.0, 1013.25, 50.0)
	return b


def _state() -> RadioRuntimeState:
	st = RadioRuntimeState()
	# Geef de state zinvolle waarden zodat TIME/GND/FRQ niet in missing staan.
	st.ground_hpa = 1013.25
	st.freq_set = True
	# SET TIME-equivalent: clock plausibel door op real-time epoch te laten staan
	# (zie preflight_checks._check_time_set).
	return st


def _bno(cs=(3, 3, 3, 3)) -> MagicMock:
	b = MagicMock()
	b.calibration_status.return_value = cs
	return b


class PreflightImuTest(unittest.TestCase):
	def test_full_cal_passes_and_reports_info(self) -> None:
		missing, info = preflight_checks(
			_state(), _fake_rfm(), _bme(), _bno((3, 3, 3, 3))
		)
		self.assertNotIn("IMU", missing)
		# Info-token ``BNO=s/a/m`` moet aanwezig zijn voor operator-diagnose.
		self.assertTrue(any(t.startswith("BNO=") for t in info), info)

	def test_low_accel_cal_triggers_imu(self) -> None:
		missing, _info = preflight_checks(
			_state(), _fake_rfm(), _bme(), _bno((3, 3, 1, 3))
		)
		self.assertIn("IMU", missing)

	def test_low_mag_cal_triggers_imu(self) -> None:
		missing, _info = preflight_checks(
			_state(), _fake_rfm(), _bme(), _bno((3, 3, 3, 1))
		)
		self.assertIn("IMU", missing)

	def test_sys_zero_still_triggers(self) -> None:
		missing, _info = preflight_checks(
			_state(), _fake_rfm(), _bme(), _bno((0, 3, 3, 3))
		)
		self.assertIn("IMU", missing)

	def test_threshold_two_is_acceptable(self) -> None:
		# 2/2 moet OK zijn (drempel = ≥2), anders is de runway in het veld
		# praktisch onhaalbaar zonder maandenlange calibratie per Zero.
		missing, _info = preflight_checks(
			_state(), _fake_rfm(), _bme(), _bno((2, 3, 2, 2))
		)
		self.assertNotIn("IMU", missing)

	def test_exception_is_imu_missing(self) -> None:
		bad = MagicMock()
		bad.calibration_status.side_effect = RuntimeError("I²C glitch")
		missing, _info = preflight_checks(_state(), _fake_rfm(), _bme(), bad)
		self.assertIn("IMU", missing)

	def test_no_sensor_is_imu_missing(self) -> None:
		missing, _info = preflight_checks(_state(), _fake_rfm(), _bme(), None)
		self.assertIn("IMU", missing)


if __name__ == "__main__":
	unittest.main()
