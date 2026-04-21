"""BNO055 9-DOF fusion IMU over I²C (SMBus) — subset van het Bosch-registerbeeld."""

from __future__ import annotations

import struct
import time
from typing import Any, List, Optional, Tuple

# --- Register-adressen (datasheet / Adafruit-layout, page 0) ---
BNO055_CHIP_ID_ADDR = 0x00
BNO055_PAGE_ID_ADDR = 0x07
BNO055_ACCEL_DATA_X_LSB = 0x08
BNO055_MAG_DATA_X_LSB = 0x0E
BNO055_GYRO_DATA_X_LSB = 0x14
BNO055_EULER_H_LSB = 0x1A
BNO055_LINEAR_ACCEL_DATA_X_LSB = 0x28
BNO055_GRAVITY_DATA_X_LSB = 0x2E
BNO055_TEMP_ADDR = 0x34
BNO055_CALIB_STAT_ADDR = 0x35
BNO055_ST_RESULT_ADDR = 0x36
BNO055_INT_STA_ADDR = 0x37
BNO055_SYS_CLK_STAT_ADDR = 0x38
BNO055_SYS_STAT_ADDR = 0x39
BNO055_SYS_ERR_ADDR = 0x3A
BNO055_UNIT_SEL_ADDR = 0x3B
BNO055_OPR_MODE_ADDR = 0x3D
BNO055_PWR_MODE_ADDR = 0x3E
BNO055_SYS_TRIGGER_ADDR = 0x3F

BNO055_CHIP_ID = 0xA0

OPERATION_MODE_CONFIG = 0x00
OPERATION_MODE_IMU = 0x08
OPERATION_MODE_NDOF = 0x0C

POWER_MODE_NORMAL = 0x00


class BNO055:
	"""BNO055 op Linux-I²C. Standaard **NDOF** (fusion: oriëntatie + euler)."""

	def __init__(
		self,
		bus: int,
		address: int = 0x28,
		*,
		mode: int = OPERATION_MODE_NDOF,
	) -> None:
		try:
			from smbus2 import SMBus
		except ImportError as e:
			raise RuntimeError(
				"smbus2 ontbreekt — op de Pi: pip install smbus2  (of pip install -e \".[sensors]\")"
			) from e
		self._address = address
		self._bus: Any = SMBus(bus)
		self._fusion_mode = mode
		if self._read_u8(BNO055_CHIP_ID_ADDR) != BNO055_CHIP_ID:
			self._bus.close()
			self._bus = None
			raise RuntimeError(
				f"BNO055 chip id onverwacht op 0x{address:02X} — verwacht 0x{BNO055_CHIP_ID:02X}"
			)
		self._write_u8(BNO055_PAGE_ID_ADDR, 0)
		time.sleep(0.01)
		self._set_mode(OPERATION_MODE_CONFIG)
		self._write_u8(BNO055_PWR_MODE_ADDR, POWER_MODE_NORMAL)
		self._write_u8(BNO055_UNIT_SEL_ADDR, 0x00)
		time.sleep(0.01)
		self._set_mode(self._fusion_mode)
		time.sleep(0.05)
		self._last_good_temperature: Optional[int] = None

	def close(self) -> None:
		b = getattr(self, "_bus", None)
		if b is not None:
			try:
				b.close()
			except Exception:
				pass
			self._bus = None

	def __enter__(self) -> BNO055:
		return self

	def __exit__(self, *exc: object) -> None:
		self.close()

	def _write_u8(self, reg: int, val: int) -> None:
		self._bus.write_byte_data(self._address, reg, val & 0xFF)

	def _read_u8(self, reg: int) -> int:
		return int(self._bus.read_i2c_block_data(self._address, reg, 1)[0])

	def _read_block(self, reg: int, length: int) -> bytes:
		return bytes(self._bus.read_i2c_block_data(self._address, reg, length))

	def _ensure_page0(self) -> None:
		"""Temperatuur en fusedata zitten op registerpagina 0; expliciet zetten voorkomt sporadische foute bytes."""
		self._write_u8(BNO055_PAGE_ID_ADDR, 0)
		time.sleep(0.001)

	def _set_mode(self, mode: int) -> None:
		self._write_u8(BNO055_OPR_MODE_ADDR, OPERATION_MODE_CONFIG)
		time.sleep(0.02)
		self._write_u8(BNO055_OPR_MODE_ADDR, mode & 0xFF)
		time.sleep(0.02)

	@property
	def chip_id(self) -> int:
		return self._read_u8(BNO055_CHIP_ID_ADDR)

	def temperature_c(self) -> int:
		"""Temperatuur in °C (8-bit signed, **chip/die**, geen omgeving).

		Sporadisch levert de BNO055 een ongeldige byte (typisch ``0x97`` → −105 °C).
		We lezen **meerdere keren** kort na elkaar, houden waarden in een ruime band,
		nemen de **mediaan** van de geldige samples, en vallen terug op de laatste
		goede meting als een hele batch ruis is.
		"""
		t_lo, t_hi = -40, 125
		self._ensure_page0()
		good: List[int] = []
		last_raw = 0
		for _ in range(7):
			raw = int(self._bus.read_byte_data(self._address, BNO055_TEMP_ADDR))
			last_raw = struct.unpack("b", bytes((raw & 0xFF,)))[0]
			if t_lo <= last_raw <= t_hi:
				good.append(last_raw)
			time.sleep(0.0007)
		if good:
			good.sort()
			out = good[len(good) // 2]
			self._last_good_temperature = out
			return out
		if self._last_good_temperature is not None:
			return self._last_good_temperature
		return last_raw

	def calibration_status(self) -> Tuple[int, int, int, int]:
		"""``(system, gyro, accel, mag)`` elk 0–3."""
		s = self._read_u8(BNO055_CALIB_STAT_ADDR)
		sys = (s >> 6) & 0x03
		gyro = (s >> 4) & 0x03
		accel = (s >> 2) & 0x03
		mag = s & 0x03
		return sys, gyro, accel, mag

	def read_euler(self) -> Tuple[float, float, float]:
		"""Euler-hoeken in graden: ``(heading, roll, pitch)`` (0–360 / ±180 conventie chip)."""
		raw = self._read_block(BNO055_EULER_H_LSB, 6)
		h, r, p = struct.unpack("<hhh", raw)
		return h / 16.0, r / 16.0, p / 16.0

	def read_linear_acceleration(self) -> Tuple[float, float, float]:
		"""Lineaire versnelling m/s² (zwaartekracht eruit)."""
		raw = self._read_block(BNO055_LINEAR_ACCEL_DATA_X_LSB, 6)
		x, y, z = struct.unpack("<hhh", raw)
		return x / 100.0, y / 100.0, z / 100.0

	def read_gyro(self) -> Tuple[float, float, float]:
		"""Rotatiesnelheid in °/s: ``(gx, gy, gz)``.

		BNO055 schaalt op 16 LSB/°/s in de default degree-unit (UNIT_SEL na
		reset). Bereik in NDOF is ±2000°/s, ruim voldoende voor een vallende
		CanSat. Voor gyro-calibratie: laat de sensor 5 s onbeweeglijk staan,
		``gyro_cal`` gaat dan naar 3.
		"""
		raw = self._read_block(BNO055_GYRO_DATA_X_LSB, 6)
		x, y, z = struct.unpack("<hhh", raw)
		return x / 16.0, y / 16.0, z / 16.0

	def read_gravity(self) -> Tuple[float, float, float]:
		"""Gravity-vector m/s²."""
		raw = self._read_block(BNO055_GRAVITY_DATA_X_LSB, 6)
		x, y, z = struct.unpack("<hhh", raw)
		return x / 100.0, y / 100.0, z / 100.0

	def read_wire_reply(self) -> str:
		"""Compact voor radio: euler + calibratie."""
		h, r, p = self.read_euler()
		cs = self.calibration_status()
		return f"OK BNO055 {h:.1f} {r:.1f} {p:.1f} {cs[0]}/{cs[1]}/{cs[2]}/{cs[3]}"
