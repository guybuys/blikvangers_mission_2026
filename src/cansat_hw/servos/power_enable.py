"""Servo-motorvoeding enable (bv. pin 31 = BCM 6) via pigpio.

Jullie driver schakelt de LiPo-rail naar de servo's — zonder enable krijgen de motoren geen spanning.

**Polariteit:** standaard **active-high** (``powered=True`` → GPIO **hoog**). Bij een active-low gate: ``active_low=True``.

**Na script-exit:** in ``finally`` zetten we de pin eerst nog als **uitgang laag** (rail uit), daarna ``pi.stop()`` — daarna wordt de GPIO weer **input**. Bij **active-high** met **hardware pull-down** op de gate (jullie ontwerp) trekt die weerstand de ingang **laag** als de MCU-pin hoog impedant is: geen zwevende “aan”-toestand. Zonder externe pull kan input wel riskant zijn; zie ``docs/rpi_pinning.md``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	import pigpio


def servo_rail_configure(pi: "pigpio.pi", pin: int, *, active_low: bool = False) -> None:
	"""Zet enable-pin als uitgang en zet **motorvoeding uit** (veilige start)."""
	if pin <= 0:
		return
	import pigpio as _pg

	pi.set_mode(pin, _pg.OUTPUT)
	servo_rail_set(pi, pin, powered=False, active_low=active_low)


def servo_rail_set(pi: "pigpio.pi", pin: int, powered: bool, *, active_low: bool = False) -> None:
	"""Motorvoeding aan (``powered=True``) of uit. ``pin`` = BCM; ``pin<=0`` = geen-op."""
	if pin <= 0:
		return
	level = (not powered) if active_low else powered
	pi.write(pin, 1 if level else 0)
