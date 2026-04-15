#!/usr/bin/env python3
"""
CanSat flight computer — Raspberry Pi Zero 2 W — radio commando-loop (CONFIG / LAUNCH).

Luistert op RFM69 naar pakketten voor dit node-ID, beantwoordt met dezelfde
tekstregels als de base station op de **Pico** (Thonny: `protocol.py` / README_basestation).

Let op: dit script leest **geen** toetsenbord — commando's met ``!`` zijn alleen voor de
**Pico**-CLI in Thonny. Op de Zero typ je hier niets; alleen Ctrl+C om te stoppen.

Start op de CanSat (Zero 2 W), venv actief, SPI aan:

  python scripts/cansat_radio_protocol.py
  python scripts/cansat_radio_protocol.py --poll 0.5 --verbose
  python scripts/cansat_radio_protocol.py --reply-delay 0      # geen extra pauze vóór antwoord

Stop: Ctrl+C
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("GPIOZERO_PIN_FACTORY", "rpigpio")

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
	sys.path.insert(0, str(_SRC))


def main() -> int:
	p = argparse.ArgumentParser(description="CanSat (Zero 2 W) RFM69 wire-protocol loop")
	p.add_argument("--freq", type=float, default=433.0, help="MHz")
	p.add_argument("--node", type=int, default=120, help="Dit toestel (CanSat) RadioHead-adres")
	p.add_argument("--dest", type=int, default=100, help="Standaard bestemming (basis); replies gaan naar afzender")
	p.add_argument("--key", type=str, default="CANSAT_2025-2026", help="16-byte UTF-8 AES")
	p.add_argument("--reset-pin", type=int, default=25)
	p.add_argument("--spi-bus", type=int, default=0)
	p.add_argument("--spi-device", type=int, default=0)
	p.add_argument("--tx-power", type=int, default=13)
	p.add_argument("--poll", type=float, default=1.0, help="receive()-timeout in seconden")
	p.add_argument(
		"--reply-delay",
		type=float,
		default=0.08,
		metavar="S",
		help="seconden wachten na verwerken vóór antwoord-TX (half-duplex marge voor de Pico; standaard 0.08, 0=uit)",
	)
	p.add_argument("--verbose", action="store_true")
	args = p.parse_args()
	if args.reply_delay < 0:
		print("--reply-delay must be >= 0", file=sys.stderr)
		return 1

	key = args.key.encode("utf-8")
	if len(key) != 16:
		print("--key must be exactly 16 UTF-8 bytes", file=sys.stderr)
		return 1

	spi_dev = Path(f"/dev/spidev{args.spi_bus}.{args.spi_device}")
	if not spi_dev.exists():
		print("Missing", spi_dev, "— enable SPI", file=sys.stderr)
		return 1

	from cansat_hw.radio import RFM69
	from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line

	rfm = RFM69(spi_bus=args.spi_bus, spi_device=args.spi_device, reset_pin=args.reset_pin)
	state = RadioRuntimeState()
	try:
		rfm.frequency_mhz = args.freq
		rfm.encryption_key = key
		rfm.node = args.node
		rfm.destination = args.dest
		rfm.tx_power = args.tx_power
		rfm.receive_timeout = min(args.poll, 10.0)

		banner_tail = "Ctrl+C stop — geen !-commando's hier; die zijn voor de Pico base station"
		if args.reply_delay > 0:
			banner_tail += f" — reply-delay {args.reply_delay}s"
		print(
			"CanSat (Zero 2 W) radio protocol — node",
			args.node,
			"freq",
			rfm.frequency_mhz,
			"MHz  mode",
			state.mode,
			f"({banner_tail})",
		)
		while True:
			# with_header=True: afzender = byte 1 voor reply destination
			# with_ack=False: geen RadioHead-ACK vóór onze tekstantwoord
			pkt = rfm.receive(timeout=args.poll, with_header=True, with_ack=False, keep_listening=True)
			if pkt is None:
				continue
			if len(pkt) < 5:
				continue
			from_node = pkt[1]
			try:
				line = pkt[4:].decode("utf-8")
			except UnicodeDecodeError:
				reply = b"ERR UTF8"
			else:
				if args.verbose:
					print("RX from", from_node, ":", line.strip())
				reply = handle_wire_line(rfm, state, line)
				if args.verbose:
					print("TX to  ", from_node, ":", reply.decode("utf-8", errors="replace"))

			if args.reply_delay > 0:
				time.sleep(args.reply_delay)
			ok = rfm.send(reply, keep_listening=True, destination=from_node)
			if args.verbose:
				print("reply TX ok:", ok, " bytes:", len(reply))
			elif not ok:
				print("WARN: reply TX failed (radio timeout)", file=sys.stderr)
			if args.verbose:
				print("state.mode =", state.mode)
	except KeyboardInterrupt:
		print("\nStopped.")
		return 0
	finally:
		rfm.close()

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
