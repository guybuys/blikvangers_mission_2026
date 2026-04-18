"""Tekstregel-protocol (zelfde idee als Pico `RadioReceiver/protocol.py`)."""

from __future__ import annotations

import os
import shutil
import subprocess
import time as time_mod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

MAX_PAYLOAD = 60

# Unix seconden — ruwe bandbreedte (2021 … ~2286)
_MIN_UNIX_TS = 1_600_000_000
_MAX_UNIX_TS = 10_000_000_000

# Zachte ondergrens voor "klok staat in 2026 of later" (fallback sanity).
_TIME_SANE_MIN = 1_735_689_600  # 2025-01-01 UTC

# Preflight-defaults (overschrijfbaar via SET TRIGGER / SET GROUND / SET FREQ).
DEFAULT_ASCENT_HEIGHT_M = 5.0
DEFAULT_DEPLOY_DESCENT_M = 3.0
DEFAULT_LAND_HZ_M = 5.0
PREFLIGHT_MIN_FREE_MB = 500
PREFLIGHT_BNO_SYS_MIN = 1
GROUND_CAL_SAMPLES = 16

# ISA barometer-constante (h_m ≈ 44330 * (1 - (p/p0)^0.1903)).
_ISA_H0_M = 44330.0
_ISA_EXP = 5.255


def height_m_to_dp_hpa(height_m: float, ground_hpa: float) -> float:
	"""Zet stijging (m) naar bijhorende drukdaling (hPa) vanaf ``ground_hpa``."""
	ratio = 1.0 - (float(height_m) / _ISA_H0_M)
	if ratio <= 0:
		return float(ground_hpa)
	p = float(ground_hpa) * (ratio ** _ISA_EXP)
	return float(ground_hpa) - p


def pressure_to_altitude_m(pressure_hpa: float, ground_hpa: float) -> float:
	"""Zet absolute druk (hPa) om naar hoogte in meters boven ``ground_hpa``.

	Inverse van ``height_m_to_dp_hpa``: ``h = 44330 * (1 - (p/p0)^(1/5.255))``.
	Negatief als de druk hoger is dan de grondreferentie (lager dan grond).
	"""
	gp = float(ground_hpa)
	if gp <= 0:
		return 0.0
	ratio = float(pressure_hpa) / gp
	if ratio <= 0:
		return _ISA_H0_M
	return _ISA_H0_M * (1.0 - (ratio ** (1.0 / _ISA_EXP)))


@dataclass
class RadioRuntimeState:
	"""Houdt CONFIG vs MISSION bij (alleen in RAM; na reboot weer default)."""

	mode: str = "CONFIG"  # "CONFIG" | "MISSION"
	exit_after_reply: bool = field(default=False, repr=False)
	time_synced: bool = field(default=False, repr=False)
	freq_set: bool = field(default=False, repr=False)
	pending_freq_mhz: Optional[float] = field(default=None, repr=False)
	ground_hpa: Optional[float] = None
	trig_ascent_height_m: float = DEFAULT_ASCENT_HEIGHT_M
	trig_deploy_descent_m: float = DEFAULT_DEPLOY_DESCENT_M
	trig_land_hz_m: float = DEFAULT_LAND_HZ_M
	# Apogee-tracking (hoogste hoogte sinds laatste RESET APOGEE).
	max_alt_m: Optional[float] = None
	min_pressure_hpa: Optional[float] = None
	max_alt_ts: Optional[float] = None


def _update_apogee(state: RadioRuntimeState, altitude_m: float, pressure_hpa: float) -> None:
	"""Werk ``state.max_alt_m`` bij als de nieuwe hoogte hoger is."""
	if state.max_alt_m is None or altitude_m > state.max_alt_m:
		state.max_alt_m = float(altitude_m)
		state.min_pressure_hpa = float(pressure_hpa)
		state.max_alt_ts = time_mod.time()


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
		"GET TIME",
		"GET ALT",
		"ALT",
		"GET APOGEE",
		"SET MODE CONFIG",
		"STOP RADIO",
	}
)


def _format_time_reply(now: float) -> bytes:
	"""``OK TIME <unix.fff> <YYYY-MM-DDTHH:MM:SS±HH:MM>`` in lokale TZ."""
	lt = time_mod.localtime(now)
	tz = time_mod.strftime("%z", lt) or "+0000"
	iso_tz = tz if len(tz) < 5 else "%s:%s" % (tz[:3], tz[3:])
	iso = time_mod.strftime("%Y-%m-%dT%H:%M:%S", lt) + iso_tz
	msg = "OK TIME %.3f %s" % (now, iso)
	return _truncate(msg.encode("utf-8", errors="replace"))


def _check_time_set(state: RadioRuntimeState) -> bool:
	"""``True`` als tijd minstens via één pad "gekalibreerd" is."""
	if state.time_synced:
		return True
	try:
		res = subprocess.run(
			["/usr/bin/timedatectl", "show", "-p", "NTPSynchronized", "--value"],
			capture_output=True,
			text=True,
			timeout=3,
		)
		if res.returncode == 0 and res.stdout.strip().lower() == "yes":
			return True
	except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
		pass
	try:
		return time_mod.time() >= _TIME_SANE_MIN
	except OSError:
		return False


def preflight_checks(
	state: RadioRuntimeState,
	rfm: Any,
	bme280: Any,
	bno055: Any,
	*,
	photo_dir: Optional[Union[str, Path]] = None,
	gimbal_cfg: Optional[Union[str, Path]] = None,
	min_free_mb: int = PREFLIGHT_MIN_FREE_MB,
) -> Tuple[List[str], List[str]]:
	"""Voer alle pre-MISSION checks uit; retourneer ``(missing_codes, info_tokens)``.

	Codes blijven kort (≤4 letters) zodat het antwoord in 60 bytes past:
	``TIME`` ``GND`` ``BME`` ``IMU`` ``DSK`` ``LOG`` ``FRQ`` ``GIM``.
	"""
	missing: List[str] = []
	info: List[str] = []

	if not _check_time_set(state):
		missing.append("TIME")

	if state.ground_hpa is None:
		missing.append("GND")
	else:
		info.append("GND=%.1f" % state.ground_hpa)

	if bme280 is None:
		missing.append("BME")
	else:
		try:
			_, p_hpa, _ = bme280.read()
			if not (800.0 <= float(p_hpa) <= 1100.0):
				missing.append("BME")
		except Exception:  # noqa: BLE001
			missing.append("BME")

	if bno055 is None:
		missing.append("IMU")
	else:
		try:
			sys_cal = bno055.calibration_status()[0]
			if int(sys_cal) < PREFLIGHT_BNO_SYS_MIN:
				missing.append("IMU")
		except Exception:  # noqa: BLE001
			missing.append("IMU")

	try:
		free_mb = shutil.disk_usage("/").free // (1024 * 1024)
		if free_mb < int(min_free_mb):
			missing.append("DSK")
	except OSError:
		missing.append("DSK")

	if photo_dir is not None:
		pd = Path(photo_dir).expanduser()
		if not (pd.is_dir() and os.access(str(pd), os.W_OK)):
			missing.append("LOG")

	if not state.freq_set:
		missing.append("FRQ")

	if gimbal_cfg is not None:
		gc = Path(gimbal_cfg).expanduser()
		if not gc.is_file():
			missing.append("GIM")

	info.append("ASC=%.1fm" % state.trig_ascent_height_m)
	info.append("DEP=%.1fm" % state.trig_deploy_descent_m)
	info.append("LND=%.1fm" % state.trig_land_hz_m)

	return missing, info


def _format_preflight_reply(missing: List[str], info: List[str]) -> bytes:
	if missing:
		msg = "ERR PRE " + " ".join(missing)
	else:
		msg = "OK PRE ALL" + ("" if not info else " " + " ".join(info))
	return _truncate(msg.encode("utf-8", errors="replace"))


def _ground_calibrate(bme280: Any) -> Tuple[bool, float, str]:
	"""Gemiddelde van ``GROUND_CAL_SAMPLES`` metingen; ``(ok, hpa, err)``."""
	samples: List[float] = []
	try:
		for _ in range(GROUND_CAL_SAMPLES):
			_, p_hpa, _ = bme280.read()
			samples.append(float(p_hpa))
	except Exception as e:  # noqa: BLE001
		return False, 0.0, ("%s" % e)[:40]
	if not samples:
		return False, 0.0, "no samples"
	avg = sum(samples) / len(samples)
	if not (800.0 <= avg <= 1100.0):
		return False, avg, "out of range"
	return True, avg, ""


def handle_wire_line(
	rfm: Any,
	state: RadioRuntimeState,
	line: str,
	*,
	bme280: Optional[Any] = None,
	bno055: Optional[Any] = None,
	photo_dir: Optional[Union[str, Path]] = None,
	gimbal_cfg: Optional[Union[str, Path]] = None,
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
		# Preflight-gate: blokkeer MISSION als iets ontbreekt.
		missing, _info = preflight_checks(
			state,
			rfm,
			bme280,
			bno055,
			photo_dir=photo_dir,
			gimbal_cfg=gimbal_cfg,
		)
		if missing:
			return _truncate(("ERR PRE " + " ".join(missing)).encode("utf-8"))
		state.mode = "MISSION"
		return b"OK MODE MISSION"

	if su == "STOP RADIO":
		state.exit_after_reply = True
		return b"OK STOP RADIO"

	if su == "GET TIME":
		return _format_time_reply(time_mod.time())

	if su == "GET FREQ":
		return _truncate(("OK FREQ %.6g" % float(rfm.frequency_mhz)).encode("utf-8"))

	if len(tokens) == 3 and tokens[0].upper() == "SET" and tokens[1].upper() == "FREQ":
		try:
			mhz = float(tokens[2])
		except ValueError:
			return b"ERR BAD FREQ"
		# Pas pas NÁ het versturen van de reply toe, zodat het base station het
		# antwoord nog op de OUDE freq hoort. De caller (radio-loop) leest
		# ``state.pending_freq_mhz`` en doet ``rfm.frequency_mhz = mhz`` + persist.
		state.pending_freq_mhz = mhz
		state.freq_set = True
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
		state.time_synced = True
		return b"OK TIME"

	if su == "CAL GROUND":
		if bme280 is None:
			return b"ERR NO BME280"
		ok, avg, err = _ground_calibrate(bme280)
		if not ok:
			return _truncate(("ERR GROUND %s" % err).encode("utf-8", errors="replace"))
		state.ground_hpa = avg
		return _truncate(("OK GROUND %.2f" % avg).encode("utf-8"))

	if len(tokens) == 3 and tokens[0].upper() == "SET" and tokens[1].upper() == "GROUND":
		try:
			p = float(tokens[2])
		except ValueError:
			return b"ERR BAD GROUND"
		if not (800.0 <= p <= 1100.0):
			return b"ERR BAD GROUND"
		state.ground_hpa = p
		return _truncate(("OK GROUND %.2f" % p).encode("utf-8"))

	if su == "GET GROUND":
		if state.ground_hpa is None:
			return b"OK GROUND NONE"
		return _truncate(("OK GROUND %.2f" % state.ground_hpa).encode("utf-8"))

	if len(tokens) == 4 and tokens[0].upper() == "SET" and tokens[1].upper() == "TRIGGER":
		which = tokens[2].upper()
		try:
			v = float(tokens[3])
		except ValueError:
			return b"ERR BAD TRIGGER"
		if which == "ASCENT":
			if not (0.5 <= v <= 1000.0):
				return b"ERR BAD TRIGGER"
			state.trig_ascent_height_m = v
			unit = "m"
		elif which == "DEPLOY":
			if not (0.5 <= v <= 100.0):
				return b"ERR BAD TRIGGER"
			state.trig_deploy_descent_m = v
			unit = "m"
		elif which in ("LAND", "LANDED"):
			if not (0.5 <= v <= 500.0):
				return b"ERR BAD TRIGGER"
			state.trig_land_hz_m = v
			unit = "m"
		else:
			return b"ERR BAD TRIGGER"
		return _truncate(("OK TRIG %s %.2f%s" % (which, v, unit)).encode("utf-8"))

	if su == "GET TRIGGERS":
		if state.ground_hpa is not None:
			dp = height_m_to_dp_hpa(state.trig_ascent_height_m, state.ground_hpa)
			asc_str = "ASC=%.1fm/%.2fhPa" % (state.trig_ascent_height_m, dp)
		else:
			asc_str = "ASC=%.1fm" % state.trig_ascent_height_m
		msg = "OK TRIG %s DEP=%.1fm LND=%.1fm" % (
			asc_str,
			state.trig_deploy_descent_m,
			state.trig_land_hz_m,
		)
		return _truncate(msg.encode("utf-8"))

	if su in ("GET ALT", "ALT"):
		if bme280 is None:
			return b"ERR NO BME280"
		if state.ground_hpa is None:
			return b"ERR NO GROUND"
		try:
			_, p_hpa, _ = bme280.read()
		except Exception as e:  # noqa: BLE001
			return _truncate(("ERR BME280 %s" % e).encode("utf-8", errors="replace"))
		alt = pressure_to_altitude_m(float(p_hpa), float(state.ground_hpa))
		_update_apogee(state, alt, float(p_hpa))
		return _truncate(("OK ALT %.2f %.2f" % (alt, float(p_hpa))).encode("utf-8"))

	if su == "GET APOGEE":
		if state.max_alt_m is None:
			return b"OK APOGEE NONE"
		age = 0.0
		if state.max_alt_ts is not None:
			age = max(0.0, time_mod.time() - float(state.max_alt_ts))
		return _truncate(
			(
				"OK APOGEE %.2f %.2f %.1f"
				% (float(state.max_alt_m), float(state.min_pressure_hpa or 0.0), age)
			).encode("utf-8")
		)

	if su == "RESET APOGEE":
		state.max_alt_m = None
		state.min_pressure_hpa = None
		state.max_alt_ts = None
		return b"OK APOGEE RESET"

	if su == "PREFLIGHT":
		missing, info = preflight_checks(
			state,
			rfm,
			bme280,
			bno055,
			photo_dir=photo_dir,
			gimbal_cfg=gimbal_cfg,
		)
		return _format_preflight_reply(missing, info)

	if su in ("READ BME280", "BME280"):
		if bme280 is None:
			return _truncate(b"ERR NO BME280")
		try:
			text = bme280.read_wire_reply()
		except Exception as e:  # noqa: BLE001 — I²C / driver
			return _truncate(("ERR BME280 %s" % e).encode("utf-8", errors="replace")[:MAX_PAYLOAD])
		# Houd apogee ook bij via expliciete BME280-reads, zolang grond bekend is.
		if state.ground_hpa is not None:
			try:
				_, p_hpa, _ = bme280.read()
				alt = pressure_to_altitude_m(float(p_hpa), float(state.ground_hpa))
				_update_apogee(state, alt, float(p_hpa))
			except Exception:  # noqa: BLE001
				pass
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
