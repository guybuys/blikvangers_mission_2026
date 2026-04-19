"""Radio draad-protocol — CONFIG / MISSION modi + MISSION-preflight."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


def _fake_bme(p_hpa: float = 1013.25) -> MagicMock:
	"""BME280-mock die ``read()`` als tuple (t, p, rh) teruggeeft."""
	b = MagicMock()
	b.read.return_value = (20.0, p_hpa, 40.0)
	b.read_wire_reply.return_value = "OK BME280 1013.25 20.0 40"
	return b


def _fake_bno(sys_cal: int = 3) -> MagicMock:
	b = MagicMock()
	b.calibration_status.return_value = (sys_cal, 3, 3, 3)
	b.read_wire_reply.return_value = "OK BNO055 1.0 2.0 3.0 %d/3/3/3" % sys_cal
	return b


def _valid_state():
	"""State waarmee preflight slaagt (afhankelijk van FS-checks op disk)."""
	from cansat_hw.radio.wire_protocol import RadioRuntimeState

	st = RadioRuntimeState()
	st.time_synced = True
	st.freq_set = True
	st.ground_hpa = 1013.2
	return st


class WireProtocolModeTest(unittest.TestCase):
	def test_set_mode_launch_alias_blocked_by_preflight(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		rfm.frequency_mhz = 434.0
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "SET MODE LAUNCH")
		self.assertTrue(out.startswith(b"ERR PRE "))
		self.assertEqual(st.mode, "CONFIG")

	def test_set_mode_mission_ok_when_preflight_clean(self) -> None:
		from cansat_hw.radio.wire_protocol import handle_wire_line

		rfm = MagicMock()
		rfm.frequency_mhz = 434.0
		st = _valid_state()
		out = handle_wire_line(rfm, st, "SET MODE MISSION", bme280=_fake_bme(), bno055=_fake_bno())
		self.assertEqual(out, b"OK MODE MISSION")
		self.assertEqual(st.mode, "MISSION")

	def test_busy_mission_blocks_bme280(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		b = MagicMock()
		rfm = MagicMock()
		st = RadioRuntimeState(mode="MISSION")
		out = handle_wire_line(rfm, st, "BME280", bme280=b)
		self.assertIn(b"ERR BUSY MISSION", out)

	def test_busy_mission_blocks_set_time(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="MISSION")
		out = handle_wire_line(rfm, st, "SET TIME 1700000000")
		self.assertIn(b"ERR BUSY MISSION", out)

	@patch("cansat_hw.radio.wire_protocol.apply_system_time_unix", return_value=(True, ""))
	def test_set_time_config_ok_sets_flag(self, _mock_apply: MagicMock) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="CONFIG")
		out = handle_wire_line(rfm, st, "SET TIME 1700000000.5")
		self.assertEqual(out, b"OK TIME")
		self.assertTrue(st.time_synced)

	def test_set_freq_sets_flag_and_defers_apply(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		rfm.frequency_mhz = 433.0
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "SET FREQ 434.0")
		self.assertTrue(out.startswith(b"OK FREQ"))
		self.assertTrue(st.freq_set)
		self.assertEqual(st.pending_freq_mhz, 434.0)
		# Handler mag de freq NIET direct zelf op de chip zetten — de caller
		# moet de reply eerst op de OUDE freq kunnen versturen.
		self.assertEqual(rfm.frequency_mhz, 433.0)

	def test_stop_radio_sets_exit_flag(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "STOP RADIO")
		self.assertEqual(out, b"OK STOP RADIO")
		self.assertTrue(st.exit_after_reply)

	def test_stop_radio_allowed_in_mission(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="MISSION")
		out = handle_wire_line(rfm, st, "STOP RADIO")
		self.assertEqual(out, b"OK STOP RADIO")
		self.assertTrue(st.exit_after_reply)

	def test_get_time_config(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "GET TIME")
		self.assertTrue(out.startswith(b"OK TIME "))
		parts = out.decode("utf-8").split()
		self.assertEqual(len(parts), 4)
		self.assertGreater(float(parts[2]), 1_700_000_000)
		self.assertRegex(parts[3], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

	def test_get_time_allowed_in_mission(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="MISSION")
		out = handle_wire_line(rfm, st, "GET TIME")
		self.assertTrue(out.startswith(b"OK TIME "))


class GroundAndTriggersTest(unittest.TestCase):
	def test_set_ground_ok_and_get(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "SET GROUND 1013.25")
		self.assertTrue(out.startswith(b"OK GROUND "))
		self.assertAlmostEqual(st.ground_hpa or 0.0, 1013.25, places=2)

		out2 = handle_wire_line(rfm, st, "GET GROUND")
		self.assertTrue(out2.startswith(b"OK GROUND "))
		self.assertNotIn(b"NONE", out2)

	def test_set_ground_out_of_range(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "SET GROUND 1500")
		self.assertEqual(out, b"ERR BAD GROUND")

	def test_get_ground_none_by_default(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "GET GROUND")
		self.assertEqual(out, b"OK GROUND NONE")

	def test_cal_ground_averages_bme280(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		bme = _fake_bme(1010.0)
		out = handle_wire_line(rfm, st, "CAL GROUND", bme280=bme)
		self.assertTrue(out.startswith(b"OK GROUND "))
		self.assertAlmostEqual(st.ground_hpa or 0.0, 1010.0, places=2)

	def test_cal_ground_does_warmup_then_average(self) -> None:
		"""CAL GROUND moet ``alt_prime_samples`` warm-ups doen vóór het middelt."""
		from cansat_hw.radio.wire_protocol import (
			GROUND_CAL_SAMPLES,
			RadioRuntimeState,
			handle_wire_line,
		)

		rfm = MagicMock()
		st = RadioRuntimeState(alt_prime_samples=5)
		bme = _fake_bme(1010.0)
		out = handle_wire_line(rfm, st, "CAL GROUND", bme280=bme)
		self.assertTrue(out.startswith(b"OK GROUND "))
		# 5 warm-ups + GROUND_CAL_SAMPLES gemiddelde-reads = 9 in totaal.
		self.assertEqual(bme.read.call_count, 5 + GROUND_CAL_SAMPLES)

	def test_cal_ground_uses_only_post_warmup_samples(self) -> None:
		"""De gerapporteerde druk moet enkel uit de samples NA de warm-up komen."""
		from cansat_hw.radio.wire_protocol import (
			GROUND_CAL_SAMPLES,
			RadioRuntimeState,
			handle_wire_line,
		)

		rfm = MagicMock()
		st = RadioRuntimeState(alt_prime_samples=3)
		bme = MagicMock()
		# Eerste 3 reads zijn warm-up (achterhaalde druk), daarna de echte ~1010.
		warmup_then_real = [(20.0, 1019.0, 40.0)] * 3 + [
			(20.0, 1010.0, 40.0)
		] * GROUND_CAL_SAMPLES
		bme.read.side_effect = warmup_then_real
		out = handle_wire_line(rfm, st, "CAL GROUND", bme280=bme)
		self.assertTrue(out.startswith(b"OK GROUND "))
		# Warm-up waarden mogen NIET in het gemiddelde meetellen.
		self.assertAlmostEqual(st.ground_hpa or 0.0, 1010.0, places=2)

	def test_cal_ground_no_sensor(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "CAL GROUND")
		self.assertEqual(out, b"ERR NO BME280")

	def test_set_trigger_ascent_is_in_meters(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "SET TRIGGER ASCENT 10")
		self.assertTrue(out.startswith(b"OK TRIG "))
		self.assertIn(b"m", out)
		self.assertAlmostEqual(st.trig_ascent_height_m, 10.0, places=2)

		out2 = handle_wire_line(rfm, st, "GET TRIGGERS")
		self.assertIn(b"ASC=10.0m", out2)
		self.assertIn(b"DEP=", out2)
		self.assertIn(b"LND=", out2)

	def test_set_trigger_deploy_is_in_meters(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "SET TRIGGER DEPLOY 4")
		self.assertTrue(out.startswith(b"OK TRIG DEPLOY "))
		self.assertIn(b"m", out)
		self.assertAlmostEqual(st.trig_deploy_descent_m, 4.0, places=2)

		out2 = handle_wire_line(rfm, st, "GET TRIGGERS")
		self.assertIn(b"DEP=4.0m", out2)

	def test_get_triggers_adds_hpa_when_ground_known(self) -> None:
		from cansat_hw.radio.wire_protocol import handle_wire_line

		rfm = MagicMock()
		st = _valid_state()
		st.trig_ascent_height_m = 5.0
		out = handle_wire_line(rfm, st, "GET TRIGGERS")
		self.assertIn(b"ASC=5.0m/", out)
		self.assertIn(b"hPa", out)

	def test_set_trigger_bad_value(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "SET TRIGGER ASCENT 9999")
		self.assertEqual(out, b"ERR BAD TRIGGER")
		out2 = handle_wire_line(rfm, st, "SET TRIGGER ASCENT 0.01")
		self.assertEqual(out2, b"ERR BAD TRIGGER")
		out3 = handle_wire_line(rfm, st, "SET TRIGGER DEPLOY 999")
		self.assertEqual(out3, b"ERR BAD TRIGGER")

	def test_height_to_dp_hpa_conversion(self) -> None:
		from cansat_hw.radio.wire_protocol import height_m_to_dp_hpa

		# Vuistregel rond zeeniveau: ~8.3 m per hPa.
		self.assertAlmostEqual(height_m_to_dp_hpa(10.0, 1013.25), 1.20, places=1)
		self.assertAlmostEqual(height_m_to_dp_hpa(5.0, 1013.25), 0.60, places=1)

	def test_pressure_to_altitude_is_inverse(self) -> None:
		from cansat_hw.radio.wire_protocol import height_m_to_dp_hpa, pressure_to_altitude_m

		gp = 1019.1
		for h in (0.0, 5.0, 10.0, 100.0, 500.0):
			p = gp - height_m_to_dp_hpa(h, gp)
			self.assertAlmostEqual(pressure_to_altitude_m(p, gp), h, places=2)


class AltitudeAndApogeeTest(unittest.TestCase):
	def test_get_alt_requires_bme(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		st.ground_hpa = 1013.25
		out = handle_wire_line(rfm, st, "GET ALT")
		self.assertEqual(out, b"ERR NO BME280")

	def test_get_alt_requires_ground(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "GET ALT", bme280=_fake_bme())
		self.assertEqual(out, b"ERR NO GROUND")

	def test_get_alt_returns_altitude_and_updates_apogee(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		st.ground_hpa = 1013.25
		# Huidige druk 1012.65 hPa ≈ ~5 m stijging.
		out = handle_wire_line(rfm, st, "GET ALT", bme280=_fake_bme(p_hpa=1012.65))
		self.assertTrue(out.startswith(b"OK ALT "))
		parts = out.decode("utf-8").split()
		self.assertEqual(len(parts), 4)
		alt = float(parts[2])
		self.assertAlmostEqual(alt, 5.0, delta=0.3)
		self.assertIsNotNone(st.max_alt_m)
		self.assertAlmostEqual(float(st.max_alt_m or 0.0), alt, delta=0.01)

	def test_apogee_keeps_maximum(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		st.ground_hpa = 1013.25
		handle_wire_line(rfm, st, "GET ALT", bme280=_fake_bme(p_hpa=1011.0))
		peak = st.max_alt_m
		handle_wire_line(rfm, st, "GET ALT", bme280=_fake_bme(p_hpa=1012.5))
		self.assertEqual(st.max_alt_m, peak)
		handle_wire_line(rfm, st, "GET ALT", bme280=_fake_bme(p_hpa=1008.0))
		self.assertGreater(float(st.max_alt_m or 0.0), float(peak or 0.0))

	def test_get_apogee_none_and_reset(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		self.assertEqual(handle_wire_line(rfm, st, "GET APOGEE"), b"OK APOGEE NONE")

		st.ground_hpa = 1013.25
		handle_wire_line(rfm, st, "GET ALT", bme280=_fake_bme(p_hpa=1011.0))
		out = handle_wire_line(rfm, st, "GET APOGEE")
		self.assertTrue(out.startswith(b"OK APOGEE "))
		self.assertNotIn(b"NONE", out)

		self.assertEqual(handle_wire_line(rfm, st, "RESET APOGEE"), b"OK APOGEE RESET")
		self.assertIsNone(st.max_alt_m)
		self.assertEqual(handle_wire_line(rfm, st, "GET APOGEE"), b"OK APOGEE NONE")

	def test_get_alt_allowed_in_mission(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="MISSION")
		st.ground_hpa = 1013.25
		out = handle_wire_line(rfm, st, "GET ALT", bme280=_fake_bme(p_hpa=1011.0))
		self.assertTrue(out.startswith(b"OK ALT "))

	def test_reset_apogee_blocked_in_mission(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="MISSION")
		out = handle_wire_line(rfm, st, "RESET APOGEE")
		self.assertEqual(out, b"ERR BUSY MISSION")


class PreflightTest(unittest.TestCase):
	@patch("cansat_hw.radio.wire_protocol._check_time_set", return_value=False)
	def test_preflight_all_missing(self, _mock_time: MagicMock) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		rfm.frequency_mhz = 433.0
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "PREFLIGHT")
		self.assertTrue(out.startswith(b"ERR PRE "))
		txt = out.decode("utf-8")
		self.assertIn("TIME", txt)
		self.assertIn("GND", txt)
		self.assertIn("BME", txt)
		self.assertIn("IMU", txt)
		self.assertIn("FRQ", txt)

	def test_preflight_bno_sys_below_min(self) -> None:
		from cansat_hw.radio.wire_protocol import handle_wire_line

		rfm = MagicMock()
		rfm.frequency_mhz = 434.0
		st = _valid_state()
		out = handle_wire_line(
			rfm, st, "PREFLIGHT", bme280=_fake_bme(), bno055=_fake_bno(sys_cal=0)
		)
		self.assertTrue(out.startswith(b"ERR PRE "))
		self.assertIn(b"IMU", out)

	def test_preflight_logdir_missing(self) -> None:
		from cansat_hw.radio.wire_protocol import handle_wire_line

		rfm = MagicMock()
		rfm.frequency_mhz = 434.0
		st = _valid_state()
		out = handle_wire_line(
			rfm,
			st,
			"PREFLIGHT",
			bme280=_fake_bme(),
			bno055=_fake_bno(),
			photo_dir="/nonexistent/path/for/preflight/test",
		)
		self.assertTrue(out.startswith(b"ERR PRE "))
		self.assertIn(b"LOG", out)

	def test_preflight_ok_includes_trigger_info(self) -> None:
		from cansat_hw.radio.wire_protocol import handle_wire_line

		rfm = MagicMock()
		rfm.frequency_mhz = 434.0
		st = _valid_state()
		out = handle_wire_line(
			rfm, st, "PREFLIGHT", bme280=_fake_bme(), bno055=_fake_bno()
		)
		self.assertTrue(out.startswith(b"OK PRE ALL"))
		self.assertIn(b"GND=", out)
		self.assertIn(b"ASC=", out)
		self.assertIn(b"m", out)


class TestModeTest(unittest.TestCase):
	def _test_state(self) -> "object":
		"""State die minimale preflight (TIME+GND+BME) haalt."""
		from cansat_hw.radio.wire_protocol import RadioRuntimeState

		st = RadioRuntimeState()
		st.time_synced = True
		st.ground_hpa = 1013.2
		return st

	def test_set_mode_test_default_duration(self) -> None:
		from cansat_hw.radio.wire_protocol import TEST_MODE_DEFAULT_S, handle_wire_line

		rfm = MagicMock()
		st = self._test_state()
		out = handle_wire_line(rfm, st, "SET MODE TEST", bme280=_fake_bme())
		self.assertTrue(out.startswith(b"OK MODE TEST "))
		self.assertEqual(st.mode, "TEST")
		self.assertAlmostEqual(st.test_duration_s, TEST_MODE_DEFAULT_S, places=3)
		self.assertIsNotNone(st.test_deadline_monotonic)
		self.assertIsNotNone(st.test_next_tlm_monotonic)

	def test_set_mode_test_explicit_duration(self) -> None:
		from cansat_hw.radio.wire_protocol import handle_wire_line

		rfm = MagicMock()
		st = self._test_state()
		out = handle_wire_line(rfm, st, "SET MODE TEST 5", bme280=_fake_bme())
		self.assertEqual(out, b"OK MODE TEST 5")
		self.assertAlmostEqual(st.test_duration_s, 5.0, places=3)

	def test_set_mode_test_clamps_too_high(self) -> None:
		from cansat_hw.radio.wire_protocol import TEST_MODE_MAX_S, handle_wire_line

		rfm = MagicMock()
		st = self._test_state()
		out = handle_wire_line(rfm, st, "SET MODE TEST 9999", bme280=_fake_bme())
		self.assertTrue(out.startswith(b"OK MODE TEST"))
		self.assertAlmostEqual(st.test_duration_s, TEST_MODE_MAX_S, places=3)

	def test_set_mode_test_clamps_too_low(self) -> None:
		from cansat_hw.radio.wire_protocol import TEST_MODE_MIN_S, handle_wire_line

		rfm = MagicMock()
		st = self._test_state()
		out = handle_wire_line(rfm, st, "SET MODE TEST 0.1", bme280=_fake_bme())
		self.assertTrue(out.startswith(b"OK MODE TEST"))
		self.assertAlmostEqual(st.test_duration_s, TEST_MODE_MIN_S, places=3)

	def test_set_mode_test_rejects_non_number(self) -> None:
		from cansat_hw.radio.wire_protocol import handle_wire_line

		rfm = MagicMock()
		st = self._test_state()
		out = handle_wire_line(rfm, st, "SET MODE TEST tien", bme280=_fake_bme())
		self.assertEqual(out, b"ERR BAD TEST")
		self.assertEqual(st.mode, "CONFIG")

	@patch(
		"cansat_hw.radio.wire_protocol._check_time_set",
		return_value=False,
	)
	def test_set_mode_test_blocked_by_minimal_preflight_time(
		self, _mock: MagicMock
	) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "SET MODE TEST 5", bme280=_fake_bme())
		self.assertTrue(out.startswith(b"ERR PRE"))
		self.assertIn(b"TIME", out)
		self.assertIn(b"GND", out)
		self.assertEqual(st.mode, "CONFIG")

	def test_set_mode_test_skips_full_preflight_fields(self) -> None:
		"""TEST vereist alleen TIME+GND+BME, dus FRQ/IMU/DSK/LOG/GIM NIET."""
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		st.time_synced = True
		st.ground_hpa = 1013.2
		# freq_set is niet gezet en bno055 is None — toch moet TEST starten.
		out = handle_wire_line(rfm, st, "SET MODE TEST 3", bme280=_fake_bme(), bno055=None)
		self.assertTrue(out.startswith(b"OK MODE TEST"))
		self.assertEqual(st.mode, "TEST")

	def test_test_mode_blocks_set_time(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="TEST")
		out = handle_wire_line(rfm, st, "SET TIME 1700000000")
		self.assertEqual(out, b"ERR BUSY TEST")

	def test_test_mode_blocks_set_mode_config_no_abort(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="TEST")
		out = handle_wire_line(rfm, st, "SET MODE CONFIG")
		self.assertEqual(out, b"ERR BUSY TEST")
		self.assertEqual(st.mode, "TEST")

	def test_test_mode_blocks_stop_radio_no_abort(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="TEST")
		out = handle_wire_line(rfm, st, "STOP RADIO")
		self.assertEqual(out, b"ERR BUSY TEST")
		self.assertFalse(st.exit_after_reply)

	def test_test_mode_allows_ping_and_get_time(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="TEST")
		self.assertEqual(handle_wire_line(rfm, st, "PING"), b"OK PING")
		self.assertTrue(handle_wire_line(rfm, st, "GET TIME").startswith(b"OK TIME "))

	def test_get_mode_returns_test_with_duration(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="TEST", test_duration_s=10.0)
		out = handle_wire_line(rfm, st, "GET MODE")
		self.assertTrue(out.startswith(b"OK MODE TEST "))

	def test_cannot_start_test_from_mission(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="MISSION")
		out = handle_wire_line(rfm, st, "SET MODE TEST 5")
		# MISSION blokkeert alle commando's behalve de allow-list.
		self.assertEqual(out, b"ERR BUSY MISSION")
		self.assertEqual(st.mode, "MISSION")


class TestModeTickTest(unittest.TestCase):
	def test_tick_sends_tlm_when_interval_elapsed(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, test_mode_tick

		st = RadioRuntimeState(mode="TEST")
		st.test_start_monotonic = 100.0
		st.test_deadline_monotonic = 110.0
		st.test_next_tlm_monotonic = 100.5
		send_tlm, end_test = test_mode_tick(st, now_monotonic=100.6)
		self.assertTrue(send_tlm)
		self.assertFalse(end_test)

	def test_tick_signals_end_after_deadline(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, test_mode_tick

		st = RadioRuntimeState(mode="TEST")
		st.test_start_monotonic = 100.0
		st.test_deadline_monotonic = 110.0
		st.test_next_tlm_monotonic = 100.5
		send_tlm, end_test = test_mode_tick(st, now_monotonic=110.01)
		self.assertFalse(send_tlm)
		self.assertTrue(end_test)

	def test_tick_inactive_outside_test_mode(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, test_mode_tick

		st = RadioRuntimeState(mode="CONFIG")
		send_tlm, end_test = test_mode_tick(st, now_monotonic=1e9)
		self.assertFalse(send_tlm)
		self.assertFalse(end_test)

	def test_advance_tlm_pushes_deadline_forward(self) -> None:
		from cansat_hw.radio.wire_protocol import (
			RadioRuntimeState,
			TEST_MODE_TLM_INTERVAL_S,
			test_mode_advance_tlm,
		)

		st = RadioRuntimeState(mode="TEST")
		test_mode_advance_tlm(st, now_monotonic=100.0)
		self.assertAlmostEqual(
			st.test_next_tlm_monotonic, 100.0 + TEST_MODE_TLM_INTERVAL_S, places=3
		)

	def test_end_resets_state_to_config(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, test_mode_end

		st = RadioRuntimeState(
			mode="TEST",
		)
		st.test_duration_s = 10.0
		st.test_start_monotonic = 1.0
		st.test_deadline_monotonic = 11.0
		st.test_next_tlm_monotonic = 2.0
		st.test_dest_node = 100
		test_mode_end(st)
		self.assertEqual(st.mode, "CONFIG")
		self.assertIsNone(st.test_duration_s)
		self.assertIsNone(st.test_deadline_monotonic)
		self.assertIsNone(st.test_dest_node)


class TelemetryPacketTest(unittest.TestCase):
	def test_binary_frame_is_exact_size_and_first_byte(self) -> None:
		from cansat_hw.radio.wire_protocol import (
			MAX_PAYLOAD,
			RadioRuntimeState,
			build_telemetry_packet,
		)
		from cansat_hw.telemetry.codec import RECORD_TLM, is_binary_packet

		st = RadioRuntimeState(mode="TEST")
		st.ground_hpa = 1013.2
		st.test_start_monotonic = 0.0
		bme = _fake_bme(1005.0)
		bno = _fake_bno(3)
		bno.read_euler = MagicMock(return_value=(123.4, 5.6, -7.8))
		bno.read_linear_acceleration = MagicMock(return_value=(0.1, 0.2, 0.3))
		pkt = build_telemetry_packet(st, bme, bno, now_monotonic=3.456)
		self.assertEqual(len(pkt), MAX_PAYLOAD)
		self.assertEqual(pkt[0], RECORD_TLM)
		self.assertTrue(is_binary_packet(pkt[0]))

	def test_round_trip_decodes_sensor_values(self) -> None:
		from cansat_hw.radio.wire_protocol import (
			RadioRuntimeState,
			build_telemetry_packet,
		)
		from cansat_hw.telemetry.codec import (
			MODE_TEST,
			STATE_DEPLOYED,
			unpack_tlm,
		)

		st = RadioRuntimeState(mode="TEST")
		st.ground_hpa = 1013.2
		st.test_start_monotonic = 0.0
		bme = _fake_bme(1005.0)  # ~70 m boven grond.
		bno = _fake_bno(3)
		bno.read_euler = MagicMock(return_value=(123.4, 5.6, -7.8))
		bno.read_linear_acceleration = MagicMock(return_value=(0.0, 0.0, 9.80665))
		pkt = build_telemetry_packet(st, bme, bno, now_monotonic=1.0)
		f = unpack_tlm(pkt)
		self.assertEqual(f.mode, MODE_TEST)
		self.assertEqual(f.state, STATE_DEPLOYED)
		self.assertGreater(f.alt_m or 0, 50)  # ~70m
		self.assertAlmostEqual(f.pressure_hpa, 1005.0, places=1)
		self.assertAlmostEqual(f.heading_deg, 123.4, places=1)
		self.assertAlmostEqual(f.roll_deg, 5.6, places=1)
		self.assertAlmostEqual(f.pitch_deg, -7.8, places=1)
		# Lineaire acceleratie z=9.81 m/s² => 1.0 g.
		self.assertAlmostEqual(f.az_g, 1.0, places=2)
		self.assertEqual(f.sys_cal, 3)

	def test_seq_increments_per_call_and_wraps_at_uint16(self) -> None:
		from cansat_hw.radio.wire_protocol import (
			RadioRuntimeState,
			build_telemetry_packet,
		)
		from cansat_hw.telemetry.codec import unpack_tlm

		st = RadioRuntimeState(mode="TEST")
		st.test_start_monotonic = 0.0
		seqs = [
			unpack_tlm(build_telemetry_packet(st, None, None)).seq
			for _ in range(3)
		]
		self.assertEqual(seqs, [1, 2, 3])

		# Forceer wrap.
		st.tlm_seq = 0xFFFE
		s1 = unpack_tlm(build_telemetry_packet(st, None, None)).seq
		s2 = unpack_tlm(build_telemetry_packet(st, None, None)).seq
		self.assertEqual(s1, 0xFFFF)
		self.assertEqual(s2, 0)

	def test_missing_sensors_decode_as_none(self) -> None:
		from cansat_hw.radio.wire_protocol import (
			RadioRuntimeState,
			build_telemetry_packet,
		)
		from cansat_hw.telemetry.codec import unpack_tlm

		st = RadioRuntimeState(mode="TEST")
		st.test_start_monotonic = 0.0
		f = unpack_tlm(build_telemetry_packet(st, None, None, now_monotonic=1.0))
		self.assertIsNone(f.alt_m)
		self.assertIsNone(f.pressure_hpa)
		self.assertIsNone(f.temp_c)
		self.assertIsNone(f.heading_deg)
		self.assertIsNone(f.sys_cal)

	def test_no_ground_reference_yields_none_alt_but_has_pressure(self) -> None:
		from cansat_hw.radio.wire_protocol import (
			RadioRuntimeState,
			build_telemetry_packet,
		)
		from cansat_hw.telemetry.codec import unpack_tlm

		st = RadioRuntimeState(mode="TEST")
		st.test_start_monotonic = 0.0
		bme = _fake_bme(1013.0)
		f = unpack_tlm(build_telemetry_packet(st, bme, None, now_monotonic=0.0))
		self.assertIsNone(f.alt_m)
		self.assertAlmostEqual(f.pressure_hpa, 1013.0, places=1)
		self.assertAlmostEqual(f.temp_c, 20.0, places=1)

	def test_utc_seconds_uses_wall_clock(self) -> None:
		from cansat_hw.radio.wire_protocol import (
			RadioRuntimeState,
			build_telemetry_packet,
		)
		from cansat_hw.telemetry.codec import unpack_tlm

		st = RadioRuntimeState(mode="TEST")
		with patch(
			"cansat_hw.radio.wire_protocol.time_mod.time",
			return_value=1_700_000_123.456,
		):
			f = unpack_tlm(build_telemetry_packet(st, None, None))
		self.assertEqual(f.utc_seconds, 1_700_000_123)
		self.assertEqual(f.utc_ms, 456)


class AltPrimeTest(unittest.TestCase):
	def test_default_alt_prime_is_set(self) -> None:
		from cansat_hw.radio.wire_protocol import (
			DEFAULT_ALT_PRIME,
			RadioRuntimeState,
		)

		st = RadioRuntimeState()
		self.assertEqual(st.alt_prime_samples, DEFAULT_ALT_PRIME)

	def test_get_alt_does_priming_burst(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		st.ground_hpa = 1013.25
		st.alt_prime_samples = 5
		bme = _fake_bme(p_hpa=1011.0)
		out = handle_wire_line(rfm, st, "GET ALT", bme280=bme)
		self.assertTrue(out.startswith(b"OK ALT "))
		# 5 reads moeten gebeurd zijn — laatste levert de gerapporteerde druk.
		self.assertEqual(bme.read.call_count, 5)

	def test_get_alt_with_prime_one_does_single_read(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(alt_prime_samples=1)
		st.ground_hpa = 1013.25
		bme = _fake_bme(p_hpa=1011.0)
		handle_wire_line(rfm, st, "GET ALT", bme280=bme)
		self.assertEqual(bme.read.call_count, 1)

	def test_get_alt_uses_last_sample_for_apogee(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(alt_prime_samples=3)
		st.ground_hpa = 1013.25
		bme = MagicMock()
		# Simuleer een dalend druk-verloop tijdens de burst — laatste read moet tellen.
		bme.read.side_effect = [
			(20.0, 1013.0, 40.0),
			(20.0, 1012.5, 40.0),
			(20.0, 1010.0, 40.0),
		]
		out = handle_wire_line(rfm, st, "GET ALT", bme280=bme)
		self.assertTrue(out.startswith(b"OK ALT "))
		parts = out.decode("utf-8").split()
		# Druk in de reply moet 1010.0 zijn (laatste sample).
		self.assertAlmostEqual(float(parts[3]), 1010.0, places=2)
		# Apogee moet ook bij die laatste (hoogste) hoogte horen.
		self.assertAlmostEqual(float(st.min_pressure_hpa or 0.0), 1010.0, places=2)

	def test_get_alt_prime_returns_current_value(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(alt_prime_samples=7)
		out = handle_wire_line(rfm, st, "GET ALT PRIME")
		self.assertEqual(out, b"OK ALT PRIME 7")

	def test_set_alt_prime_in_config(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "SET ALT PRIME 8")
		self.assertEqual(out, b"OK ALT PRIME 8")
		self.assertEqual(st.alt_prime_samples, 8)

	def test_set_alt_prime_rejects_out_of_range(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		self.assertEqual(handle_wire_line(rfm, st, "SET ALT PRIME 0"), b"ERR BAD PRIME")
		self.assertEqual(handle_wire_line(rfm, st, "SET ALT PRIME 99"), b"ERR BAD PRIME")
		self.assertEqual(handle_wire_line(rfm, st, "SET ALT PRIME abc"), b"ERR BAD PRIME")

	def test_set_alt_prime_blocked_in_mission(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="MISSION")
		out = handle_wire_line(rfm, st, "SET ALT PRIME 4")
		# MISSION-busy check vangt het al voor de inner CONFIG-check.
		self.assertEqual(out, b"ERR BUSY MISSION")

	def test_set_alt_prime_blocked_in_test(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="TEST")
		out = handle_wire_line(rfm, st, "SET ALT PRIME 4")
		self.assertEqual(out, b"ERR BUSY TEST")

	def test_get_alt_prime_allowed_in_mission(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="MISSION", alt_prime_samples=5)
		out = handle_wire_line(rfm, st, "GET ALT PRIME")
		self.assertEqual(out, b"OK ALT PRIME 5")


class IirFilterTest(unittest.TestCase):
	def test_get_iir_default_cfg_and_mis(self) -> None:
		from cansat_hw.radio.wire_protocol import (
			DEFAULT_CONFIG_IIR,
			DEFAULT_MISSION_IIR,
			RadioRuntimeState,
			handle_wire_line,
		)

		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "GET IIR")
		self.assertTrue(out.startswith(b"OK IIR "))
		text = out.decode("utf-8")
		self.assertIn("CFG=%d" % DEFAULT_CONFIG_IIR, text)
		self.assertIn("MIS=%d" % DEFAULT_MISSION_IIR, text)

	def test_get_iir_reports_chip_value_when_available(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		bme = _fake_bme()
		bme.iir_filter = 8
		out = handle_wire_line(rfm, st, "GET IIR", bme280=bme)
		self.assertIn(b"OK IIR 8", out)

	def test_set_iir_updates_config_preset_and_applies(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		bme = _fake_bme()
		bme.set_iir_filter.return_value = 8
		out = handle_wire_line(rfm, st, "SET IIR 8", bme280=bme)
		self.assertEqual(out, b"OK IIR 8")
		self.assertEqual(st.config_iir, 8)
		bme.set_iir_filter.assert_called_with(8)

	def test_set_iir_without_bme_still_updates_preset(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "SET IIR 2")
		self.assertEqual(out, b"OK IIR 2")
		self.assertEqual(st.config_iir, 2)

	def test_set_iir_rejects_invalid_coefficient(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		self.assertEqual(handle_wire_line(rfm, st, "SET IIR 5"), b"ERR BAD IIR")
		self.assertEqual(handle_wire_line(rfm, st, "SET IIR abc"), b"ERR BAD IIR")

	def test_set_iir_blocked_in_mission(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="MISSION")
		out = handle_wire_line(rfm, st, "SET IIR 4")
		self.assertEqual(out, b"ERR BUSY MISSION")

	def test_set_iir_blocked_in_test(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="TEST")
		out = handle_wire_line(rfm, st, "SET IIR 4")
		self.assertEqual(out, b"ERR BUSY TEST")

	def test_get_iir_allowed_in_test(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="TEST")
		out = handle_wire_line(rfm, st, "GET IIR")
		self.assertTrue(out.startswith(b"OK IIR "))

	def test_set_mode_test_applies_mission_iir(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(config_iir=2, mission_iir=16)
		st.time_synced = True
		st.ground_hpa = 1013.2
		bme = _fake_bme()
		bme.set_iir_filter.return_value = 16
		out = handle_wire_line(rfm, st, "SET MODE TEST 5", bme280=bme)
		self.assertTrue(out.startswith(b"OK MODE TEST"))
		bme.set_iir_filter.assert_called_with(16)

	def test_set_mode_config_restores_config_iir(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="CONFIG", config_iir=4, mission_iir=16)
		bme = _fake_bme()
		bme.set_iir_filter.return_value = 4
		out = handle_wire_line(rfm, st, "SET MODE CONFIG", bme280=bme)
		self.assertEqual(out, b"OK MODE CONFIG")
		bme.set_iir_filter.assert_called_with(4)

	def test_apply_mode_iir_safe_without_bme(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, apply_mode_iir

		st = RadioRuntimeState(mode="TEST", mission_iir=16)
		self.assertIsNone(apply_mode_iir(st, None))

	def test_apply_mode_iir_selects_based_on_mode(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, apply_mode_iir

		bme = _fake_bme()
		bme.set_iir_filter.side_effect = lambda v: v
		st = RadioRuntimeState(mode="CONFIG", config_iir=2, mission_iir=16)
		self.assertEqual(apply_mode_iir(st, bme), 2)
		st.mode = "TEST"
		self.assertEqual(apply_mode_iir(st, bme), 16)
		st.mode = "MISSION"
		self.assertEqual(apply_mode_iir(st, bme), 16)

	def test_apply_mode_iir_swallows_i2c_errors(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, apply_mode_iir

		bme = _fake_bme()
		bme.set_iir_filter.side_effect = OSError("bus busy")
		st = RadioRuntimeState(mode="TEST", mission_iir=16)
		self.assertIsNone(apply_mode_iir(st, bme))


if __name__ == "__main__":
	unittest.main()
