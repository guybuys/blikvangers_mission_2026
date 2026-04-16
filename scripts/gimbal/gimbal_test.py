"""Gimbal-test met CircuitPython BNO055 + pigpio (Blinka). Zie ook ``scripts/gimbal_level.py`` (smbus)."""

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import board
import pigpio
import adafruit_bno055

_REPO = Path(__file__).resolve().parents[2]
_src = _REPO / "src"
if _src.is_dir() and str(_src) not in sys.path:
	sys.path.insert(0, str(_src))

_DEFAULT_CAL = _REPO / "config" / "gimbal" / "servo_calibration.json"

from cansat_hw.servos.power_enable import servo_rail_configure, servo_rail_set


@dataclass
class ServoCal:
	gpio: int
	min_us: int
	center_us: int
	max_us: int

	def clamp(self, us: float) -> int:
		return int(max(self.min_us, min(self.max_us, round(us))))


def _load_servo_cal(path: Path) -> Tuple[ServoCal, ServoCal]:
	data = json.loads(path.read_text(encoding="utf-8"))

	def parse(key: str) -> ServoCal:
		d = data[key]
		missing = [k for k in ["gpio", "min_us", "center_us", "max_us"] if d.get(k) is None]
		if missing:
			raise ValueError(f"Missing {missing} in {path} for {key}")
		return ServoCal(
			gpio=int(d["gpio"]),
			min_us=int(d["min_us"]),
			center_us=int(d["center_us"]),
			max_us=int(d["max_us"]),
		)

	return parse("servo1"), parse("servo2")


def _gravity_to_angles(g: Tuple[float, float, float]) -> Tuple[float, float]:
	gx, gy, gz = g
	roll = math.degrees(math.atan2(gy, gz))
	pitch = math.degrees(math.atan2(-gx, math.sqrt(gy * gy + gz * gz)))
	return roll, pitch


def _is_finite_vec3(v: Tuple[float, float, float]) -> bool:
	return all(math.isfinite(x) for x in v)


def _norm3(v: Tuple[float, float, float]) -> float:
	x, y, z = v
	return math.sqrt(x * x + y * y + z * z)


def main() -> int:
	parser = argparse.ArgumentParser(add_help=True)
	parser.add_argument(
		"--cal",
		type=Path,
		default=_DEFAULT_CAL,
		help="Calibratie-JSON (standaard config/gimbal/servo_calibration.json)",
	)
	parser.add_argument("--bno055-address", type=lambda s: int(s, 0), default=0x28)
	parser.add_argument("--rate-hz", type=float, default=50.0)
	parser.add_argument("--log-hz", type=float, default=5.0)
	parser.add_argument("--log", type=Path, default=Path("gimbal_log.csv"))

	parser.add_argument("--kx", type=float, default=120.0)
	parser.add_argument("--ky", type=float, default=120.0)

	parser.add_argument("--alpha", type=float, default=0.85)

	parser.add_argument(
		"--deadband-x",
		type=float,
		default=0.10,
		help="No correction if |gx-gx0| below this (m/s^2)",
	)
	parser.add_argument(
		"--deadband-y",
		type=float,
		default=0.10,
		help="No correction if |gy-gy0| below this (m/s^2)",
	)
	parser.add_argument(
		"--max-us-step",
		type=int,
		default=8,
		help="Max servo microseconds change per control step (slew rate limit)",
	)

	parser.add_argument("--g-min", type=float, default=7.0, help="Min acceptable |g| (m/s^2)")
	parser.add_argument("--g-max", type=float, default=12.5, help="Max acceptable |g| (m/s^2)")
	parser.add_argument(
		"--max-dg",
		type=float,
		default=6.0,
		help="Max acceptable per-sample jump in any gravity component (m/s^2)",
	)
	parser.add_argument(
		"--enable-pin",
		type=int,
		default=6,
		help="BCM GPIO voor servo-motorvoeding (0 = niet gebruiken)",
	)
	parser.add_argument(
		"--enable-active-low",
		action="store_true",
		help="Als gezet: laag = voeding aan (active-low driver)",
	)
	parser.add_argument(
		"--swap-gpio",
		action="store_true",
		help="Wissel alleen BCM-pinnen na laden JSON (PWM naar verkeerde header: 12↔13); limieten blijven per servo1/servo2.",
	)
	parser.add_argument(
		"--swap-control-axes",
		action="store_true",
		help="Wissel regeling: gx-fout → servo2, gy-fout → servo1 (motoren zitten op andere mechanische as).",
	)
	parser.add_argument(
		"--zero-capture-at-center",
		action="store_true",
		help="Nul-referentie vastleggen met servo's al op center_us (oud gedrag). Standaard: PWM uit tijdens warm-up, "
		"jij houdt waterpas — anders lees je de helling van 'center' als 'niveau'.",
	)

	args = parser.parse_args()
	_enable_pin = int(args.enable_pin)
	_enable_al = bool(args.enable_active_low)

	if not args.cal.exists():
		print(f"Calibration file not found: {args.cal}", file=sys.stderr)
		return 2

	try:
		s1, s2 = _load_servo_cal(args.cal)
	except Exception as e:
		print(f"Failed to load calibration: {e}", file=sys.stderr)
		return 2

	if args.swap_gpio:
		s1, s2 = (
			ServoCal(gpio=s2.gpio, min_us=s1.min_us, center_us=s1.center_us, max_us=s1.max_us),
			ServoCal(gpio=s1.gpio, min_us=s2.min_us, center_us=s2.center_us, max_us=s2.max_us),
		)

	pi = pigpio.pi()
	if not pi.connected:
		print("Could not connect to pigpiod. Start it with: sudo systemctl start pigpiod", file=sys.stderr)
		return 2

	i2c = board.I2C()
	sensor = adafruit_bno055.BNO055_I2C(i2c, address=args.bno055_address)

	dt = 1.0 / float(args.rate_hz)
	log_dt = 1.0 / float(args.log_hz)

	zero_gx: Optional[float] = None
	zero_gy: Optional[float] = None

	fgx: Optional[float] = None
	fgy: Optional[float] = None
	fgz: Optional[float] = None

	last_raw_g: Optional[Tuple[float, float, float]] = None

	t_next_log = time.monotonic()

	try:
		if not args.log.exists():
			args.log.write_text(
				"t_monotonic,t_wall,gx,gy,gz,roll_deg,pitch_deg,us1,us2,calib_sys,calib_gyro,calib_accel,calib_mag\n",
				encoding="utf-8",
			)
	except Exception as e:
		print(f"Warning: could not init log file {args.log}: {e}", file=sys.stderr)

	def servo_off() -> None:
		pi.set_servo_pulsewidth(s1.gpio, 0)
		pi.set_servo_pulsewidth(s2.gpio, 0)

	try:
		servo_rail_configure(pi, _enable_pin, active_low=_enable_al)
		servo_rail_set(pi, _enable_pin, True, active_low=_enable_al)

		pi.set_mode(s1.gpio, pigpio.OUTPUT)
		pi.set_mode(s2.gpio, pigpio.OUTPUT)

		if args.zero_capture_at_center:
			pi.set_servo_pulsewidth(s1.gpio, s1.center_us)
			pi.set_servo_pulsewidth(s2.gpio, s2.center_us)
			cur_us1 = int(s1.center_us)
			cur_us2 = int(s2.center_us)
			print(
				"Warm-up ~2.5 s: hold LEVEL (servos at center_us — zero matches that pose, incl. mechanical center bias)."
			)
		else:
			pi.set_servo_pulsewidth(s1.gpio, 0)
			pi.set_servo_pulsewidth(s2.gpio, 0)
			cur_us1 = int(s1.center_us)
			cur_us2 = int(s2.center_us)
			print(
				"Warm-up ~2.5 s: hold LEVEL with servos relaxed (PWM 0). "
				"Zero = true level, not the tilt of calibration center."
			)

		print("Example tuning: python3 scripts/gimbal/gimbal_test.py --kx 50 --ky 50 --alpha 0.9")
		_remap = []
		if args.swap_gpio:
			_remap.append("swap-gpio")
		if args.swap_control_axes:
			_remap.append("swap-control-axes")
		if _remap:
			print("Remapping:", ", ".join(_remap))

		good = 0
		t0 = time.monotonic()
		while time.monotonic() - t0 < 2.5:
			g = sensor.gravity
			if g is None:
				time.sleep(0.02)
				continue
			if g[0] is None or g[1] is None or g[2] is None:
				time.sleep(0.02)
				continue
			g = (float(g[0]), float(g[1]), float(g[2]))
			if not _is_finite_vec3(g):
				time.sleep(0.02)
				continue
			gn = _norm3(g)
			if gn < float(args.g_min) or gn > float(args.g_max):
				time.sleep(0.02)

				continue
			if last_raw_g is not None:
				if max(abs(g[0] - last_raw_g[0]), abs(g[1] - last_raw_g[1]), abs(g[2] - last_raw_g[2])) > float(
					args.max_dg
				):
					last_raw_g = g
					time.sleep(0.02)
					continue
			last_raw_g = g
			gx, gy, _gz = g
			zero_gx = gx if zero_gx is None else 0.9 * zero_gx + 0.1 * gx
			zero_gy = gy if zero_gy is None else 0.9 * zero_gy + 0.1 * gy
			good += 1
			time.sleep(0.02)

		if zero_gx is None or zero_gy is None:
			print("Could not read gravity from BNO055.", file=sys.stderr)
			return 3

		print(f"Zero reference captured ({good} good samples): gx0={zero_gx:.3f} gy0={zero_gy:.3f}")

		if not args.zero_capture_at_center:
			print("Driving servos to calibration center, then closed-loop…")
			pi.set_servo_pulsewidth(s1.gpio, s1.center_us)
			pi.set_servo_pulsewidth(s2.gpio, s2.center_us)
			cur_us1 = int(s1.center_us)
			cur_us2 = int(s2.center_us)
			time.sleep(0.35)
		print(
			f"Servo1: gpio={s1.gpio} center={s1.center_us} min={s1.min_us} max={s1.max_us} | "
			f"Servo2: gpio={s2.gpio} center={s2.center_us} min={s2.min_us} max={s2.max_us}"
		)
		print(
			"Press Ctrl+C to stop. If the correction goes the wrong way, flip the sign by using --kx -60 or --ky -60."
		)

		fgx = float(zero_gx)
		fgy = float(zero_gy)
		if last_raw_g is not None:
			fgz = float(last_raw_g[2])
		else:
			fgz = 9.81

		try:
			_sys_cal = sensor.calibration_status[0]
		except Exception:
			_sys_cal = None
		if _sys_cal is not None and int(_sys_cal) < 1:
			print(
				"Warning: BNO055 system calibration is 0 (calib_sys) — gravity can be biased. "
				"Move the board in a figure-8 until calib_sys > 0 for a trustworthy horizontal reference.",
				file=sys.stderr,
			)

		while True:
			t_loop = time.monotonic()

			g = sensor.gravity
			if g is None:
				time.sleep(dt)
				continue

			if g[0] is None or g[1] is None or g[2] is None:
				time.sleep(dt)
				continue

			g = (float(g[0]), float(g[1]), float(g[2]))
			if not _is_finite_vec3(g):
				time.sleep(dt)
				continue

			gn = _norm3(g)
			if gn < float(args.g_min) or gn > float(args.g_max):
				time.sleep(dt)
				continue

			if last_raw_g is not None:
				if max(abs(g[0] - last_raw_g[0]), abs(g[1] - last_raw_g[1]), abs(g[2] - last_raw_g[2])) > float(
					args.max_dg
				):
					last_raw_g = g
					time.sleep(dt)
					continue

			last_raw_g = g
			gx, gy, gz = g

			a = float(args.alpha)
			fgx = a * fgx + (1.0 - a) * gx
			fgy = a * fgy + (1.0 - a) * gy
			fgz = a * fgz + (1.0 - a) * gz

			ex = fgx - float(zero_gx)
			ey = fgy - float(zero_gy)

			if abs(ex) < float(args.deadband_x):
				ex = 0.0
			if abs(ey) < float(args.deadband_y):
				ey = 0.0

			if args.swap_control_axes:
				target_us1 = s1.clamp(s1.center_us + (-args.ky) * ey)
				target_us2 = s2.clamp(s2.center_us + (-args.kx) * ex)
			else:
				target_us1 = s1.clamp(s1.center_us + (-args.kx) * ex)
				target_us2 = s2.clamp(s2.center_us + (-args.ky) * ey)

			max_step = int(args.max_us_step)
			if max_step > 0:
				delta1 = max(-max_step, min(max_step, target_us1 - cur_us1))
				delta2 = max(-max_step, min(max_step, target_us2 - cur_us2))
				cur_us1 += int(delta1)
				cur_us2 += int(delta2)
			else:
				cur_us1 = int(target_us1)
				cur_us2 = int(target_us2)

			pi.set_servo_pulsewidth(s1.gpio, cur_us1)
			pi.set_servo_pulsewidth(s2.gpio, cur_us2)

			if time.monotonic() >= t_next_log:
				roll_deg, pitch_deg = _gravity_to_angles((fgx, fgy, fgz))
				try:
					calib = sensor.calibration_status
				except Exception:
					calib = (None, None, None, None)

				line = (
					f"{time.monotonic():.6f},"
					f"{time.time():.6f},"
					f"{fgx:.4f},{fgy:.4f},{fgz:.4f},"
					f"{roll_deg:.3f},{pitch_deg:.3f},"
					f"{cur_us1},{cur_us2},"
					f"{calib[0]},{calib[1]},{calib[2]},{calib[3]}\n"
				)
				try:
					with args.log.open("a", encoding="utf-8") as f:
						f.write(line)
				except Exception:
					pass

				print(
					f"g=({fgx:+.2f},{fgy:+.2f},{fgz:+.2f}) "
					f"err=({ex:+.2f},{ey:+.2f}) us=({cur_us1},{cur_us2}) calib={calib}"
				)
				t_next_log = time.monotonic() + log_dt

			t_sleep = dt - (time.monotonic() - t_loop)
			if t_sleep > 0:
				time.sleep(t_sleep)

	except KeyboardInterrupt:
		print("Stopping...")
	finally:
		try:
			servo_off()
			servo_rail_set(pi, _enable_pin, False, active_low=_enable_al)
		finally:
			pi.stop()

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
