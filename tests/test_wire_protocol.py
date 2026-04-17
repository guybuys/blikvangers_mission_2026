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

	def test_height_to_dp_hpa_conversion(self) -> None:
		from cansat_hw.radio.wire_protocol import height_m_to_dp_hpa

		# Vuistregel rond zeeniveau: ~8.3 m per hPa.
		self.assertAlmostEqual(height_m_to_dp_hpa(10.0, 1013.25), 1.20, places=1)
		self.assertAlmostEqual(height_m_to_dp_hpa(5.0, 1013.25), 0.60, places=1)


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


if __name__ == "__main__":
	unittest.main()
