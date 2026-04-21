"""Gimbal-stabilisatie-lus — puur control-object (geen I/O, geen threading).

Fase 9 wil de gimbal tijdens ``DEPLOYED`` actief horizontaal houden met de
BNO055 als referentie. De echte closed-loop-logica staat historisch in
``scripts/gimbal_level.py`` (ontwikkeld op de bank, met pigpio rechtstreeks).
Voor gebruik in :mod:`scripts.cansat_radio_protocol` moeten we die logica
kunnen aanroepen **zonder** extra threads, pigpio-calls of BNO055-reads in
te bouwen — de main loop bezit die al. Deze module levert daarom enkel de
**pure regelaar**:

* caller geeft een zwaartekrachtvector ``g = (gx, gy, gz)`` (in m/s², sensor-
  frame, via :meth:`cansat_hw.sensors.bno055.BNO055.read_gravity`),
* de loop filtert, detecteert spikes, past P+I toe, clampt op ``ServoCal``
  en rate-limit, en geeft de **doel-PWM** ``(us1, us2)`` terug,
* de caller roept daarna zelf :meth:`ServoController.set_pulse` aan.

Voordeel van deze splitsing:

* de main loop blijft 100 % coöperatief (geen locking met RX/TX),
* de regelaar is triviaal test-baar zonder pigpio of I²C,
* de Zero kan de gimbal "wegschakelen" met één flag zonder de state-machine
  of radio te raken — handig tijdens dry-runs en voor batterij-sparen in
  ``PAD_IDLE/ASCENT/LANDED``.

Conventies (afgestemd op :mod:`scripts.gimbal_level` zodat tuning-waardes
compatibel zijn):

* Regeldoel is ``gx → 0`` en ``gy → 0`` (**zero-target**). Montage-conventie
  moet dus zijn dat "waterpas" ≈ ``gx=gy=0``. Zo niet: monteer opnieuw of
  herkalibreer de servo-axes.
* ``kx`` werkt op de **X-fout** (gx) en wordt naar servo ``axis_x_servo``
  gerouteerd; idem ``ky`` → ``axis_y_servo``. Per-as ``invert_x``/``invert_y``
  flippen het teken van de correctie. De helper
  :func:`gimbal_axis_mapping` leidt deze 4 velden af uit ``ServoCal.axis``
  + ``ServoCal.invert`` zodat de cal de "ground truth" blijft. De legacy
  ``swap_control_axes`` flag (CLI-flag uit pre-mapping tijdperk) wisselt
  alleen ``axis_x_servo``/``axis_y_servo``; nieuwe code zet de mapping-
  velden expliciet.
* ``α`` (LPF-factor) filtert de raw-zwaartekracht vóór de regelaar:
  ``fg = α·fg + (1−α)·g``. Default 0.90 komt uit de slinger-validatie
  (april 2026); te lage α gaf zichtbaar "dansen" tijdens vasthouden.
* Alle veiligheidsclamps (``g_min``/``g_max`` norm, ``loop_max_dg``
  spike-detectie, ``ServoCal.clamp``, ``max_us_step`` rate-limit) blijven
  identiek aan de originele implementatie; dit is een port, geen redesign.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Tuple

from .controller import ServoCal

__all__ = [
	"AxisMapping",
	"GimbalLoop",
	"GimbalStatus",
	"gimbal_axis_mapping",
]


@dataclass(frozen=True)
class AxisMapping:
	"""Hoe servo1/2 op de control-assen X/Y mappen.

	Output van :func:`gimbal_axis_mapping`. Velden:

	* ``axis_x_servo`` / ``axis_y_servo``: index (1 of 2) van de servo die
	  respectievelijk de X- en Y-as compenseert. Altijd verschillend.
	* ``invert_x`` / ``invert_y``: of de **correctie** voor die as in teken
	  omgekeerd moet worden (bv. omdat de servo mechanisch gespiegeld is
	  tov het sensor-frame). Standaard False — dan geldt het zelfde teken
	  als ``-kx*ex`` en ``-ky*ey`` (negatief feedback in beide assen).
	* ``derived_from_default``: True als we naar de hardcoded defaults
	  (``servo1=X, servo2=Y, geen invert``) terug zijn gevallen omdat de
	  cal geen geldige ``axis``-velden bevatte. Handig voor logging.
	"""

	axis_x_servo: int
	axis_y_servo: int
	invert_x: bool
	invert_y: bool
	derived_from_default: bool


def gimbal_axis_mapping(cal1: ServoCal, cal2: ServoCal) -> AxisMapping:
	"""Bouw een :class:`AxisMapping` uit twee :class:`ServoCal`-objecten.

	Beide servo's moeten een verschillende ``axis`` hebben (één "x", één "y");
	zo niet (beide hetzelfde, beide ``None``, of een typo) vallen we terug op
	de historische default ``servo1=X, servo2=Y, invert=False``.

	Deze fallback is bewust silent — de caller (radio-service of CLI) hoort
	een waarschuwing te tonen als hij dat wil. We willen niet dat een ontbrekend
	veld de hele service bricked; oude calibraties moeten blijven werken.
	"""
	a1 = (cal1.axis or "").strip().lower() if cal1.axis else ""
	a2 = (cal2.axis or "").strip().lower() if cal2.axis else ""
	if a1 in ("x", "y") and a2 in ("x", "y") and a1 != a2:
		if a1 == "x":
			return AxisMapping(
				axis_x_servo=1,
				axis_y_servo=2,
				invert_x=bool(cal1.invert),
				invert_y=bool(cal2.invert),
				derived_from_default=False,
			)
		return AxisMapping(
			axis_x_servo=2,
			axis_y_servo=1,
			invert_x=bool(cal2.invert),
			invert_y=bool(cal1.invert),
			derived_from_default=False,
		)
	return AxisMapping(
		axis_x_servo=1,
		axis_y_servo=2,
		invert_x=False,
		invert_y=False,
		derived_from_default=True,
	)


@dataclass
class GimbalStatus:
	"""Snapshot van de :class:`GimbalLoop` voor telemetrie en ``GET GIMBAL``."""

	enabled: bool = False
	primed: bool = False
	ticks: int = 0
	good_samples: int = 0
	rejected_samples: int = 0
	last_gx: Optional[float] = None
	last_gy: Optional[float] = None
	last_gz: Optional[float] = None
	last_err_x: Optional[float] = None
	last_err_y: Optional[float] = None
	last_us1: Optional[int] = None
	last_us2: Optional[int] = None


@dataclass
class GimbalLoop:
	"""Pure stateful PI-regelaar voor een 2-DOF gimbal.

	De caller instantieert deze klasse eenmalig bij boot met de per-hardware
	calibratie (``ServoCal`` voor beide servo's, typisch uit
	``config/gimbal/servo_calibration.json``) en tikt hem daarna aan in de
	main loop. :meth:`tick` is idempotent-tolerant: als ``enabled`` False is
	of als het sample gerejecteerd wordt, returnt hij ``None`` en zijn er
	geen PWM-commando's nodig.
	"""

	cal1: ServoCal
	cal2: ServoCal

	# Tuning — defaults uit de gevalideerde slinger-test in april 2026 op
	# RPITSM0 (zie docs/servo_tuning.md). Bij hogere kx/ky (=200) ging de
	# gimbal oscilleren bij rust; lagere kix/kiy (=5) lieten een rest-bias
	# staan; alpha 0.85 + deadband 0.10 gaf zichtbaar "dansen" tijdens
	# vasthouden. Deze waarden settle in ~2 s na een grote verstoring.
	kx: float = 100.0
	ky: float = 100.0
	kix: float = 15.0
	kiy: float = 15.0
	alpha: float = 0.90
	deadband_x: float = 0.18
	deadband_y: float = 0.18
	integral_max: float = 4.0
	# max_us_step is per **tick**. De main loop in MISSION/TEST tikt ~5 Hz
	# (rx_timeout 0.2 s), dus 20 µs/tick ≈ 100 µs/s — trager dan de 50 Hz-
	# variant in ``gimbal_level.py`` (300 µs/s @ 6 µs/tick) maar voldoende
	# voor normale correcties en veilig bij sensorruis. Caller kan verhogen
	# via tuning.
	max_us_step: int = 20

	# Axis-mapping. Welke servo (1 of 2) compenseert resp. de X- en Y-fout,
	# en moet het teken van die correctie omgekeerd worden? Defaults =
	# historisch gedrag (``servo1=X, servo2=Y, geen invert``). Caller die
	# dit uit ``ServoCal.axis`` wil afleiden gebruikt
	# :func:`gimbal_axis_mapping` en zet de velden expliciet.
	axis_x_servo: int = 1
	axis_y_servo: int = 2
	invert_x: bool = False
	invert_y: bool = False

	# DEPRECATED — bestond als CLI-flag voor we de mapping in de cal hadden.
	# Wanneer True interpreteren we dat als "ruil ``axis_x_servo`` en
	# ``axis_y_servo`` om" zodat oude scripts/services blijven werken zonder
	# de cal te moeten herschrijven. Nieuwe code: zet de axis-velden expliciet.
	swap_control_axes: bool = False

	# Veiligheidsgrenzen op de raw-zwaartekracht (m/s²).
	g_min: float = 7.0
	g_max: float = 12.5
	# Max |Δg| per sample in de regellus. Hoger ⇒ meer jaag-gedrag op
	# sensor-spikes; 2.5 m/s² is bewust streng (zoals gimbal_level.py).
	loop_max_dg: float = 2.5

	# Fallback-dt als de caller geen monotonic timestamp meegeeft.
	# 0.2 s = 5 Hz; past bij de normale rx_timeout in MISSION/TEST.
	dt_fallback: float = 0.2

	# Runtime-state (niet door caller zetten).
	_enabled: bool = field(default=False, init=False)
	_primed: bool = field(default=False, init=False)
	_fgx: Optional[float] = field(default=None, init=False)
	_fgy: Optional[float] = field(default=None, init=False)
	_fgz: Optional[float] = field(default=None, init=False)
	_int_ex: float = field(default=0.0, init=False)
	_int_ey: float = field(default=0.0, init=False)
	_cur_us1: Optional[int] = field(default=None, init=False)
	_cur_us2: Optional[int] = field(default=None, init=False)
	_last_raw_gx: Optional[float] = field(default=None, init=False)
	_last_raw_gy: Optional[float] = field(default=None, init=False)
	_last_raw_gz: Optional[float] = field(default=None, init=False)
	_last_err_x: Optional[float] = field(default=None, init=False)
	_last_err_y: Optional[float] = field(default=None, init=False)
	_last_monotonic: Optional[float] = field(default=None, init=False)
	_ticks: int = field(default=0, init=False)
	_good_samples: int = field(default=0, init=False)
	_rejected_samples: int = field(default=0, init=False)

	def __post_init__(self) -> None:
		# Valideer de axis-mapping zodat we nooit per ongeluk twee correcties
		# op dezelfde servo stapelen (= ongedefinieerd gedrag, motor zou de
		# som van X- en Y-correctie krijgen). Sluit ook 0/3-typo's vroeg af.
		if self.axis_x_servo not in (1, 2) or self.axis_y_servo not in (1, 2):
			raise ValueError(
				"axis_x_servo/axis_y_servo moeten 1 of 2 zijn, niet "
				f"{self.axis_x_servo}/{self.axis_y_servo}"
			)
		if self.axis_x_servo == self.axis_y_servo:
			raise ValueError(
				"axis_x_servo en axis_y_servo mogen niet gelijk zijn "
				f"(beide {self.axis_x_servo})"
			)

	@property
	def enabled(self) -> bool:
		return self._enabled

	@property
	def primed(self) -> bool:
		"""True zodra een eerste geldig sample de LPF heeft gezaaid."""
		return self._primed

	def enable(self, *, reset_integrators: bool = True) -> None:
		"""Zet de regelaar aan.

		``reset_integrators=True`` (default) wist ``∫e·dt`` en de LPF-state;
		zo vertrekt een nieuwe enable-sessie schoon vanaf de volgende tick.
		Voor kortstondige pauzes (bv. tijdelijk dode zone) zou de caller
		``reset_integrators=False`` kunnen kiezen — voorlopig gebruiken we
		dat nergens.
		"""
		self._enabled = True
		if reset_integrators:
			self._primed = False
			self._fgx = self._fgy = self._fgz = None
			self._int_ex = 0.0
			self._int_ey = 0.0
			self._last_raw_gx = None
			self._last_raw_gy = None
			self._last_raw_gz = None
			self._last_err_x = None
			self._last_err_y = None
			self._last_monotonic = None
			# ``_cur_us{1,2}`` laten we bewust staan: bij re-enable kunnen
			# we zo vanaf de huidige bekende positie rate-limiten i.p.v.
			# abrupt naar center_us te springen.

	def disable(self) -> None:
		"""Zet de regelaar uit; laat de interne state staan voor analyse."""
		self._enabled = False

	def home_pulses(self) -> Optional[Tuple[int, int]]:
		"""Return ``(center_us1, center_us2)`` als beide servo's ``center_us`` hebben.

		Handig voor de "HOME"-actie: caller schrijft deze PWM-waardes naar
		de servo's (via ``ServoController.home_all()``) en laat de loop
		vanaf die positie oppikken wanneer hij weer enabled wordt.
		"""
		c1 = self.cal1.center_us
		c2 = self.cal2.center_us
		if c1 is None or c2 is None:
			return None
		self._cur_us1 = int(c1)
		self._cur_us2 = int(c2)
		return (int(c1), int(c2))

	def tick(
		self,
		g: Optional[Tuple[float, float, float]],
		*,
		now_monotonic: Optional[float] = None,
	) -> Optional[Tuple[int, int]]:
		"""Eén regellus-stap.

		Parameters
		----------
		g
			Ruwe zwaartekrachtvector in m/s² (sensor-frame). ``None`` of
			niet-eindig: sample wordt gerejecteerd en ``None`` teruggegeven
			zonder de interne state te raken. Dit wordt geteld als
			``rejected_samples``.
		now_monotonic
			Optionele monotonic-tijdstempel (sec). Gebruikt om de
			integratie-stap ``dt`` exact te houden; valt terug op
			``dt_fallback`` als niet gegeven of als eerste sample.

		Returns
		-------
		tuple or None
			``(us1, us2)`` met de **doel-PWM** voor servo1/servo2 na clamp
			en rate-limit, of ``None`` als de loop uit staat / het sample
			verworpen werd / er nog geen ``center_us`` calibratie is.
		"""
		if not self._enabled:
			return None
		c1 = self.cal1.center_us
		c2 = self.cal2.center_us
		if c1 is None or c2 is None:
			# Zonder calibratie mogen we geen PWM schrijven — zou de servo
			# in een random pose dwingen. Stil ``None``; de caller kan dat
			# via ``GimbalStatus.primed=False`` diagnosticeren.
			self._rejected_samples += 1
			return None

		if g is None:
			self._rejected_samples += 1
			return None
		gx, gy, gz = g
		if not (math.isfinite(gx) and math.isfinite(gy) and math.isfinite(gz)):
			self._rejected_samples += 1
			return None

		# Norm-check: weiger samples buiten het plausibele bereik (sensor-
		# uitvallers, saturatie, kabel-glitch). We kiezen bewust 7–12.5
		# m/s² conform ``gimbal_level.py`` — ruim genoeg voor kantelingen
		# maar streng genoeg om een 0 g of 30 g spike te weren.
		gn = math.sqrt(gx * gx + gy * gy + gz * gz)
		if gn < self.g_min or gn > self.g_max:
			self._rejected_samples += 1
			return None

		# Spike-detectie t.o.v. vorig raw sample. Eerste sample: niets te
		# vergelijken, accepteer altijd om de LPF te zaaien.
		if (
			self._last_raw_gx is not None
			and self._last_raw_gy is not None
			and self._last_raw_gz is not None
		):
			d = max(
				abs(gx - self._last_raw_gx),
				abs(gy - self._last_raw_gy),
				abs(gz - self._last_raw_gz),
			)
			if d > self.loop_max_dg:
				# Bewaar het nieuwe sample als referentie (zodat een echte
				# stap na 1 rejected tick alsnog wordt gevolgd) maar laat
				# de LPF-state ongemoeid. Analoog aan gimbal_level.py.
				self._last_raw_gx = gx
				self._last_raw_gy = gy
				self._last_raw_gz = gz
				self._rejected_samples += 1
				return None

		# Accepteer sample.
		self._last_raw_gx = gx
		self._last_raw_gy = gy
		self._last_raw_gz = gz
		self._good_samples += 1
		self._ticks += 1

		# LPF.
		a = float(self.alpha)
		if self._fgx is None:
			self._fgx, self._fgy, self._fgz = gx, gy, gz
			self._primed = True
		else:
			self._fgx = a * self._fgx + (1.0 - a) * gx
			self._fgy = a * self._fgy + (1.0 - a) * gy
			self._fgz = a * self._fgz + (1.0 - a) * gz

		# Dt-bepaling — integreer alleen als we een vorige tick-tijd hebben;
		# zo blijft de eerste tick ∫e·dt ≈ 0.
		if now_monotonic is None or self._last_monotonic is None:
			dt = float(self.dt_fallback)
		else:
			dt = max(0.0, float(now_monotonic) - float(self._last_monotonic))
			if dt <= 0.0 or dt > 5.0:
				# 5 s pauze (bv. uit CONFIG teruggekomen) ⇒ fallback i.p.v.
				# een enorme geïntegreerde fout.
				dt = float(self.dt_fallback)
		if now_monotonic is not None:
			self._last_monotonic = float(now_monotonic)

		# Fout (zero-target).
		ex_raw = float(self._fgx)
		ey_raw = float(self._fgy)
		self._last_err_x = ex_raw
		self._last_err_y = ey_raw

		# Deadband op de P-term; I-term integreert altijd over de raw fout
		# zodat een klein biasje toch wegregelt (zelfde gedrag als
		# gimbal_level.py).
		ex = 0.0 if abs(ex_raw) < self.deadband_x else ex_raw
		ey = 0.0 if abs(ey_raw) < self.deadband_y else ey_raw

		if self.kix != 0.0:
			self._int_ex = max(
				-self.integral_max,
				min(self.integral_max, self._int_ex + ex_raw * dt),
			)
		if self.kiy != 0.0:
			self._int_ey = max(
				-self.integral_max,
				min(self.integral_max, self._int_ey + ey_raw * dt),
			)

		# Bouw de twee assen-correcties (in µs t.o.v. center). Daarna routeren
		# we ze naar servo1/servo2 volgens de mapping. Splitsing maakt
		# ``swap_control_axes`` triviaal (= ruil welke servo welke as krijgt)
		# en vermijdt dubbele if-branches voor swap × invert × axis.
		corr_x = (-self.kx) * ex + (-self.kix) * self._int_ex
		corr_y = (-self.ky) * ey + (-self.kiy) * self._int_ey
		if self.invert_x:
			corr_x = -corr_x
		if self.invert_y:
			corr_y = -corr_y

		# Effectieve mapping: legacy swap-flag wint, anders de expliciete
		# axis-velden. Bij swap zwaaien we ALLEEN de servo-toewijzing om;
		# de invert-flags blijven aan hun "as" hangen (een fysieke remontage
		# kan zowel de as als het teken flippen — twee onafhankelijke knoppen).
		if self.swap_control_axes:
			ax = self.axis_y_servo
			ay = self.axis_x_servo
		else:
			ax = self.axis_x_servo
			ay = self.axis_y_servo

		# Combineer per-servo. axis_x_servo != axis_y_servo (gegarandeerd door
		# __post_init__), dus precies één van beide additions raakt elke servo.
		target1_f = float(c1)
		target2_f = float(c2)
		if ax == 1:
			target1_f += corr_x
		else:
			target2_f += corr_x
		if ay == 1:
			target1_f += corr_y
		else:
			target2_f += corr_y

		target_us1 = self.cal1.clamp(int(round(target1_f)))
		target_us2 = self.cal2.clamp(int(round(target2_f)))

		# Rate-limit. Als we nog geen "huidige" PWM kennen (eerste tick, of
		# caller heeft nooit center gesteld), neem center als vertrekpunt
		# zodat delta1/delta2 begrensd blijven vanaf een bekende pose.
		cur1 = self._cur_us1 if self._cur_us1 is not None else int(c1)
		cur2 = self._cur_us2 if self._cur_us2 is not None else int(c2)
		step = int(self.max_us_step)
		if step > 0:
			d1 = max(-step, min(step, target_us1 - cur1))
			d2 = max(-step, min(step, target_us2 - cur2))
			cur1 += d1
			cur2 += d2
		else:
			cur1 = int(target_us1)
			cur2 = int(target_us2)

		self._cur_us1 = int(cur1)
		self._cur_us2 = int(cur2)
		return (self._cur_us1, self._cur_us2)

	def status(self) -> GimbalStatus:
		"""Leesbare snapshot voor ``GET GIMBAL`` en logging."""
		return GimbalStatus(
			enabled=self._enabled,
			primed=self._primed,
			ticks=self._ticks,
			good_samples=self._good_samples,
			rejected_samples=self._rejected_samples,
			last_gx=self._fgx,
			last_gy=self._fgy,
			last_gz=self._fgz,
			last_err_x=self._last_err_x,
			last_err_y=self._last_err_y,
			last_us1=self._cur_us1,
			last_us2=self._cur_us2,
		)
