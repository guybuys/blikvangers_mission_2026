"""Offline tests BNO055 wire-protocol."""

from __future__ import annotations

import unittest


class WireProtocolBNO055Test(unittest.TestCase):
	def test_read_bno055_no_sensor(self) -> None:
		from unittest.mock import MagicMock

		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "BNO055", bno055=None)
		self.assertIn(b"NO BNO055", out)

	def test_read_bno055_ok(self) -> None:
		from unittest.mock import MagicMock

		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		b = MagicMock()
		b.read_wire_reply.return_value = "OK BNO055 1.0 2.0 3.0 3/3/3/3"
		rfm = MagicMock()
		st = RadioRuntimeState()
		for cmd in ("READ BNO055", "BNO055"):
			out = handle_wire_line(rfm, st, cmd, bno055=b)
			self.assertTrue(out.startswith(b"OK BNO055"))


if __name__ == "__main__":
	unittest.main()
