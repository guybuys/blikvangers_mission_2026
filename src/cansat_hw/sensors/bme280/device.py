"""BME280 over I²C (SMBus) — compensatie gelijk aan Pico `bme280.py` (Adafruit-derivatie)."""

from __future__ import annotations

import struct
import time
from typing import Any, Tuple


# Zelfde constanten als MicroPython-module in pico_files
BME280_REGISTER_CONTROL_HUM = 0xF2
BME280_REGISTER_CONTROL = 0xF4
BME280_REGISTER_CONFIG = 0xF5
BME280_REGISTER_CHIPID = 0xD0

BME280_CHIP_ID = 0x60

BME280_OSAMPLE_1 = 1
BME280_OSAMPLE_2 = 2
BME280_OSAMPLE_4 = 3
BME280_OSAMPLE_8 = 4
BME280_OSAMPLE_16 = 5


def _conversion_sleep_us(oversampling: int) -> float:
	"""Minimale wachttijd na forced-measure trigger (Pico-formule, microseconden)."""
	if oversampling not in (1, 2, 3, 4, 5):
		raise ValueError("oversampling must be 1..5 (Pico OSAMPLE constants)")
	st = 1250 + 2300 * (1 << oversampling)
	st = st + 2300 * (1 << oversampling) + 575
	st = st + 2300 * (1 << oversampling) + 575
	return st / 1_000_000.0


class BME280:
	"""BME280 op Linux-I²C (bv. bus 1 = ``/dev/i2c-1``)."""

	def __init__(
		self,
		bus: int,
		address: int = 0x76,
		*,
		oversampling: int = BME280_OSAMPLE_1,
	) -> None:
		try:
			from smbus2 import SMBus
		except ImportError as e:
			raise RuntimeError(
				"smbus2 ontbreekt — op de Pi: pip install smbus2  (of pip install -e \".[sensors]\")"
			) from e
		if oversampling not in (1, 2, 3, 4, 5):
			raise ValueError("oversampling must be 1..5")
		self._address = address
		self._oversampling = oversampling
		self._bus: Any = SMBus(bus)
		self._sleep_s = _conversion_sleep_us(oversampling)
		self.t_fine = 0
		self._load_calibration()
		# Zelfde init-write als Pico (control vóór eerste meting)
		self._bus.write_byte_data(self._address, BME280_REGISTER_CONTROL, 0x3F)

	def close(self) -> None:
		b = getattr(self, "_bus", None)
		if b is not None:
			try:
				b.close()
			except Exception:
				pass
			self._bus = None

	def __enter__(self) -> BME280:
		return self

	def __exit__(self, *exc: object) -> None:
		self.close()

	@property
	def chip_id(self) -> int:
		return int(self._bus.read_i2c_block_data(self._address, BME280_REGISTER_CHIPID, 1)[0])

	def _load_calibration(self) -> None:
		bus = self._bus
		addr = self._address
		dig_88_a1 = bytes(bus.read_i2c_block_data(addr, 0x88, 26))
		dig_e1_e7 = bytes(bus.read_i2c_block_data(addr, 0xE1, 7))
		(
			self.dig_T1,
			self.dig_T2,
			self.dig_T3,
			self.dig_P1,
			self.dig_P2,
			self.dig_P3,
			self.dig_P4,
			self.dig_P5,
			self.dig_P6,
			self.dig_P7,
			self.dig_P8,
			self.dig_P9,
			_,
			self.dig_H1,
		) = struct.unpack("<HhhHhhhhhhhhBB", dig_88_a1)

		self.dig_H2, self.dig_H3 = struct.unpack("<hB", dig_e1_e7[:3])
		e4_sign = struct.unpack("<b", dig_e1_e7[3:4])[0]
		self.dig_H4 = (e4_sign << 4) | (dig_e1_e7[4] & 0x0F)
		e6_sign = struct.unpack("<b", dig_e1_e7[5:6])[0]
		self.dig_H5 = (e6_sign << 4) | (dig_e1_e7[4] >> 4)
		self.dig_H6 = struct.unpack("<b", dig_e1_e7[6:7])[0]

	def _read_raw_adc(self) -> Tuple[int, int, int]:
		m = self._oversampling
		bus = self._bus
		addr = self._address
		bus.write_byte_data(addr, BME280_REGISTER_CONTROL_HUM, m)
		bus.write_byte_data(addr, BME280_REGISTER_CONTROL, (m << 5) | (m << 2) | 1)
		time.sleep(self._sleep_s)
		readout = bytes(bus.read_i2c_block_data(addr, 0xF7, 8))
		raw_press = ((readout[0] << 16) | (readout[1] << 8) | readout[2]) >> 4
		raw_temp = ((readout[3] << 16) | (readout[4] << 8) | readout[5]) >> 4
		raw_hum = (readout[6] << 8) | readout[7]
		return raw_temp, raw_press, raw_hum

	def read_compensated_int(self) -> Tuple[int, int, int]:
		"""Ruwe gecompenseerde integers (zelfde als Pico ``read_compensated_data``)."""
		raw_temp, raw_press, raw_hum = self._read_raw_adc()
		var1 = ((raw_temp >> 3) - (self.dig_T1 << 1)) * (self.dig_T2 >> 11)
		var2 = (
			((((raw_temp >> 4) - self.dig_T1) * ((raw_temp >> 4) - self.dig_T1)) >> 12) * self.dig_T3
		) >> 14
		self.t_fine = var1 + var2
		temp = (self.t_fine * 5 + 128) >> 8

		var1 = self.t_fine - 128000
		var2 = var1 * var1 * self.dig_P6
		var2 = var2 + ((var1 * self.dig_P5) << 17)
		var2 = var2 + (self.dig_P4 << 35)
		var1 = (((var1 * var1 * self.dig_P3) >> 8) + ((var1 * self.dig_P2) << 12))
		var1 = (((1 << 47) + var1) * self.dig_P1) >> 33
		if var1 == 0:
			pressure = 0
		else:
			p = 1048576 - raw_press
			p = (((p << 31) - var2) * 3125) // var1
			var1 = (self.dig_P9 * (p >> 13) * (p >> 13)) >> 25
			var2 = (self.dig_P8 * p) >> 19
			pressure = ((p + var1 + var2) >> 8) + (self.dig_P7 << 4)

		h = self.t_fine - 76800
		h = (
			(
				((((raw_hum << 14) - (self.dig_H4 << 20) - (self.dig_H5 * h)) + 16384) >> 15)
				* (
					(
						(((((h * self.dig_H6) >> 10) * (((h * self.dig_H3) >> 11) + 32768)) >> 10) + 2097152)
						* self.dig_H2
						+ 8192
					)
					>> 14
				)
			)
		)
		h = h - (((((h >> 15) * (h >> 15)) >> 7) * self.dig_H1) >> 4)
		h = 0 if h < 0 else h
		h = 419430400 if h > 419430400 else h
		humidity = h >> 12
		return temp, pressure, humidity

	def read(self) -> Tuple[float, float, float]:
		"""Lees één forced sample. Retourneert ``(temp_C, pressure_hPa, rh_pct)``."""
		temp, pressure, humidity = self.read_compensated_int()
		p = pressure // 256
		pi = p // 100
		pd = p - pi * 100
		p_hpa = pi + pd / 100.0
		hi = humidity // 1024
		hd = humidity * 100 // 1024 - hi * 100
		rh = hi + hd / 100.0
		return temp / 100.0, p_hpa, rh

	def read_wire_reply(self) -> str:
		"""Compacte string voor radio (max. payload)."""
		t, p, rh = self.read()
		return f"OK BME280 {p:.2f} {t:.1f} {rh:.0f}"
