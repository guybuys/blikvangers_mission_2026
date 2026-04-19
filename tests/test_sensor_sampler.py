"""Unit tests voor :mod:`cansat_hw.sensors.sampler` (Fase 7)."""

from __future__ import annotations

import math
import unittest
from typing import List, Optional, Tuple

from cansat_hw.sensors.sampler import (
	DEFAULT_ALT_STABLE_EPS_M,
	DEFAULT_FREEFALL_THRESH_G,
	SensorSampler,
	SensorSnapshot,
)


_G = 9.80665


class FakeBME280:
	"""Geeft scripted reads terug; faalt bij ``None`` in de queue."""

	def __init__(self, scripted: List[Optional[Tuple[float, float, float]]]):
		self._scripted = list(scripted)
		self.calls = 0

	def read(self) -> Tuple[float, float, float]:
		self.calls += 1
		if not self._scripted:
			raise IOError("BME exhausted")
		val = self._scripted.pop(0)
		if val is None:
			raise IOError("scripted failure")
		return val  # (temp_c, p_hpa, rh)


class FakeBNO055:
	"""Scripted euler/accel/cal; ``None`` => raise."""

	def __init__(
		self,
		eulers: List[Optional[Tuple[float, float, float]]],
		accels_ms2: List[Optional[Tuple[float, float, float]]],
		cals: List[Optional[Tuple[int, int, int, int]]] = None,
	):
		self._eulers = list(eulers)
		self._accels = list(accels_ms2)
		self._cals = list(cals or [(3, 3, 3, 3)] * max(len(eulers), len(accels_ms2)))

	def read_euler(self) -> Tuple[float, float, float]:
		if not self._eulers:
			raise IOError("euler exhausted")
		v = self._eulers.pop(0)
		if v is None:
			raise IOError("scripted euler fail")
		return v

	def read_linear_acceleration(self) -> Tuple[float, float, float]:
		if not self._accels:
			raise IOError("accel exhausted")
		v = self._accels.pop(0)
		if v is None:
			raise IOError("scripted accel fail")
		return v

	def calibration_status(self) -> Tuple[int, int, int, int]:
		if not self._cals:
			return (3, 3, 3, 3)
		v = self._cals.pop(0)
		if v is None:
			raise IOError("scripted cal fail")
		return v


class SnapshotPopulationTest(unittest.TestCase):
	def test_tick_populates_snapshot_from_both_sensors(self) -> None:
		bme = FakeBME280([(20.0, 1013.25, 50.0)])
		# 1 g op +Z, dus ‖a‖ = 1.0 g; we geven m/s² aan sampler.
		bno = FakeBNO055(
			eulers=[(123.4, -10.0, 5.0)],
			accels_ms2=[(0.0, 0.0, _G)],
			cals=[(3, 3, 2, 1)],
		)
		s = SensorSampler(bme280=bme, bno055=bno)

		snap = s.tick(ground_hpa=1013.25, now_monotonic=10.0, now_wall=1_700_000_000.0)

		self.assertAlmostEqual(snap.pressure_hpa, 1013.25, places=4)
		self.assertAlmostEqual(snap.temp_c, 20.0, places=4)
		self.assertAlmostEqual(snap.alt_m, 0.0, places=2)
		self.assertAlmostEqual(snap.heading_deg, 123.4, places=4)
		self.assertAlmostEqual(snap.az_g, 1.0, places=4)
		self.assertAlmostEqual(snap.accel_mag_g, 1.0, places=4)
		self.assertEqual(snap.sys_cal, 3)
		self.assertEqual(snap.mag_cal, 1)
		self.assertEqual(snap.samples_taken, 1)
		self.assertEqual(snap.bme_failures, 0)
		self.assertEqual(snap.bno_failures, 0)

	def test_tick_without_ground_leaves_alt_none(self) -> None:
		bme = FakeBME280([(20.0, 1013.25, 50.0)])
		s = SensorSampler(bme280=bme, bno055=None)
		snap = s.tick(ground_hpa=None, now_monotonic=0.0)
		self.assertIsNotNone(snap.pressure_hpa)
		self.assertIsNone(snap.alt_m)


class FailureHandlingTest(unittest.TestCase):
	def test_bme_failure_increments_counter_and_keeps_old_value(self) -> None:
		# Eerste read OK, tweede faalt — pressure moet op de oude waarde blijven.
		bme = FakeBME280([(20.0, 1010.0, 50.0), None])
		s = SensorSampler(bme280=bme, bno055=None)
		s.tick(ground_hpa=1013.25, now_monotonic=0.0)
		s.tick(ground_hpa=1013.25, now_monotonic=0.1)
		self.assertEqual(s.snapshot.bme_failures, 1)
		self.assertEqual(s.snapshot.pressure_hpa, 1010.0)

	def test_bno_failure_does_not_break_bme(self) -> None:
		bme = FakeBME280([(20.0, 1010.0, 50.0)])
		bno = FakeBNO055(eulers=[None], accels_ms2=[None], cals=[None])
		s = SensorSampler(bme280=bme, bno055=bno)
		snap = s.tick(ground_hpa=None, now_monotonic=0.0)
		self.assertEqual(snap.pressure_hpa, 1010.0)
		# 3 onafhankelijke fouten geteld (euler, accel, cal).
		self.assertEqual(snap.bno_failures, 3)


class RollingPeakTest(unittest.TestCase):
	def test_peak_tracks_max_in_window(self) -> None:
		# Window = 3; we voeren 1 g, 5 g, 2 g, 1 g — peak moet 5 g zijn,
		# en na nóg een 1 g tick (window verschuift, 5 g valt eruit) zakt
		# peak naar max(2,1,1) = 2 g.
		accels = [
			(0.0, 0.0, 1.0 * _G),
			(0.0, 0.0, 5.0 * _G),
			(0.0, 0.0, 2.0 * _G),
			(0.0, 0.0, 1.0 * _G),
			(0.0, 0.0, 1.0 * _G),
		]
		bno = FakeBNO055(eulers=[(0, 0, 0)] * 5, accels_ms2=accels)
		s = SensorSampler(bme280=None, bno055=bno, window_samples=3)
		for i in range(4):
			s.tick(now_monotonic=float(i) * 0.05)
		self.assertAlmostEqual(s.snapshot.peak_accel_g, 5.0, places=3)
		s.tick(now_monotonic=0.20)
		self.assertAlmostEqual(s.snapshot.peak_accel_g, 2.0, places=3)


class FreefallCounterTest(unittest.TestCase):
	def test_freefall_accumulates_and_resets(self) -> None:
		# Sample-pattern (g, dt=0.1 s):
		#   1.0  -> not freefall
		#   0.05 -> freefall start
		#   0.05 -> 0.1 s
		#   0.05 -> 0.2 s
		#   1.0  -> reset to 0
		#   0.05 -> nieuwe freefall start, 0 s
		accels = [
			(0.0, 0.0, 1.0 * _G),
			(0.0, 0.0, 0.05 * _G),
			(0.0, 0.0, 0.05 * _G),
			(0.0, 0.0, 0.05 * _G),
			(0.0, 0.0, 1.0 * _G),
			(0.0, 0.0, 0.05 * _G),
		]
		bno = FakeBNO055(eulers=[(0, 0, 0)] * 6, accels_ms2=accels)
		s = SensorSampler(
			bme280=None, bno055=bno, freefall_thresh_g=DEFAULT_FREEFALL_THRESH_G
		)
		s.tick(now_monotonic=0.0)
		self.assertEqual(s.snapshot.freefall_for_s, 0.0)
		s.tick(now_monotonic=0.1)
		self.assertAlmostEqual(s.snapshot.freefall_for_s, 0.0, places=4)  # juist gestart
		s.tick(now_monotonic=0.2)
		self.assertAlmostEqual(s.snapshot.freefall_for_s, 0.1, places=4)
		s.tick(now_monotonic=0.3)
		self.assertAlmostEqual(s.snapshot.freefall_for_s, 0.2, places=4)
		s.tick(now_monotonic=0.4)
		self.assertEqual(s.snapshot.freefall_for_s, 0.0)
		s.tick(now_monotonic=0.5)
		self.assertAlmostEqual(s.snapshot.freefall_for_s, 0.0, places=4)


class AltStabilityTest(unittest.TestCase):
	def test_alt_stable_accumulates_when_within_eps(self) -> None:
		# Constante druk -> alt = 0; met ground_hpa = 1013.25.
		bme = FakeBME280([(20.0, 1013.25, 50.0)] * 5)
		s = SensorSampler(
			bme280=bme,
			bno055=None,
			window_samples=10,
			alt_stable_eps_m=DEFAULT_ALT_STABLE_EPS_M,
		)
		s.tick(ground_hpa=1013.25, now_monotonic=0.0)
		s.tick(ground_hpa=1013.25, now_monotonic=1.0)
		s.tick(ground_hpa=1013.25, now_monotonic=2.0)
		s.tick(ground_hpa=1013.25, now_monotonic=3.0)
		# 3 s na de eerste sample, allemaal binnen eps -> stable_for_s = 3.0
		self.assertAlmostEqual(s.snapshot.alt_stable_for_s, 3.0, places=4)

	def test_alt_stable_resets_on_jump(self) -> None:
		# Eerst stabiel, dan plotse drukval (≈ 1 hPa = ~8 m hoogte) -> reset.
		bme = FakeBME280(
			[
				(20.0, 1013.25, 50.0),
				(20.0, 1013.25, 50.0),
				(20.0, 1012.0, 50.0),  # +8 m
				(20.0, 1012.0, 50.0),
			]
		)
		s = SensorSampler(bme280=bme, bno055=None, window_samples=4)
		s.tick(ground_hpa=1013.25, now_monotonic=0.0)
		s.tick(ground_hpa=1013.25, now_monotonic=1.0)
		self.assertGreater(s.snapshot.alt_stable_for_s, 0.0)
		s.tick(ground_hpa=1013.25, now_monotonic=2.0)
		self.assertEqual(s.snapshot.alt_stable_for_s, 0.0)
		# Na de jump weer stabiel -> begint opnieuw te tellen.
		s.tick(ground_hpa=1013.25, now_monotonic=3.0)
		self.assertGreaterEqual(s.snapshot.alt_stable_for_s, 0.0)


class StddevTest(unittest.TestCase):
	def test_stddev_zero_for_constant_signal(self) -> None:
		bno = FakeBNO055(
			eulers=[(0, 0, 0)] * 5,
			accels_ms2=[(0.0, 0.0, 1.0 * _G)] * 5,
		)
		s = SensorSampler(bme280=None, bno055=bno, window_samples=5)
		for i in range(5):
			s.tick(now_monotonic=float(i) * 0.05)
		self.assertAlmostEqual(s.snapshot.accel_stddev_g, 0.0, places=6)

	def test_stddev_nonzero_for_varying_signal(self) -> None:
		# ‖a‖ = 1, 2, 1, 2 -> mean = 1.5, var = 0.25, σ = 0.5
		bno = FakeBNO055(
			eulers=[(0, 0, 0)] * 4,
			accels_ms2=[
				(0.0, 0.0, 1.0 * _G),
				(0.0, 0.0, 2.0 * _G),
				(0.0, 0.0, 1.0 * _G),
				(0.0, 0.0, 2.0 * _G),
			],
		)
		s = SensorSampler(bme280=None, bno055=bno, window_samples=10)
		for i in range(4):
			s.tick(now_monotonic=float(i) * 0.05)
		self.assertAlmostEqual(s.snapshot.accel_stddev_g, 0.5, places=4)


class ResetWindowsTest(unittest.TestCase):
	def test_reset_clears_rolling_state_but_not_snapshot_values(self) -> None:
		bno = FakeBNO055(
			eulers=[(0, 0, 0)] * 3,
			accels_ms2=[(0.0, 0.0, 5.0 * _G)] * 3,
		)
		s = SensorSampler(bme280=None, bno055=bno, window_samples=5)
		for i in range(3):
			s.tick(now_monotonic=float(i) * 0.1)
		self.assertAlmostEqual(s.snapshot.peak_accel_g, 5.0, places=3)
		self.assertAlmostEqual(s.snapshot.az_g, 5.0, places=3)
		s.reset_windows()
		self.assertIsNone(s.snapshot.peak_accel_g)
		self.assertEqual(s.snapshot.freefall_for_s, 0.0)
		# Laatste momentane waardes blijven beschikbaar (handig voor TLM).
		self.assertAlmostEqual(s.snapshot.az_g, 5.0, places=3)


if __name__ == "__main__":
	unittest.main()
