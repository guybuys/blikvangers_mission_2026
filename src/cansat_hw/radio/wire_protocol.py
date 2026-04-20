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
	STATE_ASCENT,
	STATE_DEPLOYED,
	STATE_LANDED,
	STATE_NONE,
	STATE_PAD_IDLE,
	TagDetection,
	mode_for_string,
	pack_tlm,
	state_name,
	state_value_for_name,
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

# IMU-trigger defaults (Fase 8b). Aanvullend op de altitude-backup-drempels
# hierboven: de raket levert de eerste seconden na ontsteking veel acceleratie,
# dan vrije val tot parachute-open, en bij landing een impact-spike.
#
#   - ASCENT-ACC: peak ‖a_lin‖ tijdens motor-burn ligt typisch op 5–15 g voor
#     een hobby-raket. 3 g geeft margebij wind/handling-ruis maar wordt niet
#     bereikt door op de tafel zwaaien.
#   - DEPLOY-FREEFALL: na motor-burnout daalt ‖a_lin‖ snel naar 0 g. Een halve
#     seconde aanhoudende vrije val geeft genoeg zekerheid om parachute uit te
#     gooien (~5 m valdiepte).
#   - DEPLOY-SHOCK: parachute-snap zelf is ook detectabel (>5 g jerk); dient
#     als 2e backup naast freefall en altitude-descent.
#   - LAND-IMPACT: impact-piek bij touchdown (asfalt/gras geeft ~10–30 g, gras
#     ~5–10 g). 8 g balanceert detectie versus false positives door turbulentie.
#   - LAND-STABLE: 5 s zonder hoogteverandering ⇒ rust gevonden — fail-safe
#     voor zachte landingen die de impact-drempel niet halen.
#
# Indoor-test-ervaring (apr-2026): drempels van 3 g / 0.5 s / 5 g triggerden
# allemaal door simpel oppakken+neerzetten (oppakschok ≈ 3-5 g, korte hand-
# beweging duurt ~0.5 s in vrije val, neerzet-tik ≈ 5-10 g). De huidige
# defaults zijn daarom opgehoogd zodat GEWONE handelingen géén volledig
# missie-sequence triggeren, terwijl een echte raket-launch er nog ruim
# overheen gaat.
DEFAULT_ASCENT_ACC_G = 6.0
DEFAULT_DEPLOY_FREEFALL_S = 1.0
DEFAULT_DEPLOY_SHOCK_G = 8.0
DEFAULT_LAND_IMPACT_G = 12.0
DEFAULT_LAND_STABLE_S = 8.0
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

# MISSION-TLM-loop: standaard tempo waarmee de Zero ongevraagd telemetrie naar
# het base station pusht zodra MISSION actief is. Lagere getallen = meer data
# (en hoger CPU/radio-gebruik); hoger = stiller. Configureerbaar via de
# main-loop (CLI ``--mission-tlm-interval``). De TLM-loop is óók de motor
# achter de flight-state-machine: zonder reads bewegen ASCENT/DEPLOY/LANDED
# nooit. Geen apart commando om dit te wijzigen — keuze maken bij boot.
MISSION_MODE_TLM_INTERVAL_S = 1.0

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
	# Fase 8b: IMU-triggers (primair); altitude/descent/hz zijn de backup.
	# Gebruikt door :func:`evaluate_flight_state` met OR-logica per transitie.
	trig_ascent_accel_g: float = DEFAULT_ASCENT_ACC_G
	trig_deploy_freefall_s: float = DEFAULT_DEPLOY_FREEFALL_S
	trig_deploy_shock_g: float = DEFAULT_DEPLOY_SHOCK_G
	trig_land_impact_g: float = DEFAULT_LAND_IMPACT_G
	trig_land_stable_s: float = DEFAULT_LAND_STABLE_S
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
	# Vlucht-substate (Fase 8): één-richting machine PAD_IDLE -> ASCENT ->
	# DEPLOYED -> LANDED. In CONFIG = NONE; in TEST = DEPLOYED (vast). Wordt in
	# MISSION dynamisch bijgewerkt door :func:`maybe_advance_flight_state` o.b.v.
	# de net gemeten ``alt_m`` en ``state.max_alt_m`` (apogee-tracking).
	# Default = ``STATE_NONE``; ``__post_init__`` synct met ``mode`` zodat
	# ``RadioRuntimeState(mode="MISSION")`` direct ``PAD_IDLE`` geeft (idem
	# TEST -> DEPLOYED).
	flight_state: int = STATE_NONE
	# Laatst aangekondigde flight_state — main-loop bookkeeping om dubbele EVT
	# STATE-uitzendingen te voorkomen. Start op ``STATE_NONE`` (gelijk aan de
	# initiële ``flight_state``) zodat boot in CONFIG geen onnodige
	# ``EVT STATE NONE`` over de radio gooit; pas bij de eerste echte transitie
	# (bv. SET MODE MISSION → PAD_IDLE) wordt de EVT verzonden en deze
	# gelijkgetrokken aan ``flight_state``.
	last_announced_flight_state: int = field(default=STATE_NONE, repr=False)
	# Laatste reden waarom de state transities maakte (Fase 8b). ``None`` bij
	# boot of na een SET STATE-handmatige transitie. De main loop pikt deze op
	# om mee te sturen in ``EVT STATE <NAME> <REASON>``; daarna mag het veld
	# blijven staan tot de volgende transitie 'm overschrijft (handig voor
	# diagnose via GET STATE).
	last_transition_reason: Optional[str] = field(default=None, repr=False)

	def __post_init__(self) -> None:
		# Sync flight_state met mode tenzij de aanroeper expliciet een andere
		# combinatie heeft opgegeven. We doen dit alleen als flight_state nog
		# op de default ``STATE_NONE`` staat — anders respecteren we de keuze
		# van de aanroeper (bv. tests die een specifieke transitie checken).
		if self.flight_state == STATE_NONE:
			m = (self.mode or "").upper()
			if m == "TEST":
				self.flight_state = STATE_DEPLOYED
			elif m == "MISSION":
				self.flight_state = STATE_PAD_IDLE


def _update_apogee(state: RadioRuntimeState, altitude_m: float, pressure_hpa: float) -> None:
	"""Werk ``state.max_alt_m`` bij als de nieuwe hoogte hoger is."""
	if state.max_alt_m is None or altitude_m > state.max_alt_m:
		state.max_alt_m = float(altitude_m)
		state.min_pressure_hpa = float(pressure_hpa)
		state.max_alt_ts = time_mod.time()


def _reset_apogee(state: RadioRuntimeState) -> None:
	"""Wis apogee-tracking. Aanroepen door ``RESET APOGEE`` én bij elke nieuwe
	missiestart, anders bestuurt een vorige sessie de DEPLOY-trigger:
	``(max_alt - current_alt) >= trig_deploy_descent_m`` zou met een stale
	``max_alt`` van bv. 5 m direct DEPLOYED triggeren zodra je een paar meter
	stijgt — terwijl de nieuwe missie nog niet eens ASCENT bereikte.
	"""
	state.max_alt_m = None
	state.min_pressure_hpa = None
	state.max_alt_ts = None


# --- Flight-state machine (Fase 8 + 8b) --------------------------------------
# Eén-richting machine die alleen actief is in MISSION. Per transitie hebben we
# **OR-logica met meerdere triggers**: IMU-signalen (primair, snel, betrouwbaar
# tijdens de motor-burn / vrije val / impact) én altitude-/descent-/at-rest-
# backups. Zo komen we toch in DEPLOYED als de IMU-trigger faalt — kritisch
# omdat we maar één lancering hebben.
#
# Conventie van de **reason-string** in EVT STATE / log-records:
#
#   ASCENT    : "ACC"       (peak ‖a_lin‖ ≥ trig_ascent_accel_g)
#               "ALT"       (alt_m ≥ trig_ascent_height_m, backup)
#   DEPLOYED  : "FREEFALL"  (vrije val ≥ trig_deploy_freefall_s)
#               "SHOCK"     (peak ‖a‖ ≥ trig_deploy_shock_g, parachute-snap)
#               "DESCENT"   (max_alt − alt ≥ trig_deploy_descent_m, backup)
#   LANDED    : "IMPACT"    (peak ‖a‖ ≥ trig_land_impact_g)
#               "STABLE"    (alt_stable_for_s ≥ trig_land_stable_s)
#               "ALT"       (alt_m ≤ trig_land_hz_m, backup)
#
# Volgorde van checks per transitie = volgorde waarin de reason wordt
# gerapporteerd (eerst IMU, dan backup) — als beide tegelijk waar zijn winnen
# de IMU-redenen.

# Sentinel voor backwards-compat: ``evaluate_flight_state(state, alt_m)`` mag
# nog gewoon een float meekrijgen (oude callers). Intern wrappen we dat dan in
# een minimal "alleen alt"-snapshot view zodat de IMU-checks falen (None) en
# alleen de backup-altitudechecks meedoen.
class _AltOnlyView:
	"""Read-only view die alleen ``alt_m`` invult; alle IMU-velden = None."""

	def __init__(self, alt_m: Optional[float]) -> None:
		self.alt_m = alt_m
		self.peak_accel_g = None
		self.freefall_for_s = 0.0
		self.alt_stable_for_s = 0.0


def _coerce_snapshot(arg: object) -> object:
	"""Accepteer zowel ``SensorSnapshot`` als rauwe float (oud callsignature)."""

	if isinstance(arg, (int, float)):
		return _AltOnlyView(float(arg))
	if arg is None:
		return _AltOnlyView(None)
	return arg


def evaluate_flight_state(
	state: RadioRuntimeState, snap_or_alt: object
) -> Tuple[int, Optional[str]]:
	"""Bereken (next_state, reason).

	``snap_or_alt`` mag een :class:`SensorSnapshot` zijn (volledige multi-
	trigger evaluatie) **of** een float (alleen altitude — backwards compat
	voor callers die nog geen sampler hebben gepland).

	``reason`` is ``None`` als er geen transitie nodig is; anders één van de
	strings beschreven boven (bv. ``"ACC"``, ``"FREEFALL"``).
	"""

	cur = int(state.flight_state)
	if state.mode != "MISSION":
		return cur, None

	snap = _coerce_snapshot(snap_or_alt)
	alt_m = getattr(snap, "alt_m", None)
	peak_g = getattr(snap, "peak_accel_g", None)
	freefall_s = float(getattr(snap, "freefall_for_s", 0.0) or 0.0)
	stable_s = float(getattr(snap, "alt_stable_for_s", 0.0) or 0.0)

	if cur == STATE_PAD_IDLE:
		# IMU-primair: motor-burn -> piek-acceleratie. Backup: hoogte-drempel.
		if peak_g is not None and float(peak_g) >= float(state.trig_ascent_accel_g):
			return STATE_ASCENT, "ACC"
		if alt_m is not None and float(alt_m) >= float(state.trig_ascent_height_m):
			return STATE_ASCENT, "ALT"
		return cur, None

	if cur == STATE_ASCENT:
		# IMU-primair: vrije val (motor uit) of plotse jerk (parachute).
		# Backup: descent vanaf apogee.
		if freefall_s >= float(state.trig_deploy_freefall_s):
			return STATE_DEPLOYED, "FREEFALL"
		if peak_g is not None and float(peak_g) >= float(state.trig_deploy_shock_g):
			return STATE_DEPLOYED, "SHOCK"
		if state.max_alt_m is not None and alt_m is not None:
			descent = float(state.max_alt_m) - float(alt_m)
			if descent >= float(state.trig_deploy_descent_m):
				return STATE_DEPLOYED, "DESCENT"
		return cur, None

	if cur == STATE_DEPLOYED:
		# IMU-primair: impact-spike of langdurige rust.
		# Backup: terug binnen trig_land_hz_m boven grond.
		if peak_g is not None and float(peak_g) >= float(state.trig_land_impact_g):
			return STATE_LANDED, "IMPACT"
		if stable_s >= float(state.trig_land_stable_s):
			return STATE_LANDED, "STABLE"
		if alt_m is not None and float(alt_m) <= float(state.trig_land_hz_m):
			return STATE_LANDED, "ALT"
		return cur, None

	return cur, None


def maybe_advance_flight_state(
	state: RadioRuntimeState, snap_or_alt: object
) -> Optional[str]:
	"""Voer één state-machine-stap uit; retourneer ``reason`` bij transitie.

	Returns:
		``None`` als de state niet veranderd is, anders de **reden-string**
		(bv. ``"ACC"``, ``"FREEFALL"``, ``"IMPACT"``). Caller kan die meegeven
		aan EVT STATE en log-record.

	Backwards compat: ``snap_or_alt`` mag een float zijn (alleen altitude).
	"""

	new, reason = evaluate_flight_state(state, snap_or_alt)
	if new != int(state.flight_state):
		state.flight_state = int(new)
		state.last_transition_reason = reason
		return reason
	return None


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
		"GET STATE",
		"SERVO STATUS",  # alleen-lezen; geen rail/pulse-effect
		"GET CAMSTATS",  # alleen-lezen; geen capture/pulse
		# Gimbal-status en aan/uit-toggle mogen **wel** tijdens MISSION:
		# de operator moet de closed-loop kunnen stilleggen als hij ziet
		# dat de gimbal jaagt (of juist inschakelen na een vertraagde
		# deploy). GIMBAL HOME is bewust **niet** toegestaan omdat dat
		# rechtstreeks PWM schrijft en de autonome state-policy zou
		# doorkruisen.
		"GET GIMBAL",
		"GIMBAL ON",
		"GIMBAL OFF",
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
		"GET STATE",
		"SERVO STATUS",
		"GET CAMSTATS",
		# Zelfde reden als in MISSION: tijdens een dry-run wil je live
		# kunnen toggelen om het regelgedrag te observeren.
		"GET GIMBAL",
		"GIMBAL ON",
		"GIMBAL OFF",
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
	servo: Optional[Any] = None,
	min_free_mb: int = PREFLIGHT_MIN_FREE_MB,
) -> Tuple[List[str], List[str]]:
	"""Voer alle pre-MISSION checks uit; retourneer ``(missing_codes, info_tokens)``.

	Codes blijven kort (≤4 letters) zodat het antwoord in 60 bytes past:
	``TIME`` ``GND`` ``BME`` ``IMU`` ``DSK`` ``LOG`` ``FRQ`` ``GIM`` ``SVO``.
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

	# Fase 12: SVO-preflight. Falen ⇒ "SVO" in missing-codes.
	#   * Tuning-sub-state mag NIET actief zijn (anders blijft de rail aan
	#     tijdens de transitie en vertelt PARK liegen).
	#   * Calibratie moet compleet zijn voor BEIDE servo's, inclusief stow_us
	#     (anders kan PARK niet uitgevoerd worden).
	# Als ``servo`` None is, slaan we de check over (operator draait zonder
	# servo-hardware; gimbal_cfg-check hierboven heeft de JSON al geverifieerd).
	if servo is not None:
		try:
			if getattr(servo, "tuning_active", False):
				missing.append("SVO")
			elif not servo.calibration_complete():
				missing.append("SVO")
		except Exception:  # noqa: BLE001
			missing.append("SVO")

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
	snapshot: Optional[Any] = None,
	now_monotonic: Optional[float] = None,
	tags: Optional[List[TagDetection]] = None,
) -> bytes:
	"""Bouw één **binary** TLM-pakket (exact ``FRAME_SIZE`` = 60 bytes).

	Layout en sentinels: zie :mod:`cansat_hw.telemetry.codec`. Het
	``mode_state``-byte wordt afgeleid uit ``state.mode``; de flight-state
	is voorlopig een vaste mapping per mode (Fase 8 zal die dynamisch maken).

	Twee data-bronnen worden ondersteund:

	1. **``snapshot=...``** (Fase 7+8b): ``SensorSnapshot`` van een actieve
	   :class:`~cansat_hw.sensors.sampler.SensorSampler`. Geen extra sensor-
	   I/O hier; de state-machine krijgt de hele snapshot zodat IMU-triggers
	   (peak ‖a‖, freefall, alt-stable) meedoen.
	2. **``bme280`` / ``bno055`` direct** (legacy): valt terug op één-shot
	   reads zoals vóór Fase 7. De state-machine krijgt dan alleen ``alt_m``
	   en gebruikt enkel de altitude-backup-triggers.

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

	if snapshot is not None:
		# Fase 7-pad: lees alles uit de snapshot. Geen extra sensor I/O — de
		# sampler heeft al getikt in de main loop voordat we hier komen.
		alt_m = getattr(snapshot, "alt_m", None)
		p_hpa = getattr(snapshot, "pressure_hpa", None)
		t_c = getattr(snapshot, "temp_c", None)
		heading = getattr(snapshot, "heading_deg", None)
		roll = getattr(snapshot, "roll_deg", None)
		pitch = getattr(snapshot, "pitch_deg", None)
		ax_g = getattr(snapshot, "ax_g", None)
		ay_g = getattr(snapshot, "ay_g", None)
		az_g = getattr(snapshot, "az_g", None)
		sys_cal = getattr(snapshot, "sys_cal", None)
		gyro_cal = getattr(snapshot, "gyro_cal", None)
		accel_cal = getattr(snapshot, "accel_cal", None)
		mag_cal = getattr(snapshot, "mag_cal", None)
		if alt_m is not None and p_hpa is not None:
			_update_apogee(state, float(alt_m), float(p_hpa))
		# Volle multi-trigger evaluatie. Geen-op buiten MISSION.
		maybe_advance_flight_state(state, snapshot)
	else:
		# Legacy-pad: één-shot sensor reads.
		if bme280 is not None:
			try:
				t_read, p_read, _ = bme280.read()
				p_hpa = float(p_read)
				t_c = float(t_read)
				if state.ground_hpa is not None:
					alt_m = pressure_to_altitude_m(p_hpa, float(state.ground_hpa))
					_update_apogee(state, alt_m, p_hpa)
					maybe_advance_flight_state(state, alt_m)
			except Exception:  # noqa: BLE001
				alt_m = p_hpa = t_c = None

		if bno055 is not None:
			try:
				heading, roll, pitch = bno055.read_euler()
			except Exception:  # noqa: BLE001
				heading = roll = pitch = None
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
		# Fase 8: gebruik de **dynamische** flight_state (PAD_IDLE, ASCENT,
		# DEPLOYED, LANDED) i.p.v. een vaste mapping per system mode. Wordt
		# door SET MODE / state-machine / SET STATE up-to-date gehouden.
		state=int(state.flight_state),
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

	Werkt in **TEST én MISSION** — beide modes draaien dezelfde TLM-loop.
	Retourneert ``(send_telemetry, end_test)``. ``end_test`` is alleen ooit
	True in TEST (bij verstreken duration). De **caller** doet de TX en
	roept daarna ``test_mode_end(state)`` aan als ``end_test`` True is.
	Geen sensor-I/O hier zodat dit deterministisch te testen blijft.
	"""
	if state.mode not in ("TEST", "MISSION"):
		return False, False
	if now_monotonic is None:
		now_monotonic = time_mod.monotonic()
	# TEST heeft een harde deadline; MISSION loopt door tot SET MODE CONFIG.
	if (
		state.mode == "TEST"
		and state.test_deadline_monotonic is not None
		and now_monotonic >= state.test_deadline_monotonic
	):
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
	"""Schuif de volgende TLM-deadline op na een geslaagde TX.

	De aanroeper kiest een ``interval_s`` dat past bij de actieve mode
	(``TEST_MODE_TLM_INTERVAL_S`` voor TEST, ``MISSION_MODE_TLM_INTERVAL_S``
	of een CLI-override voor MISSION).
	"""
	if now_monotonic is None:
		now_monotonic = time_mod.monotonic()
	state.test_next_tlm_monotonic = now_monotonic + float(interval_s)


def _clear_session_bookkeeping(state: RadioRuntimeState) -> None:
	"""Leeg alle TLM-/TEST-bookkeeping. Gebruikt door SET MODE CONFIG en test_mode_end."""
	state.test_duration_s = None
	state.test_start_monotonic = None
	state.test_deadline_monotonic = None
	state.test_next_tlm_monotonic = None
	state.test_dest_node = None


def test_mode_end(state: RadioRuntimeState) -> None:
	"""Zet de state netjes terug naar CONFIG ná een TEST-timer-afloop."""
	state.mode = "CONFIG"
	state.flight_state = STATE_NONE
	_clear_session_bookkeeping(state)


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


def _format_servo_status(controller: Any) -> bytes:
	"""``OK SVO STATUS R=<on|off> T=<on|off> SEL=<n|none> US1=<u> US2=<u>``."""
	st = controller.status()
	rail = "on" if st.rail_on else "off"
	tun = "on" if st.tuning_active else "off"
	sel = str(st.selected) if st.selected is not None else "-"
	us1 = int(st.current_us.get(1, 0))
	us2 = int(st.current_us.get(2, 0))
	cal = "yes" if st.cal_complete else "no"
	msg = "OK SVO R=%s T=%s SEL=%s US1=%d US2=%d CAL=%s" % (
		rail,
		tun,
		sel,
		us1,
		us2,
		cal,
	)
	return _truncate(msg.encode("utf-8"))


def _handle_servo_cmd(
	state: RadioRuntimeState,
	controller: Optional[Any],
	tokens: List[str],
) -> bytes:
	"""Dispatch alle ``SERVO …``-commando's. Caller heeft al geverifieerd dat
	``tokens[0].upper() == "SERVO"``.

	Return-conventie: ``OK SVO …`` of ``ERR SVO …`` (3-letter code houdt
	het reply binnen 60 B). ``SERVO STATUS`` is altijd toegestaan; alle
	andere SERVO-commando's vereisen ``state.mode == "CONFIG"`` zodat de
	autonome rail-policy in MISSION/TEST niet doorkruist wordt.
	"""
	if controller is None:
		return _truncate(b"ERR SVO NOHW")
	if len(tokens) < 2:
		return _truncate(b"ERR SVO BAD")

	sub = tokens[1].upper()
	# STATUS overal toegestaan (read-only). Reset wel de tuning-watchdog
	# zodat de operator de REPL kan refreshen zonder de servo's te bewegen.
	if sub == "STATUS":
		controller.note_activity()
		return _format_servo_status(controller)

	# Vanaf hier: alleen in CONFIG. Tijdens MISSION/TEST regelt de
	# state-policy de rail; manueel ingrijpen zou de gimbal saboteren.
	if state.mode != "CONFIG":
		return _truncate(b"ERR SVO BUSY")

	# --- Park / rail commands (geen tuning vereist) ---
	if sub == "ENABLE":
		controller.enable_rail()
		return _truncate(b"OK SVO ENABLE")
	if sub == "DISABLE":
		# Als tuning actief is, eerst stoppen (anders watchdog vuurt later toch).
		if controller.tuning_active:
			controller.stop_tuning()
			return _truncate(b"OK SVO DISABLE TUNING_STOPPED")
		controller.disable_rail()
		return _truncate(b"OK SVO DISABLE")
	if sub == "PARK":
		# Convenience: full sequence ENABLE → STOW BOTH → wait → DISABLE.
		ok = controller.park_all()
		if not ok:
			return _truncate(b"ERR SVO NOSTOW")
		return _truncate(b"OK SVO PARK")
	if sub == "HOME":
		# Convenience: ENABLE → write center_us BOTH (rail blijft aan).
		# Niet bruikbaar tijdens tuning (gebruik daar SET).
		if controller.tuning_active:
			return _truncate(b"ERR SVO TUNON")
		try:
			us1, us2 = controller.home_all()
		except RuntimeError as e:
			return _truncate(("ERR SVO %s" % e).encode("utf-8", errors="replace"))
		if us1 is None or us2 is None:
			return _truncate(b"ERR SVO NOCEN")
		return _truncate(("OK SVO HOME US1=%d US2=%d" % (us1, us2)).encode("utf-8"))
	if sub == "STOW":
		# Manual stow: vereist actieve rail; geen wait/disable hier.
		if not controller.rail_on:
			return _truncate(b"ERR SVO RAILOFF")
		us1, us2 = controller.stow_all()
		if us1 is None or us2 is None:
			return _truncate(b"ERR SVO NOSTOW")
		return _truncate(("OK SVO STOW US1=%d US2=%d" % (us1, us2)).encode("utf-8"))

	# --- Tuning sub-state ---
	if sub == "START":
		# Optionele servo-index (default = 1).
		idx = 1
		if len(tokens) >= 3:
			try:
				idx = int(tokens[2])
			except ValueError:
				return _truncate(b"ERR SVO BAD")
			if idx not in (1, 2):
				return _truncate(b"ERR SVO BAD")
		try:
			controller.start_tuning(idx)
		except Exception as e:  # noqa: BLE001
			return _truncate(("ERR SVO %s" % e).encode("utf-8", errors="replace"))
		return _format_servo_status(controller)
	if sub == "STOP":
		controller.stop_tuning()
		return _truncate(b"OK SVO STOP")
	if sub == "SAVE":
		try:
			path = controller.save_calibration()
		except OSError as e:
			return _truncate(("ERR SVO SAVE %s" % e).encode("utf-8", errors="replace"))
		# Pad-naam kan lang zijn; geef alleen filename om binnen 60 B te blijven.
		return _truncate(("OK SVO SAVE %s" % Path(path).name).encode("utf-8"))

	# Hierna: alleen geldig tijdens actieve tuning.
	if not controller.tuning_active:
		return _truncate(b"ERR SVO NOTUN")

	if sub == "SEL":
		if len(tokens) < 3:
			return _truncate(b"ERR SVO BAD")
		try:
			idx = int(tokens[2])
		except ValueError:
			return _truncate(b"ERR SVO BAD")
		try:
			controller.select(idx)
		except (ValueError, RuntimeError) as e:
			return _truncate(("ERR SVO %s" % e).encode("utf-8", errors="replace"))
		return _format_servo_status(controller)
	if sub == "STEP":
		if len(tokens) < 3:
			return _truncate(b"ERR SVO BAD")
		try:
			delta = int(tokens[2])
		except ValueError:
			return _truncate(b"ERR SVO BAD")
		# Sanity: max 200 µs per stap zodat een typo niet de servo door
		# de mechanische limieten ramt.
		if not (-200 <= delta <= 200):
			return _truncate(b"ERR SVO BAD")
		try:
			us = controller.step(delta)
		except RuntimeError as e:
			return _truncate(("ERR SVO %s" % e).encode("utf-8", errors="replace"))
		return _truncate(("OK SVO STEP %d" % us).encode("utf-8"))
	if sub == "SET":
		if len(tokens) < 3:
			return _truncate(b"ERR SVO BAD")
		try:
			us = int(tokens[2])
		except ValueError:
			return _truncate(b"ERR SVO BAD")
		if not (500 <= us <= 2500):
			return _truncate(b"ERR SVO BAD")
		try:
			out = controller.set_us(us)
		except RuntimeError as e:
			return _truncate(("ERR SVO %s" % e).encode("utf-8", errors="replace"))
		return _truncate(("OK SVO SET %d" % out).encode("utf-8"))
	if sub in ("MIN", "CENTER", "MAX", "STOW_MARK"):
		# STOW_MARK i.p.v. STOW omdat SERVO STOW al de manual-stow-actie is.
		mark_kind = sub if sub != "STOW_MARK" else "STOW"
		try:
			us = controller.mark(mark_kind)
		except (ValueError, RuntimeError) as e:
			return _truncate(("ERR SVO %s" % e).encode("utf-8", errors="replace"))
		return _truncate(("OK SVO %s %d" % (mark_kind, us)).encode("utf-8"))

	return _truncate(b"ERR SVO BAD")


def _format_tag_list(detections: List[Any], *, top_n: int = 2) -> str:
	"""Compacte ``<id>=<cm>`` representatie van tag-detecties voor 60-B replies.

	We sorteren op ``max_side_px`` (grootste tag eerst) en nemen de top-N
	om binnen de RFM69-frame-limiet te blijven. Afstand wordt in hele
	centimeters gerapporteerd (``int16``-compat met TLM-codec) zodat de
	operator meteen met !alt/!apogee kan vergelijken zonder unit-switch.
	"""
	if not detections:
		return ""
	ordered = sorted(
		detections,
		key=lambda m: float(getattr(m, "max_side_px", 0.0)),
		reverse=True,
	)[: max(1, int(top_n))]
	parts = []
	for m in ordered:
		tid = int(getattr(m, "tag_id", 0)) & 0xFF
		d_cm = int(round(float(getattr(m, "distance_m", 0.0)) * 100.0))
		parts.append("%d=%d" % (tid, d_cm))
	return " ".join(parts)


def _format_camstats(
	services: Optional[Any],
	thread: Optional[Any],
) -> bytes:
	"""``OK CAMSTATS A=<on|off> F=<frames> S=<saved> E=<errors> D=<detects>``.

	Laat leeg als er geen hardware is. ``A=`` = ``CameraThread.active``
	(``off`` in CONFIG, ``on`` in DEPLOYED). ``F``/``S`` komen uit de
	thread; ``D`` (synchrone ``CAM DETECT``-calls) uit de services.
	"""
	if services is None and thread is None:
		return _truncate(b"ERR CAM NOHW")
	tstats = thread.stats() if thread is not None else {}
	sstats = services.stats() if services is not None else {}
	active = "on" if tstats.get("active") else "off"
	frames = int(tstats.get("frames", 0))
	saved = int(tstats.get("saved", 0))
	errors = int(tstats.get("errors", 0)) + int(sstats.get("errors", 0))
	detects = int(sstats.get("detects", 0))
	msg = "OK CAMSTATS A=%s F=%d S=%d E=%d D=%d" % (
		active,
		frames,
		saved,
		errors,
		detects,
	)
	return _truncate(msg.encode("utf-8"))


def _handle_cam_cmd(
	state: RadioRuntimeState,
	services: Optional[Any],
	thread: Optional[Any],
	tokens: List[str],
) -> bytes:
	"""Dispatch ``CAM SHOOT`` / ``CAM DETECT``. ``GET CAMSTATS`` loopt via
	een aparte branch in :func:`handle_wire_line`.

	Return-conventie: ``OK SHOOT …`` / ``OK DETECT …`` of ``ERR CAM …``.
	CONFIG-only: de :class:`CameraThread` mag niet gelijktijdig draaien
	(anders concurrent access op Picamera2). Als de thread actief staat —
	d.w.z. we zitten in DEPLOYED — krijgt de operator ``ERR CAM BUSY``.
	Debug-foto's tijdens DEPLOYED komen van de thread zelf (save-every-N).
	"""
	if services is None:
		return _truncate(b"ERR CAM NOHW")
	if len(tokens) < 2:
		return _truncate(b"ERR CAM BAD")
	if state.mode != "CONFIG":
		return _truncate(b"ERR CAM BUSY")
	if thread is not None and thread.is_active():
		return _truncate(b"ERR CAM BUSY")

	sub = tokens[1].upper()
	if sub == "SHOOT":
		try:
			result = services.shoot_and_detect(save=True)
		except RuntimeError as e:
			return _truncate(("ERR CAM " + str(e))[:MAX_PAYLOAD].encode("utf-8", errors="replace"))
		w, h = result.image_wh
		name = result.path.name if result.path is not None else "-"
		tag_str = _format_tag_list(result.detections, top_n=2)
		body = "OK SHOOT %s %dx%d T=%d" % (name, int(w), int(h), len(result.detections))
		if tag_str:
			body = body + " " + tag_str
		return _truncate(body.encode("utf-8"))
	if sub == "DETECT":
		try:
			metrics, (w, h) = services.detect_once()
		except RuntimeError as e:
			return _truncate(("ERR CAM " + str(e))[:MAX_PAYLOAD].encode("utf-8", errors="replace"))
		tag_str = _format_tag_list(metrics, top_n=2)
		body = "OK DETECT %dx%d T=%d" % (int(w), int(h), len(metrics))
		if tag_str:
			body = body + " " + tag_str
		return _truncate(body.encode("utf-8"))
	return _truncate(b"ERR CAM BAD")


def _format_gimbal_status(gimbal_loop: Optional[Any]) -> bytes:
	"""``OK GIMBAL E=<on|off> P=<prim|cold> T=<ticks> R=<rejected> ...``.

	Compacte status die in één 60-byte reply past. Laat bewust de PI-tuning
	(kx/ky/…) achterwege — dat is een debug-commando en hoeft niet elk
	tick. ``U1/U2`` = laatste geschreven PWM (µs); ``E1/E2`` = laatste
	LPF-fout in cg (1/100 m/s²) zodat we onder het 60 B-limiet blijven.
	"""
	if gimbal_loop is None:
		return _truncate(b"ERR GMB NOHW")
	st = gimbal_loop.status()
	ex = "NA" if st.last_err_x is None else ("%+d" % int(round(st.last_err_x * 100.0)))
	ey = "NA" if st.last_err_y is None else ("%+d" % int(round(st.last_err_y * 100.0)))
	u1 = "NA" if st.last_us1 is None else str(int(st.last_us1))
	u2 = "NA" if st.last_us2 is None else str(int(st.last_us2))
	msg = "OK GIMBAL E=%s P=%s T=%d R=%d EX=%s EY=%s U1=%s U2=%s" % (
		"on" if st.enabled else "off",
		"prim" if st.primed else "cold",
		int(st.ticks),
		int(st.rejected_samples),
		ex,
		ey,
		u1,
		u2,
	)
	return _truncate(msg.encode("utf-8"))


def _handle_gimbal_cmd(
	state: RadioRuntimeState,
	gimbal_loop: Optional[Any],
	controller: Optional[Any],
	tokens: List[str],
) -> bytes:
	"""Dispatch ``GIMBAL ON|OFF|HOME``.

	* ``GIMBAL ON/OFF``: zet de closed-loop-flag. Toegestaan in CONFIG,
	  TEST én MISSION (de operator moet in nood kunnen deactiveren). De
	  loop gebeurt pas écht iets als de main loop óók ``flight_state ==
	  DEPLOYED`` ziet en de rail aan staat — die autonome policy blijft
	  gelden. Zie ``docs/servo_tuning.md``.
	* ``GIMBAL HOME``: stuurt beide servo's naar ``center_us``. Alleen in
	  CONFIG omdat het rechtstreeks PWM schrijft en interfereert met een
	  actieve MISSION/TEST-regellus. Reset ook de rate-limit referentie
	  in de :class:`GimbalLoop` zodat een volgende enable schoon vertrekt.
	"""
	if gimbal_loop is None:
		return _truncate(b"ERR GMB NOHW")
	if len(tokens) < 2:
		return _truncate(b"ERR GMB BAD")

	sub = tokens[1].upper()
	if sub == "ON":
		gimbal_loop.enable()
		return _truncate(b"OK GIMBAL ON")
	if sub == "OFF":
		gimbal_loop.disable()
		return _truncate(b"OK GIMBAL OFF")
	if sub == "HOME":
		if state.mode != "CONFIG":
			return _truncate(b"ERR GMB BUSY")
		if controller is None:
			return _truncate(b"ERR GMB NOSVO")
		# Reset de loop-referentie en verkrijg de center-PWM via de
		# ServoController (die ook de rail aan zet en clamps toepast —
		# niet rechtstreeks via gimbal_loop zodat we bij afwezige
		# calibratie dezelfde foutcodes krijgen als ``SERVO HOME``).
		if controller.tuning_active:
			return _truncate(b"ERR GMB TUNON")
		try:
			us1, us2 = controller.home_all()
		except RuntimeError as e:
			return _truncate(("ERR GMB %s" % e).encode("utf-8", errors="replace"))
		if us1 is None or us2 is None:
			return _truncate(b"ERR GMB NOCEN")
		# Sync de loop-positie zodat rate-limit vanaf center rekent.
		gimbal_loop.home_pulses()
		return _truncate(("OK GIMBAL HOME US1=%d US2=%d" % (us1, us2)).encode("utf-8"))
	return _truncate(b"ERR GMB BAD")


def handle_wire_line(
	rfm: Any,
	state: RadioRuntimeState,
	line: str,
	*,
	bme280: Optional[Any] = None,
	bno055: Optional[Any] = None,
	photo_dir: Optional[Union[str, Path]] = None,
	gimbal_cfg: Optional[Union[str, Path]] = None,
	servo: Optional[Any] = None,
	camera_services: Optional[Any] = None,
	camera_thread: Optional[Any] = None,
	gimbal_loop: Optional[Any] = None,
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
		state.flight_state = STATE_NONE
		state.last_transition_reason = None
		# MISSION/TEST → CONFIG: ruim de TLM-scheduler en session-dest op,
		# anders blijft de Zero proberen telemetrie te pushen of stuurt hij
		# bij een volgende mode-wissel meteen naar een verouderd node.
		_clear_session_bookkeeping(state)
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
			servo=servo,
		)
		if missing:
			return _truncate(("ERR PRE " + " ".join(missing)).encode("utf-8"))
		now = time_mod.monotonic()
		state.mode = "MISSION"
		# Start altijd in PAD_IDLE bij overgang naar MISSION; de state-machine
		# klimt vanaf hier op basis van autonome TLM-reads (mini-fase 7).
		state.flight_state = STATE_PAD_IDLE
		# Verse missie → vorige reason wissen.
		state.last_transition_reason = None
		# Apogee MOET resetten bij elke nieuwe missie, anders bestuurt de
		# vorige sessie de DEPLOY-trigger. Voorbeeld: een vorige LANDED-run
		# liet ``max_alt_m=5.08m`` achter; de volgende missie ziet meteen na
		# een paar meter stijgen ``descent>=3m`` t.o.v. die stale max en
		# springt prematuur naar DEPLOYED.
		_reset_apogee(state)
		# Activeer de MISSION-TLM-loop: zelfde scheduler als TEST, maar zonder
		# deadline. De main loop kiest het interval (CLI ``--mission-tlm-interval``)
		# en zet ``test_dest_node`` op ``from_node`` ná het reply zodat
		# unsolicited TLM/EVT bij de juiste base station aankomt.
		state.test_next_tlm_monotonic = now + 0.5
		state.test_duration_s = None
		state.test_start_monotonic = None
		state.test_deadline_monotonic = None
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
		# TEST is een vaste dry-run van DEPLOYED — geen state-machine.
		state.flight_state = STATE_DEPLOYED
		state.last_transition_reason = None
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

	# Fase 8b — multi-trigger SET TRIG <STATE> <FIELD> <VAL>. Naast de oude
	# ``SET TRIGGER ASCENT/DEPLOY/LAND <m>`` (alt-only) kunnen we nu ook IMU-
	# drempels tunen. Compact 5-token-formaat houdt het binnen één RFM69-frame.
	#
	#   SET TRIG ASC HEIGHT 5.0     -> trig_ascent_height_m
	#   SET TRIG ASC ACC    3.0     -> trig_ascent_accel_g
	#   SET TRIG DEP DESCENT 3.0    -> trig_deploy_descent_m
	#   SET TRIG DEP SHOCK   5.0    -> trig_deploy_shock_g
	#   SET TRIG DEP FREEFALL 0.5   -> trig_deploy_freefall_s
	#   SET TRIG LND ALT     5.0    -> trig_land_hz_m
	#   SET TRIG LND IMPACT  8.0    -> trig_land_impact_g
	#   SET TRIG LND STABLE  5.0    -> trig_land_stable_s
	if len(tokens) == 5 and tokens[0].upper() == "SET" and tokens[1].upper() == "TRIG":
		st_name = tokens[2].upper()
		field_name = tokens[3].upper()
		try:
			v = float(tokens[4])
		except ValueError:
			return b"ERR BAD TRIG"
		# Tabel (state, field) -> (attr, range, unit, fmt)
		field_map = {
			("ASC", "HEIGHT"): ("trig_ascent_height_m", (0.5, 1000.0), "m", "%.2f"),
			("ASC", "ACC"): ("trig_ascent_accel_g", (0.5, 20.0), "g", "%.2f"),
			("DEP", "DESCENT"): ("trig_deploy_descent_m", (0.5, 100.0), "m", "%.2f"),
			("DEP", "SHOCK"): ("trig_deploy_shock_g", (1.0, 20.0), "g", "%.2f"),
			("DEP", "FREEFALL"): ("trig_deploy_freefall_s", (0.05, 10.0), "s", "%.2f"),
			("LND", "ALT"): ("trig_land_hz_m", (0.5, 500.0), "m", "%.2f"),
			("LND", "IMPACT"): ("trig_land_impact_g", (1.0, 30.0), "g", "%.2f"),
			("LND", "STABLE"): ("trig_land_stable_s", (1.0, 60.0), "s", "%.2f"),
		}
		entry = field_map.get((st_name, field_name))
		if entry is None:
			return b"ERR BAD TRIG"
		attr, (lo, hi), unit, fmt = entry
		if not (lo <= v <= hi):
			return b"ERR BAD TRIG"
		setattr(state, attr, v)
		msg = ("OK TRIG %s %s " + fmt + "%s") % (st_name, field_name, v, unit)
		return _truncate(msg.encode("utf-8"))

	if su == "GET TRIGGERS":
		# Compacte alt-only weergave (back-compat met oude operator-tools).
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

	# Volledige (multi-trigger) weergave; past nét binnen 60 B als float-formats
	# kort blijven. Bewust een apart commando zodat ``GET TRIGGERS`` de oude
	# vorm behoudt en bestaande tooling niet stuk gaat.
	if su == "GET TRIG ALL":
		msg = (
			"OK TRIG A=%.1fm/%.1fg D=%.1fm/%.1fg/%.1fs L=%.1fm/%.1fg/%.1fs"
		) % (
			state.trig_ascent_height_m,
			state.trig_ascent_accel_g,
			state.trig_deploy_descent_m,
			state.trig_deploy_shock_g,
			state.trig_deploy_freefall_s,
			state.trig_land_hz_m,
			state.trig_land_impact_g,
			state.trig_land_stable_s,
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
		# Als we in MISSION zitten, zet de state machine eventueel een stap
		# vooruit. Buiten MISSION is dit een no-op.
		maybe_advance_flight_state(state, alt)
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
		_reset_apogee(state)
		return b"OK APOGEE RESET"

	if su == "GET STATE":
		# Hang de laatste transitie-reden mee zodat de operator achteraf nog
		# weet WAT de overgang triggerde (ACC/ALT/FREEFALL/SHOCK/DESCENT/
		# IMPACT/STABLE). Backwards-compat: oude parsers lezen ``OK STATE
		# <NAME>`` en negeren extra tokens; de Pico-CLI parsed parts[3] mee.
		base = "OK STATE %s" % state_name(state.flight_state)
		if state.last_transition_reason:
			base += " " + str(state.last_transition_reason)
		return _truncate(base.encode("utf-8"))

	if len(tokens) == 3 and tokens[0].upper() == "SET" and tokens[1].upper() == "STATE":
		# Forceer een flight-state. Alleen in CONFIG bedoeld voor pre-staging
		# of klas-demo; tijdens MISSION/TEST blijft de state-machine (resp. de
		# vaste TEST-mapping) heilig.
		if state.mode != "CONFIG":
			return b"ERR BUSY"
		new = state_value_for_name(tokens[2])
		if new is None:
			return b"ERR BAD STATE"
		state.flight_state = int(new)
		# Manuele override: oude transitie-reden wissen zodat een volgende
		# GET STATE niet ten onrechte ``OK STATE <NEW> <STALE_REASON>``
		# rapporteert (zou suggereren dat de IMU triggerde terwijl de
		# operator zelf de state forceerde).
		state.last_transition_reason = None
		return _truncate(("OK STATE %s" % state_name(new)).encode("utf-8"))

	if su == "PREFLIGHT":
		missing, info = preflight_checks(
			state,
			rfm,
			bme280,
			bno055,
			photo_dir=photo_dir,
			gimbal_cfg=gimbal_cfg,
			servo=servo,
		)
		return _format_preflight_reply(missing, info)

	if tokens and tokens[0].upper() == "SERVO":
		return _handle_servo_cmd(state, servo, tokens)

	if su == "GET CAMSTATS":
		return _format_camstats(camera_services, camera_thread)

	if tokens and tokens[0].upper() == "CAM":
		return _handle_cam_cmd(state, camera_services, camera_thread, tokens)

	if su == "GET GIMBAL":
		return _format_gimbal_status(gimbal_loop)

	if tokens and tokens[0].upper() == "GIMBAL":
		return _handle_gimbal_cmd(state, gimbal_loop, servo, tokens)

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
				maybe_advance_flight_state(state, alt)
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
