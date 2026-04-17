"""
Eenvoudig tekstcommando-protocol over RFM69 (payload = UTF-8, max 60 bytes).

Doel: zelfde regels kunnen versturen vanaf de **Pico** base station (Thonny CLI)
als de **CanSat (Zero 2 W)** die lokaal of over de lucht verwerpt.

Conventies
----------
- Eén logische regel = één pakket (geen newline in de payload).
- Antwoorden beginnen met ``OK `` of ``ERR `` (spatie na prefix) waar mogelijk.
- Mode op de CanSat (wanneer geïmplementeerd):
    ``GET MODE``  ->  ``OK MODE CONFIG`` of ``OK MODE MISSION``
    ``SET MODE CONFIG`` / ``SET MODE MISSION``  ->  ``OK MODE ...`` of ``ERR ...``
    (Oude alias: ``SET MODE LAUNCH`` wordt als ``MISSION`` behandeld; antwoord is ``OK MODE MISSION``.)
- Radio (frequentie op de CanSat via RFM69; **persistent** in ``config/radio_runtime.json``
  op de Zero én op de Pico zelf — bij boot wordt de laatst bekende waarde hersteld):
    ``GET FREQ``  ->  ``OK FREQ 433.0``
    ``SET FREQ 433.0``  ->  ``OK FREQ 433.0`` (Zero antwoordt op OUDE freq, schakelt dan door;
    de Pico-CLI past zijn eigen ``frequency_mhz`` vervolgens automatisch aan en schrijft
    ``radio_freq.json`` op de flash zodat beide kanten in sync blijven)
- Alive-check: ``PING``  ->  ``OK PING``
- Sensor (alleen CONFIG, als de Zero de sensor heeft geïnitialiseerd):
    ``READ BME280`` of ``BME280``  ->  ``OK BME280 <hPa> <°C> <%RH>`` of ``ERR NO BME280`` / ``ERR BME280 …``
    ``READ BNO055`` of ``BNO055``  ->  ``OK BNO055 <h> <r> <p> <cal>`` (euler ° + sys/gyro/accel/mag 0–3) of ``ERR NO BNO055``
- Tijd (alleen CONFIG op de Zero; zet systeemklok — meestal **root** of systemd ``User=root``):
    ``SET TIME <unix_epoch>``  ->  ``OK TIME`` of ``ERR TIME …`` / ``ERR BAD TIME`` / ``ERR BUSY MISSION``
    ``GET TIME``  ->  ``OK TIME <unix.fff> <YYYY-MM-DDTHH:MM:SS±HH:MM>`` (CONFIG én MISSION; lokale TZ van de Zero)
- Radio-loop op de Zero netjes beëindigen (CONFIG of MISSION):
    ``STOP RADIO``  ->  ``OK STOP RADIO`` (Zero stopt na het antwoordpakket)
- MISSION-preflight (alleen CONFIG):
    ``CAL GROUND``  ->  ``OK GROUND <hPa>`` (BME280-gemiddelde) of ``ERR NO BME280`` / ``ERR GROUND …``
    ``SET GROUND <hPa>``  ->  ``OK GROUND <hPa>`` (handmatig zetten)
    ``GET GROUND``  ->  ``OK GROUND <hPa>`` of ``OK GROUND NONE``
    ``SET TRIGGER ASCENT <m>`` (stijging) / ``DEPLOY <s>`` / ``LAND <m>``  ->  ``OK TRIG <naam> <value><eenheid>``
    ``GET TRIGGERS``  ->  ``OK TRIG ASC=<m>[/<hPa>] DEP=<s>s LND=<m>m`` (hPa-equivalent alleen als grond gekend)
    ``PREFLIGHT``  ->  ``OK PRE ALL GND=… ASC=… DEP=… LND=…`` of ``ERR PRE TIME GND BME IMU DSK LOG FRQ GIM``
    ``SET MODE MISSION`` antwoordt ``ERR PRE …`` zolang niet alles klaar is.

Lokale Pico-commando's (niet over de lucht) beginnen met ``!`` — zie basestation_cli.py
"""

MAX_PAYLOAD = 60

# Standaard RadioHead-node nummers (afstemmen met CanSat / emitter)
DEFAULT_BASE_NODE = 100
DEFAULT_CANSAT_NODE = 120


def validate_wire_line(line: str) -> str:
	"""Stript en controleert lengte; ``ValueError`` als te lang of leeg na strip."""
	s = line.strip()
	if not s:
		raise ValueError("empty line")
	raw = s.encode("utf-8")
	if len(raw) > MAX_PAYLOAD:
		raise ValueError("max %u bytes" % MAX_PAYLOAD)
	return s


def help_wire_commands() -> str:
	return (
		"Draad-commando's (naar CanSat, max %u bytes UTF-8):\n"
		"  PING\n"
		"  GET MODE / SET MODE CONFIG / SET MODE MISSION\n"
		"  GET FREQ / SET FREQ <mhz>\n"
		"  SET TIME <unix_epoch>   (alleen CONFIG; systeemklok op de Zero)\n"
		"  GET TIME                (huidige tijd van de Zero, lokaal ISO + epoch)\n"
		"  CAL GROUND              (BME280-gemiddelde als grondreferentie)\n"
		"  SET GROUND <hPa>        (grondreferentie handmatig)\n"
		"  GET GROUND              (huidige grondreferentie)\n"
		"  SET TRIGGER ASCENT <m> / DEPLOY <s> / LAND <m>\n"
		"  GET TRIGGERS            (ASC in m + hPa-equiv, DEP in s, LND in m)\n"
		"  PREFLIGHT               (MISSION-gate check; SET MODE MISSION gebruikt 'm automatisch)\n"
		"  STOP RADIO              (Zero stopt de commando-loop na OK-antwoord)\n"
		"  READ BME280 / BME280   (druk/temp/RH)\n"
		"  READ BNO055 / BNO055   (euler + cal; fusion IMU)\n"
		"  (vrije tekst wordt ook verstuurd — handig om te debuggen)\n"
	) % MAX_PAYLOAD
