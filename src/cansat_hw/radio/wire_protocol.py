"""Tekstregel-protocol (zelfde idee als Pico `RadioReceiver/protocol.py`)."""

from __future__ import annotations

from dataclasses import dataclass

MAX_PAYLOAD = 60


@dataclass
class RadioRuntimeState:
	"""Houdt CONFIG vs LAUNCH bij (alleen in RAM; na reboot weer default)."""

	mode: str = "CONFIG"  # "CONFIG" | "LAUNCH"


def _truncate(msg: bytes) -> bytes:
	if len(msg) <= MAX_PAYLOAD:
		return msg
	return msg[:MAX_PAYLOAD]


def handle_wire_line(rfm, state: RadioRuntimeState, line: str) -> bytes:
	"""
	Verwerk één payload-regel (zonder RadioHead-header).
	Retourneert UTF-8 bytes (max MAX_PAYLOAD) om terug te sturen.
	"""
	s = line.strip()
	if not s:
		return b"ERR EMPTY"

	su = s.upper()
	tokens = s.split()

	if state.mode == "LAUNCH":
		if su not in ("PING", "GET MODE", "SET MODE CONFIG"):
			return _truncate(b"ERR BUSY LAUNCH")

	if su == "PING":
		return b"OK PING"

	if su == "GET MODE":
		return _truncate(("OK MODE %s" % state.mode).encode("utf-8"))

	if su == "SET MODE CONFIG":
		state.mode = "CONFIG"
		return b"OK MODE CONFIG"

	if su == "SET MODE LAUNCH":
		state.mode = "LAUNCH"
		return b"OK MODE LAUNCH"

	if su == "GET FREQ":
		return _truncate(("OK FREQ %.6g" % float(rfm.frequency_mhz)).encode("utf-8"))

	if len(tokens) == 3 and tokens[0].upper() == "SET" and tokens[1].upper() == "FREQ":
		try:
			mhz = float(tokens[2])
		except ValueError:
			return b"ERR BAD FREQ"
		rfm.frequency_mhz = mhz
		return _truncate(("OK FREQ %.6g" % mhz).encode("utf-8"))

	return _truncate(b"ERR UNKNOWN")
