#!/usr/bin/env python3
"""
CanSat flight computer — Raspberry Pi Zero 2 W — radio commando-loop (CONFIG / MISSION).

Luistert op RFM69 naar pakketten voor dit node-ID, beantwoordt met dezelfde
tekstregels als de base station op de **Pico** (Thonny: `protocol.py` / README_basestation).

Let op: dit script leest **geen** toetsenbord — commando's met ``!`` zijn alleen voor de
**Pico**-CLI in Thonny. Op de Zero typ je hier niets; alleen Ctrl+C om te stoppen.

Start op de CanSat (Zero 2 W), venv actief, SPI aan:

  python scripts/cansat_radio_protocol.py
  python scripts/cansat_radio_protocol.py --poll 0.5 --verbose
  python scripts/cansat_radio_protocol.py --reply-delay 0      # geen extra pauze vóór antwoord

Stop: **Ctrl+C**, of op afstand (half-duplex) het draad-commando ``STOP RADIO`` (antwoord
``OK STOP RADIO``; daarna stopt het proces — handig bij **systemd**-autostart). Met SSH:
``sudo systemctl stop …`` of ``kill`` op het PID werkt ook.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

os.environ.setdefault("GPIOZERO_PIN_FACTORY", "rpigpio")

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
	sys.path.insert(0, str(_SRC))

_DEFAULT_RUNTIME_PATH = _ROOT / "config" / "radio_runtime.json"


def _load_dotenv(path: Path) -> None:
	"""Mini-loader voor ``.env`` — zet variabelen in ``os.environ`` tenzij al gezet.

	Formaat: ``KEY=VALUE`` per regel, ``#`` voor commentaar, enkele/dubbele quotes
	rond de waarde mogen (worden gestript). Geen shell-expansie. We blijven
	bewust dep-vrij; voor een 'echte' parser: ``python-dotenv``.
	"""
	if not path.is_file():
		return
	try:
		text = path.read_text(encoding="utf-8")
	except OSError:
		return
	for raw in text.splitlines():
		line = raw.strip()
		if not line or line.startswith("#"):
			continue
		if "=" not in line:
			continue
		key, _, value = line.partition("=")
		key = key.strip()
		value = value.strip()
		if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
			value = value[1:-1]
		os.environ.setdefault(key, value)


_load_dotenv(_ROOT / ".env")


def _load_persisted_freq(path: Path) -> Optional[float]:
	try:
		data = json.loads(path.read_text(encoding="utf-8"))
	except (OSError, ValueError):
		return None
	try:
		return float(data.get("freq_mhz"))
	except (TypeError, ValueError):
		return None


def _save_persisted_freq(path: Path, mhz: float) -> Optional[str]:
	try:
		path.parent.mkdir(parents=True, exist_ok=True)
		tmp = path.with_suffix(path.suffix + ".tmp")
		tmp.write_text(json.dumps({"freq_mhz": float(mhz)}) + "\n", encoding="utf-8")
		tmp.replace(path)
		return None
	except OSError as e:
		return str(e)[:80]


def main() -> int:
	p = argparse.ArgumentParser(
		description="CanSat (Zero 2 W) RFM69 wire-protocol loop. "
		"Defaults kunnen via .env (CANSAT_RADIO_KEY/NODE/DEST/FREQ_MHZ) overschreven worden; "
		"CLI-args winnen altijd.",
	)
	p.add_argument(
		"--freq",
		type=float,
		default=float(os.environ.get("CANSAT_RADIO_FREQ_MHZ", "433.0")),
		help="MHz (default: $CANSAT_RADIO_FREQ_MHZ of 433.0; wordt overruled door persisted freq in --runtime-path)",
	)
	p.add_argument(
		"--node",
		type=int,
		default=int(os.environ.get("CANSAT_RADIO_NODE", "120")),
		help="Dit toestel (CanSat) RadioHead-adres (default: $CANSAT_RADIO_NODE of 120)",
	)
	p.add_argument(
		"--dest",
		type=int,
		default=int(os.environ.get("CANSAT_RADIO_DEST", "100")),
		help="Standaard bestemming (basis); replies gaan naar afzender (default: $CANSAT_RADIO_DEST of 100)",
	)
	p.add_argument(
		"--key",
		type=str,
		default=os.environ.get("CANSAT_RADIO_KEY", "CANSAT_2025-2026"),
		help="16-byte UTF-8 AES (default: $CANSAT_RADIO_KEY uit .env of demo-key)",
	)
	p.add_argument("--reset-pin", type=int, default=25)
	p.add_argument(
		"--dio0-pin",
		type=int,
		default=None,
		metavar="BCM",
		help="BCM GPIO voor RFM69 DIO0 (IRQ: PayloadReady/PacketSent); bv. 24 per pinning-doc. "
		"Weglaten = alleen SPI-pollen (hogere CPU-belasting)",
	)
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
	p.add_argument("--i2c-bus", type=int, default=1, help="I²C-bus voor sensoren (meestal 1)")
	p.add_argument(
		"--bme280-addr",
		type=lambda x: int(x, 0),
		default=0x76,
		help="BME280 I²C-adres (0x76 of 0x77)",
	)
	p.add_argument(
		"--bme280-os",
		type=int,
		default=16,
		choices=[1, 2, 4, 8, 16],
		help="BME280 oversampling (1/2/4/8/16); hogere waarden = minder ruis (±0.2 Pa bij x16). Default 16 voor vluchtgebruik.",
	)
	p.add_argument(
		"--bme280-iir",
		type=int,
		default=4,
		choices=[0, 2, 4, 8, 16],
		help="BME280 IIR bij boot (CONFIG-preset). 4 = snelle step-response voor !alt / !calground; "
		"wordt bij SET MODE TEST/MISSION automatisch opgetild naar --bme280-iir-mission.",
	)
	p.add_argument(
		"--bme280-iir-mission",
		type=int,
		default=16,
		choices=[0, 2, 4, 8, 16],
		help="BME280 IIR-preset voor TEST/MISSION. Default 16 dempt hoogfrequente ruis tot ~0.3 Pa RMS "
		"(~2 cm); wordt automatisch toegepast op SET MODE TEST/MISSION en teruggerold op END_TEST / "
		"SET MODE CONFIG.",
	)
	p.add_argument(
		"--bme280-alt-prime",
		type=int,
		default=5,
		metavar="N",
		help="Aantal back-to-back BME280-reads per GET ALT (1..32). Vult het IIR-filter zodat één "
		"losse GET ALT meteen accuraat is, ook na lange stilte. 1 = oud gedrag (geen priming). "
		"Default 5 = ~750 ms bij OSP×16; live bij te stellen via SET ALT PRIME N.",
	)
	p.add_argument(
		"--no-bme280",
		action="store_true",
		help="Geen BME280 initialiseren (geen READ BME280 over radio)",
	)
	p.add_argument(
		"--bno055-addr",
		type=lambda x: int(x, 0),
		default=0x28,
		help="BNO055 I²C-adres (0x28 of 0x29)",
	)
	p.add_argument(
		"--no-bno055",
		action="store_true",
		help="Geen BNO055 initialiseren (geen READ BNO055 over radio)",
	)
	p.add_argument(
		"--photo-dir",
		type=str,
		default=str(Path.home() / "photos"),
		help="Foto-/log-map waarvan bestaan+schrijfbaarheid als MISSION-preflight wordt gecheckt",
	)
	p.add_argument(
		"--gimbal-cfg",
		type=str,
		default=str(_ROOT / "config" / "gimbal" / "servo_calibration.json"),
		help="Pad naar servo-calibratie JSON (MISSION-preflight)",
	)
	p.add_argument(
		"--runtime-path",
		type=str,
		default=str(_DEFAULT_RUNTIME_PATH),
		help="JSON-bestand waarin de laatst toegepaste frequentie wordt bewaard (sync met Pico)",
	)
	args = p.parse_args()
	if args.reply_delay < 0:
		print("--reply-delay must be >= 0", file=sys.stderr)
		return 1

	key = args.key.encode("utf-8")
	if len(key) != 16:
		print("--key must be exactly 16 UTF-8 bytes", file=sys.stderr)
		return 1
	if args.key == "CANSAT_2025-2026" and not os.environ.get("CANSAT_RADIO_KEY"):
		print(
			"WARN: RFM69 draait met de publieke demo-key. Zet CANSAT_RADIO_KEY in .env "
			"(zie .env.example) voor een privé-sleutel.",
			file=sys.stderr,
		)

	spi_dev = Path(f"/dev/spidev{args.spi_bus}.{args.spi_device}")
	if not spi_dev.exists():
		print("Missing", spi_dev, "— enable SPI", file=sys.stderr)
		return 1

	i2c_dev = Path(f"/dev/i2c-{args.i2c_bus}")
	bme280 = None
	if not args.no_bme280 and i2c_dev.exists():
		try:
			from cansat_hw.sensors.bme280 import BME280

			# Map OSP 1/2/4/8/16 naar Pico-OSAMPLE-codes 1..5.
			_OS_TO_CODE = {1: 1, 2: 2, 4: 3, 8: 4, 16: 5}
			bme280 = BME280(
				args.i2c_bus,
				args.bme280_addr,
				oversampling=_OS_TO_CODE[args.bme280_os],
				iir_filter=args.bme280_iir,
			)
			if bme280.chip_id != 0x60:
				print(
					f"WARN: BME280 chip id 0x{bme280.chip_id:02X} (verwacht 0x60) — READ BME280 uit",
					file=sys.stderr,
				)
				bme280.close()
				bme280 = None
		except Exception as e:  # noqa: BLE001
			print("WARN: BME280 niet beschikbaar:", e, file=sys.stderr)
			if bme280 is not None:
				try:
					bme280.close()
				except Exception:
					pass
				bme280 = None
	elif not args.no_bme280:
		print("WARN: geen", i2c_dev, "— BME280 over radio uit", file=sys.stderr)

	bno055 = None
	if not args.no_bno055 and i2c_dev.exists():
		try:
			from cansat_hw.sensors.bno055 import BNO055

			bno055 = BNO055(args.i2c_bus, args.bno055_addr)
			if bno055.chip_id != 0xA0:
				print(
					f"WARN: BNO055 chip id 0x{bno055.chip_id:02X} (verwacht 0xA0) — READ BNO055 uit",
					file=sys.stderr,
				)
				bno055.close()
				bno055 = None
		except Exception as e:  # noqa: BLE001
			print("WARN: BNO055 niet beschikbaar:", e, file=sys.stderr)
			if bno055 is not None:
				try:
					bno055.close()
				except Exception:
					pass
				bno055 = None
	elif not args.no_bno055:
		print("WARN: geen", i2c_dev, "— BNO055 over radio uit", file=sys.stderr)

	from cansat_hw.radio import RFM69
	from cansat_hw.radio.wire_protocol import (
		RadioRuntimeState,
		apply_mode_iir,
		build_telemetry_packet,
		handle_wire_line,
		test_mode_advance_tlm,
		test_mode_end,
		test_mode_tick,
	)

	rfm = RFM69(
		spi_bus=args.spi_bus,
		spi_device=args.spi_device,
		reset_pin=args.reset_pin,
		dio0_pin=args.dio0_pin,
	)
	from cansat_hw.radio.wire_protocol import ALT_PRIME_MAX, ALT_PRIME_MIN

	alt_prime = max(ALT_PRIME_MIN, min(ALT_PRIME_MAX, int(args.bme280_alt_prime)))
	if alt_prime != args.bme280_alt_prime:
		print(
			f"WARN: --bme280-alt-prime geclamped naar {alt_prime} ({ALT_PRIME_MIN}..{ALT_PRIME_MAX})",
			file=sys.stderr,
		)
	state = RadioRuntimeState(
		config_iir=int(args.bme280_iir),
		mission_iir=int(args.bme280_iir_mission),
		alt_prime_samples=alt_prime,
	)
	runtime_path = Path(args.runtime_path).expanduser()
	persisted_freq = _load_persisted_freq(runtime_path)
	if persisted_freq is not None:
		print(f"Geladen freq {persisted_freq} MHz uit {runtime_path}")
		args.freq = persisted_freq
		state.freq_set = True

	try:
		rfm.frequency_mhz = args.freq
		rfm.encryption_key = key
		rfm.node = args.node
		rfm.destination = args.dest
		rfm.tx_power = args.tx_power
		rfm.receive_timeout = min(args.poll, 10.0)

		banner_tail = (
			"Ctrl+C of STOP RADIO om te stoppen — geen !-commando's hier; die zijn voor de Pico base station"
		)
		if args.reply_delay > 0:
			banner_tail += f" — reply-delay {args.reply_delay}s"
		if args.dio0_pin is not None:
			banner_tail += f" — DIO0 IRQ GPIO{args.dio0_pin}"
		if bme280 is not None:
			banner_tail += " — BME280"
		if bno055 is not None:
			banner_tail += " — BNO055"
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
			# TEST-mode: timer + periodieke telemetrie vóór we verder luisteren.
			# Geen threads, geen locking — pure coöperatieve scheduling tussen
			# twee opeenvolgende receive()-calls. Veilig op de Zero 2 W en
			# voorkomt half-duplex conflicten met inkomende commando's.
			send_tlm, end_test = test_mode_tick(state)
			if end_test:
				dest = state.test_dest_node if state.test_dest_node is not None else args.dest
				evt = b"EVT MODE CONFIG END_TEST"
				ok_evt = rfm.send(evt, keep_listening=True, destination=dest)
				if args.verbose or not ok_evt:
					print(
						"TEST: timer expired, EVT MODE CONFIG ->",
						dest,
						"ok=",
						ok_evt,
					)
				test_mode_end(state)
				# Terug naar de responsieve CONFIG-IIR (bv. 4) zodat !alt weer
				# snel reageert; test_mode_end heeft state.mode al op CONFIG gezet.
				apply_mode_iir(state, bme280)
			elif send_tlm:
				dest = state.test_dest_node if state.test_dest_node is not None else args.dest
				tlm = build_telemetry_packet(state, bme280, bno055)
				ok_tlm = rfm.send(tlm, keep_listening=True, destination=dest)
				if args.verbose:
					print(
						"TLM ->",
						dest,
						f"seq={state.tlm_seq}",
						f"({len(tlm)} B binary)",
						"ok=",
						ok_tlm,
					)
				elif not ok_tlm:
					print("WARN: TLM TX failed (radio timeout)", file=sys.stderr)
				test_mode_advance_tlm(state)

			# Korte receive-timeout tijdens TEST zodat de timer/interval responsief blijft.
			rx_timeout = 0.2 if state.mode == "TEST" else args.poll
			# with_header=True: afzender = byte 1 voor reply destination
			# with_ack=False: geen RadioHead-ACK vóór onze tekstantwoord
			pkt = rfm.receive(timeout=rx_timeout, with_header=True, with_ack=False, keep_listening=True)
			if pkt is None:
				continue
			if len(pkt) < 5:
				continue
			from_node = pkt[1]
			mode_before = state.mode
			try:
				line = pkt[4:].decode("utf-8")
			except UnicodeDecodeError:
				reply = b"ERR UTF8"
			else:
				if args.verbose:
					print("RX from", from_node, ":", line.strip())
				reply = handle_wire_line(
					rfm,
					state,
					line,
					bme280=bme280,
					bno055=bno055,
					photo_dir=args.photo_dir,
					gimbal_cfg=args.gimbal_cfg,
				)
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

			# Net overgegaan naar TEST? Onthoud wie het vroeg zodat
			# unsolicited TLM/EVT naar datzelfde node terug gaan.
			if mode_before != "TEST" and state.mode == "TEST" and ok:
				state.test_dest_node = int(from_node)
				if args.verbose:
					print("TEST: destination set to node", from_node)

			if state.pending_freq_mhz is not None and ok:
				new_freq = float(state.pending_freq_mhz)
				state.pending_freq_mhz = None
				try:
					rfm.frequency_mhz = new_freq
					print(f"Nieuwe RF-freq toegepast: {new_freq} MHz")
				except Exception as e:  # noqa: BLE001
					print("WARN: nieuwe freq toepassen mislukte:", e, file=sys.stderr)
				err = _save_persisted_freq(runtime_path, new_freq)
				if err:
					print("WARN: persist freq mislukte:", err, file=sys.stderr)
				else:
					print(f"Freq persistent in {runtime_path}")

			if state.exit_after_reply:
				print("STOP RADIO: exiting.")
				break
	except KeyboardInterrupt:
		print("\nStopped.")
		return 0
	finally:
		rfm.close()
		if bme280 is not None:
			try:
				bme280.close()
			except Exception:
				pass
		if bno055 is not None:
			try:
				bno055.close()
			except Exception:
				pass

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
