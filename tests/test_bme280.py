"""Offline tests voor BME280 (mock I²C)."""

from __future__ import annotations

import unittest


class ConversionSleepTest(unittest.TestCase):
	def test_sleep_increases_with_os(self) -> None:
		from cansat_hw.sensors.bme280.device import _conversion_sleep_us

		t1 = _conversion_sleep_us(1)
		t5 = _conversion_sleep_us(5)
		self.assertLess(t1, t5)


class WireProtocolBME280Test(unittest.TestCase):
	def test_read_bme280_no_sensor(self) -> None:
		from unittest.mock import MagicMock

		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "READ BME280", bme280=None)
		self.assertIn(b"NO BME280", out)

	def test_read_bme280_ok(self) -> None:
		from unittest.mock import MagicMock

		from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

		b = MagicMock()
		b.read_wire_reply.return_value = "OK BME280 1013.25 20.0 50"
		rfm = MagicMock()
		st = RadioRuntimeState()
		out = handle_wire_line(rfm, st, "READ BME280", bme280=b)
		self.assertTrue(out.startswith(b"OK BME280"))
		out2 = handle_wire_line(rfm, st, "BME280", bme280=b)
		self.assertTrue(out2.startswith(b"OK BME280"))


if __name__ == "__main__":
	unittest.main()
