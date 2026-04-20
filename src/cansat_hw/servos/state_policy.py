"""Servo-rail policy bij flight-state transities (Fase 12).

Pure helper-module: geen pigpio, geen I/O. Bepaalt **wat** er met de servo's
moet gebeuren bij een ``(mode, flight_state)``-overgang, zodat de main-loop
daarna één call naar :class:`ServoController` kan doen.

Rationale (rail off in PAD_IDLE / ASCENT / LANDED):

* **PAD_IDLE** kan uren duren (raket op de mast, wachten op countdown). Servo's
  warm laten draaien is verspilling van LiPo én geeft trillingen die de IMU-
  drempels onnodig laten oplichten.
* **ASCENT** = motor-burn + coast: enorme g-loads, niets dat een servo kan
  corrigeren. Rail uit voorkomt dat een spike op de PWM-lijn de servo
  beschadigt.
* **LANDED** = einde missie. Gewoon uit.

In **DEPLOYED** wordt de gimbal actief — daar moet de rail aan staan zodat de
control-loop pulsewidths kan zetten.

Conventie: alle transities die een **veranderde** ``(mode, flight_state)``
betekenen kiezen één van vijf acties:

* :data:`ServoAction.NONE`     — niets doen (rail blijft staan zoals hij stond).
* :data:`ServoAction.ENABLE`   — rail aanzetten (gimbal komt actief, geen move).
* :data:`ServoAction.HOME`     — rail aan + beide servo's naar ``center_us``.
* :data:`ServoAction.DISABLE`  — alleen rail uit (geen stow-sequence, geen beweging).
* :data:`ServoAction.PARK`     — ENABLE → STOW BOTH → wachten → DISABLE.
"""

from __future__ import annotations

from enum import Enum
from typing import Tuple

from cansat_hw.telemetry.codec import (
	STATE_ASCENT,
	STATE_DEPLOYED,
	STATE_LANDED,
	STATE_NONE,
	STATE_PAD_IDLE,
)


class ServoAction(str, Enum):
	"""Welke actie de :class:`ServoController` moet uitvoeren."""

	NONE = "none"
	ENABLE = "enable"
	HOME = "home"
	DISABLE = "disable"
	PARK = "park"


# Volledige tabel ``(prev_mode, prev_state) -> (new_mode, new_state) -> action``.
# We werken bewust met een functie i.p.v. een dict: er zijn veel "any prev"
# cases (bv. CONFIG → MISSION ongeacht voorgaande flight_state).
def action_for_transition(
	prev: Tuple[str, int],
	new: Tuple[str, int],
) -> ServoAction:
	"""Bereken servo-actie bij een ``(mode, flight_state)``-transitie.

	``prev`` en ``new`` zijn ``(mode, flight_state)``-tuples. ``mode`` is een
	van ``"CONFIG" | "MISSION" | "TEST"`` (case-sensitive zoals in
	:class:`RadioRuntimeState`); ``flight_state`` is een van de
	``STATE_*``-constanten uit :mod:`cansat_hw.telemetry.codec`.

	Retourneert :data:`ServoAction.NONE` als de policy zegt "niets autonoom
	veranderen" — bv. bij ``MISSION → CONFIG`` (operator besluit zelf wat te
	doen) of bij identieke ``prev``/``new``.
	"""
	if prev == new:
		return ServoAction.NONE

	prev_mode, prev_state = prev
	new_mode, new_state = new

	# CONFIG → MISSION: preflight is door (anders waren we hier niet); zet de
	# servo's in stow voor lift-off zodat ze de raket-sluis niet blokkeren.
	if prev_mode == "CONFIG" and new_mode == "MISSION":
		return ServoAction.PARK

	# CONFIG → TEST (DEPLOYED-dry-run). Zelfde rail-policy als DEPLOYED: rail
	# aan én servo's actief in ``center_us`` houden zodat de operator visueel
	# kan valideren dat de gimbal reageert (i.p.v. slappe servo's die naar
	# random posities driften zodra de rail open gaat).
	if prev_mode == "CONFIG" and new_mode == "TEST":
		return ServoAction.HOME

	# TEST → CONFIG (timer afgelopen of EVT MODE CONFIG END_TEST). DISABLE:
	# rail uit zónder beweging. Een PARK hier (zoals vroeger) stowt de gimbal
	# na elke dry-run, wat ongewenst is als de operator de gimbal in home-
	# positie wil houden voor inspectie en volgende tests. De operator kan
	# altijd expliciet ``!servo park`` sturen als hij toch wil stowen.
	if prev_mode == "TEST" and new_mode == "CONFIG":
		return ServoAction.DISABLE

	# Binnen MISSION: per flight_state-overgang.
	if prev_mode == "MISSION" and new_mode == "MISSION":
		# PAD_IDLE → ASCENT: rail al uit na de eerdere PARK. Niets te doen.
		if prev_state == STATE_PAD_IDLE and new_state == STATE_ASCENT:
			return ServoAction.NONE
		# ASCENT → DEPLOYED: parachute open, gimbal komt actief en wordt
		# direct naar center_us gestuurd zodat de control-loop vanaf een
		# bekende neutrale positie vertrekt.
		if prev_state == STATE_ASCENT and new_state == STATE_DEPLOYED:
			return ServoAction.HOME
		# DEPLOYED → LANDED: impact op de grond gedetecteerd. NIET meer
		# stowen — de gimbal kan in het gras/puin/kantelstand staan en een
		# autonome beweging naar ``stow_us`` zou de servo's tegen een
		# obstakel kunnen forceren. Enkel de rail uitzetten; de operator
		# kan na recovery handmatig ``!servo park`` sturen.
		if prev_state == STATE_DEPLOYED and new_state == STATE_LANDED:
			return ServoAction.DISABLE
		# Andere intra-MISSION sprongen (zelden, bv. SET STATE handmatig).
		# Zelfde policy als boven: LANDED → enkel rail uit, DEPLOYED → HOME.
		if new_state == STATE_LANDED:
			return ServoAction.DISABLE
		if new_state == STATE_DEPLOYED:
			return ServoAction.HOME
		return ServoAction.NONE

	# MISSION → CONFIG: operator forceerde abort. Niets autonoom doen — laat de
	# operator zelf SERVO PARK / SERVO DISABLE versturen als hij dat wil.
	# (Reden: rail kan al uit zijn; ongewenste extra beweging wil je niet.)
	if prev_mode == "MISSION" and new_mode == "CONFIG":
		return ServoAction.NONE

	# Onbekende combinatie: veilig kiezen voor "doe niets".
	return ServoAction.NONE


def action_for_shutdown(current_rail_on: bool) -> ServoAction:
	"""Welke actie nemen bij service-shutdown (atexit / SIGTERM)?

	Als de rail nog aan staat, willen we netjes parken zodat de servo's in een
	bekende positie achterblijven. Anders niets doen.
	"""
	return ServoAction.PARK if current_rail_on else ServoAction.NONE


__all__ = [
	"ServoAction",
	"action_for_transition",
	"action_for_shutdown",
	# Re-exports voor gemak in callers (zelfde plek als de policy).
	"STATE_NONE",
	"STATE_PAD_IDLE",
	"STATE_ASCENT",
	"STATE_DEPLOYED",
	"STATE_LANDED",
]
