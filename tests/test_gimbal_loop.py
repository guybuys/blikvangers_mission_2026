"""Unit-tests voor :mod:`cansat_hw.servos.gimbal_loop`.

Focus: puur control-gedrag. Geen pigpio, geen BNO055 — we voeren de loop
synthetische zwaartekrachtvectoren en valideren dat:

* gates werken (disabled → ``None``, geen calibratie → ``None``, bad sample → ``None``),
* P-gain de juiste richting heeft en clamps respecteert,
* I-term restfouten wegregelt,
* deadband + norm-check + spike-check samples verwerpen,
* rate-limit delta's begrensd blijven,
* status-snapshot tellers consistent bijhoudt.
"""

from __future__ import annotations

import math
import unittest

from cansat_hw.servos.controller import ServoCal
from cansat_hw.servos.gimbal_loop import GimbalLoop


def _cal1() -> ServoCal:
	return ServoCal(gpio=13, min_us=1000, center_us=1500, max_us=2000, stow_us=1100)


def _cal2() -> ServoCal:
	return ServoCal(gpio=12, min_us=1100, center_us=1600, max_us=2100, stow_us=2000)


def _loop(**kw: float) -> GimbalLoop:
	return GimbalLoop(cal1=_cal1(), cal2=_cal2(), **kw)


class GimbalLoopGatesTest(unittest.TestCase):
	def test_disabled_returns_none(self) -> None:
		loop = _loop()
		self.assertIsNone(loop.tick((0.0, 0.0, 9.81)))
		self.assertFalse(loop.enabled)

	def test_missing_calibration_rejects(self) -> None:
		bad_cal = ServoCal(gpio=13, min_us=None, center_us=None, max_us=None)
		loop = GimbalLoop(cal1=bad_cal, cal2=_cal2())
		loop.enable()
		self.assertIsNone(loop.tick((0.0, 0.0, 9.81)))
		st = loop.status()
		self.assertEqual(st.rejected_samples, 1)

	def test_none_gravity_rejected(self) -> None:
		loop = _loop()
		loop.enable()
		self.assertIsNone(loop.tick(None))
		self.assertEqual(loop.status().rejected_samples, 1)

	def test_non_finite_rejected(self) -> None:
		loop = _loop()
		loop.enable()
		self.assertIsNone(loop.tick((float("nan"), 0.0, 9.81)))
		self.assertIsNone(loop.tick((0.0, float("inf"), 9.81)))
		self.assertEqual(loop.status().rejected_samples, 2)

	def test_norm_out_of_bounds_rejected(self) -> None:
		loop = _loop()
		loop.enable()
		# 30 g spike (saturation) — moet verworpen worden.
		self.assertIsNone(loop.tick((0.0, 0.0, 30.0)))
		# Bijna 0 g (freefall-achtig) — ook weg.
		self.assertIsNone(loop.tick((0.0, 0.0, 1.0)))
		self.assertEqual(loop.status().rejected_samples, 2)
		self.assertEqual(loop.status().good_samples, 0)


class GimbalLoopControlMathTest(unittest.TestCase):
	def test_level_sample_near_center(self) -> None:
		# g=(0,0,9.81) ⇒ geen fout, doel-PWM ≈ center. Rate-limit haalt
		# geen stap af (delta=0). Disable deadband to avoid snapping to 0.
		loop = _loop(alpha=0.0, kix=0.0, kiy=0.0)
		loop.enable()
		out = loop.tick((0.0, 0.0, 9.81))
		self.assertIsNotNone(out)
		assert out is not None
		us1, us2 = out
		self.assertEqual(us1, 1500)
		self.assertEqual(us2, 1600)

	def test_positive_gx_drives_servo1_below_center(self) -> None:
		# alpha=0 ⇒ LPF negeert historie; deadband=0 ⇒ alle fout door.
		# Met kx=200 en gx=+1 m/s² ⇒ target_us1 = 1500 + (-200)*1 = 1300.
		# max_us_step=20 zorgt dat we in één tick met -20 gaan.
		loop = _loop(alpha=0.0, kix=0.0, kiy=0.0, deadband_x=0.0, deadband_y=0.0)
		loop.enable()
		out = loop.tick((1.0, 0.0, 9.5))
		assert out is not None
		us1, _us2 = out
		self.assertEqual(us1, 1500 - 20)  # rate-limited step

	def test_swap_axes_routes_gy_to_servo1(self) -> None:
		# Met swap=True regelt servo1 op gy en servo2 op gx. Check aan
		# het teken: gy=+1 (bij swap) moet servo1 naar beneden duwen.
		loop = _loop(
			alpha=0.0,
			kix=0.0,
			kiy=0.0,
			deadband_x=0.0,
			deadband_y=0.0,
			swap_control_axes=True,
		)
		loop.enable()
		out = loop.tick((0.0, 1.0, 9.5))
		assert out is not None
		us1, us2 = out
		self.assertLess(us1, 1500)  # servo1 ↔ gy bij swap
		self.assertEqual(us2, 1600)

	def test_rate_limit_bounds_delta(self) -> None:
		loop = _loop(
			alpha=0.0,
			kix=0.0,
			kiy=0.0,
			deadband_x=0.0,
			deadband_y=0.0,
			max_us_step=5,
		)
		loop.enable()
		out = loop.tick((2.0, 0.0, 9.0))
		assert out is not None
		us1, _ = out
		# Target zou 1500 - 400 = 1100 zijn; rate-limit: slechts -5/tick.
		self.assertEqual(us1, 1495)

	def test_deadband_zeros_small_error(self) -> None:
		loop = _loop(
			alpha=0.0,
			kix=0.0,
			kiy=0.0,
			deadband_x=0.15,
			deadband_y=0.15,
		)
		loop.enable()
		out = loop.tick((0.05, -0.05, 9.81))
		assert out is not None
		us1, us2 = out
		# Binnen deadband: P-term = 0 en I-term off ⇒ geen beweging.
		self.assertEqual(us1, 1500)
		self.assertEqual(us2, 1600)

	def test_integral_winds_up_within_clamp(self) -> None:
		# Klein constant errortje, P=0 (buiten deadband niet). Met kix
		# actief moet de I-term doorgroeien tot integral_max en dan
		# stoppen. We meten |int_ex| indirect door te kijken dat us1
		# binnen z'n clamp blijft.
		loop = _loop(
			alpha=0.0,
			kx=0.0,
			ky=0.0,
			kix=50.0,
			kiy=0.0,
			deadband_x=0.0,
			deadband_y=0.0,
			integral_max=1.0,
			max_us_step=100,
			dt_fallback=0.1,
		)
		loop.enable()
		# 50 ticks van ex_raw=+1 ⇒ ∫=50 × 0.1 = 5, geclampt op 1.
		for _ in range(50):
			loop.tick((1.0, 0.0, 9.5))
		out = loop.tick((1.0, 0.0, 9.5))
		assert out is not None
		us1, _ = out
		# target_us1 ≈ 1500 + (-50)*1 = 1450; rate-limit niet bindend.
		# Met clamp op min_us 1000 blijft us1 ≥ 1000. Sanity:
		self.assertGreaterEqual(us1, 1000)
		self.assertLess(us1, 1500)

	def test_spike_rejected_without_breaking_lpf(self) -> None:
		loop = _loop(loop_max_dg=1.0)
		loop.enable()
		# Zaai de LPF met een stabiel sample.
		loop.tick((0.0, 0.0, 9.81))
		good_after_first = loop.status().good_samples
		# Gigantische sprong op gx → moet rejected worden.
		out = loop.tick((5.0, 0.0, 9.81))
		self.assertIsNone(out)
		st = loop.status()
		self.assertEqual(st.good_samples, good_after_first)
		self.assertEqual(st.rejected_samples, 1)


class GimbalLoopLifecycleTest(unittest.TestCase):
	def test_enable_resets_integrators(self) -> None:
		loop = _loop(alpha=0.0, deadband_x=0.0, deadband_y=0.0, dt_fallback=0.1)
		loop.enable()
		for _ in range(10):
			loop.tick((1.0, 0.0, 9.5))
		self.assertGreater(loop.status().ticks, 0)
		loop.enable()  # re-enable resets
		self.assertFalse(loop.primed)
		# Geen geïntegreerde fout meer: eerste tick na re-enable vertrekt
		# vanuit schone state.
		out = loop.tick((0.0, 0.0, 9.81))
		assert out is not None

	def test_home_pulses_returns_center_and_syncs_rate_limit(self) -> None:
		loop = _loop()
		self.assertEqual(loop.home_pulses(), (1500, 1600))
		# Na home: eerste tick met kleine fout moet vanaf center
		# vertrekken, niet vanaf None (dus geen onverwachte rate-limit-
		# sprong naar center vooraleer we erop reageren).
		loop.enable()
		out = loop.tick((0.1, 0.0, 9.8))
		self.assertIsNotNone(out)

	def test_status_fields_consistent(self) -> None:
		loop = _loop()
		loop.enable()
		loop.tick((0.0, 0.0, 9.81))
		st = loop.status()
		self.assertTrue(st.enabled)
		self.assertTrue(st.primed)
		self.assertEqual(st.ticks, 1)
		self.assertEqual(st.good_samples, 1)
		self.assertEqual(st.rejected_samples, 0)
		assert st.last_gx is not None
		self.assertAlmostEqual(st.last_gx, 0.0, places=6)
		self.assertAlmostEqual(math.sqrt((st.last_gz or 0.0) ** 2), 9.81, places=4)


if __name__ == "__main__":
	unittest.main()
