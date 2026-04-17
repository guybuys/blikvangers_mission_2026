#!/usr/bin/env python3
"""
BME280 I²C-test op de Raspberry Pi Zero 2 W (CONFIG / bench).

Vereist: I²C aan, gebruiker in groep ``i2c``, ``pip install smbus2`` (of ``pip install -e ".[sensors]"``).

Voorbeelden:
  python scripts/bme280_test.py --chip-id
  python scripts/bme280_test.py --samples 20 --interval 0
  python scripts/bme280_test.py --samples 100 --interval 0.01 --os 1
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
	sys.path.insert(0, str(_SRC))


def main() -> int:
	p = argparse.ArgumentParser(description="BME280 I²C smoke / rate test")
	p.add_argument("--bus", type=int, default=1, help="I²C-bus (meestal 1 = /dev/i2c-1)")
	p.add_argument("--address", type=lambda x: int(x, 0), default=0x77, help="I²C-adres (0x76 of 0x77)")
	p.add_argument(
		"--os",
		type=int,
		default=1,
		choices=(1, 2, 3, 4, 5),
		help="Oversampling 1..5 (Pico-compat); 1 = snelst, meer ruis; 5 = langzamer, stiller",
	)
	p.add_argument("--chip-id", action="store_true", help="Lees chip-id (0x60) en stop")
	p.add_argument("--samples", type=int, default=10, help="Aantal metingen")
	p.add_argument(
		"--interval",
		type=float,
		default=0.0,
		help="Extra pauze in seconden tussen samples (0 = zo snel mogelijk)",
	)
	args = p.parse_args()

	i2c_dev = Path(f"/dev/i2c-{args.bus}")
	if not i2c_dev.exists():
		print(f"Geen {i2c_dev} — zet I²C aan (raspi-config) en reboot.", file=sys.stderr)
		return 1

	try:
		from cansat_hw.sensors.bme280 import BME280
	except ImportError as e:
		print(e, file=sys.stderr)
		return 1

	try:
		with BME280(args.bus, args.address, oversampling=args.os) as bme:
			cid = bme.chip_id
			print(f"Chip ID: 0x{cid:02X} (verwacht 0x60 voor BME280)")
			if cid != 0x60:
				print("Waarschuwing: geen typische BME280 id — check adres (0x76/0x77).", file=sys.stderr)
			if args.chip_id:
				return 0

			pressures: list[float] = []
			t0 = time.perf_counter()
			for i in range(args.samples):
				t_c, p_hpa, rh = bme.read()
				pressures.append(p_hpa)
				if args.interval > 0:
					time.sleep(args.interval)
				if (i + 1) % max(1, args.samples // 10) == 0 or i == 0:
					print(f"  [{i+1}/{args.samples}]  {p_hpa:.2f} hPa  {t_c:.2f} °C  {rh:.1f} %RH")
			dt = time.perf_counter() - t0
			rate = args.samples / dt if dt > 0 else 0.0
			print(
				f"Klaar: {args.samples} samples in {dt:.3f} s → {rate:.1f} Hz  |  "
				f"p min/max {min(pressures):.2f} / {max(pressures):.2f} hPa  "
				f"stdev {statistics.pstdev(pressures):.3f} hPa"
			)
	except OSError as e:
		print(f"I²C-fout: {e}", file=sys.stderr)
		print("  sudo usermod -aG i2c $USER  — uitloggen — of i2cdetect -y 1", file=sys.stderr)
		return 2
	except RuntimeError as e:
		print(e, file=sys.stderr)
		return 3

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
