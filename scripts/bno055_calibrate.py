#!/usr/bin/env python3
"""Interactieve BNO055-kalibratie + profiel-persistence.

De BNO055 vergeet z'n kalibratie bij elke power-cycle. Deze tool voert
operator door een live kalibratie-sessie, dumpt het 22-byte profiel in
``config/bno055_calibration.json`` (Zero-only, buiten git) en/of in
``config/bno055_calibration.default.json`` (best-effort default in de repo).

Bij boot leest :mod:`scripts.cansat_radio_protocol` dezelfde JSON en schrijft
de offsets terug in de chip — zodat ``GET GIMBAL`` direct plausibele
``gx/gy/gz`` heeft en de gimbal-loop niet meer saturatie draait op een
ongekalibreerde accel.

Flow:

1. Open de BNO055 op I²C-bus 1, adres 0x28.
2. Print live de ``(sys, gyr, acc, mag)``-cal. De operator:
   * Laat de sensor ~5 s onbeweeglijk liggen → ``gyr`` gaat naar 3.
   * Doet een **6-posities-accelrotatie** (elke as 3–5 s stil) → ``acc`` → 3.
   * Doet een **figuur-8** door de lucht (~20 s) → ``mag`` → 3.
   * ``sys`` volgt automatisch zodra bovenstaande rond zijn.
3. Zodra het doel bereikt is (standaard: ``acc ≥ 2`` én ``mag ≥ 2``), dumpt
   de tool het profiel. Wachten op 3/3/3/3 kan met ``--strict``.

Gebruik (op de Zero, repo-root):

.. code-block:: bash

   sudo systemctl stop cansat-radio-protocol
   source .venv/bin/activate
   python scripts/bno055_calibrate.py --save
   # of, om óók het default-profiel in de repo te updaten:
   python scripts/bno055_calibrate.py --save --save-default

Vereist: ``smbus2`` (``pip install -e '.[sensors]'``). De service moet uit,
anders vecht die over de I²C-bus.
"""

from __future__ import annotations

import argparse
import sys
import time
from math import sqrt
from pathlib import Path
from typing import Optional, Tuple

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
	sys.path.insert(0, str(_SRC))


_DEFAULT_ZERO_PROFILE = _ROOT / "config" / "bno055_calibration.json"
_DEFAULT_REPO_PROFILE = _ROOT / "config" / "bno055_calibration.default.json"


def _cal_ok(cs: Tuple[int, int, int, int], *, strict: bool) -> bool:
	"""Criterium 'klaar met kalibreren'.

	Default (pragmatisch): accel én mag ≥ 2. In die zone is ``read_gravity``
	stabiel binnen enkele graden, ruim voldoende voor de gimbal-loop. Met
	``--strict`` wacht je op de volle 3/3/3/3 (nuttig voor een default-profiel
	dat je in de repo commit).
	"""
	_sys, _gyr, acc, mag = cs
	threshold = 3 if strict else 2
	return acc >= threshold and mag >= threshold


def _live_calibrate(imu, *, timeout_s: float, strict: bool, hz: float) -> Tuple[int, int, int, int]:
	"""Pol ``calibration_status`` tot target bereikt of timeout; print changes."""
	deadline = time.monotonic() + timeout_s
	last: Optional[Tuple[int, int, int, int]] = None
	period = 1.0 / max(hz, 0.5)
	print(
		"Rotatie-plan (elk ~5 s): +X up, −X up, +Y up, −Y up, +Z up, −Z up."
		"  Dan figuur-8 door de lucht tot mag=3."
	)
	print("Start — druk Ctrl-C om te stoppen met huidige status.\n")
	try:
		while time.monotonic() < deadline:
			cs = imu.calibration_status()
			if cs != last:
				s, g, a, m = cs
				gx, gy, gz = imu.read_gravity()
				n = sqrt(gx * gx + gy * gy + gz * gz)
				print(
					f"t={timeout_s - (deadline - time.monotonic()):6.1f}s  "
					f"sys={s} gyr={g} acc={a} mag={m}  "
					f"|g|={n:4.2f} m/s²  (gx={gx:+5.2f} gy={gy:+5.2f} gz={gz:+5.2f})"
				)
				last = cs
			if _cal_ok(cs, strict=strict):
				return cs
			time.sleep(period)
	except KeyboardInterrupt:
		print("\n[ctrl-c] stoppen met huidige cal-status")
	return last if last is not None else imu.calibration_status()


def main() -> int:
	p = argparse.ArgumentParser(description="BNO055 kalibratie + save-to-JSON")
	p.add_argument("--bus", type=int, default=1)
	p.add_argument("--address", type=lambda x: int(x, 0), default=0x28)
	p.add_argument(
		"--timeout", type=float, default=300.0,
		help="Max tijd (s) voor de cal-sessie (default 5 min)",
	)
	p.add_argument(
		"--strict", action="store_true",
		help="Wacht op volle 3/3/3/3 i.p.v. accel+mag ≥ 2 (trager maar nauwkeuriger)",
	)
	p.add_argument(
		"--hz", type=float, default=2.0,
		help="Sample-rate van de status-polling (Hz)",
	)
	p.add_argument(
		"--save", nargs="?", const=str(_DEFAULT_ZERO_PROFILE),
		help=(
			"Schrijf het profiel naar deze JSON (default: "
			f"{_DEFAULT_ZERO_PROFILE.relative_to(_ROOT)})"
		),
	)
	p.add_argument(
		"--save-default", nargs="?", const=str(_DEFAULT_REPO_PROFILE),
		help=(
			"Schrijf het profiel óók naar de repo-default (default: "
			f"{_DEFAULT_REPO_PROFILE.relative_to(_ROOT)}). "
			"Commit dit als je een betrouwbaar starting-point wilt delen."
		),
	)
	p.add_argument(
		"--restore", metavar="PATH",
		help=(
			"Skip live cal; laad profiel uit PATH en schrijf het in de chip "
			"(dry-run van wat de radio-service bij boot doet)"
		),
	)
	p.add_argument(
		"--dump-only", action="store_true",
		help="Geen cal-sessie: lees huidig profiel uit de chip en dump het",
	)
	args = p.parse_args()

	i2c_dev = Path(f"/dev/i2c-{args.bus}")
	if not i2c_dev.exists():
		print(f"Geen {i2c_dev} — zet I²C aan of gebruik een andere --bus.", file=sys.stderr)
		return 1

	try:
		from cansat_hw.sensors.bno055 import (
			BNO055,
			load_profile_file,
			save_profile_file,
		)
	except ImportError as e:
		print(f"cansat_hw import faalde: {e}", file=sys.stderr)
		return 1

	try:
		with BNO055(args.bus, args.address) as imu:
			print(f"BNO055 chip 0x{imu.chip_id:02X} op bus {args.bus} adres 0x{args.address:02X}")

			if args.restore:
				data = load_profile_file(args.restore)
				if data is None:
					print(f"Geen profiel op {args.restore}", file=sys.stderr)
					return 2
				print(f"Schrijven profiel ({len(data)} B) uit {args.restore}")
				imu.write_calibration_profile(data)
				# Even laten zetten zodat de nieuwe sys-cal zichtbaar wordt.
				time.sleep(2.0)
				cs = imu.calibration_status()
				print(f"Na restore: sys={cs[0]} gyr={cs[1]} acc={cs[2]} mag={cs[3]}")
				return 0

			if args.dump_only:
				cs = imu.calibration_status()
				print(f"Huidige cal: sys={cs[0]} gyr={cs[1]} acc={cs[2]} mag={cs[3]}")
			else:
				cs = _live_calibrate(imu, timeout_s=args.timeout, strict=args.strict, hz=args.hz)
				if not _cal_ok(cs, strict=args.strict):
					print(
						f"\nWAARSCHUWING: cal-doel niet bereikt (sys={cs[0]} gyr={cs[1]} "
						f"acc={cs[2]} mag={cs[3]}); profiel sla ik NIET op.",
						file=sys.stderr,
					)
					return 3
				print(f"\nCal OK: sys={cs[0]} gyr={cs[1]} acc={cs[2]} mag={cs[3]}")

			data = imu.read_calibration_profile()
			print(f"Profile hex ({len(data)} B): {data.hex()}")

			if args.save:
				path = save_profile_file(args.save, data, calibration_status=cs)
				print(f"Opgeslagen: {path}")
			if args.save_default:
				path = save_profile_file(args.save_default, data, calibration_status=cs)
				print(f"Default bijgewerkt: {path}")

			if not (args.save or args.save_default or args.dump_only):
				print(
					"Geen --save en geen --save-default meegegeven — profiel alleen gedumpt.",
					file=sys.stderr,
				)

	except Exception as e:  # noqa: BLE001
		print(f"BNO055-fout: {e}", file=sys.stderr)
		return 1
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
