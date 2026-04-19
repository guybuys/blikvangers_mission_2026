"""Tekstregel-protocol (zelfde idee als Pico `RadioReceiver/protocol.py`)."""

from __future__ import annotations

import os
import shutil
import subprocess
import time as time_mod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

from cansat_hw.telemetry.codec import (
	FRAME_SIZE,
	TagDetection,
	mode_for_string,
	pack_tlm,
	state_for_mode_string,
)

# ``MAX_PAYLOAD`` blijft de harde grens van één RFM69-frame (chip-FIFO + 4 B
# RadioHead-header). Binary TLM-frames zijn altijd exact ``FRAME_SIZE`` (= 60).
MAX_PAYLOAD = 60
assert FRAME_SIZE == MAX_PAYLOAD, (
	"binary TLM frame size %d != MAX_PAYLOAD %d" % (FRAME_SIZE, MAX_PAYLOAD)
)

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
# CAL GROUND: eerst ``state.alt_prime_samples`` warm-up reads om het IIR-filter
# in te halen op de echte huidige druk, dán ``GROUND_CAL_SAMPLES`` extra reads
# middelen voor een ruisarme grondreferentie. Het oude gedrag (8 samples zonder
# warmup) gaf systematisch een te lage grondreferentie wanneer het filter lang
# stilgelegen had — zie comment in ``_ground_calibrate``.
GROUND_CAL_SAMPLES = 4

# IIR-coëfficienten die de BME280 accepteert (zie driver `_IIR_FILTER_CODES`).
_IIR_ALLOWED = (0, 2, 4, 8, 16)
# Standaard IIR per mode. In CONFIG willen we responsief blijven (snelle
# hoogte-updates via GET ALT); in TEST/MISSION willen we stillere signalen.
# De main loop mag deze overschrijven via ``state.config_iir`` / ``mission_iir``.
DEFAULT_CONFIG_IIR = 4
DEFAULT_MISSION_IIR = 16

# GET ALT prime: aantal back-to-back BME280-reads dat we per GET ALT doen.
# De LAATSTE read wordt teruggemeld; de eerdere zijn enkel "voer" voor het IIR-
# filter zodat één losse GET ALT in CONFIG meteen accuraat is, ook als er lang
# geen sample meer was. 1 = oud gedrag (geen priming).
DEFAULT_ALT_PRIME = 5
ALT_PRIME_MIN = 1
ALT_PRIME_MAX = 32

# TEST-mode: bootst een stuk MISSION/DEPLOYED na voor een korte timer zodat we
# de deployed-logica kunnen bouwen zonder echte triggers (stijging/apogee/land).
TEST_MODE_DEFAULT_S = 10.0
TEST_MODE_MIN_S = 2.0
TEST_MODE_MAX_S = 60.0
TEST_MODE_TLM_INTERVAL_S = 1.0

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
	"""Houdt CONFIG vs MISSION vs TEST bij (alleen in RAM; na reboot weer default)."""

	mode: str = "CONFIG"  # "CONFIG" | "MISSION" | "TEST"
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
	# TEST-mode bookkeeping (alles monotonic seconden; None buiten TEST).
	test_duration_s: Optional[float] = field(default=None, repr=False)
	test_start_monotonic: Optional[float] = field(default=None, repr=False)
	test_deadline_monotonic: Optional[float] = field(default=None, repr=False)
	test_next_tlm_monotonic: Optional[float] = field(default=None, repr=False)
	# Bestemmingsnode voor unsolicited TLM/EVT (wordt door de main loop gezet).
	test_dest_node: Optional[int] = field(default=None, repr=False)
	# IIR-presets per mode. Worden bij mode-wissels automatisch naar de BME280
	# geschreven (responsief in CONFIG, rustig in TEST/MISSION).
	config_iir: int = DEFAULT_CONFIG_IIR
	mission_iir: int = DEFAULT_MISSION_IIR
	# Aantal samples per GET ALT (priming-burst voor het IIR-filter).
	alt_prime_samples: int = DEFAULT_ALT_PRIME
	# Volgnummer (uint16, wraps na 65535) voor binary TLM-frames. Wordt door
	# ``build_telemetry_packet`` zelf opgehoogd; de Pico/laptop kan packet-loss
	# detecteren door gaten in de reeks.
	tlm_seq: int = field(default=0, repr=False)


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
		"GET IIR",
		"GET ALT PRIME",
		"SET MODE CONFIG",
		"STOP RADIO",
	}
)

# In TEST-mode is er bewust **geen** abort: de timer op de Zero is heilig tot
# hij afloopt, anders is het geen echte dry-run van DEPLOYED. Alleen de minst
# invasieve observatie-commando's mogen erdoor.
_TEST_ALWAYS_CMDS = frozenset(
	{
		"PING",
		"GET MODE",
		"GET TIME",
		"GET IIR",
	}
)


def _desired_iir_for_mode(state: RadioRuntimeState) -> int:
	"""Welke IIR-coëfficient hoort bij de huidige mode?"""
	if state.mode in ("TEST", "MISSION"):
		return int(state.mission_iir)
	return int(state.config_iir)


def apply_mode_iir(state: RadioRuntimeState, bme280: Any) -> Optional[int]:
	"""Zet BME280-IIR naar de preset die bij ``state.mode`` hoort.

	Veilig om altijd te callen; doet niets als ``bme280`` of de driver geen
	``set_iir_filter`` ondersteunt, en slikt I²C-fouten zodat mode-wissels niet
	sneuvelen op een flaky bus. Retourneert de nieuwe waarde (of None bij fail).
	"""
	if bme280 is None:
		return None
	setter = getattr(bme280, "set_iir_filter", None)
	if setter is None:
		return None
	target = _desired_iir_for_mode(state)
	try:
		return int(setter(target))
	except Exception:  # noqa: BLE001
		return None


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


def preflight_checks_minimal(
	state: RadioRuntimeState,
	bme280: Any,
) -> List[str]:
	"""Snelle preflight voor **TEST-mode** — alleen TIME, GND, BME.

	Geen IMU/DSK/LOG/FRQ/GIM: we willen snel kunnen testen zonder alle
	missie-ceremonie. Zie ``preflight_checks`` voor de volledige MISSION-variant.
	"""
	missing: List[str] = []
	if not _check_time_set(state):
		missing.append("TIME")
	if state.ground_hpa is None:
		missing.append("GND")
	if bme280 is None:
		missing.append("BME")
	else:
		try:
			_, p_hpa, _ = bme280.read()
			if not (800.0 <= float(p_hpa) <= 1100.0):
				missing.append("BME")
		except Exception:  # noqa: BLE001
			missing.append("BME")
	return missing


# Conversie m/s² -> g (lineaire versnelling) voor het binary TLM-veld.
_G_MS2 = 9.80665


def build_telemetry_packet(
	state: RadioRuntimeState,
	bme280: Any,
	bno055: Any,
	*,
	now_monotonic: Optional[float] = None,
	tags: Optional[List[TagDetection]] = None,
) -> bytes:
	"""Bouw één **binary** TLM-pakket (exact ``FRAME_SIZE`` = 60 bytes).

	Layout en sentinels: zie :mod:`cansat_hw.telemetry.codec`. Het
	``mode_state``-byte wordt afgeleid uit ``state.mode``; de flight-state
	is voorlopig een vaste mapping per mode (Fase 8 zal die dynamisch maken).

	De aanroeper (de radio-loop) is verantwoordelijk voor het zenden; deze
	functie heeft géén radio-IO. ``state.tlm_seq`` wordt opgehoogd zodat
	opeenvolgende calls oplopende sequentie-nummers krijgen (16-bit wrap).
	"""
	if now_monotonic is None:
		now_monotonic = time_mod.monotonic()

	# UTC tijd: gebruik de wandklok (gezet via SET TIME / NTP). Als die nog
	# niet plausibel is, blijven we functioneel — de Pico ziet dan gewoon een
	# zinvol-ogend laag epoch en weet via de minimal preflight (TIME) dat de
	# tijd bijgesteld moet worden.
	now_wall = time_mod.time()
	utc_seconds = int(now_wall)
	utc_ms = int(round((now_wall - utc_seconds) * 1000.0))
	if utc_ms >= 1000:
		utc_seconds += 1
		utc_ms = 0
	if utc_ms < 0:
		utc_ms = 0

	alt_m: Optional[float] = None
	p_hpa: Optional[float] = None
	t_c: Optional[float] = None
	if bme280 is not None:
		try:
			t_read, p_read, _ = bme280.read()
			p_hpa = float(p_read)
			t_c = float(t_read)
			if state.ground_hpa is not None:
				alt_m = pressure_to_altitude_m(p_hpa, float(state.ground_hpa))
				_update_apogee(state, alt_m, p_hpa)
		except Exception:  # noqa: BLE001
			alt_m = p_hpa = t_c = None

	heading: Optional[float] = None
	roll: Optional[float] = None
	pitch: Optional[float] = None
	ax_g: Optional[float] = None
	ay_g: Optional[float] = None
	az_g: Optional[float] = None
	sys_cal: Optional[int] = None
	gyro_cal: Optional[int] = None
	accel_cal: Optional[int] = None
	mag_cal: Optional[int] = None
	if bno055 is not None:
		try:
			heading, roll, pitch = bno055.read_euler()
		except Exception:  # noqa: BLE001
			heading = roll = pitch = None
		# Linear acceleration (m/s², zwaartekracht eruit) -> g voor TLM.
		try:
			ax_ms2, ay_ms2, az_ms2 = bno055.read_linear_acceleration()
			ax_g = float(ax_ms2) / _G_MS2
			ay_g = float(ay_ms2) / _G_MS2
			az_g = float(az_ms2) / _G_MS2
		except Exception:  # noqa: BLE001
			ax_g = ay_g = az_g = None
		try:
			cs = bno055.calibration_status()
			sys_cal = int(cs[0])
			gyro_cal = int(cs[1])
			accel_cal = int(cs[2])
			mag_cal = int(cs[3])
		except Exception:  # noqa: BLE001
			sys_cal = gyro_cal = accel_cal = mag_cal = None

	# Onderdrukkingsregel om Lint-noise (now_monotonic ongebruikt) weg te krijgen.
	# Hij is hier voor consistentie met de rest van de mode-loop én voor
	# toekomstig gebruik (TEST-relatieve uptime in een EVT-veld bv.).
	_ = now_monotonic

	# Hoog seq na bouw zodat het volgnummer dat we packen het "huidige" frame
	# representeert (1, 2, 3, ...). Bij wrap (>65535) gewoon terug naar 0.
	state.tlm_seq = (int(state.tlm_seq) + 1) & 0xFFFF
	seq = state.tlm_seq

	frame = pack_tlm(
		mode=mode_for_string(state.mode),
		state=state_for_mode_string(state.mode),
		seq=seq,
		utc_seconds=utc_seconds,
		utc_ms=utc_ms,
		alt_m=alt_m,
		pressure_hpa=p_hpa,
		temp_c=t_c,
		heading_deg=heading,
		roll_deg=roll,
		pitch_deg=pitch,
		ax_g=ax_g,
		ay_g=ay_g,
		az_g=az_g,
		# Gyro-rate is (nog) niet uit de BNO-driver te halen; reserveerd voor later.
		gx_dps=None,
		gy_dps=None,
		gz_dps=None,
		sys_cal=sys_cal,
		gyro_cal=gyro_cal,
		accel_cal=accel_cal,
		mag_cal=mag_cal,
		tags=tags,
	)
	# pack_tlm garandeert FRAME_SIZE bytes; assert is goedkoop maar vangt
	# regressies in de codec snel op.
	if len(frame) != MAX_PAYLOAD:
		# Truncate/pad als laatste vangnet — mag in de praktijk nooit gebeuren.
		frame = (frame + b"\x00" * MAX_PAYLOAD)[:MAX_PAYLOAD]
	return frame


def test_mode_tick(
	state: RadioRuntimeState,
	*,
	now_monotonic: Optional[float] = None,
) -> Tuple[bool, bool]:
	"""Bepaal of de Zero nu telemetrie moet zenden of TEST moet afsluiten.

	Retourneert ``(send_telemetry, end_test)``. De **caller** doet de TX en
	roept daarna ``test_mode_end(state)`` aan als ``end_test`` True is.
	Geen sensor-I/O hier zodat dit deterministisch te testen blijft.
	"""
	if state.mode != "TEST":
		return False, False
	if now_monotonic is None:
		now_monotonic = time_mod.monotonic()
	if state.test_deadline_monotonic is not None and now_monotonic >= state.test_deadline_monotonic:
		return False, True
	if state.test_next_tlm_monotonic is not None and now_monotonic >= state.test_next_tlm_monotonic:
		return True, False
	return False, False


def test_mode_advance_tlm(
	state: RadioRuntimeState,
	*,
	now_monotonic: Optional[float] = None,
	interval_s: float = TEST_MODE_TLM_INTERVAL_S,
) -> None:
	"""Schuif de volgende TLM-deadline op na een geslaagde TX."""
	if now_monotonic is None:
		now_monotonic = time_mod.monotonic()
	state.test_next_tlm_monotonic = now_monotonic + float(interval_s)


def test_mode_end(state: RadioRuntimeState) -> None:
	"""Zet de state netjes terug naar CONFIG ná een TEST-timer-afloop."""
	state.mode = "CONFIG"
	state.test_duration_s = None
	state.test_start_monotonic = None
	state.test_deadline_monotonic = None
	state.test_next_tlm_monotonic = None
	state.test_dest_node = None


def _format_preflight_reply(missing: List[str], info: List[str]) -> bytes:
	if missing:
		msg = "ERR PRE " + " ".join(missing)
	else:
		msg = "OK PRE ALL" + ("" if not info else " " + " ".join(info))
	return _truncate(msg.encode("utf-8", errors="replace"))


def _ground_calibrate(bme280: Any, *, prime_samples: int = 0) -> Tuple[bool, float, str]:
	"""Warm-up + gemiddelde voor de grondreferentie; ``(ok, hpa, err)``.

	De BME280 staat in forced mode: het IIR-filter advanceert alléén tijdens
	een effectieve ``read()``. Als er lang geen reads zijn geweest, "loopt het
	filter achter" op de echte druk. We doen daarom eerst ``prime_samples``
	weggegooide reads om het filter bij te benen, en pas daarna middelen we
	``GROUND_CAL_SAMPLES`` reads tot één grondreferentie. Zo komt
	``CAL GROUND`` direct overeen met wat een opvolgende ``GET ALT`` ziet.
	"""
	prime = max(0, int(prime_samples))
	samples: List[float] = []
	try:
		for _ in range(prime):
			bme280.read()
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

	if state.mode == "TEST":
		if su not in _TEST_ALWAYS_CMDS:
			return _truncate(b"ERR BUSY TEST")

	if su == "PING":
		return b"OK PING"

	if su == "GET MODE":
		if state.mode == "TEST" and state.test_duration_s is not None:
			return _truncate(
				("OK MODE TEST %g" % float(state.test_duration_s)).encode("utf-8")
			)
		return _truncate(("OK MODE %s" % state.mode).encode("utf-8"))

	if su == "SET MODE CONFIG":
		state.mode = "CONFIG"
		apply_mode_iir(state, bme280)
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
		apply_mode_iir(state, bme280)
		return b"OK MODE MISSION"

	if (
		len(tokens) >= 3
		and tokens[0].upper() == "SET"
		and tokens[1].upper() == "MODE"
		and tokens[2].upper() == "TEST"
	):
		# Alleen vanuit CONFIG starten (geen test-in-test, geen mid-mission switch).
		if state.mode != "CONFIG":
			return _truncate(b"ERR BUSY")
		# Optionele duur; default, clamp naar veilige grenzen.
		duration: float = TEST_MODE_DEFAULT_S
		if len(tokens) >= 4:
			try:
				duration = float(tokens[3])
			except ValueError:
				return b"ERR BAD TEST"
		if duration < TEST_MODE_MIN_S:
			duration = TEST_MODE_MIN_S
		elif duration > TEST_MODE_MAX_S:
			duration = TEST_MODE_MAX_S
		missing = preflight_checks_minimal(state, bme280)
		if missing:
			return _truncate(("ERR PRE " + " ".join(missing)).encode("utf-8"))
		now = time_mod.monotonic()
		state.mode = "TEST"
		state.test_duration_s = duration
		state.test_start_monotonic = now
		state.test_deadline_monotonic = now + duration
		# Eerste telemetrie-pakket kort na de reply (0.5 s marge voor de half-
		# duplex switch; daarna elk ``TEST_MODE_TLM_INTERVAL_S`` interval).
		state.test_next_tlm_monotonic = now + 0.5
		# ``test_dest_node`` wordt door de main loop gezet op ``from_node`` na
		# het zenden van dit antwoord.
		apply_mode_iir(state, bme280)
		return _truncate(("OK MODE TEST %g" % duration).encode("utf-8"))

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
		ok, avg, err = _ground_calibrate(bme280, prime_samples=state.alt_prime_samples)
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

	if su == "GET IIR":
		# Lees bij voorkeur de echte chip-waarde; val terug op de state-preset
		# (bv. wanneer er geen BME280 aangesloten is of de driver ouder is).
		current: Optional[int] = None
		if bme280 is not None:
			current = getattr(bme280, "iir_filter", None)
		if current is None:
			current = _desired_iir_for_mode(state)
		return _truncate(
			(
				"OK IIR %d CFG=%d MIS=%d"
				% (int(current), int(state.config_iir), int(state.mission_iir))
			).encode("utf-8")
		)

	if len(tokens) == 3 and tokens[0].upper() == "SET" and tokens[1].upper() == "IIR":
		# Manueel overschrijven van de CONFIG-preset. Alleen toegestaan in CONFIG
		# zodat we tijdens TEST/MISSION nooit de filter-lag van onder de missie
		# uit trekken; daar telt ``mission_iir``.
		if state.mode != "CONFIG":
			return b"ERR BUSY"
		try:
			coef = int(tokens[2])
		except ValueError:
			return b"ERR BAD IIR"
		if coef not in _IIR_ALLOWED:
			return b"ERR BAD IIR"
		state.config_iir = coef
		new_val = apply_mode_iir(state, bme280)
		# Als er geen BME280 / setter is, bevestigen we toch dat de preset is
		# bijgewerkt (handig voor dry-run / tests).
		reported = int(new_val) if new_val is not None else coef
		return _truncate(("OK IIR %d" % reported).encode("utf-8"))

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
		# Priming-burst: doe N samples back-to-back zodat het IIR-filter de echte
		# huidige druk weer "ingehaald" heeft, ook als er secondes geen GET ALT
		# was. Alleen de LAATSTE read wordt gerapporteerd / in apogee verwerkt.
		n = max(1, int(state.alt_prime_samples))
		p_hpa: float = 0.0
		try:
			for _ in range(n):
				_, p_hpa, _ = bme280.read()
		except Exception as e:  # noqa: BLE001
			return _truncate(("ERR BME280 %s" % e).encode("utf-8", errors="replace"))
		alt = pressure_to_altitude_m(float(p_hpa), float(state.ground_hpa))
		_update_apogee(state, alt, float(p_hpa))
		return _truncate(("OK ALT %.2f %.2f" % (alt, float(p_hpa))).encode("utf-8"))

	if su == "GET ALT PRIME":
		return _truncate(("OK ALT PRIME %d" % int(state.alt_prime_samples)).encode("utf-8"))

	if (
		len(tokens) == 4
		and tokens[0].upper() == "SET"
		and tokens[1].upper() == "ALT"
		and tokens[2].upper() == "PRIME"
	):
		# Tijdens MISSION/TEST nooit retunen; daar moet de tuning vastliggen.
		if state.mode != "CONFIG":
			return b"ERR BUSY"
		try:
			n = int(tokens[3])
		except ValueError:
			return b"ERR BAD PRIME"
		if not (ALT_PRIME_MIN <= n <= ALT_PRIME_MAX):
			return b"ERR BAD PRIME"
		state.alt_prime_samples = n
		return _truncate(("OK ALT PRIME %d" % n).encode("utf-8"))

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
