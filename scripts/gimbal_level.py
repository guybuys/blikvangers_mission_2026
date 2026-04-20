#!/usr/bin/env python3
"""
Gimbal-nivellering op de Pi met **cansat_hw BNO055** (smbus2) + **pigpio** servo's.

Bedoeld voor **vlucht / na deploy**: geen Blinka, standaard **IMU-fusion** (accel+gyro,
geen magnetometer) zodat je niet op mag-calibratie of figure-8 hoeft te wachten. Nul
vastgelegd met **PWM uit** tijdens warm-up (hand waterpas), daarna center → closed loop
(zelfde idee als ``scripts/gimbal/gimbal_test.py``).

Standaard regelt de lus **gx → 0** en **gy → 0** (sensor-zwaartekrachtvector). Dat is “waterpas”
alleen als de BNO055 zo gemonteerd is dat niveau daar `gx≈gy≈0` geeft; anders: ``--level-target warmup``.

Voorbeeld (repo-root; standaard ``--cal`` = ``config/gimbal/servo_calibration.json``)::

  pip install -e ".[gimbal]"    # pigpio + smbus2 in de venv
  sudo systemctl start pigpiod
  python3 scripts/gimbal_level.py
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

try:
	import pigpio
except ModuleNotFoundError as e:
	print(
		"Module 'pigpio' ontbreekt in deze Python-installatie.\n"
		f"  Huidige interpreter: {sys.executable}\n"
		"  Installeer in dezelfde venv (vanaf repo-root, met .venv actief):\n"
		f"    {sys.executable} -m pip install pigpio\n"
		"  of:  python -m pip install -e \".[gimbal]\"\n"
		"Als dat faalt: sync eerst pyproject.toml vanaf je Mac (extras [gimbal]). "
		"Controleer ook: `which python` moet …/cansat_mission_2026/.venv/bin/python zijn.",
		file=sys.stderr,
	)
	raise SystemExit(1) from e

# scripts/gimbal_level.py → parents[0]=scripts, parents[1]=repo-root
_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
_DEFAULT_CAL = _ROOT / "config" / "gimbal" / "servo_calibration.json"
if _SRC.is_dir() and str(_SRC) not in sys.path:
	sys.path.insert(0, str(_SRC))

from cansat_hw.sensors.bno055 import BNO055, OPERATION_MODE_IMU, OPERATION_MODE_NDOF
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
		missing = [k for k in ("gpio", "min_us", "center_us", "max_us") if d.get(k) is None]
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


def _read_gravity(imu: BNO055) -> Optional[Tuple[float, float, float]]:
	try:
		g = imu.read_gravity()
	except OSError:
		return None
	if not _is_finite_vec3(g):
		return None
	return g


def _settle_gravity_lpf(
	imu: BNO055,
	*,
	settle_s: float,
	g_min: float,
	g_max: float,
	max_dg: float,
	alpha: float,
	last_raw: Optional[Tuple[float, float, float]],
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[Tuple[float, float, float]]]:
	"""Vast op center-PWM: LPF laten convergeren naar belaste g; last_raw bijgewerkt."""
	fgx: Optional[float] = None
	fgy: Optional[float] = None
	fgz: Optional[float] = None
	lr = last_raw
	t_end = time.monotonic() + max(0.01, settle_s)
	while time.monotonic() < t_end:
		g = _read_gravity(imu)
		if g is None:
			time.sleep(0.01)
			continue
		gn = _norm3(g)
		if gn < g_min or gn > g_max:
			time.sleep(0.01)
			continue
		if lr is not None:
			if max(abs(g[0] - lr[0]), abs(g[1] - lr[1]), abs(g[2] - lr[2])) > max_dg:
				lr = g
				time.sleep(0.01)
				continue
		lr = g
		gx, gy, gz = g
		if fgx is None:
			fgx, fgy, fgz = gx, gy, gz
		else:
			fgx = alpha * fgx + (1.0 - alpha) * gx
			fgy = alpha * fgy + (1.0 - alpha) * gy
			fgz = alpha * fgz + (1.0 - alpha) * gz
		time.sleep(0.01)
	return fgx, fgy, fgz, lr


def main() -> int:
	parser = argparse.ArgumentParser(description="Gimbal level — BNO055 (smbus) + pigpio")
	parser.add_argument(
		"--cal",
		type=Path,
		default=_DEFAULT_CAL,
		help="Servo-calibratie-JSON (standaard config/gimbal/servo_calibration.json)",
	)
	parser.add_argument("--i2c-bus", type=int, default=1)
	parser.add_argument("--bno055-address", type=lambda s: int(s, 0), default=0x28)
	parser.add_argument(
		"--fusion-mode",
		choices=("imu", "ndof"),
		default="imu",
		help="imu = accel+gyro fusion, geen mag (aanbevolen zonder pad-cal). ndof = volledige fusion.",
	)
	parser.add_argument("--rate-hz", type=float, default=50.0)
	parser.add_argument("--log-hz", type=float, default=5.0)
	parser.add_argument("--log", type=Path, default=Path("gimbal_level_log.csv"))
	parser.add_argument(
		"--kx",
		type=float,
		default=200.0,
		help="P-versterking servo1 / gx-fout: Δµs ≈ −kx×fout (m/s²). Verhogen = harder sturen (let op oscillatie).",
	)
	parser.add_argument(
		"--ky",
		type=float,
		default=200.0,
		help="P-versterking servo2 / gy-fout (idem).",
	)
	parser.add_argument(
		"--kix",
		type=float,
		default=20.0,
		help="I-term op gx-fout (µs per geïntegreerde fout·s); 0=uit. Helpt restfout te slepen; bij oscillatie/windup verlagen of --integral-max aanpassen.",
	)
	parser.add_argument(
		"--kiy",
		type=float,
		default=20.0,
		help="I-term op gy-fout; 0=uit.",
	)
	parser.add_argument(
		"--integral-max",
		type=float,
		default=4.0,
		help="Anti-windup: clamp op |∫ e·dt| (zelfde eenheid als e×tijd).",
	)
	parser.add_argument("--alpha", type=float, default=0.85)
	parser.add_argument("--deadband-x", type=float, default=0.10)
	parser.add_argument("--deadband-y", type=float, default=0.10)
	parser.add_argument(
		"--max-us-step",
		type=int,
		default=8,
		help="Max µs per stap naar doel-PWM. Bij hogere --kx/--ky evt. verhogen (12–16) anders traag.",
	)
	parser.add_argument("--g-min", type=float, default=7.0)
	parser.add_argument("--g-max", type=float, default=12.5)
	parser.add_argument("--max-dg", type=float, default=6.0, help="Max |Δg| per sample tijdens warm-up (m/s²)")
	parser.add_argument(
		"--loop-max-dg",
		type=float,
		default=2.5,
		help="Strengere max |Δg| in de regellus (m/s²) — minder jagen op ruis/spikes",
	)
	parser.add_argument("--warmup-s", type=float, default=2.5, help="Duur nul vastleggen (s)")
	parser.add_argument(
		"--post-center-settle-s",
		type=float,
		default=0.75,
		help="Na inschakelen center-PWM: even wachten + LPF laten aansluiten op de belaste mechanica (0=uit). "
		"Helpt tegen verschil los vs onder spanning.",
	)
	parser.add_argument("--enable-pin", type=int, default=6)
	parser.add_argument("--enable-active-low", action="store_true")
	parser.add_argument("--swap-gpio", action="store_true")
	parser.add_argument("--swap-control-axes", action="store_true")
	parser.add_argument(
		"--zero-capture-at-center",
		action="store_true",
		help="Nul bij servo's op center (oud gedrag).",
	)
	parser.add_argument(
		"--level-target",
		choices=("zero", "warmup"),
		default="zero",
		help="zero (standaard) = regel gx,gy naar 0. warmup = fout t.o.v. gx0/gy0 uit warm-up (vorig gedrag).",
	)
	args = parser.parse_args()

	cal_path = args.cal if args.cal.is_absolute() else (_ROOT / args.cal)

	_enable_pin = int(args.enable_pin)
	_enable_al = bool(args.enable_active_low)
	fusion_mode = OPERATION_MODE_IMU if args.fusion_mode == "imu" else OPERATION_MODE_NDOF

	i2c_dev = Path(f"/dev/i2c-{args.i2c_bus}")
	if not i2c_dev.exists():
		print(f"Geen {i2c_dev} — zet I²C aan.", file=sys.stderr)
		return 1

	if not cal_path.exists():
		print(
			f"Calibration file not found: {cal_path}\n"
			f"  Repo-root: {_ROOT}\n"
			f"  Standaard: {_DEFAULT_CAL}\n"
			f"  Tip: per-hardware bestand, niet in git. Calibreer met"
			f" 'python scripts/gimbal/servo_calibration.py' of geef expliciet"
			f" --cal pad/naar/servo_calibration.json. Schema-voorbeeld:"
			f" config/gimbal/servo_calibration.example.json.",
			file=sys.stderr,
		)
		return 2

	try:
		s1, s2 = _load_servo_cal(cal_path)
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

	try:
		imu = BNO055(args.i2c_bus, args.bno055_address, mode=fusion_mode)
	except (OSError, RuntimeError) as e:
		print(f"BNO055: {e}", file=sys.stderr)
		pi.stop()
		return 3

	if args.fusion_mode == "imu":
		print(
			"Fusion: IMU (accel+gyro, geen mag). Geschikt om zonder figure-8 naar zwaartekracht te sturen.",
			file=sys.stderr,
		)

	dt = 1.0 / float(args.rate_hz)
	log_dt = 1.0 / float(args.log_hz)
	warmup_s = max(0.5, float(args.warmup_s))
	level_zero = args.level_target == "zero"

	zero_gx: Optional[float] = None
	zero_gy: Optional[float] = None
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
			if level_zero:
				print(
					f"Warm-up {warmup_s:.1f} s: servo's op center — IMU laten stabiliseren; regeldoel daarna gx→0, gy→0.",
					file=sys.stderr,
				)
			else:
				print(
					f"Warm-up {warmup_s:.1f} s: hold LEVEL (servos at center_us — gx0/gy0 = die pose).",
					file=sys.stderr,
				)
		else:
			pi.set_servo_pulsewidth(s1.gpio, 0)
			pi.set_servo_pulsewidth(s2.gpio, 0)
			cur_us1 = int(s1.center_us)
			cur_us2 = int(s2.center_us)
			if level_zero:
				print(
					f"Warm-up {warmup_s:.1f} s: servo's uit (PWM 0) — IMU stabiliseren; daarna regeldoel gx→0, gy→0.",
					file=sys.stderr,
				)
			else:
				print(
					f"Warm-up {warmup_s:.1f} s: hold LEVEL, servos relaxed (PWM 0) — gx0/gy0 = waterpas-referentie.",
					file=sys.stderr,
				)

		_remap = []
		if args.swap_gpio:
			_remap.append("swap-gpio")
		if args.swap_control_axes:
			_remap.append("swap-control-axes")
		if _remap:
			print("Remapping:", ", ".join(_remap), file=sys.stderr)

		good = 0
		t0 = time.monotonic()
		while time.monotonic() - t0 < warmup_s:
			g = _read_gravity(imu)
			if g is None:
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
			if not level_zero:
				zero_gx = gx if zero_gx is None else 0.9 * zero_gx + 0.1 * gx
				zero_gy = gy if zero_gy is None else 0.9 * zero_gy + 0.1 * gy
			good += 1
			time.sleep(0.02)

		if good == 0:
			print("Could not read gravity from BNO055 during warm-up.", file=sys.stderr)
			return 3

		if not level_zero:
			if zero_gx is None or zero_gy is None:
				print("Could not build gx0/gy0 reference from warm-up.", file=sys.stderr)
				return 3
			print(f"Warm-up reference: gx0={zero_gx:.3f} gy0={zero_gy:.3f} ({good} samples)")
			print(
				"Regeling: ex = gx gefilterd − gx0, ey = gy gefilterd − gy0 (doel ex,ey → 0).",
				file=sys.stderr,
			)
		else:
			print(
				f"Warm-up klaar ({good} samples). Regeling: ex→gx gefilterd→0, ey→gy gefilterd→0 "
				"(geen warm-up-referentie; montage moet dan ‘niveau’ ≈ gx=gy=0 geven).",
				file=sys.stderr,
			)

		ref_gx = 0.0 if level_zero else float(zero_gx)
		ref_gy = 0.0 if level_zero else float(zero_gy)

		post_settle_s = 0.0
		boot_fgx: Optional[float] = None
		boot_fgy: Optional[float] = None
		boot_fgz: Optional[float] = None

		if not args.zero_capture_at_center:
			print("Driving servos to calibration center, then closed-loop…")
			pi.set_servo_pulsewidth(s1.gpio, s1.center_us)
			pi.set_servo_pulsewidth(s2.gpio, s2.center_us)
			cur_us1 = int(s1.center_us)
			cur_us2 = int(s2.center_us)
			time.sleep(0.35)

			post_settle_s = max(0.0, float(args.post_center_settle_s))
			if post_settle_s > 0:
				print(
					f"Settling {post_settle_s:.2f}s op center-PWM (handen los): LPF volgt nu de belaste mechanica "
					"(anders verschilt g van gx0/gy0 door span vs losse koppeling).",
					file=sys.stderr,
				)
				boot_fgx, boot_fgy, boot_fgz, last_raw_g = _settle_gravity_lpf(
					imu,
					settle_s=post_settle_s,
					g_min=float(args.g_min),
					g_max=float(args.g_max),
					max_dg=float(args.loop_max_dg),
					alpha=float(args.alpha),
					last_raw=last_raw_g,
				)
				if boot_fgx is not None and boot_fgy is not None and boot_fgz is not None:
					if level_zero:
						print(
							f"Na settle: gefilterde g ≈ ({boot_fgx:+.2f},{boot_fgy:+.2f},{boot_fgz:+.2f}) "
							f"(regelaar start; doel gx=gy=0).",
							file=sys.stderr,
						)
					else:
						print(
							f"Na settle: gefilterde g ≈ ({boot_fgx:+.2f},{boot_fgy:+.2f},{boot_fgz:+.2f}) "
							f"(start LPF; gx0/gy0 = {zero_gx:.3f}, {zero_gy:.3f}).",
							file=sys.stderr,
						)

		print(
			f"Servo1: gpio={s1.gpio} center={s1.center_us} | Servo2: gpio={s2.gpio} center={s2.center_us}"
		)
		print("Ctrl+C to stop. Verkeerde richting: --kx/--ky negatief; I-term: --kix/--kiy.")

		if (
			not args.zero_capture_at_center
			and post_settle_s > 0
			and boot_fgx is not None
			and boot_fgy is not None
			and boot_fgz is not None
		):
			fgx, fgy, fgz = boot_fgx, boot_fgy, boot_fgz
		elif level_zero and last_raw_g is not None:
			fgx, fgy, fgz = float(last_raw_g[0]), float(last_raw_g[1]), float(last_raw_g[2])
		elif not level_zero:
			fgx = float(zero_gx)
			fgy = float(zero_gy)
			fgz = float(last_raw_g[2]) if last_raw_g is not None else 9.81
		else:
			fgx = fgy = 0.0
			fgz = float(last_raw_g[2]) if last_raw_g is not None else 9.81

		cs0 = imu.calibration_status()
		if args.fusion_mode == "ndof" and cs0[0] < 1:
			print(
				"Tip: calib_sys is 0 in NDOF — overweeg --fusion-mode imu zonder pad-calibratie.",
				file=sys.stderr,
			)

		int_ex = 0.0
		int_ey = 0.0
		i_max = float(args.integral_max)

		while True:
			t_loop = time.monotonic()

			g = _read_gravity(imu)
			if g is None:
				time.sleep(dt)
				continue
			gn = _norm3(g)
			if gn < float(args.g_min) or gn > float(args.g_max):
				time.sleep(dt)
				continue
			if last_raw_g is not None:
				if max(abs(g[0] - last_raw_g[0]), abs(g[1] - last_raw_g[1]), abs(g[2] - last_raw_g[2])) > float(
					args.loop_max_dg
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

			ex_raw = fgx - ref_gx
			ey_raw = fgy - ref_gy

			ex = ex_raw
			ey = ey_raw
			if abs(ex) < float(args.deadband_x):
				ex = 0.0
			if abs(ey) < float(args.deadband_y):
				ey = 0.0

			if float(args.kix) != 0.0:
				int_ex += ex_raw * dt
				int_ex = max(-i_max, min(i_max, int_ex))
			if float(args.kiy) != 0.0:
				int_ey += ey_raw * dt
				int_ey = max(-i_max, min(i_max, int_ey))

			if args.swap_control_axes:
				target_us1 = s1.clamp(s1.center_us + (-args.ky) * ey + (-float(args.kiy)) * int_ey)
				target_us2 = s2.clamp(s2.center_us + (-args.kx) * ex + (-float(args.kix)) * int_ex)
			else:
				target_us1 = s1.clamp(s1.center_us + (-args.kx) * ex + (-float(args.kix)) * int_ex)
				target_us2 = s2.clamp(s2.center_us + (-args.ky) * ey + (-float(args.kiy)) * int_ey)

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
				calib = imu.calibration_status()

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
					f"err=({ex_raw:+.2f},{ey_raw:+.2f}) us=({cur_us1},{cur_us2}) calib={calib}"
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
		imu.close()

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
