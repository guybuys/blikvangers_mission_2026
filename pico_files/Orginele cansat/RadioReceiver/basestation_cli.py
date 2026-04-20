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
from tlm_decode import (
	FRAME_SIZE as TLM_FRAME_SIZE,
	decode_tlm,
	format_tlm_short,
	is_binary_packet,
)

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

REPLY_TIMEOUT_S = 8.0
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


def _try_float(s):
	try:
		return float(s)
	except (ValueError, TypeError):
		return s


def _parse_reply(text):
	"""Ontleed een wire-reply naar een dict. None als er niks te matchen valt."""
	parts = text.strip().split()
	if not parts:
		return None
	if parts[0] == "ERR":
		return {"kind": "ERR", "err": " ".join(parts[1:])}
	if parts[0] == "TLM" and len(parts) >= 9:
		return {
			"kind": "TLM",
			"dt_ms": _try_float(parts[1]),
			"alt_m": _try_float(parts[2]),
			"pressure_hpa": _try_float(parts[3]),
			"temp_c": _try_float(parts[4]),
			"heading_deg": _try_float(parts[5]),
			"roll_deg": _try_float(parts[6]),
			"pitch_deg": _try_float(parts[7]),
			"bno_sys_cal": _try_float(parts[8]),
		}
	if parts[0] == "EVT" and len(parts) >= 3 and parts[1] == "MODE":
		return {
			"kind": "EVT_MODE",
			"mode": parts[2],
			"reason": " ".join(parts[3:]) if len(parts) >= 4 else "",
		}
	if parts[0] == "EVT" and len(parts) >= 3 and parts[1] == "STATE":
		# Ongevraagde flight-state-overgang in MISSION (Fase 8). Bv.
		# ``EVT STATE ASCENT`` (Fase 8) of ``EVT STATE ASCENT ACC`` (Fase 8b
		# multi-trigger). De reason is optioneel zodat oude logs en oude
		# Zero-firmware blijven werken; wanneer aanwezig is hij één van
		# ACC/ALT/FREEFALL/SHOCK/DESCENT/IMPACT/STABLE.
		return {
			"kind": "EVT_STATE",
			"state": parts[2],
			"reason": parts[3] if len(parts) >= 4 else None,
		}
	if parts[0] != "OK" or len(parts) < 2:
		return None
	kind = parts[1]
	if kind == "ALT" and len(parts) >= 4 and parts[2].upper() != "PRIME":
		return _try_floats({"kind": "ALT"}, (("alt_m", parts[2]), ("pressure_hpa", parts[3])))
	if kind == "ALT" and len(parts) >= 4 and parts[2].upper() == "PRIME":
		return _try_floats({"kind": "ALT_PRIME"}, (("samples", parts[3]),))
	if kind == "APOGEE":
		if len(parts) >= 5:
			return _try_floats({"kind": "APOGEE"}, (("alt_m", parts[2]), ("pressure_hpa", parts[3]), ("age_s", parts[4])))
		return {"kind": "APOGEE", "empty": True}
	if kind == "GROUND" and len(parts) >= 3:
		return _try_floats({"kind": "GROUND"}, (("ground_hpa", parts[2]),))
	if kind == "MODE" and len(parts) >= 3:
		return {"kind": "MODE", "mode": parts[2]}
	if kind == "STATE" and len(parts) >= 3:
		# Optionele reason mee (Fase 8b): ``OK STATE LANDED IMPACT``. Zo
		# weet de operator achteraf nog WAT de overgang triggerde.
		return {
			"kind": "STATE",
			"state": parts[2],
			"reason": parts[3] if len(parts) >= 4 else None,
		}
	if kind == "FREQ" and len(parts) >= 3:
		return _try_floats({"kind": "FREQ"}, (("freq_mhz", parts[2]),))
	if kind == "TIME" and len(parts) >= 3:
		out = _try_floats({"kind": "TIME"}, (("epoch", parts[2]),))
		if len(parts) >= 4:
			out["iso"] = parts[3]
		return out
	if kind == "BME280" and len(parts) >= 5:
		return _try_floats({"kind": "BME280"}, (("temp_c", parts[2]), ("pressure_hpa", parts[3]), ("humidity_pct", parts[4])))
	if kind == "IIR" and len(parts) >= 3:
		out = _try_floats({"kind": "IIR"}, (("iir", parts[2]),))
		for token in parts[3:]:
			if "=" in token:
				k, _, v = token.partition("=")
				out[k.lower()] = _try_float(v)
		return out
	if kind == "BNO055":
		return {"kind": "BNO055", "raw": " ".join(parts[2:])}
	if kind == "TRIG":
		return {"kind": "TRIG", "raw": " ".join(parts[2:])}
	if kind == "PRE":
		return {"kind": "PRE", "raw": " ".join(parts[2:])}
	return {"kind": kind, "raw": " ".join(parts[2:]) if len(parts) > 2 else ""}


def _update_mode_from_parsed(parsed):
	global MODE_LAST
	if not parsed:
		return
	k = parsed.get("kind")
	if k in ("MODE", "EVT_MODE", "TLM"):
		m = parsed.get("mode")
		if isinstance(m, str):
			MODE_LAST = m


def _decode_packet(pkt):
	"""Decodeer een RX-pakket (bytes/None) naar (text_or_None, parsed_dict_or_None).

	- Binary frames (eerste byte < 0x20) gaan via :func:`decode_tlm`.
	- Tekst-frames worden eerst als UTF-8 gedecodeerd en daarna door
	  :func:`_parse_reply` gestructureerd.
	- Voor binary frames bevat ``text`` een korte mens-leesbare samenvatting
	  zodat de bestaande print/log-paden gewoon werken.
	"""
	if not pkt:
		return None, None
	if is_binary_packet(pkt[0]):
		if len(pkt) < TLM_FRAME_SIZE:
			return ("<binary frame too short: %d B>" % len(pkt)), {
				"kind": "BIN_ERR",
				"err": "short",
				"first_byte": pkt[0],
				"len": len(pkt),
			}
		try:
			parsed = decode_tlm(pkt)
		except Exception as e:
			return ("<binary decode err: %s>" % e), {
				"kind": "BIN_ERR",
				"err": str(e),
				"first_byte": pkt[0],
			}
		return format_tlm_short(parsed), parsed
	try:
		text = str(pkt, "utf-8")
	except Exception:
		return None, {"kind": "BIN_RAW", "first_byte": pkt[0], "len": len(pkt)}
	return text, _parse_reply(text)


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
	print("  !state         stuur GET STATE (huidige flight state PAD_IDLE/ASCENT/DEPLOYED/LANDED)")
	print("  !setstate NAME stuur SET STATE NAME — alleen in CONFIG; voor demo / pre-staging")
	print("  !iir [N]       zonder N: GET IIR; met N (0/2/4/8/16): SET IIR N — lager = sneller, hoger = stiller")
	print("  !altprime [N]  zonder N: GET ALT PRIME; met N (1..32): SET ALT PRIME N — meer = accuratere !alt, trager")
	print("  !listen        alleen ontvangen (ACK aan) tot Ctrl+C — Thonny: stop knop")
	print("  !test [s]      vraag TEST-mode op de CanSat (default 10s, 2..60), luister naar TLM")
	print("  !servo         open servo-tuning sub-REPL (SERVO START -> letters -> SAVE/STOP)")
	print("  !servo enable  zet servo-rail aan (geen pulse)")
	print("  !servo disable zet servo-rail uit (stopt tuning indien actief)")
	print("  !servo park    PARK-sequentie: rail aan -> stow -> wacht 800ms -> rail uit")
	print("  !servo home    HOME: rail aan + beide servo's naar center_us (rail blijft aan)")
	print("  !servo status  toont rail/tuning/cur-us van de Zero-controller")
	print("  !park          alias voor !servo park (snelle veilige stow)")
	print("  !home          alias voor !servo home (centerstand vasthouden)")
	print("  !shoot         CAM SHOOT — foto maken (JPEG op de Zero) + AprilTag-detectie (CONFIG)")
	print("  !detect        CAM DETECT — zelfde, zonder foto op te slaan (CONFIG)")
	print("  !camstats      GET CAMSTATS — thread-active, frames, saves, errors, detect-calls")
	print("  !log on [pad]  start JSONL-log (default cansat_<ts>.jsonl op Pico-flash)")
	print("  !log off       sluit de huidige log af")
	print("  !log status    toont of er gelogd wordt + pad")
	print()
	print("Typ een regel zonder ! om die naar de CanSat te sturen (max %u bytes UTF-8)." % MAX_PAYLOAD)
	print()


def _print_servo_help():
	print()
	print("=== Servo-tuning sub-REPL (alleen in CONFIG) ===")
	print("  1 / 2          selecteer servo 1 / 2")
	print("  a / d          -10 / +10 µs  (klein)")
	print("  A / D          -50 / +50 µs  (groot)")
	print("  N              SET us-waarde (bv. 1500)")
	print("  z / c / x      mark MIN / CENTER / MAX op huidige us")
	print("  w              mark STOW op huidige us")
	print("  p              status (rail/tuning/cur-us/cal)")
	print("  s              SAVE calibratie naar JSON op de Zero")
	print("  q              STOP tuning (rail uit) en sluit sub-REPL")
	print("  ?              dit overzicht")
	print()


def _run_servo_repl():
	"""Open de tuning-sub-REPL: stuur SERVO START en handel letters af."""
	# Default: starten met servo 1. ``_send_and_wait_reply`` geeft niets terug
	# (None) — we openen de REPL ook als de Zero niet antwoordt zodat de
	# operator kan retry'en met 'p' (status) of 'q' (stop).
	_send_and_wait_reply("SERVO START 1")
	_print_servo_help()
	while True:
		try:
			cmd = input("servo> ").strip()
		except (KeyboardInterrupt, EOFError):
			print()
			_send_and_wait_reply("SERVO STOP")
			return
		if not cmd:
			continue
		if cmd in ("q", "Q"):
			_send_and_wait_reply("SERVO STOP")
			return
		if cmd in ("?", "h", "help"):
			_print_servo_help()
			continue
		if cmd in ("1", "2"):
			_send_and_wait_reply("SERVO SEL %s" % cmd)
			continue
		if cmd == "a":
			_send_and_wait_reply("SERVO STEP -10")
			continue
		if cmd == "d":
			_send_and_wait_reply("SERVO STEP 10")
			continue
		if cmd == "A":
			_send_and_wait_reply("SERVO STEP -50")
			continue
		if cmd == "D":
			_send_and_wait_reply("SERVO STEP 50")
			continue
		if cmd == "z":
			_send_and_wait_reply("SERVO MIN")
			continue
		if cmd == "c":
			_send_and_wait_reply("SERVO CENTER")
			continue
		if cmd == "x":
			_send_and_wait_reply("SERVO MAX")
			continue
		if cmd == "w":
			_send_and_wait_reply("SERVO STOW_MARK")
			continue
		if cmd == "p":
			_send_and_wait_reply("SERVO STATUS")
			continue
		if cmd == "s":
			_send_and_wait_reply("SERVO SAVE")
			continue
		# Numeriek: directe SET us
		try:
			us = int(cmd)
		except ValueError:
			print("?  typ '?' voor help")
			continue
		if not (500 <= us <= 2500):
			print("ERR: us moet 500..2500")
			continue
		_send_and_wait_reply("SERVO SET %d" % us)


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
	elif cmd == "!state":
		_send_and_wait_reply("GET STATE")
	elif cmd == "!setstate":
		if len(parts) < 2:
			print("ERR: !setstate <PAD_IDLE|ASCENT|DEPLOYED|LANDED|NONE>")
			return True
		name = parts[1].strip().upper()
		if name not in ("PAD_IDLE", "ASCENT", "DEPLOYED", "LANDED", "NONE"):
			print("ERR: !setstate <PAD_IDLE|ASCENT|DEPLOYED|LANDED|NONE>")
			return True
		_send_and_wait_reply("SET STATE %s" % name)
	elif cmd == "!iir":
		if len(parts) >= 2:
			arg = parts[1].strip()
			try:
				coef = int(arg)
			except ValueError:
				print("ERR: !iir <0|2|4|8|16>")
				return True
			if coef not in (0, 2, 4, 8, 16):
				print("ERR: !iir <0|2|4|8|16>")
				return True
			_send_and_wait_reply("SET IIR %d" % coef)
		else:
			_send_and_wait_reply("GET IIR")
	elif cmd == "!altprime":
		if len(parts) >= 2:
			arg = parts[1].strip()
			try:
				n = int(arg)
			except ValueError:
				print("ERR: !altprime <1..32>")
				return True
			if not (1 <= n <= 32):
				print("ERR: !altprime <1..32>")
				return True
			_send_and_wait_reply("SET ALT PRIME %d" % n)
		else:
			_send_and_wait_reply("GET ALT PRIME")
	elif cmd == "!park":
		_send_and_wait_reply("SERVO PARK")
	elif cmd == "!home":
		_send_and_wait_reply("SERVO HOME")
	elif cmd == "!shoot":
		_send_and_wait_reply("CAM SHOOT")
	elif cmd == "!detect":
		_send_and_wait_reply("CAM DETECT")
	elif cmd == "!camstats":
		_send_and_wait_reply("GET CAMSTATS")
	elif cmd == "!servo":
		if len(parts) < 2:
			_run_servo_repl()
			return True
		sub = parts[1].strip().lower()
		if sub == "enable":
			_send_and_wait_reply("SERVO ENABLE")
		elif sub == "disable":
			_send_and_wait_reply("SERVO DISABLE")
		elif sub == "park":
			_send_and_wait_reply("SERVO PARK")
		elif sub == "home":
			_send_and_wait_reply("SERVO HOME")
		elif sub == "stow":
			_send_and_wait_reply("SERVO STOW")
		elif sub == "status":
			_send_and_wait_reply("SERVO STATUS")
		elif sub == "tune":
			_run_servo_repl()
		else:
			print("ERR: !servo [enable|disable|park|home|stow|status|tune] of !servo (= tune)")
	elif cmd == "!test":
		seconds = 10.0
		if len(parts) >= 2:
			try:
				seconds = float(parts[1])
			except ValueError:
				print("ERR: !test [seconden] — bv. !test 10")
				return True
		_run_test_mode(seconds)
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
				text, parsed = _decode_packet(pkt)
				if parsed and parsed.get("kind") == "TLM":
					print("RX TLM:", text)
				else:
					print("RX:", pkt)
					if text:
						print("    ASCII:", text)
				print("    RSSI:", rfm.last_rssi)
				_update_mode_from_parsed(parsed)
				_log_emit(
					"RX",
					text if text else "",
					rssi=rfm.last_rssi,
					parsed=parsed,
					extra={"listen": True},
				)
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


def _run_test_mode(seconds):
	"""Start TEST-mode op de CanSat en luister (read-only) tot EVT MODE CONFIG.

	Werking:
	  1. Stuur ``SET MODE TEST <seconds>`` en wacht op antwoord (mag ook ``ERR PRE …`` zijn).
	  2. Bij ``OK MODE TEST …`` gaan we ``seconds + 3`` seconden luisteren naar TLM en
	     het ``EVT MODE CONFIG``-event dat de CanSat na afloop stuurt.
	  3. Zodra dat event binnenkomt vallen we terug op de prompt. Lokale mode-markering
	     (``MODE_LAST``) wordt automatisch bijgewerkt via ``_parse_reply``.
	"""
	wire = "SET MODE TEST %g" % float(seconds)
	payload = validate_wire_line(wire).encode("utf-8")
	if len(payload) > MAX_PAYLOAD:
		print("ERR payload te lang")
		return
	ok = rfm.send(payload, keep_listening=True)
	if not ok:
		print("ERR TX timeout (radio)")
		_log_emit("ERR", wire, extra={"why": "tx-timeout"})
		return
	print("TX ->", wire)
	_log_emit("TX", wire)
	if REPLY_GAP_S > 0:
		time.sleep(REPLY_GAP_S)
	# Zelfde filtering als bij gewone commando's: TLM/EVT mogen het echte
	# ``OK MODE TEST <s>`` antwoord niet overstemmen.
	reply_text, parsed = _recv_reply_filtering_telemetry(wire)
	if reply_text is None and parsed is None:
		print("(geen antwoord binnen %.1f s)" % REPLY_TIMEOUT_S)
		_log_emit("TIMEOUT", wire, extra={"timeout_s": REPLY_TIMEOUT_S})
		return
	print("RX <-", reply_text if reply_text else "(undecodable)")
	print("    RSSI:", rfm.last_rssi)
	_update_mode_from_parsed(parsed)
	_log_emit("RX", reply_text or "", rssi=rfm.last_rssi, parsed=parsed)
	if not (reply_text and reply_text.startswith("OK MODE TEST")):
		print("TEST-mode niet gestart — zie reply.")
		return
	deadline_s = time.ticks_add(time.ticks_ms(), int((float(seconds) + 3.0) * 1000))
	print("Listen-only voor TLM (%.1f s + 3s buffer); Ctrl+C om vroeger te stoppen." % float(seconds))
	# Bijhouden van seq-gaten zodat de operator pakketverlies meteen ziet.
	frames = 0
	last_seq = None
	missed = 0
	try:
		while time.ticks_diff(deadline_s, time.ticks_ms()) > 0:
			rx = rfm.receive(with_ack=False, timeout=0.5, keep_listening=True)
			if rx is None:
				continue
			rx_text, rx_parsed = _decode_packet(rx)
			if rx_parsed and rx_parsed.get("kind") == "TLM":
				seq = rx_parsed.get("seq")
				if last_seq is not None and isinstance(seq, int):
					gap = (seq - last_seq - 1) & 0xFFFF
					if 0 < gap < 1000:
						missed += gap
				last_seq = seq
				print("RX TLM:", rx_text)
			else:
				print("RX:", rx_text if rx_text else rx)
			print("    RSSI:", rfm.last_rssi)
			_update_mode_from_parsed(rx_parsed)
			_log_emit(
				"RX",
				rx_text or "",
				rssi=rfm.last_rssi,
				parsed=rx_parsed,
				extra={"test": True},
			)
			frames += 1
			if rx_parsed and rx_parsed.get("kind") == "EVT_MODE" and rx_parsed.get("mode") == "CONFIG":
				print(
					"TEST-mode afgesloten (EVT MODE CONFIG). Frames:",
					frames,
					"missed seq:",
					missed,
				)
				return
	except KeyboardInterrupt:
		print()
		print("Listen onderbroken.")
		return
	print(
		"TEST-mode listen-window afgelopen (geen EVT ontvangen). Frames:",
		frames,
		"missed seq:",
		missed,
	)


def _is_passthrough_packet(parsed):
	"""True voor pakketten die GEEN antwoord op een commando zijn.

	Bij MISSION/TEST pusht de Zero elke seconde een binary TLM-frame en
	stuurt hij ongevraagd ``EVT STATE …`` / ``EVT MODE …`` bij een transitie.
	Die mogen een command-reply (``OK STATE`` / ``OK MODE …`` / ``OK STOP RADIO``)
	NIET overstemmen — anders zien we het echte antwoord nooit en denkt
	de operator dat zijn commando faalde (terwijl het wél is uitgevoerd).
	"""
	if not parsed:
		return False
	kind = parsed.get("kind")
	return kind == "TLM" or (isinstance(kind, str) and kind.startswith("EVT_"))


# Aantal TX-pogingen bij MISSION/TEST-verkeer. RFM69 is half-duplex en doet
# geen ACK/retry op deze laag; als de Zero net een TLM-frame staat te zenden
# (of de bijhorende sensor-reads doet) wanneer wij TX'en, raakt ons commando
# verloren. Met meerdere pogingen verspreiden we onze TX over verschillende
# fases van de TLM-cyclus zodat er statistisch zeker een poging tijdens een
# RX-window van de Zero valt.
MAX_TX_ATTEMPTS = 3


def _recv_reply_filtering_telemetry(wire_line, max_wait_s=None, log_extra_pass=None):
	"""Wacht tot een echte text-reply binnenkomt; passeer TLM/EVT pakketten.

	Retourneert ``(reply_text, parsed)`` of ``(None, None)`` bij timeout.
	Onderweg printen + loggen we elk passing-through pakket zodat de
	operator nog steeds telemetrie/EVT-overgangen ziet binnenstromen.

	``max_wait_s`` overrulet ``REPLY_TIMEOUT_S`` voor één call (gebruikt door
	de retry-loop om elk poging een eigen, korter venster te geven). Tijd
	verstrijkt cumulatief: pass-through pakketten zonder reply blijven binnen
	hetzelfde venster.
	"""
	wait_s = float(REPLY_TIMEOUT_S if max_wait_s is None else max_wait_s)
	deadline_ms = time.ticks_add(time.ticks_ms(), int(wait_s * 1000))
	while True:
		remaining_ms = time.ticks_diff(deadline_ms, time.ticks_ms())
		if remaining_ms <= 0:
			return None, None
		# Korte chunks (max 0.5 s) zodat we tussen pakketten responsief
		# blijven en de remaining-budget netjes afpellen.
		chunk_s = min(0.5, remaining_ms / 1000.0)
		pkt = rfm.receive(timeout=chunk_s, with_ack=False, keep_listening=True)
		if pkt is None:
			continue
		text, parsed = _decode_packet(pkt)
		if _is_passthrough_packet(parsed):
			# Pass-through: laten zien, loggen, doorgaan met wachten.
			label = "TLM" if (parsed and parsed.get("kind") == "TLM") else "EVT"
			print("  (pass-through %s tijdens wachten op reply: %s)" % (label, text))
			_update_mode_from_parsed(parsed)
			_log_emit(
				"RX",
				text or "",
				rssi=rfm.last_rssi,
				parsed=parsed,
				extra=log_extra_pass,
			)
			continue
		# Echte reply (OK …, ERR …, of onbekend tekstpakket) — terugsturen.
		return text, parsed


def _send_and_wait_reply(wire_line: str):
	"""Stuurt één pakket naar ``rfm.destination`` en wacht op een antwoordpakket.

	Bij MISSION/TEST-verkeer probeert deze functie tot ``MAX_TX_ATTEMPTS``
	keer opnieuw te zenden als er geen text-reply komt binnen het deel-
	venster (`REPLY_TIMEOUT_S / MAX_TX_ATTEMPTS`). Op die manier valt
	statistisch elke poging in een ander stuk van de TLM-cyclus van de Zero;
	zonder dat horen wij dezelfde TLM-burst als "antwoord" of helemaal niets.
	"""
	payload = validate_wire_line(wire_line).encode("utf-8")
	if len(payload) > MAX_PAYLOAD:
		print("ERR payload te lang")
		_log_emit("ERR", wire_line, extra={"why": "payload-too-long"})
		return
	# Per-poging venster: deel REPLY_TIMEOUT_S over MAX_TX_ATTEMPTS, maar nooit
	# minder dan 2.5 s. Sommige commando's (CAL GROUND doet 9 BME280-reads à
	# ~225 ms bij OS=16 ⇒ ~2.0 s, plus reply-delay + radio-TX) hebben echt 2 s
	# nodig; bij een te krap deelvenster zou élke try systematisch missen
	# terwijl de Zero gewoon nog rustig aan het meten is.
	per_try_s = max(2.5, float(REPLY_TIMEOUT_S) / float(MAX_TX_ATTEMPTS))
	for attempt in range(MAX_TX_ATTEMPTS):
		ok = rfm.send(payload, keep_listening=True)
		if not ok:
			print("ERR TX timeout (radio)")
			_log_emit("ERR", wire_line, extra={"why": "tx-timeout", "attempt": attempt})
			return
		if attempt == 0:
			print("TX ->", wire_line)
			_log_emit("TX", wire_line)
		else:
			print("TX -> %s (retry %d/%d)" % (wire_line, attempt + 1, MAX_TX_ATTEMPTS))
			_log_emit(
				"TX_RETRY",
				wire_line,
				extra={"attempt": attempt + 1, "of": MAX_TX_ATTEMPTS},
			)
		if REPLY_GAP_S > 0:
			time.sleep(REPLY_GAP_S)
		reply_text, parsed = _recv_reply_filtering_telemetry(
			wire_line, max_wait_s=per_try_s
		)
		if reply_text is not None or parsed is not None:
			print("RX <-", reply_text if reply_text else "(undecodable)")
			print("    RSSI:", rfm.last_rssi)
			_update_mode_from_parsed(parsed)
			_log_emit("RX", reply_text or "", rssi=rfm.last_rssi, parsed=parsed)
			if reply_text:
				_post_process_reply(wire_line, reply_text)
			return
	# Alle pogingen op — meld dat consistent.
	print(
		"(geen antwoord binnen %.1f s, %d pogingen)"
		% (per_try_s * MAX_TX_ATTEMPTS, MAX_TX_ATTEMPTS)
	)
	print("  Tip: !timeout 6   of   verlaag --mission-tlm-interval op de Zero (meer RX-tijd)")
	_log_emit(
		"TIMEOUT",
		wire_line,
		extra={"timeout_s": REPLY_TIMEOUT_S, "attempts": MAX_TX_ATTEMPTS},
	)


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
