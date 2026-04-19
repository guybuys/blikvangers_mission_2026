"""Servo controller — rail, pulse, stow/park, tuning sub-state, JSON I/O.

Ontworpen om vanuit twee plekken bestuurd te worden:

1. **Autonoom** door :mod:`cansat_radio_protocol` via :mod:`state_policy`
   (rail aan/uit + park bij flight-state-overgangen).
2. **Manueel via radio** door :func:`cansat_hw.radio.wire_protocol.handle_wire_line`
   met de ``SERVO …``-commando-familie (alleen toegestaan in CONFIG-mode).

Hardware-onafhankelijk: de :class:`ServoController` werkt tegen een ``driver``
die alleen ``set_pulse(gpio, us)`` en ``set_rail(powered)`` hoeft te
implementeren. Op de Zero gebruikt :func:`make_pigpio_driver` ``pigpio``;
unit-tests gebruiken :class:`FakeRailDriver` zodat we zonder hardware kunnen
asserten dat de juiste sequenties opgeroepen worden.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover — alleen voor typings
	import pigpio  # noqa: F401


# Veiligheidsgrenzen die we **altijd** afdwingen, zelfs als de calibratie-JSON
# ze ruimer wil (corrupte file, overschrijving). Bron: pigpio rejects pulse
# widths buiten 500–2500 µs.
_HW_MIN_US = 500
_HW_MAX_US = 2500

# Vertraging bij PARK: rail aan → stow-pulse → ``PARK_WAIT_S`` wachten →
# rail uit. 800 ms is ruim voor een hobbyservo om vanuit een willekeurige
# positie naar stowed te draaien (typische slew rate ~300°/s, range ~180°).
PARK_WAIT_S = 0.8

# Watchdog: hoe lang mag een tuning-sessie inactief blijven (geen STEP/SET/MARK
# /SEL/STATUS) voor we automatisch stoppen? Voorkomt dat een vergeten ``SERVO
# START`` de rail urenlang aan houdt (LiPo leeg). 5 min is realistisch voor
# handmatig hardware-tuning (operator stapt even weg, hangt mech. iets bij,
# overlegt, …) en valt nog ruim binnen "veilig" voor de servo-rail.
TUNING_WATCHDOG_S = 300.0

# Kleine veilige stappen (small/big) — matcht ``scripts/gimbal/servo_calibration.py``.
DEFAULT_STEP_US = 10
DEFAULT_BIG_STEP_US = 50


# --- Calibratie-data ---------------------------------------------------------


@dataclass
class ServoCal:
	"""Calibratie van één servo (alle waardes in microseconden, BCM-gpio)."""

	gpio: int
	min_us: Optional[int] = None
	center_us: Optional[int] = None
	max_us: Optional[int] = None
	stow_us: Optional[int] = None  # Fase 12: bekende veilige park-positie.

	def is_complete(self) -> bool:
		"""``True`` als alle limieten + center én stow gezet zijn."""
		return all(
			isinstance(v, int)
			for v in (self.min_us, self.center_us, self.max_us, self.stow_us)
		)

	def clamp(self, us: int) -> int:
		"""Clamp ``us`` tussen min/max indien gezet, anders alleen hardware-grenzen."""
		lo = self.min_us if self.min_us is not None else _HW_MIN_US
		hi = self.max_us if self.max_us is not None else _HW_MAX_US
		return max(lo, min(hi, int(us)))


def _load_cal_dict(path: Path) -> Dict[int, ServoCal]:
	"""Lees ``servo_calibration.json``. Geeft ``{}`` als file ontbreekt/corrupt."""
	out: Dict[int, ServoCal] = {}
	try:
		raw = json.loads(path.read_text(encoding="utf-8"))
	except (OSError, ValueError):
		return out
	if not isinstance(raw, dict):
		return out
	for idx, key in [(1, "servo1"), (2, "servo2")]:
		val = raw.get(key)
		if not isinstance(val, dict):
			continue
		try:
			gpio = int(val["gpio"])
		except (KeyError, TypeError, ValueError):
			continue
		cal = ServoCal(gpio=gpio)
		for field_name in ("min_us", "center_us", "max_us", "stow_us"):
			v = val.get(field_name)
			if isinstance(v, int):
				setattr(cal, field_name, v)
		out[idx] = cal
	return out


def _save_cal_dict(path: Path, cal: Dict[int, ServoCal]) -> None:
	"""Atomic write van de calibratie-JSON (tmp → replace)."""
	data: Dict[str, Any] = {}
	for idx, key in [(1, "servo1"), (2, "servo2")]:
		c = cal.get(idx)
		if c is None:
			continue
		data[key] = {
			"gpio": int(c.gpio),
			"min_us": c.min_us,
			"center_us": c.center_us,
			"max_us": c.max_us,
			"stow_us": c.stow_us,
		}
	data["saved_at"] = int(time.time())
	path.parent.mkdir(parents=True, exist_ok=True)
	tmp = path.with_suffix(path.suffix + ".tmp")
	tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
	tmp.replace(path)


# --- Drivers -----------------------------------------------------------------


class FakeRailDriver:
	"""Spy-driver voor unit-tests: registreert alle calls in volgorde."""

	def __init__(self) -> None:
		self.calls: List[Tuple[str, Any, ...]] = []
		self.rail_on: bool = False
		self.pulses: Dict[int, int] = {}

	def set_pulse(self, gpio: int, us: int) -> None:
		self.calls.append(("pulse", int(gpio), int(us)))
		self.pulses[int(gpio)] = int(us)

	def set_rail(self, powered: bool) -> None:
		self.calls.append(("rail", bool(powered)))
		self.rail_on = bool(powered)

	def close(self) -> None:
		self.calls.append(("close",))


class _PigpioDriver:
	"""Production driver bovenop ``pigpio``. Alleen aanmaken via :func:`make_pigpio_driver`."""

	def __init__(self, pi: Any, enable_pin: int, *, active_low: bool = False) -> None:
		import pigpio as _pg

		self._pi = pi
		self._pin = int(enable_pin)
		self._al = bool(active_low)
		# Configureer enable-pin als output, rail uit. Idempotent.
		if self._pin > 0:
			pi.set_mode(self._pin, _pg.OUTPUT)
			self.set_rail(False)

	def set_pulse(self, gpio: int, us: int) -> None:
		# pigpio: 0 = no pulses (servo "off"); 500..2500 = effective pulse.
		# We mappen 0 expliciet door zodat ``stop_tuning`` / shutdown een
		# silent state geeft.
		self._pi.set_servo_pulsewidth(int(gpio), int(us))

	def set_rail(self, powered: bool) -> None:
		if self._pin <= 0:
			return
		level = (not powered) if self._al else powered
		self._pi.write(self._pin, 1 if level else 0)

	def close(self) -> None:
		# We bezitten ``pi`` niet — laat de aanroeper ``pi.stop()`` doen.
		pass


def make_pigpio_driver(pi: Any, enable_pin: int, *, active_low: bool = False) -> Any:
	"""Bouw een rail-driver bovenop een al verbonden ``pigpio.pi`` instance."""
	return _PigpioDriver(pi, enable_pin, active_low=active_low)


# --- Controller --------------------------------------------------------------


@dataclass
class ServoStatus:
	"""Snapshot voor ``SERVO STATUS``-replies en logging."""

	rail_on: bool
	tuning_active: bool
	selected: Optional[int]
	current_us: Dict[int, int] = field(default_factory=dict)
	cal_complete: bool = False


class ServoController:
	"""Beheert servos + rail; thread-onveilig (één caller per keer)."""

	def __init__(
		self,
		driver: Any,
		cal_path: Path,
		*,
		park_wait_s: float = PARK_WAIT_S,
		tuning_watchdog_s: float = TUNING_WATCHDOG_S,
		sleep_func: Optional[Any] = None,
		monotonic_func: Optional[Any] = None,
	) -> None:
		self._driver = driver
		self._cal_path = Path(cal_path)
		self._park_wait_s = float(park_wait_s)
		self._watchdog_s = float(tuning_watchdog_s)
		self._sleep = sleep_func or time.sleep
		self._monotonic = monotonic_func or time.monotonic

		self._cal: Dict[int, ServoCal] = _load_cal_dict(self._cal_path)
		# Defaults als de JSON ontbreekt — match met ``servo_calibration.py``.
		if 1 not in self._cal:
			self._cal[1] = ServoCal(gpio=13)
		if 2 not in self._cal:
			self._cal[2] = ServoCal(gpio=12)

		self._rail_on: bool = False
		self._tuning_active: bool = False
		self._selected: Optional[int] = None
		self._current_us: Dict[int, int] = {1: 0, 2: 0}
		self._tuning_started_at: Optional[float] = None
		self._tuning_last_action_at: Optional[float] = None

	# --- introspectie --------------------------------------------------------

	@property
	def cal_path(self) -> Path:
		return self._cal_path

	@property
	def rail_on(self) -> bool:
		return self._rail_on

	@property
	def tuning_active(self) -> bool:
		return self._tuning_active

	def calibration_complete(self) -> bool:
		"""``True`` als beide servo's volledig gecalibreerd zijn (incl. stow)."""
		return all(self._cal[i].is_complete() for i in (1, 2))

	def status(self) -> ServoStatus:
		return ServoStatus(
			rail_on=self._rail_on,
			tuning_active=self._tuning_active,
			selected=self._selected,
			current_us=dict(self._current_us),
			cal_complete=self.calibration_complete(),
		)

	def calibration_for(self, idx: int) -> Optional[ServoCal]:
		return self._cal.get(int(idx))

	# --- rail ----------------------------------------------------------------

	def enable_rail(self) -> None:
		"""Schakel servo-voeding aan. Idempotent."""
		if self._rail_on:
			return
		self._driver.set_rail(True)
		self._rail_on = True

	def disable_rail(self) -> None:
		"""Schakel servo-voeding uit; pulses worden eerst op 0 gezet."""
		# Eerst pulses neutraliseren zodat er geen kortsluit-stroompiek volgt.
		for idx in (1, 2):
			gpio = self._cal[idx].gpio
			self._driver.set_pulse(gpio, 0)
		self._driver.set_rail(False)
		self._rail_on = False

	# --- pulses --------------------------------------------------------------

	def _write_pulse(self, idx: int, us: int) -> int:
		"""Schrijf een pulse.

		Tijdens een **tuning-sessie** passen we alleen de hardware-grenzen
		(500..2500 µs) toe — de bestaande ``cal.min_us``/``cal.max_us`` mogen
		het zoeken naar nieuwe grenzen niet beperken (anders zit je vast aan
		de vorige calibratie en kun je de range nooit verruimen).

		Buiten tuning (autonome stow/park, mission-runtime SET) gebruiken we
		``cal.clamp(...)`` zodat een corrupte JSON of een mis-getypeerd pulse
		nooit de servo door zijn mechanische einde duwt.
		"""
		cal = self._cal[idx]
		if self._tuning_active:
			clamped = max(_HW_MIN_US, min(_HW_MAX_US, int(us)))
		else:
			clamped = cal.clamp(int(us))
			clamped = max(_HW_MIN_US, min(_HW_MAX_US, clamped))
		self._driver.set_pulse(cal.gpio, clamped)
		self._current_us[idx] = clamped
		return clamped

	def stow_servo(self, idx: int) -> Optional[int]:
		"""Schrijf de stow-positie naar één servo. Vereist gekalibreerde stow_us."""
		cal = self._cal[idx]
		if cal.stow_us is None:
			return None
		return self._write_pulse(idx, int(cal.stow_us))

	def stow_all(self) -> Tuple[Optional[int], Optional[int]]:
		"""Stow beide servo's; geeft ``(us1, us2)``, ``None`` als niet gecalibreerd."""
		return (self.stow_servo(1), self.stow_servo(2))

	def park_all(self) -> bool:
		"""Volle park-sequence: ENABLE → STOW BOTH → wacht → DISABLE.

		Retourneert ``False`` als (een van) de stow-positie(s) ontbreekt; rail
		gaat dan toch uit (veilig). ``True`` bij volledig succes.
		"""
		had_stow = self._cal[1].stow_us is not None and self._cal[2].stow_us is not None
		self.enable_rail()
		us1, us2 = self.stow_all()
		# Geef de servo's tijd om te bewegen, ook als één van beide geen
		# stow had: we wachten consistent zodat de operator weet hoe lang.
		self._sleep(self._park_wait_s)
		self.disable_rail()
		return had_stow and us1 is not None and us2 is not None

	def home_all(self) -> Tuple[Optional[int], Optional[int]]:
		"""Stuur beide servo's naar hun gekalibreerde ``center_us``.

		Anders dan ``park_all`` blijft de **rail aan** zodat de servo's hun
		center-positie actief vasthouden — handig voor een gimbal-test of
		voor het visueel valideren van een nieuwe calibratie. Geeft
		``(us1, us2)`` terug; ``None`` voor servo's zonder ``center_us``.
		Niet bruikbaar tijdens een tuning-sessie (gebruik daar ``SERVO SET``).
		"""
		if self._tuning_active:
			raise RuntimeError("home_all not allowed during tuning")
		self.enable_rail()
		out: Tuple[Optional[int], Optional[int]] = (None, None)
		results: List[Optional[int]] = []
		for idx in (1, 2):
			cal = self._cal[idx]
			if cal.center_us is None:
				results.append(None)
				continue
			results.append(self._write_pulse(idx, int(cal.center_us)))
		out = (results[0], results[1])
		return out

	# --- watchdog ------------------------------------------------------------

	def note_activity(self) -> None:
		"""Reset de tuning-watchdog. No-op buiten tuning.

		Bedoeld om read-only commando's (zoals ``SERVO STATUS``) ook als
		"de operator is wakker"-signaal te laten meetellen, zodat hij de
		REPL kan refreshen zonder de servo's te moeten bewegen.
		"""
		if self._tuning_active:
			self._touch_watchdog()

	# --- tuning sub-state ----------------------------------------------------

	def start_tuning(self, idx: int = 1) -> None:
		"""Open een tuning-sessie: rail aan, selecteer servo, start watchdog."""
		if int(idx) not in (1, 2):
			raise ValueError("servo idx must be 1 or 2")
		self._tuning_active = True
		self._selected = int(idx)
		now = self._monotonic()
		self._tuning_started_at = now
		self._tuning_last_action_at = now
		self.enable_rail()
		# Start op center indien bekend, anders 1500 µs.
		cal = self._cal[idx]
		start_us = int(cal.center_us) if cal.center_us is not None else 1500
		self._write_pulse(idx, start_us)

	def stop_tuning(self) -> None:
		"""Sluit tuning-sessie netjes: pulses uit, rail uit, state wissen."""
		self._tuning_active = False
		self._selected = None
		self._tuning_started_at = None
		self._tuning_last_action_at = None
		# Pulses op 0 én rail uit. ``disable_rail`` doet beide.
		self.disable_rail()

	def select(self, idx: int) -> None:
		"""Selecteer welke servo door volgende STEP/SET-commando's bewogen wordt."""
		if int(idx) not in (1, 2):
			raise ValueError("servo idx must be 1 or 2")
		if not self._tuning_active:
			raise RuntimeError("not tuning")
		self._selected = int(idx)
		self._touch_watchdog()

	def step(self, delta_us: int) -> int:
		"""Verplaats geselecteerde servo met ``delta_us`` (positief = richting MAX)."""
		if not self._tuning_active or self._selected is None:
			raise RuntimeError("not tuning")
		new_us = self._current_us[self._selected] + int(delta_us)
		out = self._write_pulse(self._selected, new_us)
		self._touch_watchdog()
		return out

	def set_us(self, us: int) -> int:
		"""Zet geselecteerde servo direct op ``us`` (clamp + watchdog)."""
		if not self._tuning_active or self._selected is None:
			raise RuntimeError("not tuning")
		out = self._write_pulse(self._selected, int(us))
		self._touch_watchdog()
		return out

	def mark(self, kind: str) -> int:
		"""Markeer huidige us als MIN/CENTER/MAX/STOW voor de geselecteerde servo."""
		if not self._tuning_active or self._selected is None:
			raise RuntimeError("not tuning")
		k = kind.strip().upper()
		idx = self._selected
		us = int(self._current_us[idx])
		cal = self._cal[idx]
		if k == "MIN":
			cal.min_us = us
		elif k == "CENTER":
			cal.center_us = us
		elif k == "MAX":
			cal.max_us = us
		elif k == "STOW":
			cal.stow_us = us
		else:
			raise ValueError("kind must be MIN|CENTER|MAX|STOW")
		self._touch_watchdog()
		return us

	def save_calibration(self) -> Path:
		"""Schrijf de huidige calibratie naar JSON. Toegestaan ook buiten tuning."""
		_save_cal_dict(self._cal_path, self._cal)
		if self._tuning_active:
			self._touch_watchdog()
		return self._cal_path

	def reload_calibration(self) -> None:
		"""Herlaad calibratie van schijf (negeert in-memory wijzigingen)."""
		fresh = _load_cal_dict(self._cal_path)
		for idx in (1, 2):
			if idx in fresh:
				self._cal[idx] = fresh[idx]
		if self._tuning_active:
			self._touch_watchdog()

	# --- watchdog & shutdown -------------------------------------------------

	def _touch_watchdog(self) -> None:
		self._tuning_last_action_at = self._monotonic()

	def tick(self) -> Optional[str]:
		"""Roep periodiek aan; retourneert ``"WATCHDOG"`` als sessie gekapt is."""
		if not self._tuning_active or self._tuning_last_action_at is None:
			return None
		idle = self._monotonic() - self._tuning_last_action_at
		if idle >= self._watchdog_s:
			self.stop_tuning()
			return "WATCHDOG"
		return None

	def shutdown(self) -> None:
		"""Veilige cleanup voor atexit / SIGTERM. Park indien rail aan."""
		try:
			if self._tuning_active:
				self.stop_tuning()
				return
			if self._rail_on:
				self.park_all()
		finally:
			closer = getattr(self._driver, "close", None)
			if callable(closer):
				try:
					closer()
				except Exception:  # noqa: BLE001
					pass


__all__ = [
	"DEFAULT_BIG_STEP_US",
	"DEFAULT_STEP_US",
	"FakeRailDriver",
	"PARK_WAIT_S",
	"ServoCal",
	"ServoController",
	"ServoStatus",
	"TUNING_WATCHDOG_S",
	"make_pigpio_driver",
]
