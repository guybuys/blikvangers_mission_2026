#!/usr/bin/env python3
"""Gimbal axis-mapping diagnose: welke servo kantelt welke as?

Na de hardware-swap gedraagt de gimbal-regelaar zich alsof er positive feedback
is, maar dat kan twee oorzaken hebben:

1. **Sign omgedraaid** — servo1 kantelt *wel* de gx-as, maar ``+µs`` geeft
   ``+tilt`` i.p.v. ``-tilt`` (servo fysiek 180° gedraaid gemonteerd).
2. **Assen verwisseld** — servo1 kantelt in werkelijkheid de gy-as en servo2
   de gx-as (GPIO verkeerd om gepatched of servo's op de andere gimbal-ring
   gemonteerd).

Met signs alleen wegkomen uit (2) is onmogelijk: de regelaar stuurt dan
nog steeds de verkeerde servo op basis van de verkeerde fout.

Wat dit script doet:

* Rail aan, beide servo's naar ``center_us``.
* Baseline: 2 s lang ``(gx, gy, gz)`` samplen.
* Servo1 **één stap richting min_us** (300 µs), 1.5 s wachten, meten.
* Servo1 terug naar center, daarna **één stap richting max_us**, meten.
* Hetzelfde voor servo2.
* Rail uit.

Aan het einde print het script een tabel::

    servo1  min→max  Δgx=+2.10  Δgy=-0.04   → primaire as: X
    servo2  min→max  Δgx=+0.05  Δgy=-1.95   → primaire as: Y

Uit de tabel kan je direct aflezen:

* "primaire as" vertelt of de bedrading/montage **X/Y** of **verwisseld** is.
  Verwacht bij een gezonde roll-pitch gimbal: servo1=X, servo2=Y (of omgekeerd,
  maar consistent met je controller-assumptie).
* **Teken van Δ** vertelt de sign. Bij een negatief-feedback controller wil je
  dat `+µs → -tilt` (dus Δ negatief bij min→max).

Voorbeeld (bevestigt cross-axis swap): ``servo1 min→max Δgx=+0.04 Δgy=+2.10``
→ servo1 stuurt gy, niet gx. Fix: ``--swap-control-axes`` in gimbal_level.py
of invert_axes in GimbalConfig voor de productie-loop.

Gebruik (repo-root, service moet uit)::

    sudo systemctl stop cansat-radio-protocol
    source .venv/bin/activate
    python scripts/gimbal/test_axis_response.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from math import sqrt
from pathlib import Path
from statistics import mean
from typing import List, Tuple

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
	sys.path.insert(0, str(_SRC))


def _sample_grav(imu, secs: float) -> Tuple[float, float, float]:
	"""Gemiddelde (gx, gy, gz) over ``secs`` seconden bij ~50 Hz; |g| in band 7–12."""
	gxs: List[float] = []
	gys: List[float] = []
	gzs: List[float] = []
	t_end = time.monotonic() + secs
	while time.monotonic() < t_end:
		try:
			gx, gy, gz = imu.read_gravity()
		except OSError:
			time.sleep(0.01)
			continue
		n = sqrt(gx * gx + gy * gy + gz * gz)
		if 7.0 <= n <= 12.5:
			gxs.append(gx)
			gys.append(gy)
			gzs.append(gz)
		time.sleep(0.02)
	if not gxs:
		return (float("nan"),) * 3
	return mean(gxs), mean(gys), mean(gzs)


def _load_cal(path: Path) -> Tuple[dict, dict]:
	data = json.loads(path.read_text(encoding="utf-8"))
	return data["servo1"], data["servo2"]


def _move(pi, gpio: int, us: int) -> None:
	pi.set_servo_pulsewidth(gpio, us)


def _step_one_servo(
	pi, imu, gpio: int, center: int, low: int, high: int, settle_s: float
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
	"""Zet servo op ``low``, meet. Zet op ``high``, meet. Return (g_low, g_high)."""
	_move(pi, gpio, low)
	time.sleep(settle_s)
	g_low = _sample_grav(imu, 1.2)
	_move(pi, gpio, high)
	time.sleep(settle_s)
	g_high = _sample_grav(imu, 1.2)
	_move(pi, gpio, center)
	time.sleep(settle_s * 0.5)
	return g_low, g_high


def _primary_axis(dx: float, dy: float) -> str:
	"""Welke as domineert? Drempel 2× scheidt 'duidelijk X' van 'duidelijk Y'."""
	ax, ay = abs(dx), abs(dy)
	if ax < 0.3 and ay < 0.3:
		return "geen (dode gimbal-band?)"
	if ax >= 2.0 * ay:
		return "X  (primair: gx)"
	if ay >= 2.0 * ax:
		return "Y  (primair: gy)"
	return "X/Y gekoppeld (mechanische kruiskoppeling)"


def main() -> int:
	p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	p.add_argument(
		"--cal",
		type=Path,
		default=_ROOT / "config" / "gimbal" / "servo_calibration.json",
	)
	p.add_argument("--bus", type=int, default=1)
	p.add_argument("--addr", type=lambda s: int(s, 0), default=0x28)
	p.add_argument("--enable-pin", type=int, default=6)
	p.add_argument("--swing", type=int, default=300, help="µs offset richting min/max")
	p.add_argument("--settle", type=float, default=1.2, help="wachttijd na servo-beweging (s)")
	args = p.parse_args()

	try:
		import pigpio
	except ImportError:
		print("pigpio niet gevonden — `pip install -e '.[gimbal]'`", file=sys.stderr)
		return 1
	try:
		from cansat_hw.sensors.bno055 import BNO055
	except ImportError as e:
		print(f"cansat_hw import: {e}", file=sys.stderr)
		return 1

	if not args.cal.is_file():
		print(f"Geen calibratie op {args.cal}", file=sys.stderr)
		return 2

	s1, s2 = _load_cal(args.cal)
	c1, c2 = int(s1["center_us"]), int(s2["center_us"])
	g1, g2 = int(s1["gpio"]), int(s2["gpio"])
	# Houd de swing binnen de gekalibreerde min/max (anders kan servo mechanisch
	# klemmen — zelfde reden dat ServoController dit clampt).
	lo1 = max(int(s1["min_us"]), c1 - args.swing)
	hi1 = min(int(s1["max_us"]), c1 + args.swing)
	lo2 = max(int(s2["min_us"]), c2 - args.swing)
	hi2 = min(int(s2["max_us"]), c2 + args.swing)

	pi = pigpio.pi()
	if not pi.connected:
		print("pigpiod draait niet — `sudo systemctl start pigpiod`", file=sys.stderr)
		return 2

	try:
		with BNO055(args.bus, args.addr) as imu:
			print(f"BNO055 chip 0x{imu.chip_id:02X}")
			print(f"servo1 gpio={g1} center={c1} swing [{lo1}..{hi1}]")
			print(f"servo2 gpio={g2} center={c2} swing [{lo2}..{hi2}]")

			pi.set_mode(args.enable_pin, pigpio.OUTPUT)
			pi.write(args.enable_pin, 1)
			time.sleep(0.3)

			pi.set_servo_pulsewidth(g1, c1)
			pi.set_servo_pulsewidth(g2, c2)
			time.sleep(args.settle)

			print("\n[BASELINE] beide servo's op center")
			gb = _sample_grav(imu, 2.0)
			print(f"   gx={gb[0]:+.3f}  gy={gb[1]:+.3f}  gz={gb[2]:+.3f}")

			print("\n[SERVO1] low → high (gpio %d)" % g1)
			s1_lo, s1_hi = _step_one_servo(pi, imu, g1, c1, lo1, hi1, args.settle)
			d1x = s1_hi[0] - s1_lo[0]
			d1y = s1_hi[1] - s1_lo[1]
			print(f"   low  gx={s1_lo[0]:+.3f} gy={s1_lo[1]:+.3f}")
			print(f"   high gx={s1_hi[0]:+.3f} gy={s1_hi[1]:+.3f}")
			print(f"   Δ(gx,gy)=({d1x:+.3f},{d1y:+.3f})")

			print("\n[SERVO2] low → high (gpio %d)" % g2)
			s2_lo, s2_hi = _step_one_servo(pi, imu, g2, c2, lo2, hi2, args.settle)
			d2x = s2_hi[0] - s2_lo[0]
			d2y = s2_hi[1] - s2_lo[1]
			print(f"   low  gx={s2_lo[0]:+.3f} gy={s2_lo[1]:+.3f}")
			print(f"   high gx={s2_hi[0]:+.3f} gy={s2_hi[1]:+.3f}")
			print(f"   Δ(gx,gy)=({d2x:+.3f},{d2y:+.3f})")

			print("\n=== DIAGNOSE ===")
			print(f"servo1 primair: {_primary_axis(d1x, d1y)}")
			print(f"servo2 primair: {_primary_axis(d2x, d2y)}")
			# Aanbevolen mapping: s1 op ex, s2 op ey met negatief teken ⇒ +µs
			# geeft -tilt. Als de gemeten Δ positief is bij min→max, is de servo
			# qua teken juist (want de regelaar doet ook -kx × e). Bij negatieve
			# Δ moet de kx/ky dus omgedraaid.
			s1_axis_x = abs(d1x) > 2.0 * abs(d1y) and abs(d1x) > 0.3
			s1_axis_y = abs(d1y) > 2.0 * abs(d1x) and abs(d1y) > 0.3
			s2_axis_x = abs(d2x) > 2.0 * abs(d2y) and abs(d2x) > 0.3
			s2_axis_y = abs(d2y) > 2.0 * abs(d2x) and abs(d2y) > 0.3

			if s1_axis_y and s2_axis_x:
				print("→ assen **verwisseld**: gebruik --swap-control-axes in gimbal_level.py")
				sign_s1 = "positief" if d1y > 0 else "negatief (kx omdraaien)"
				sign_s2 = "positief" if d2x > 0 else "negatief (ky omdraaien)"
				print(f"   servo1 gy-sign: {sign_s1}")
				print(f"   servo2 gx-sign: {sign_s2}")
			elif s1_axis_x and s2_axis_y:
				print("→ assen in verwachte volgorde (servo1=X, servo2=Y)")
				sign_s1 = "positief" if d1x > 0 else "negatief (kx omdraaien)"
				sign_s2 = "positief" if d2y > 0 else "negatief (ky omdraaien)"
				print(f"   servo1 gx-sign: {sign_s1}")
				print(f"   servo2 gy-sign: {sign_s2}")
			else:
				print("→ mapping onduidelijk — mechanische kruiskoppeling of te kleine swing.")
				print(f"   swing={args.swing}µs evt verhogen of montage controleren.")

	finally:
		try:
			pi.set_servo_pulsewidth(g1, 0)
			pi.set_servo_pulsewidth(g2, 0)
			pi.write(args.enable_pin, 0)
		finally:
			pi.stop()

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
