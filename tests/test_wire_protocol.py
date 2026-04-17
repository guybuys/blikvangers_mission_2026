"""Radio draad-protocol — CONFIG / MISSION modi."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class WireProtocolModeTest(unittest.TestCase):
	def test_set_mode_mission(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "SET MODE MISSION")
		self.assertEqual(st.mode, "MISSION")
		self.assertEqual(out, b"OK MODE MISSION")

	def test_set_mode_launch_alias(self) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "SET MODE LAUNCH")
		self.assertEqual(st.mode, "MISSION")
		self.assertEqual(out, b"OK MODE MISSION")

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
	def test_set_time_config_ok(self, _mock_apply: MagicMock) -> None:
		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState(mode="CONFIG")
		out = handle_wire_line(rfm, st, "SET TIME 1700000000.5")
		self.assertEqual(out, b"OK TIME")

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


if __name__ == "__main__":
	unittest.main()
