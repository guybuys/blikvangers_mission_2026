#!/usr/bin/env python3
"""BNO055 vs servo-rail: bevestig of de rail de sensor-fusie verstoort.

Achtergrond (gimbal-debug april 2026): na een testvlucht gaf de gimbal-loop
een grote PWM-saturatie (U1=min, U2=max) terwijl de CanSat fysiek bijna
recht stond. ``GET GIMBAL`` meldde ~28° kanteling uit ``read_gravity``,
maar ``READ BNO055`` (Euler) zei <5°. Die discrepantie is onmogelijk bij
een gezonde sensor en wees op één van twee oorzaken:

1. **Calibratie-gap** — ``cs=0/3/0/0`` levert een ongecompenseerde
   accel-bias; fusion gebruikt die dan voor de gravity-vector.
2. **EMI/magnetische storing** — servo-rail aan + motor-stroom dicht bij
   de BNO055 kan de magnetometer verzieken, ``sys`` laten zakken en de
   gravity-schatting laten driften.

Dit script vergelijkt vier fases zonder de service te starten (zodat een
verse BNO055-kalibratie in RAM behouden blijft):

* ``RAIL_OFF``     — rail uit, baseline.
* ``RAIL_ON_MID``  — rail aan, beide servo's op ``center_us`` (houdkracht-
  stroom, geen beweging).
* ``S1_JITTER``    — servo 1 schuift ±150 µs t.o.v. center (bekabeling
  actief belast, PWM-edges snijden).
* ``RAIL_OFF_END`` — rail weer uit; herstelt de baseline of niet?

Output toont per sample Euler + gravity + ``|g|`` + cal-status zodat je
visueel ziet of ``gx``/``gy`` verschuiven tussen fases of ``sys`` zakt
van 3 naar 0. Run bijvoorbeeld vanaf de repo-root op de Zero::

    sudo systemctl stop cansat-radio-protocol
    source .venv/bin/activate
    python scripts/gimbal/test_bno_rail.py

Vereist: ``pigpio`` + ``smbus2`` (``pip install -e '.[gimbal,sensors]'``)
en ``sudo systemctl start pigpiod``.
"""

from __future__ import annotations

import argparse
import sys
import time
from math import sqrt
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
	sys.path.insert(0, str(_SRC))


def _sample(imu, tag: str) -> None:
	gx, gy, gz = imu.read_gravity()
	h, r, p = imu.read_euler()
	s, g, a, m = imu.calibration_status()
	n = sqrt(gx * gx + gy * gy + gz * gz)
	print(
		f"{tag}  h={h:6.1f} r={r:+5.1f} p={p:+5.1f}   "
		f"gx={gx:+5.2f} gy={gy:+5.2f} gz={gz:+5.2f}  |g|={n:4.2f}   "
		f"cs={s}/{g}/{a}/{m}"
	)


def _phase(imu, name: str, secs: float, hz: float) -> None:
	print(f"\n=== {name} (t={secs:.0f}s, {hz:g} Hz) ===")
	n = max(1, int(secs * hz))
	for i in range(n):
		_sample(imu, f"[{name:<13} {i+1:2d}/{n:2d}]")
		time.sleep(1.0 / hz)


def main() -> int:
	p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	p.add_argument("--enable-pin", type=int, default=6, help="rail-enable BCM (default 6)")
	p.add_argument("--s1-pin", type=int, default=13)
	p.add_argument("--s2-pin", type=int, default=12)
	p.add_argument("--s1-center", type=int, default=1100)
	p.add_argument("--s2-center", type=int, default=2100)
	p.add_argument("--s1-jitter", type=int, default=150, help="±µs swing voor servo1-jitter")
	p.add_argument("--secs", type=float, default=10.0, help="duur per fase (s)")
	p.add_argument("--hz", type=float, default=2.0, help="sample rate (Hz)")
	args = p.parse_args()

	try:
		import pigpio
	except ImportError:
		print("pigpio niet gevonden — `pip install -e '.[gimbal]'`", file=sys.stderr)
		return 1
	try:
		from cansat_hw.sensors.bno055 import BNO055
	except ImportError as e:
		print(f"cansat_hw import faalde: {e}", file=sys.stderr)
		return 1

	pi = pigpio.pi()
	if not pi.connected:
		print("pigpiod draait niet — `sudo systemctl start pigpiod`", file=sys.stderr)
		return 2

	try:
		pi.set_mode(args.enable_pin, pigpio.OUTPUT)
		pi.write(args.enable_pin, 0)
		pi.set_servo_pulsewidth(args.s1_pin, 0)
		pi.set_servo_pulsewidth(args.s2_pin, 0)

		with BNO055(1, 0x28) as imu:
			print(f"chip 0x{imu.chip_id:02X}  enable=BCM{args.enable_pin}  "
				f"s1=BCM{args.s1_pin}@{args.s1_center}µs  "
				f"s2=BCM{args.s2_pin}@{args.s2_center}µs")

			_phase(imu, "RAIL_OFF", args.secs, args.hz)

			pi.write(args.enable_pin, 1)
			pi.set_servo_pulsewidth(args.s1_pin, args.s1_center)
			pi.set_servo_pulsewidth(args.s2_pin, args.s2_center)
			_phase(imu, "RAIL_ON_MID", args.secs, args.hz)

			for us in (
				args.s1_center - args.s1_jitter,
				args.s1_center + args.s1_jitter,
				args.s1_center,
			):
				pi.set_servo_pulsewidth(args.s1_pin, us)
				time.sleep(0.3)
			_phase(imu, "S1_JITTER", args.secs * 0.8, args.hz)

			pi.set_servo_pulsewidth(args.s1_pin, 0)
			pi.set_servo_pulsewidth(args.s2_pin, 0)
			pi.write(args.enable_pin, 0)
			_phase(imu, "RAIL_OFF_END", args.secs * 0.8, args.hz)

	finally:
		pi.set_servo_pulsewidth(args.s1_pin, 0)
		pi.set_servo_pulsewidth(args.s2_pin, 0)
		pi.write(args.enable_pin, 0)
		pi.stop()

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
