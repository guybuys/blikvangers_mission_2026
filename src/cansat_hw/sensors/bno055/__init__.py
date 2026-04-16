"""BNO055 (I²C fusion IMU)."""

from cansat_hw.sensors.bno055.device import (
	BNO055,
	OPERATION_MODE_IMU,
	OPERATION_MODE_NDOF,
)

__all__ = ["BNO055", "OPERATION_MODE_IMU", "OPERATION_MODE_NDOF"]
