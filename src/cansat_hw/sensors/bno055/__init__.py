"""BNO055 (I²C fusion IMU)."""

from cansat_hw.sensors.bno055.device import (
	BNO055,
	BNO055_CALIB_PROFILE_LENGTH,
	OPERATION_MODE_IMU,
	OPERATION_MODE_NDOF,
)
from cansat_hw.sensors.bno055.profile import (
	PROFILE_LENGTH,
	PROFILE_SCHEMA_VERSION,
	load_profile_file,
	profile_from_dict,
	profile_to_dict,
	save_profile_file,
)

__all__ = [
	"BNO055",
	"BNO055_CALIB_PROFILE_LENGTH",
	"OPERATION_MODE_IMU",
	"OPERATION_MODE_NDOF",
	"PROFILE_LENGTH",
	"PROFILE_SCHEMA_VERSION",
	"load_profile_file",
	"profile_from_dict",
	"profile_to_dict",
	"save_profile_file",
]
