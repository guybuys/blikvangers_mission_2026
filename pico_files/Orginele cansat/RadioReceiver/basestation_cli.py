"""
Base station op Raspberry Pi Pico (MicroPython) — Thonny + USB-serial.

- Lokale commando's beginnen met ``!`` (blijven op de Pico, gaan niet over RFM69).
- Alle andere regels worden als UTF-8 naar de CanSat gestuurd (standaard node 120)
  en daarna kort op antwoord gewacht.

Vereist op de Pico (zelfde map als dit bestand):
  - ``rfm69.py`` (kopieer uit ../rfm69.py)
  - ``protocol.py`` (dit project)

Zie README_basestation.md voor protocol en bedrading.
"""

import json
import time

from machine import SPI, Pin

from protocol import (
	DEFAULT_BASE_NODE,
	DEFAULT_CANSAT_NODE,
	MAX_PAYLOAD,
	help_wire_commands,
	validate_wire_line,
)
from rfm69 import RFM69

# --- Standaard RF-instellingen (gelijk houden met CanSat / rfm69test_emitter) ---
FREQ = 433.0
ENCRYPTION_KEY = bytes("CANSAT_2025-2026", "utf-8")
NODE_LOCAL = DEFAULT_BASE_NODE
DEST_CANSAT = DEFAULT_CANSAT_NODE

REPLY_TIMEOUT_S = 2.0
# Korte pauze na eigen TX vóór RX — geeft de CanSat tijd om naar RX te gaan (half-duplex).
REPLY_GAP_S = 0.05

# Persistent bewaarde freq (sync met Zero). Bij boot inlezen; na geslaagde
# ``SET FREQ``-roundtrip ook lokaal toepassen + opslaan.
RUNTIME_PATH = "radio_freq.json"


def _load_persisted_freq():
	try:
		with open(RUNTIME_PATH, "r") as fh:
			data = json.load(fh)
		return float(data.get("freq_mhz"))
	except (OSError, ValueError, TypeError):
		return None


def _save_persisted_freq(mhz):
	try:
		with open(RUNTIME_PATH, "w") as fh:
			fh.write(json.dumps({"freq_mhz": float(mhz)}))
			fh.write("\n")
		return None
	except OSError as e:
		return str(e)


_persisted = _load_persisted_freq()
if _persisted is not None:
	FREQ = _persisted
	print("Geladen freq", FREQ, "MHz uit", RUNTIME_PATH)

spi = SPI(
	0,
	miso=Pin(4),
	mosi=Pin(7),
	sck=Pin(6),
	baudrate=50000,
	polarity=0,
	phase=0,
	firstbit=SPI.MSB,
)
nss = Pin(5, Pin.OUT, value=True)
rst = Pin(3, Pin.OUT, value=False)

rfm = RFM69(spi=spi, nss=nss, reset=rst)
rfm.frequency_mhz = FREQ
rfm.encryption_key = ENCRYPTION_KEY
rfm.node = NODE_LOCAL
rfm.destination = DEST_CANSAT
rfm.ack_retries = 2
rfm.ack_wait = 0.4


def _print_help_local():
	print()
	print("=== Lokale commando's (Pico, begin met !) ===")
	print("  !help          dit overzicht")
	print("  !wirehelp      lijst draad-commando's voor de CanSat")
	print("  !freq 433.0    lokale RX/TX-frequentie (MHz)")
	print("  !dest 120      RadioHead-bestemming voor volgende lucht-regels")
	print("  !node 100      eigen node-ID (basisstation)")
	print("  !timeout 2.0   seconden wachten op antwoord na zenden")
	print("  !gap 0.05      seconden wachten na TX vóór RX (half-duplex marge)")
	print("  !info          huidige freq / node / dest / timeout / gap")
	print("  !time          stuur SET TIME <epoch> naar CanSat (Pico-klok; sync via Thonny indien nodig)")
	print("  !timeepoch N   zelfde, met Unix-tijd N vanaf de laptop (bv. van `date +%s`)")
	print("  !gettime       stuur GET TIME naar CanSat; antwoord = OK TIME <epoch> <ISO local>")
	print("  !preflight     stuur PREFLIGHT naar CanSat (check T=TIME, G=GND, BME, IMU, DSK, LOG, FRQ, GIM)")
	print("  !calground     stuur CAL GROUND (gemiddelde druk als grondreferentie)")
	print("  !triggers      stuur GET TRIGGERS (huidige ASC/DEP/LND drempels)")
	print("  !alt           stuur GET ALT (hoogte in m boven grond + actuele druk)")
	print("  !apogee        stuur GET APOGEE (hoogste hoogte sinds laatste reset + leeftijd)")
	print("  !resetapogee   stuur RESET APOGEE (apogee-tracking opnieuw beginnen)")
	print("  !listen        alleen ontvangen (ACK aan) tot Ctrl+C — Thonny: stop knop")
	print()
	print("Typ een regel zonder ! om die naar de CanSat te sturen (max %u bytes UTF-8)." % MAX_PAYLOAD)
	print()


def _handle_local(line: str) -> bool:
	"""Verwerk ``!``-commando. Retourneert True als verwerkt."""
	# MicroPython: str.split accepteert geen keyword maxsplit= — gebruik split(None, n)
	parts = line.strip().split(None, 2)
	cmd = parts[0].lower() if parts else ""
	if cmd == "!help":
		_print_help_local()
	elif cmd == "!wirehelp":
		print(help_wire_commands())
	elif cmd == "!freq" and len(parts) >= 2:
		rfm.frequency_mhz = float(parts[1])
		print("Freq (lokaal):", rfm.frequency_mhz)
	elif cmd == "!dest" and len(parts) >= 2:
		rfm.destination = int(parts[1])
		print("Dest (CanSat node):", rfm.destination)
	elif cmd == "!node" and len(parts) >= 2:
		rfm.node = int(parts[1])
		print("Eigen NODE:", rfm.node)
	elif cmd == "!timeout" and len(parts) >= 2:
		global REPLY_TIMEOUT_S
		REPLY_TIMEOUT_S = float(parts[1])
		print("Reply timeout (s):", REPLY_TIMEOUT_S)
	elif cmd == "!gap" and len(parts) >= 2:
		global REPLY_GAP_S
		REPLY_GAP_S = float(parts[1])
		print("TX->RX gap (s):", REPLY_GAP_S)
	elif cmd == "!info":
		print(
			"freq:",
			rfm.frequency_mhz,
			"MHz  node:",
			rfm.node,
			"dest:",
			rfm.destination,
			"timeout:",
			REPLY_TIMEOUT_S,
			"gap:",
			REPLY_GAP_S,
		)
	elif cmd == "!time":
		wire = "SET TIME %.3f" % time.time()
		_send_and_wait_reply(wire)
	elif cmd == "!timeepoch":
		if len(parts) >= 2:
			wire = "SET TIME %s" % parts[1].strip()
			_send_and_wait_reply(wire)
		else:
			print("ERR: !timeepoch <unix> — op de laptop: date +%s")
	elif cmd == "!gettime":
		_send_and_wait_reply("GET TIME")
	elif cmd == "!preflight":
		_send_and_wait_reply("PREFLIGHT")
	elif cmd == "!calground":
		_send_and_wait_reply("CAL GROUND")
	elif cmd == "!triggers":
		_send_and_wait_reply("GET TRIGGERS")
	elif cmd == "!alt":
		_send_and_wait_reply("GET ALT")
	elif cmd == "!apogee":
		_send_and_wait_reply("GET APOGEE")
	elif cmd == "!resetapogee":
		_send_and_wait_reply("RESET APOGEE")
	elif cmd == "!listen":
		print("Listen-only (ACK aan). Stop met Thonny Stop of hardware reset.")
		while True:
			pkt = rfm.receive(with_ack=True)
			if pkt is not None:
				print("RX:", pkt)
				try:
					print("    ASCII:", str(pkt, "utf-8"))
				except Exception:
					pass
				print("    RSSI:", rfm.last_rssi)
	else:
		print("Onbekend lokaal commando — typ !help")
	return True


def _post_process_reply(sent_line, reply_text):
	"""Voert ná een geslaagde roundtrip eventuele lokale synchronisatie uit."""
	sent_parts = sent_line.strip().split()
	if len(sent_parts) < 3:
		return
	if sent_parts[0].upper() != "SET" or sent_parts[1].upper() != "FREQ":
		return
	rparts = reply_text.strip().split()
	if len(rparts) < 3 or rparts[0].upper() != "OK" or rparts[1].upper() != "FREQ":
		return
	try:
		new_mhz = float(rparts[2])
	except ValueError:
		return
	try:
		rfm.frequency_mhz = new_mhz
	except Exception as e:
		print("WARN: lokale freq zetten mislukte:", e)
		return
	err = _save_persisted_freq(new_mhz)
	if err:
		print("WARN: persist freq mislukte:", err)
	print("Lokale Pico RF-freq gesynchroniseerd:", new_mhz, "MHz (persistent)")


def _send_and_wait_reply(wire_line: str):
	"""Stuurt één pakket naar ``rfm.destination`` en wacht kort op een antwoordpakket."""
	payload = validate_wire_line(wire_line).encode("utf-8")
	if len(payload) > MAX_PAYLOAD:
		print("ERR payload te lang")
		return
	ok = rfm.send(payload, keep_listening=True)
	if not ok:
		print("ERR TX timeout (radio)")
		return
	print("TX ->", wire_line)
	if REPLY_GAP_S > 0:
		time.sleep(REPLY_GAP_S)
	# Geen clear_fifo() hier: die doet STDBY→RX en wist de RX-FIFO. Een snel
	# antwoord van de CanSat kan tijdens REPLY_GAP al binnenkomen — dan zou je
	# het met clear_fifo() weggooien vóór receive().
	pkt = rfm.receive(timeout=REPLY_TIMEOUT_S, with_ack=False, keep_listening=True)
	if pkt is None:
		print("(geen antwoord binnen %.1f s)" % REPLY_TIMEOUT_S)
		print("  Tip: start op de CanSat (Zero 2 W) eerst: python scripts/cansat_radio_protocol.py")
		print("  Probeer: !timeout 5   en/of   !gap 0.1   — zelfde freq/key als CanSat (!info)")
		return
	print("RX <-", pkt)
	reply_text = ""
	try:
		reply_text = str(pkt, "utf-8")
		print("    ASCII:", reply_text)
	except Exception:
		pass
	print("    RSSI:", rfm.last_rssi)
	if reply_text:
		_post_process_reply(wire_line, reply_text)


def main():
	print("Base station (Pico) CLI — node", NODE_LOCAL, "freq", FREQ, "MHz")
	print("Standaard bestemming CanSat (Zero 2 W, node):", rfm.destination)
	_print_help_local()

	while True:
		try:
			line = input("BS> ")
		except EOFError:
			break
		except KeyboardInterrupt:
			print()
			break
		s = line.strip()
		if not s:
			continue
		if s.startswith("!"):
			_handle_local(s)
			continue
		try:
			_send_and_wait_reply(s)
		except ValueError as e:
			print("ERR", e)


if __name__ == "__main__":
	main()
