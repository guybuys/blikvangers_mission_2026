#!/usr/bin/env python3
"""
BNO055 I²C-test op de Raspberry Pi Zero 2 W (CONFIG / bench).

Vereist: I²C aan, gebruiker in groep ``i2c``, ``pip install smbus2``.

Voorbeelden:
  python scripts/bno055_test.py --chip-id
  python scripts/bno055_test.py --samples 30 --interval 0.05
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
	sys.path.insert(0, str(_SRC))


def main() -> int:
	p = argparse.ArgumentParser(description="BNO055 I²C smoke test")
	p.add_argument("--bus", type=int, default=1)
	p.add_argument("--address", type=lambda x: int(x, 0), default=0x28)
	p.add_argument("--chip-id", action="store_true")
	p.add_argument("--samples", type=int, default=15)
	p.add_argument("--interval", type=float, default=0.1)
	args = p.parse_args()

	i2c_dev = Path(f"/dev/i2c-{args.bus}")
	if not i2c_dev.exists():
		print(f"Geen {i2c_dev} — zet I²C aan.", file=sys.stderr)
		return 1

	try:
		from cansat_hw.sensors.bno055 import BNO055
	except ImportError as e:
		print(e, file=sys.stderr)
		return 1

	try:
		with BNO055(args.bus, args.address) as imu:
			cid = imu.chip_id
			print(f"Chip ID: 0x{cid:02X} (verwacht 0xA0 voor BNO055)")
			if cid != 0xA0:
				print("Waarschuwing: id klopt niet — check adres (0x28/0x29).", file=sys.stderr)
			if args.chip_id:
				return 0

			for i in range(args.samples):
				h, r, p = imu.read_euler()
				cs = imu.calibration_status()
				t = imu.temperature_c()
				print(
					f"  [{i+1}/{args.samples}]  h={h:7.2f}° r={r:7.2f}° p={p:7.2f}°  "
					f"cal sys/gyr/acc/mag {cs[0]}/{cs[1]}/{cs[2]}/{cs[3]}  T={t}°C"
				)
				if args.interval > 0:
					time.sleep(args.interval)
	except OSError as e:
		print(f"I²C-fout: {e}", file=sys.stderr)
		return 2
	except RuntimeError as e:
		print(e, file=sys.stderr)
		return 3

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
