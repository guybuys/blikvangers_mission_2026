"""Continue sensor-sampler (Fase 7) — coöperatief, geen threads.

Centralizeert BME280- en BNO055-reads in één **pull-based** lus die de main
loop in elke iteratie aanroept. Dat geeft drie voordelen die de Fase 8b
multi-trigger state-machine nodig heeft:

1. **Verse snapshot zonder priming.** Door op elke main-loop-tick één read te
   doen blijft het IIR-filter van de BME280 vanzelf "ingehaald"; ad-hoc
   commando's als ``GET ALT`` hoeven geen aparte burst meer te doen.
2. **Rolling-window statistieken** (peak ‖a‖, σ‖a‖, freefall-duur, alt-
   stabiliteit) — de signalen waarop de IMU-triggers in de state-machine
   beslissen. Deze worden incrementeel onderhouden, dus elke ``tick()`` is
   O(1) qua geheugen en CPU.
3. **Robust tegen sensor-failures.** Een falende BME-read laat de BNO-read
   ongemoeid, en omgekeerd. De velden in de snapshot blijven dan op de laatst
   bekende waarde of ``None`` als er nog nooit een succesvolle read was.

Geen threads, geen async — dit is bewust pull-based zodat de bestaande
``handle_wire_line``-/TLM-loop de baas blijft over de cadence (cruciaal voor
half-duplex RFM69 timing).
"""

from __future__ import annotations

import math
import time as time_mod
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional, Tuple

# ``g`` in m/s² — zelfde constante als in ``wire_protocol.py`` zodat alle
# accel-conversies consistent zijn.
_G_MS2 = 9.80665

# Default rolling-window-grootte voor peak/σ-berekening. Bij ~20 Hz tick-rate
# is dit ongeveer 1.25 s aan recente samples — kort genoeg om scherpe pieken
# (raket-boost ~0.5–1 s, parachute-snap <100 ms wordt door peak-tracking apart
# gevangen) te zien, lang genoeg om σ stabiel te schatten.
DEFAULT_WINDOW_SAMPLES = 25

# Default freefall-drempel: ‖a_lin‖ in g. BNO055 ``read_linear_acceleration``
# trekt zwaartekracht eraf, dus stilstand ≈ 0 g. Tijdens echte vrije val (na
# motor-burnout, vóór parachute-opening) blijft ‖a‖ < ~0.3 g. We meten dit als
# magnitude over de drie assen zodat de canister-oriëntatie er niet toe doet.
DEFAULT_FREEFALL_THRESH_G = 0.3

# Default alt-stabiliteit: max |Δalt| binnen het venster waarbinnen we de
# CanSat als "ligt stil op de grond" beschouwen. 0.5 m is comfortabel boven
# de BME280-ruis met IIR×16 (~0.1–0.2 m σ) en onder de typische daalsnelheid
# onder een parachute (~3–5 m/s).
DEFAULT_ALT_STABLE_EPS_M = 0.5


def _safe_norm(x: Optional[float], y: Optional[float], z: Optional[float]) -> Optional[float]:
	"""Euclidische norm; None als één van de componenten None is."""

	if x is None or y is None or z is None:
		return None
	return math.sqrt(float(x) * float(x) + float(y) * float(y) + float(z) * float(z))


def _pressure_to_altitude_m(pressure_hpa: float, ground_hpa: float) -> float:
	"""Inverse barometric formula (zie wire_protocol.pressure_to_altitude_m).

	Hier dupliceren we de berekening om een **import-cycle** met
	``cansat_hw.radio.wire_protocol`` te vermijden — die module importeert al
	``state_*``/``pack_tlm`` enz. en moet zelf ook ``SensorSnapshot``
	kunnen gebruiken.
	"""

	gp = float(ground_hpa)
	if gp <= 0:
		return 0.0
	ratio = float(pressure_hpa) / gp
	if ratio <= 0:
		return 44330.0
	return 44330.0 * (1.0 - (ratio ** (1.0 / 5.255)))


@dataclass
class SensorSnapshot:
	"""Laatste-bekende sensor-waardes plus rolling-window-statistieken.

	Velden zijn ``None`` zolang er nog geen succesvolle read was. ``alt_m``
	is alleen ingevuld als de aanroeper een ``ground_hpa`` meegaf aan
	``SensorSampler.tick(...)`` (en de BME-read slaagde).
	"""

	# Tijdstempels (monotonic = jitter-vrij voor intervallen; wall = UTC).
	monotonic: float = 0.0
	wall_time: float = 0.0

	# BME280 ruwe waardes + afgeleide hoogte.
	pressure_hpa: Optional[float] = None
	temp_c: Optional[float] = None
	humidity_pct: Optional[float] = None
	alt_m: Optional[float] = None

	# BNO055 oriëntatie (graden).
	heading_deg: Optional[float] = None
	roll_deg: Optional[float] = None
	pitch_deg: Optional[float] = None

	# BNO055 lineaire acceleratie (g, zwaartekracht eruit).
	ax_g: Optional[float] = None
	ay_g: Optional[float] = None
	az_g: Optional[float] = None
	# Magnitude ‖(ax,ay,az)‖ in g — handig voor trigger-logica (oriëntatie-
	# onafhankelijk).
	accel_mag_g: Optional[float] = None

	# BNO055 calibration counters (0–3).
	sys_cal: Optional[int] = None
	gyro_cal: Optional[int] = None
	accel_cal: Optional[int] = None
	mag_cal: Optional[int] = None

	# Rolling-window-statistieken voor de state-machine.
	# Allemaal over hetzelfde venster van laatste N samples.
	peak_accel_g: Optional[float] = None      # max ‖a‖ in venster
	accel_stddev_g: Optional[float] = None    # σ van ‖a‖ in venster
	freefall_for_s: float = 0.0               # consec. duur ‖a‖ < threshold
	alt_stable_for_s: float = 0.0             # consec. duur |Δalt| binnen eps

	# Sample-statistieken (handig voor diagnose).
	samples_taken: int = 0
	bme_failures: int = 0
	bno_failures: int = 0


@dataclass
class SensorSampler:
	"""Pull-based sampler die elke main-loop-iteratie ``tick()`` krijgt.

	Houdt een ``SensorSnapshot`` up-to-date. De main loop **bezit** de cadence
	en hoeft niet bang te zijn dat sampling de RFM69-RX/TX-timing kapotmaakt.
	"""

	bme280: object = None
	bno055: object = None
	window_samples: int = DEFAULT_WINDOW_SAMPLES
	freefall_thresh_g: float = DEFAULT_FREEFALL_THRESH_G
	alt_stable_eps_m: float = DEFAULT_ALT_STABLE_EPS_M

	# Interne state — niet rechtstreeks aanlezen; gebruik ``snapshot``.
	snapshot: SensorSnapshot = field(default_factory=SensorSnapshot)
	_accel_window: Deque[float] = field(default_factory=deque)
	_alt_window: Deque[Tuple[float, float]] = field(default_factory=deque)
	_freefall_started_monotonic: Optional[float] = None
	_alt_stable_started_monotonic: Optional[float] = None

	def tick(
		self,
		*,
		ground_hpa: Optional[float] = None,
		now_monotonic: Optional[float] = None,
		now_wall: Optional[float] = None,
	) -> SensorSnapshot:
		"""Lees beide sensoren één keer; werk snapshot + rolling stats bij.

		Parameters
		----------
		ground_hpa
			Indien gegeven én de BME-read slaagt, vult ``snapshot.alt_m``
			(via barometrische formule). Anders blijft ``alt_m`` ``None``.
		now_monotonic, now_wall
			Time-injection voor tests; default = ``time.monotonic()`` /
			``time.time()``.
		"""

		if now_monotonic is None:
			now_monotonic = time_mod.monotonic()
		if now_wall is None:
			now_wall = time_mod.time()

		snap = self.snapshot
		snap.monotonic = float(now_monotonic)
		snap.wall_time = float(now_wall)
		snap.samples_taken += 1

		# --- BME280 -----------------------------------------------------
		if self.bme280 is not None:
			try:
				t_c, p_hpa, rh = self.bme280.read()
				snap.pressure_hpa = float(p_hpa)
				snap.temp_c = float(t_c)
				snap.humidity_pct = float(rh)
				if ground_hpa is not None:
					snap.alt_m = _pressure_to_altitude_m(
						float(p_hpa), float(ground_hpa)
					)
				else:
					snap.alt_m = None
			except Exception:  # noqa: BLE001 — sensor I/O is ruw
				snap.bme_failures += 1
				# We laten de oude waardes staan zodat de TLM-loop niet
				# plots gaten ziet bij één hikkende read.

		# --- BNO055 -----------------------------------------------------
		if self.bno055 is not None:
			# Euler en lineaire accel zijn aparte I²C-blokken; afzonderlijk
			# wrappen zodat één hikkende read niet de andere meesleurt.
			try:
				h, r, p = self.bno055.read_euler()
				snap.heading_deg = float(h)
				snap.roll_deg = float(r)
				snap.pitch_deg = float(p)
			except Exception:  # noqa: BLE001
				snap.bno_failures += 1

			try:
				ax_ms2, ay_ms2, az_ms2 = self.bno055.read_linear_acceleration()
				snap.ax_g = float(ax_ms2) / _G_MS2
				snap.ay_g = float(ay_ms2) / _G_MS2
				snap.az_g = float(az_ms2) / _G_MS2
				snap.accel_mag_g = _safe_norm(snap.ax_g, snap.ay_g, snap.az_g)
			except Exception:  # noqa: BLE001
				snap.bno_failures += 1

			try:
				cs = self.bno055.calibration_status()
				snap.sys_cal = int(cs[0])
				snap.gyro_cal = int(cs[1])
				snap.accel_cal = int(cs[2])
				snap.mag_cal = int(cs[3])
			except Exception:  # noqa: BLE001
				snap.bno_failures += 1

		# --- Rolling window: ‖a‖ peak + σ ------------------------------
		if snap.accel_mag_g is not None:
			self._accel_window.append(float(snap.accel_mag_g))
			while len(self._accel_window) > int(self.window_samples):
				self._accel_window.popleft()
			if self._accel_window:
				snap.peak_accel_g = max(self._accel_window)
				if len(self._accel_window) >= 2:
					mean = sum(self._accel_window) / len(self._accel_window)
					var = sum(
						(v - mean) * (v - mean) for v in self._accel_window
					) / len(self._accel_window)
					snap.accel_stddev_g = math.sqrt(var)
				else:
					snap.accel_stddev_g = 0.0

			# --- Freefall counter (consec. tijd onder drempel) ---------
			if float(snap.accel_mag_g) < float(self.freefall_thresh_g):
				if self._freefall_started_monotonic is None:
					self._freefall_started_monotonic = float(now_monotonic)
				snap.freefall_for_s = float(now_monotonic) - float(
					self._freefall_started_monotonic
				)
			else:
				self._freefall_started_monotonic = None
				snap.freefall_for_s = 0.0

		# --- Alt-stability counter -------------------------------------
		# We meten max-spread |alt - alt'| over alle samples binnen het
		# venster; zolang die onder eps blijft, telt de duur door. Anders
		# resetten we de start. Dit is robuuster dan kijken naar enkel
		# Δalt tussen opeenvolgende samples (1 ruis-spike zou dan reseten).
		if snap.alt_m is not None:
			self._alt_window.append((float(now_monotonic), float(snap.alt_m)))
			while len(self._alt_window) > int(self.window_samples):
				self._alt_window.popleft()
			alts = [a for _, a in self._alt_window]
			spread = max(alts) - min(alts) if alts else 0.0
			if spread <= float(self.alt_stable_eps_m):
				if self._alt_stable_started_monotonic is None:
					self._alt_stable_started_monotonic = float(now_monotonic)
				snap.alt_stable_for_s = float(now_monotonic) - float(
					self._alt_stable_started_monotonic
				)
			else:
				self._alt_stable_started_monotonic = float(now_monotonic)
				snap.alt_stable_for_s = 0.0

		return snap

	def reset_windows(self) -> None:
		"""Wis rolling-window state (handig na een state-transitie of mode-wissel).

		De ``snapshot``-velden zelf blijven staan — alleen de geschiedenis-
		gebaseerde signalen (peak, σ, freefall_for_s, alt_stable_for_s) worden
		opnieuw begonnen, zodat een nieuwe trigger-evaluatie niet door oude
		samples uit een vorige fase wordt vertroebeld.
		"""

		self._accel_window.clear()
		self._alt_window.clear()
		self._freefall_started_monotonic = None
		self._alt_stable_started_monotonic = None
		self.snapshot.peak_accel_g = None
		self.snapshot.accel_stddev_g = None
		self.snapshot.freefall_for_s = 0.0
		self.snapshot.alt_stable_for_s = 0.0


__all__ = [
	"SensorSnapshot",
	"SensorSampler",
	"DEFAULT_WINDOW_SAMPLES",
	"DEFAULT_FREEFALL_THRESH_G",
	"DEFAULT_ALT_STABLE_EPS_M",
]
