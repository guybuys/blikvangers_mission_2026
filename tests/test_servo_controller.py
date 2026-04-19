"""Servo-controller (Fase 12): rail/stow/park, tuning state, watchdog, JSON."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cansat_hw.servos.controller import (
	FakeRailDriver,
	PARK_WAIT_S,
	ServoCal,
	ServoController,
	TUNING_WATCHDOG_S,
)


def _full_cal_dict():
	return {
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


class _FakeClock:
	"""Manueel-gestuurde monotonic + sleep voor deterministische tests."""

	def __init__(self) -> None:
		self.t = 0.0
		self.sleeps = []

	def monotonic(self) -> float:
		return self.t

	def sleep(self, s: float) -> None:
		self.sleeps.append(float(s))
		self.t += float(s)


def _make_controller(tmp: Path, *, full_cal: bool = True):
	clock = _FakeClock()
	cal_path = tmp / "servo_calibration.json"
	if full_cal:
		cal_path.write_text(json.dumps(_full_cal_dict()), encoding="utf-8")
	driver = FakeRailDriver()
	ctrl = ServoController(
		driver,
		cal_path,
		sleep_func=clock.sleep,
		monotonic_func=clock.monotonic,
	)
	return ctrl, driver, clock


class CalibrationLoadSaveTest(unittest.TestCase):
	def test_loads_stow_us_field(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			ctrl, _, _ = _make_controller(Path(td))
			c1 = ctrl.calibration_for(1)
			self.assertIsNotNone(c1)
			assert c1 is not None  # narrow for type-checker
			self.assertEqual(c1.stow_us, 1100)
			self.assertTrue(ctrl.calibration_complete())

	def test_missing_json_uses_defaults(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			ctrl, _, _ = _make_controller(Path(td), full_cal=False)
			self.assertFalse(ctrl.calibration_complete())
			# GPIO defaults volgen ``servo_calibration.py`` (13/12).
			self.assertEqual(ctrl.calibration_for(1).gpio, 13)  # type: ignore[union-attr]
			self.assertEqual(ctrl.calibration_for(2).gpio, 12)  # type: ignore[union-attr]

	def test_save_roundtrip_preserves_stow(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			ctrl, _, _ = _make_controller(Path(td))
			# Wijzig stow1 in-memory en sla op.
			cal1 = ctrl.calibration_for(1)
			assert cal1 is not None
			cal1.stow_us = 1234
			ctrl.save_calibration()
			# Reload van disk moet de nieuwe waarde zien.
			data = json.loads(ctrl.cal_path.read_text(encoding="utf-8"))
			self.assertEqual(data["servo1"]["stow_us"], 1234)


class RailAndPulseTest(unittest.TestCase):
	def test_enable_disable_rail_idempotent(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			ctrl, drv, _ = _make_controller(Path(td))
			ctrl.enable_rail()
			ctrl.enable_rail()  # idempotent → geen extra rail-call
			self.assertTrue(ctrl.rail_on)
			rail_calls = [c for c in drv.calls if c[0] == "rail"]
			self.assertEqual(rail_calls, [("rail", True)])

	def test_disable_rail_zeroes_pulses_first(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			ctrl, drv, _ = _make_controller(Path(td))
			ctrl.enable_rail()
			drv.calls.clear()
			ctrl.disable_rail()
			# Eerst pulses op 0 voor BEIDE gpios, dan rail uit.
			self.assertEqual(drv.calls[0], ("pulse", 13, 0))
			self.assertEqual(drv.calls[1], ("pulse", 12, 0))
			self.assertEqual(drv.calls[2], ("rail", False))

	def test_stow_servo_uses_calibrated_us(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			ctrl, drv, _ = _make_controller(Path(td))
			ctrl.enable_rail()
			drv.calls.clear()
			us = ctrl.stow_servo(1)
			self.assertEqual(us, 1100)
			self.assertEqual(drv.calls[-1], ("pulse", 13, 1100))

	def test_stow_servo_returns_none_without_calibration(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			ctrl, _, _ = _make_controller(Path(td), full_cal=False)
			self.assertIsNone(ctrl.stow_servo(1))


class ParkSequenceTest(unittest.TestCase):
	def test_park_full_sequence(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			ctrl, drv, clock = _make_controller(Path(td))
			ok = ctrl.park_all()
			self.assertTrue(ok)
			# Verwachte volgorde: rail True → pulse 13=1100 → pulse 12=2000 →
			# sleep PARK_WAIT_S → pulses op 0 → rail False.
			seq = [c for c in drv.calls if c[0] in ("rail", "pulse")]
			self.assertEqual(seq[0], ("rail", True))
			self.assertEqual(seq[1], ("pulse", 13, 1100))
			self.assertEqual(seq[2], ("pulse", 12, 2000))
			self.assertEqual(seq[3], ("pulse", 13, 0))
			self.assertEqual(seq[4], ("pulse", 12, 0))
			self.assertEqual(seq[5], ("rail", False))
			self.assertEqual(clock.sleeps, [PARK_WAIT_S])
			self.assertFalse(ctrl.rail_on)

	def test_park_returns_false_if_stow_missing(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			ctrl, _, _ = _make_controller(Path(td), full_cal=False)
			self.assertFalse(ctrl.park_all())
			self.assertFalse(ctrl.rail_on)


class TuningTest(unittest.TestCase):
	def test_start_tuning_enables_rail_and_centers(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			ctrl, drv, _ = _make_controller(Path(td))
			ctrl.start_tuning(1)
			self.assertTrue(ctrl.rail_on)
			self.assertTrue(ctrl.tuning_active)
			# Eerste pulse = center_us (1500) op gpio 13.
			pulse_calls = [c for c in drv.calls if c[0] == "pulse"]
			self.assertEqual(pulse_calls[0], ("pulse", 13, 1500))

	def test_step_clamps_to_max(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			ctrl, _, _ = _make_controller(Path(td))
			ctrl.start_tuning(1)
			# 1500 + 100 * 6 = 2100 → clamp naar max=2000
			for _ in range(6):
				ctrl.step(100)
			self.assertEqual(ctrl.status().current_us[1], 2000)

	def test_set_us_outside_hw_range_via_clamp(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			ctrl, _, _ = _make_controller(Path(td))
			ctrl.start_tuning(1)
			# Buiten cal-min → clamp naar 1000 (min).
			ctrl.set_us(800)
			self.assertEqual(ctrl.status().current_us[1], 1000)

	def test_mark_stow_updates_calibration(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			ctrl, _, _ = _make_controller(Path(td))
			ctrl.start_tuning(1)
			ctrl.set_us(1234)
			marked = ctrl.mark("STOW")
			self.assertEqual(marked, 1234)
			self.assertEqual(ctrl.calibration_for(1).stow_us, 1234)  # type: ignore[union-attr]

	def test_select_requires_active_tuning(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			ctrl, _, _ = _make_controller(Path(td))
			with self.assertRaises(RuntimeError):
				ctrl.select(2)

	def test_stop_tuning_disables_rail(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			ctrl, _, _ = _make_controller(Path(td))
			ctrl.start_tuning(1)
			ctrl.stop_tuning()
			self.assertFalse(ctrl.tuning_active)
			self.assertFalse(ctrl.rail_on)
			self.assertIsNone(ctrl.status().selected)


class WatchdogTest(unittest.TestCase):
	def test_watchdog_fires_after_idle(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			ctrl, _, clock = _make_controller(Path(td))
			ctrl.start_tuning(1)
			# Tijd net onder watchdog → geen actie.
			clock.t += TUNING_WATCHDOG_S - 0.01
			self.assertIsNone(ctrl.tick())
			# Tijd voorbij watchdog → kappen.
			clock.t += 0.02
			out = ctrl.tick()
			self.assertEqual(out, "WATCHDOG")
			self.assertFalse(ctrl.tuning_active)
			self.assertFalse(ctrl.rail_on)

	def test_actions_reset_watchdog(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			ctrl, _, clock = _make_controller(Path(td))
			ctrl.start_tuning(1)
			# Tegen het einde een step doen; watchdog moet weer reset worden.
			clock.t += TUNING_WATCHDOG_S - 1.0
			ctrl.step(10)
			clock.t += 1.5
			self.assertIsNone(ctrl.tick())  # nog binnen het venster sinds reset.

	def test_shutdown_parks_when_rail_on(self) -> None:
		with tempfile.TemporaryDirectory() as td:
			ctrl, _, _ = _make_controller(Path(td))
			ctrl.enable_rail()
			ctrl.shutdown()
			self.assertFalse(ctrl.rail_on)

	def test_shutdown_stops_tuning_without_park(self) -> None:
		# In tuning hoeft er niet geparked te worden — stop_tuning rondt af.
		with tempfile.TemporaryDirectory() as td:
			ctrl, _, clock = _make_controller(Path(td))
			ctrl.start_tuning(1)
			ctrl.shutdown()
			self.assertFalse(ctrl.tuning_active)
			# Geen sleep gebeurd (we kwamen niet in park_all).
			self.assertEqual(clock.sleeps, [])


class ClampTest(unittest.TestCase):
	def test_servo_cal_clamp_uses_hardware_when_unset(self) -> None:
		c = ServoCal(gpio=13)
		self.assertEqual(c.clamp(100), 500)
		self.assertEqual(c.clamp(9999), 2500)


if __name__ == "__main__":
	unittest.main()
