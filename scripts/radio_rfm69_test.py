#!/usr/bin/env python3
"""
Hardware smoke test for RFM69HCW on Raspberry Pi (SPI0 / CE0, reset BCM GPIO).

Run on the Pi after: pip install -e .  (from repo root) and SPI + gpio groups.

Examples:
  python scripts/radio_rfm69_test.py --version-only
  python scripts/radio_rfm69_test.py --chip-temp   # interne SX1231-temp (kort RX uit)
  python scripts/radio_rfm69_test.py --listen
  python scripts/radio_rfm69_test.py --send "hello"          # wacht op ACK van ontvanger
  python scripts/radio_rfm69_test.py --send "hello" --no-ack # alleen zenden (geen gateway nodig)

Pi: minder gpiozero-warnings na  pip install -e ".[rpi]"  (RPi.GPIO in venv).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Voor gpiozero: liever RPi.GPIO dan NativeFactory (installeer: pip install -e ".[rpi]")
os.environ.setdefault("GPIOZERO_PIN_FACTORY", "rpigpio")

# Repo root: allow running without editable install
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
	sys.path.insert(0, str(_SRC))


def main() -> int:
	parser = argparse.ArgumentParser(description="RFM69HCW Pi smoke test")
	parser.add_argument("--version-only", action="store_true", help="Read chip version register and exit")
	parser.add_argument(
		"--chip-temp",
		action="store_true",
		help="Lees interne chip-temperatuur (± grove waarde; onderbreekt kort RX)",
	)
	parser.add_argument("--listen", action="store_true", help="Print received packets (ACK to sender if requested)")
	parser.add_argument("--send", type=str, metavar="TEXT", help="Send one packet (default: wait for ACK)")
	parser.add_argument(
		"--no-ack",
		action="store_true",
		help="With --send: only transmit, do not wait for ACK (geen ontvanger nodig)",
	)
	parser.add_argument("--freq", type=float, default=433.0, metavar="MHZ", help="RF frequency (MHz)")
	parser.add_argument("--node", type=int, default=120, help="This node's RadioHead address")
	parser.add_argument("--dest", type=int, default=100, help="Destination address (base station)")
	parser.add_argument(
		"--key",
		type=str,
		default="CANSAT_2025-2026",
		help="16-byte AES key (exact length 16) or shorter string (UTF-8, must be 16 bytes)",
	)
	parser.add_argument("--reset-pin", type=int, default=25, help="BCM GPIO for RFM69 reset")
	parser.add_argument(
		"--dio0-pin",
		type=int,
		default=None,
		metavar="BCM",
		help="BCM GPIO for RFM69 DIO0 IRQ (see docs/rpi_pinning.md); omit to poll IRQ flags over SPI",
	)
	parser.add_argument("--spi-bus", type=int, default=0)
	parser.add_argument("--spi-device", type=int, default=0)
	parser.add_argument("--tx-power", type=int, default=13, help="dBm (HCW: up to 20)")
	args = parser.parse_args()

	try:
		from cansat_hw.radio import RFM69
	except ImportError as e:
		print("Import failed:", e, file=sys.stderr)
		print("Install on the Pi:  pip install -e .", file=sys.stderr)
		return 1

	key = args.key.encode("utf-8")
	if len(key) != 16:
		print("--key must be exactly 16 bytes as UTF-8", file=sys.stderr)
		return 1

	modes = (
		int(args.version_only)
		+ int(args.chip_temp)
		+ int(args.listen)
		+ int(args.send is not None)
	)
	if modes != 1:
		parser.error("choose exactly one of: --version-only, --chip-temp, --listen, --send TEXT")
	if args.no_ack and args.send is None:
		parser.error("--no-ack only applies with --send")

	spi_dev = Path(f"/dev/spidev{args.spi_bus}.{args.spi_device}")
	if not spi_dev.exists():
		print(f"Fout: {spi_dev} bestaat niet — SPI staat waarschijnlijk uit.", file=sys.stderr)
		print("  sudo raspi-config → Interface Options → SPI → Enable", file=sys.stderr)
		print("  of in /boot/firmware/config.txt: dtparam=spi=on   → daarna sudo reboot", file=sys.stderr)
		print("  Daarna: ls /dev/spidev*  (verwacht o.a. /dev/spidev0.0)", file=sys.stderr)
		return 1

	rfm = None
	try:
		rfm = RFM69(
			spi_bus=args.spi_bus,
			spi_device=args.spi_device,
			reset_pin=args.reset_pin,
			dio0_pin=args.dio0_pin,
		)
		rfm.frequency_mhz = args.freq
		rfm.encryption_key = key
		rfm.node = args.node
		rfm.destination = args.dest
		rfm.tx_power = args.tx_power

		ver = rfm.version
		print(f"RFM69 version register: 0x{ver:02X} (expect 0x24 for SX1231 / common RFM69)")

		if args.version_only:
			return 0

		if args.chip_temp:
			try:
				t = rfm.temperature
			except Exception as e:
				print("chip-temp failed:", e, file=sys.stderr)
				return 2
			print(f"RFM69 chip temperature (rough): {t:.1f} °C")
			return 0

		if args.listen:
			rfm.node = args.node
			print(f"Listening as node {args.node}, freq={rfm.frequency_mhz} MHz, ACK replies on")
			while True:
				pkt = rfm.receive(with_ack=True)
				if pkt is not None:
					print("RX:", pkt)

		if args.send is not None:
			data = args.send.encode("utf-8")
			if len(data) > 60:
				print("Payload max 60 bytes", file=sys.stderr)
				return 1
			if args.no_ack:
				ok = rfm.send(data, keep_listening=False)
				print("TX OK" if ok else "TX failed (timeout)")
				return 0 if ok else 2
			rfm.ack_retries = 3
			rfm.ack_wait = 0.5
			ok = rfm.send_with_ack(data)
			if ok:
				print("ACK OK")
			else:
				print("ACK missing — is er een ontvanger op --dest met dezelfde --freq en --key?")
				print("  Of test zonder ACK:  --send \"hello\" --no-ack")
			return 0 if ok else 2
	finally:
		if rfm is not None:
			rfm.close()


if __name__ == "__main__":
	raise SystemExit(main())
