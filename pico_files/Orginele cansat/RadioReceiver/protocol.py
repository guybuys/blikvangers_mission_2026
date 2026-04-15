"""
Eenvoudig tekstcommando-protocol over RFM69 (payload = UTF-8, max 60 bytes).

Doel: zelfde regels kunnen versturen vanaf de **Pico** base station (Thonny CLI)
als de **CanSat (Zero 2 W)** die lokaal of over de lucht verwerpt.

Conventies
----------
- Eén logische regel = één pakket (geen newline in de payload).
- Antwoorden beginnen met ``OK `` of ``ERR `` (spatie na prefix) waar mogelijk.
- Mode op de CanSat (wanneer geïmplementeerd):
    ``GET MODE``  ->  ``OK MODE CONFIG`` of ``OK MODE LAUNCH``
    ``SET MODE CONFIG`` / ``SET MODE LAUNCH``  ->  ``OK MODE ...`` of ``ERR ...``
- Radio (frequentie op de CanSat via RFM69; **alleen in RAM** tot reboot — geen config-bestand):
    ``GET FREQ``  ->  ``OK FREQ 433.0``
    ``SET FREQ 433.0``  ->  ``OK FREQ 433.0`` (zelfde LO voor TX en RX op die chip)
- Alive-check: ``PING``  ->  ``OK PING``

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
		"  GET MODE / SET MODE CONFIG / SET MODE LAUNCH\n"
		"  GET FREQ / SET FREQ <mhz>\n"
		"  (vrije tekst wordt ook verstuurd — handig om te debuggen)\n"
	) % MAX_PAYLOAD
