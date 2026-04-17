"""Tekstregel-protocol (zelfde idee als Pico `RadioReceiver/protocol.py`)."""

from __future__ import annotations

import os
import subprocess
import time as time_mod
from dataclasses import dataclass, field
from typing import Any, Optional

MAX_PAYLOAD = 60

# Unix seconden — ruwe bandbreedte (2021 … ~2286)
_MIN_UNIX_TS = 1_600_000_000
_MAX_UNIX_TS = 10_000_000_000


@dataclass
class RadioRuntimeState:
	"""Houdt CONFIG vs MISSION bij (alleen in RAM; na reboot weer default)."""

	mode: str = "CONFIG"  # "CONFIG" | "MISSION"
	exit_after_reply: bool = field(default=False, repr=False)


def _truncate(msg: bytes) -> bytes:
	if len(msg) <= MAX_PAYLOAD:
		return msg
	return msg[:MAX_PAYLOAD]


def apply_system_time_unix(ts: float) -> tuple[bool, str]:
	"""
	Zet de kernel real-time clock naar Unix-epoch ``ts`` (seconden, mag float).

	- Als dit proces **root** is: ``clock_settime`` (aanbevolen voor systemd User=root).
	- Anders: ``timedatectl set-time`` met lokale wandtijd (past bij Pi met TZ Europe/Brussels).

	Retourneert ``(True, "")`` bij succes, anders ``(False, korte fouttekst)``.
	"""
	try:
		tf = float(ts)
	except (TypeError, ValueError):
		return False, "bad float"
	if not (tf == tf):  # NaN
		return False, "nan"
	if tf < _MIN_UNIX_TS or tf > _MAX_UNIX_TS:
		return False, "out of range"

	if hasattr(time_mod, "clock_settime") and os.geteuid() == 0:
		try:
			time_mod.clock_settime(time_mod.CLOCK_REALTIME, tf)
			return True, ""
		except OSError as e:
			return False, str(e)[:48]

	# Lokale YYYY-MM-DD HH:MM:SS (wandtijd volgens systeem-TZ / TZ-omgeving)
	lt = time_mod.localtime(tf)
	wall = time_mod.strftime("%Y-%m-%d %H:%M:%S", lt)
	try:
		subprocess.run(
			["/usr/bin/timedatectl", "set-time", wall],
			check=True,
			timeout=15,
			capture_output=True,
			text=True,
		)
		return True, ""
	except FileNotFoundError:
		return False, "no timedatectl"
	except subprocess.CalledProcessError as e:
		err = (e.stderr or e.stdout or str(e))[:48]
		return False, err.strip() or "timedatectl failed"
	except OSError as e:
		return False, str(e)[:48]


_MISSION_ALWAYS_CMDS = frozenset(
	{
		"PING",
		"GET MODE",
		"SET MODE CONFIG",
		"STOP RADIO",
	}
)


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
		if su not in _MISSION_ALWAYS_CMDS:
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

	if su == "STOP RADIO":
		state.exit_after_reply = True
		return b"OK STOP RADIO"

	if su == "GET FREQ":
		return _truncate(("OK FREQ %.6g" % float(rfm.frequency_mhz)).encode("utf-8"))

	if len(tokens) == 3 and tokens[0].upper() == "SET" and tokens[1].upper() == "FREQ":
		try:
			mhz = float(tokens[2])
		except ValueError:
			return b"ERR BAD FREQ"
		rfm.frequency_mhz = mhz
		return _truncate(("OK FREQ %.6g" % mhz).encode("utf-8"))

	if len(tokens) == 3 and tokens[0].upper() == "SET" and tokens[1].upper() == "TIME":
		if state.mode != "CONFIG":
			return b"ERR BUSY MISSION"
		try:
			unix_ts = float(tokens[2])
		except ValueError:
			return b"ERR BAD TIME"
		ok, err = apply_system_time_unix(unix_ts)
		if not ok:
			return _truncate(("ERR TIME %s" % err).encode("utf-8", errors="replace"))
		return b"OK TIME"

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
