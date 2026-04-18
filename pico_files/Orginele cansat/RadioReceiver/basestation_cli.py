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
# Optioneel overschrijven via ``secrets.py`` (niet in git). Zie
# ``secrets.example.py`` als template. De waarde van ``RADIO_KEY`` MOET exact
# gelijk zijn aan ``CANSAT_RADIO_KEY`` in de .env op de Zero.
FREQ = 433.0
ENCRYPTION_KEY = bytes("CANSAT_2025-2026", "utf-8")
NODE_LOCAL = DEFAULT_BASE_NODE
DEST_CANSAT = DEFAULT_CANSAT_NODE

try:
	import secrets as _secrets  # type: ignore[import-not-found]
except ImportError:
	_secrets = None
if _secrets is not None:
	_key = getattr(_secrets, "RADIO_KEY", None)
	if isinstance(_key, (bytes, bytearray)) and len(_key) == 16:
		ENCRYPTION_KEY = bytes(_key)
	elif _key is not None:
		print("WARN: secrets.RADIO_KEY is geen 16-byte bytes — demo-key wordt gebruikt")
	_freq = getattr(_secrets, "RADIO_FREQ_MHZ", None)
	if isinstance(_freq, (int, float)):
		FREQ = float(_freq)
	_node = getattr(_secrets, "RADIO_NODE", None)
	if isinstance(_node, int):
		NODE_LOCAL = _node
	_dest = getattr(_secrets, "RADIO_DEST", None)
	if isinstance(_dest, int):
		DEST_CANSAT = _dest
else:
	print("WARN: secrets.py niet gevonden — RFM69 draait met de publieke demo-key.")
	print("      Zie secrets.example.py; kopieer naar secrets.py op de Pico.")

REPLY_TIMEOUT_S = 2.0
# Korte pauze na eigen TX vóór RX — geeft de CanSat tijd om naar RX te gaan (half-duplex).
REPLY_GAP_S = 0.05

# Persistent bewaarde freq (sync met Zero). Bij boot inlezen; na geslaagde
# ``SET FREQ``-roundtrip ook lokaal toepassen + opslaan.
RUNTIME_PATH = "radio_freq.json"

# --- Logging (optioneel) ------------------------------------------------------
# JSON Lines: 1 record per regel. Elke TX/RX-regel wordt bijgehouden met een
# monotone ``dt_ms`` (ms sinds log-open) en, als de Pico-RTC bij benadering gezet
# is, een ISO-tijd ``t``. Voor MISSION wordt elk OK-antwoord ook ontleed naar
# een ``parsed``-dict zodat meetdata kolom-gewijs te analyseren is.
LOG_FH = None
LOG_PATH = None
LOG_START_MS = 0
MODE_LAST = None  # bijgehouden via OK MODE ... replies


def _log_iso_time():
	"""ISO-achtige tijd als de Pico-RTC plausibel gezet is, anders None."""
	try:
		y, mo, d, h, mi, s = time.localtime()[:6]
		if y >= 2020:
			return "%04d-%02d-%02dT%02d:%02d:%02d" % (y, mo, d, h, mi, s)
	except Exception:
		pass
	return None


def _log_default_path():
	iso = _log_iso_time()
	if iso is not None:
		return "cansat_" + iso.replace(":", "").replace("-", "").replace("T", "_") + ".jsonl"
	return "cansat_log.jsonl"


def _log_open(path=None):
	global LOG_FH, LOG_PATH, LOG_START_MS
	_log_close()
	if path is None:
		path = _log_default_path()
	try:
		LOG_FH = open(path, "a")
		LOG_PATH = path
		LOG_START_MS = time.ticks_ms()
		_log_emit("INFO", "LOG_OPEN", extra={"version": 1, "node": rfm.node, "dest": rfm.destination, "freq_mhz": rfm.frequency_mhz})
		return None
	except OSError as e:
		LOG_FH = None
		LOG_PATH = None
		return str(e)


def _log_close():
	global LOG_FH, LOG_PATH
	if LOG_FH is not None:
		try:
			_log_emit("INFO", "LOG_CLOSE")
			LOG_FH.flush()
			LOG_FH.close()
		except Exception:
			pass
	LOG_FH = None
	LOG_PATH = None


def _log_emit(direction, text, rssi=None, parsed=None, extra=None):
	"""Schrijf één JSONL-record. No-op als er geen log open is."""
	if LOG_FH is None:
		return
	rec = {"dt_ms": time.ticks_diff(time.ticks_ms(), LOG_START_MS), "dir": direction, "text": text}
	iso = _log_iso_time()
	if iso is not None:
		rec["t"] = iso
	if rssi is not None:
		rec["rssi"] = rssi
	if parsed:
		rec["parsed"] = parsed
	if MODE_LAST is not None:
		rec["mode"] = MODE_LAST
	if extra:
		for k, v in extra.items():
			rec[k] = v
	try:
		LOG_FH.write(json.dumps(rec))
		LOG_FH.write("\n")
		LOG_FH.flush()
	except Exception:
		pass


def _try_floats(base, pairs):
	for k, v in pairs:
		try:
			base[k] = float(v)
		except (ValueError, TypeError):
			base[k] = v
	return base


def _parse_reply(text):
	"""Ontleed een wire-reply naar een dict. None als er niks te matchen valt."""
	parts = text.strip().split()
	if not parts:
		return None
	if parts[0] == "ERR":
		return {"kind": "ERR", "err": " ".join(parts[1:])}
	if parts[0] != "OK" or len(parts) < 2:
		return None
	kind = parts[1]
	if kind == "ALT" and len(parts) >= 4:
		return _try_floats({"kind": "ALT"}, (("alt_m", parts[2]), ("pressure_hpa", parts[3])))
	if kind == "APOGEE":
		if len(parts) >= 5:
			return _try_floats({"kind": "APOGEE"}, (("alt_m", parts[2]), ("pressure_hpa", parts[3]), ("age_s", parts[4])))
		return {"kind": "APOGEE", "empty": True}
	if kind == "GROUND" and len(parts) >= 3:
		return _try_floats({"kind": "GROUND"}, (("ground_hpa", parts[2]),))
	if kind == "MODE" and len(parts) >= 3:
		return {"kind": "MODE", "mode": parts[2]}
	if kind == "FREQ" and len(parts) >= 3:
		return _try_floats({"kind": "FREQ"}, (("freq_mhz", parts[2]),))
	if kind == "TIME" and len(parts) >= 3:
		out = _try_floats({"kind": "TIME"}, (("epoch", parts[2]),))
		if len(parts) >= 4:
			out["iso"] = parts[3]
		return out
	if kind == "BME280" and len(parts) >= 5:
		return _try_floats({"kind": "BME280"}, (("temp_c", parts[2]), ("pressure_hpa", parts[3]), ("humidity_pct", parts[4])))
	if kind == "BNO055":
		return {"kind": "BNO055", "raw": " ".join(parts[2:])}
	if kind == "TRIG":
		return {"kind": "TRIG", "raw": " ".join(parts[2:])}
	if kind == "PRE":
		return {"kind": "PRE", "raw": " ".join(parts[2:])}
	return {"kind": kind, "raw": " ".join(parts[2:]) if len(parts) > 2 else ""}


def _update_mode_from_parsed(parsed):
	global MODE_LAST
	if parsed and parsed.get("kind") == "MODE":
		m = parsed.get("mode")
		if isinstance(m, str):
			MODE_LAST = m


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
	print("  !log on [pad]  start JSONL-log (default cansat_<ts>.jsonl op Pico-flash)")
	print("  !log off       sluit de huidige log af")
	print("  !log status    toont of er gelogd wordt + pad")
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
	elif cmd == "!log":
		sub = parts[1].lower() if len(parts) >= 2 else "status"
		if sub == "on":
			path = parts[2].strip() if len(parts) >= 3 else None
			err = _log_open(path)
			if err:
				print("ERR log:", err)
			else:
				print("Log open:", LOG_PATH)
		elif sub == "off":
			if LOG_FH is None:
				print("Log is niet open.")
			else:
				p = LOG_PATH
				_log_close()
				print("Log gesloten:", p)
		elif sub == "status":
			if LOG_FH is None:
				print("Log: uit")
			else:
				print("Log: aan →", LOG_PATH, "(mode:", MODE_LAST, ")")
		else:
			print("ERR: !log on [pad] | !log off | !log status")
	elif cmd == "!listen":
		print("Listen-only (ACK aan). Stop met Thonny Stop of hardware reset.")
		while True:
			pkt = rfm.receive(with_ack=True)
			if pkt is not None:
				print("RX:", pkt)
				reply_text = ""
				try:
					reply_text = str(pkt, "utf-8")
					print("    ASCII:", reply_text)
				except Exception:
					pass
				print("    RSSI:", rfm.last_rssi)
				parsed = _parse_reply(reply_text) if reply_text else None
				_update_mode_from_parsed(parsed)
				_log_emit("RX", reply_text, rssi=rfm.last_rssi, parsed=parsed, extra={"listen": True})
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
		_log_emit("ERR", wire_line, extra={"why": "payload-too-long"})
		return
	ok = rfm.send(payload, keep_listening=True)
	if not ok:
		print("ERR TX timeout (radio)")
		_log_emit("ERR", wire_line, extra={"why": "tx-timeout"})
		return
	print("TX ->", wire_line)
	_log_emit("TX", wire_line)
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
		_log_emit("TIMEOUT", wire_line, extra={"timeout_s": REPLY_TIMEOUT_S})
		return
	print("RX <-", pkt)
	reply_text = ""
	try:
		reply_text = str(pkt, "utf-8")
		print("    ASCII:", reply_text)
	except Exception:
		pass
	print("    RSSI:", rfm.last_rssi)
	parsed = _parse_reply(reply_text) if reply_text else None
	_update_mode_from_parsed(parsed)
	_log_emit("RX", reply_text if reply_text else "", rssi=rfm.last_rssi, parsed=parsed)
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
