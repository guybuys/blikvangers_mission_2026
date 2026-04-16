"""Tekstregel-protocol (zelfde idee als Pico `RadioReceiver/protocol.py`)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

MAX_PAYLOAD = 60


@dataclass
class RadioRuntimeState:
	"""Houdt CONFIG vs MISSION bij (alleen in RAM; na reboot weer default)."""

	mode: str = "CONFIG"  # "CONFIG" | "MISSION"


def _truncate(msg: bytes) -> bytes:
	if len(msg) <= MAX_PAYLOAD:
		return msg
	return msg[:MAX_PAYLOAD]


def handle_wire_line(
	rfm,
	state: RadioRuntimeState,
	line: str,
	*,
	bme280: Optional[Any] = None,
	bno055: Optional[Any] = None,
) -> bytes:
	"""
	Verwerk één payload-regel (zonder RadioHead-header).
	Retourneert UTF-8 bytes (max MAX_PAYLOAD) om terug te sturen.
	"""
	s = line.strip()
	if not s:
		return b"ERR EMPTY"

	su = s.upper()
	tokens = s.split()

	if state.mode == "MISSION":
		if su not in ("PING", "GET MODE", "SET MODE CONFIG"):
			return _truncate(b"ERR BUSY MISSION")

	if su == "PING":
		return b"OK PING"

	if su == "GET MODE":
		return _truncate(("OK MODE %s" % state.mode).encode("utf-8"))

	if su == "SET MODE CONFIG":
		state.mode = "CONFIG"
		return b"OK MODE CONFIG"

	if su in ("SET MODE MISSION", "SET MODE LAUNCH"):
		# LAUNCH: oude alias (zelfde modus als MISSION)
		state.mode = "MISSION"
		return b"OK MODE MISSION"

	if su == "GET FREQ":
		return _truncate(("OK FREQ %.6g" % float(rfm.frequency_mhz)).encode("utf-8"))

	if len(tokens) == 3 and tokens[0].upper() == "SET" and tokens[1].upper() == "FREQ":
		try:
			mhz = float(tokens[2])
		except ValueError:
			return b"ERR BAD FREQ"
		rfm.frequency_mhz = mhz
		return _truncate(("OK FREQ %.6g" % mhz).encode("utf-8"))

	if su in ("READ BME280", "BME280"):
		if bme280 is None:
			return _truncate(b"ERR NO BME280")
		try:
			text = bme280.read_wire_reply()
		except Exception as e:  # noqa: BLE001 — I²C / driver
			return _truncate(("ERR BME280 %s" % e).encode("utf-8", errors="replace")[:MAX_PAYLOAD])
		return _truncate(text.encode("utf-8", errors="replace"))

	if su in ("READ BNO055", "BNO055"):
		if bno055 is None:
			return _truncate(b"ERR NO BNO055")
		try:
			text = bno055.read_wire_reply()
		except Exception as e:  # noqa: BLE001
			return _truncate(("ERR BNO055 %s" % e).encode("utf-8", errors="replace")[:MAX_PAYLOAD])
		return _truncate(text.encode("utf-8", errors="replace"))

	return _truncate(b"ERR UNKNOWN")
