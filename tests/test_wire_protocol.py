"""Radio draad-protocol — CONFIG / MISSION modi."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock


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


if __name__ == "__main__":
	unittest.main()
