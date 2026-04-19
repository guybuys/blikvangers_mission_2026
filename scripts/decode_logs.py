#!/usr/bin/env python3
"""Decoder/analyse-tool voor CanSat binary log-files.

Leest één of meerdere ``.bin``-bestanden zoals geschreven door
:mod:`cansat_hw.telemetry.log_writer` (HEADER + TLM + EVT records ingepakt
in magic+len+payload+CRC frames). Twee output-modi:

    summary  (default)  per file: counts, mode/state-histogram, apex,
                        peak ‖a‖, alle state-transities, EVT-records.
    csv      (--csv)    één regel per TLM-record naar stdout (handig
                        voor Excel/pandas/gnuplot na een vlucht).
    raw      (--raw)    print elk record, één per regel — voor diepe
                        debug.

Voorbeelden:

    # snelle samenvatting van de laatste fetch
    PYTHONPATH=src python scripts/decode_logs.py

    # CSV van één missie naar bestand
    PYTHONPATH=src python scripts/decode_logs.py --csv \\
        zero_logs/latest/cansat_mission_20260419T135804Z.bin > flight.csv

    # ruwe records (alle types)
    PYTHONPATH=src python scripts/decode_logs.py --raw \\
        zero_logs/latest/cansat_continuous.bin | less

    # archief-sessie analyseren
    PYTHONPATH=src python scripts/decode_logs.py \\
        zero_logs/archive/2026-04-19T17-14-32/*.bin

De decoder is volledig stand-alone bedoeld voor de laptop ná een sessie;
de Pico/Zero hoeven hier niets van te weten. Werkt zonder externe deps,
alleen :mod:`cansat_hw` (= dit project) hoeft op ``PYTHONPATH`` te staan.
"""

from __future__ import annotations

import argparse
import glob
import math
import sys
import time as time_mod
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

# Werk ook als de gebruiker vergat ``PYTHONPATH=src`` te zetten — we kennen
# de project-layout en duwen ``src/`` zelf vooraan in sys.path.
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
	sys.path.insert(0, str(_SRC))

from cansat_hw.telemetry.codec import (  # noqa: E402  (na sys.path-fix)
	RECORD_EVT,
	RECORD_HDR,
	RECORD_TLM,
	TelemetryFrame,
	state_name,
	unpack_tlm,
)
from cansat_hw.telemetry.log_writer import (  # noqa: E402
	iter_records,
	parse_header_payload,
)

# Mapping van mode-byte naar leesbare naam (zelfde indices als
# ``cansat_hw.telemetry.codec.mode_for_string``).
MODE_NAMES = {0: "CONFIG", 1: "MISSION", 2: "TEST"}


def _fmt_utc(s: int, ms: int) -> str:
	"""``HH:MM:SS.mmm`` (UTC) of ``"??"`` als de epoch onzin is."""
	if s <= 0:
		return "??"
	t = time_mod.gmtime(s)
	return time_mod.strftime("%H:%M:%S", t) + (".%03d" % (ms % 1000))


def _fmt_iso(s: int, ms: int) -> str:
	"""``YYYY-MM-DDTHH:MM:SS.mmmZ`` voor CSV-export (sortable)."""
	if s <= 0:
		return ""
	t = time_mod.gmtime(s)
	return time_mod.strftime("%Y-%m-%dT%H:%M:%S", t) + (".%03dZ" % (ms % 1000))


def _accel_mag(f: TelemetryFrame) -> float:
	"""‖(ax, ay, az)‖ in g; sentinels (None) tellen als 0 — diagnostisch."""
	ax = float(f.ax_g or 0.0)
	ay = float(f.ay_g or 0.0)
	az = float(f.az_g or 0.0)
	return math.sqrt(ax * ax + ay * ay + az * az)


def _iter_payloads(path: Path) -> Iterable[Tuple[int, bytes, bool]]:
	"""Wrap ``iter_records`` met file-open + close."""
	with open(path, "rb") as fh:
		yield from iter_records(fh)


# --- Output mode: summary ----------------------------------------------------


def _print_summary(path: Path) -> int:
	print(f"\n=== {path.name}  ({path.stat().st_size:,} B) ===")
	records: List[Tuple[int, bytes, bool]] = list(_iter_payloads(path))
	bad_crc = sum(1 for _, _, ok in records if not ok)
	print(f"records: {len(records):>5}   bad-CRC: {bad_crc}")
	if not records:
		return 0

	tlm_count = evt_count = hdr_count = decode_err = 0
	first_tlm: Optional[TelemetryFrame] = None
	last_tlm: Optional[TelemetryFrame] = None
	prev_seq: Optional[int] = None
	seq_gaps: List[Tuple[int, int]] = []
	mode_hist: dict = {}
	state_hist: dict = {}
	transitions: List[Tuple[str, int, str, str, str, str]] = []  # (utc, seq, prev, new, mode, reason)
	last_state: Optional[int] = None
	peak_alt = -float("inf")
	peak_alt_t = ""
	peak_g = 0.0
	peak_g_t = ""
	cal_last: Optional[Tuple[Optional[int], ...]] = None
	pressures: List[float] = []
	# Reden voor de eerstvolgende state-transitie wordt soms eerder dan de
	# transitie-frame zelf vastgelegd (EVT STATE … REASON komt voor de TLM
	# die de nieuwe state al rapporteert). We onthouden de laatste reason
	# zodat we die bij de transitie-regel kunnen tonen.
	pending_reason: Optional[str] = None
	text_evts: List[Tuple[int, str]] = []

	for idx, (_off, payload, ok) in enumerate(records):
		if not ok or not payload:
			continue
		rt = payload[0]
		if rt == RECORD_HDR:
			try:
				hdr = parse_header_payload(payload)
			except Exception as e:  # noqa: BLE001
				print(f"  HDR idx={idx} parse err: {e}")
				continue
			print(
				f"  HDR: ver={hdr['header_version']}  "
				f"mode_state=0x{hdr['mode_state']:02X}  "
				f"frame_size={hdr['frame_size']}  "
				f"utc={_fmt_utc(hdr['utc_seconds'], hdr['utc_ms'])}  "
				f"host={hdr['hostname']}"
			)
			hdr_count += 1
		elif rt == RECORD_TLM:
			try:
				f = unpack_tlm(payload)
			except Exception as e:  # noqa: BLE001
				decode_err += 1
				print(f"  TLM idx={idx} decode err: {e}")
				continue
			tlm_count += 1
			mname = MODE_NAMES.get(f.mode, f"M{f.mode}")
			sname = state_name(f.state)
			mode_hist[mname] = mode_hist.get(mname, 0) + 1
			state_hist[sname] = state_hist.get(sname, 0) + 1
			if first_tlm is None:
				first_tlm = f
			last_tlm = f
			if prev_seq is not None and f.seq != ((prev_seq + 1) & 0xFFFF):
				seq_gaps.append((prev_seq, f.seq))
			prev_seq = f.seq
			if f.alt_m is not None and f.alt_m > peak_alt:
				peak_alt = f.alt_m
				peak_alt_t = _fmt_utc(f.utc_seconds, f.utc_ms)
			if f.pressure_hpa is not None:
				pressures.append(f.pressure_hpa)
			mag = _accel_mag(f)
			if mag > peak_g:
				peak_g = mag
				peak_g_t = _fmt_utc(f.utc_seconds, f.utc_ms)
			cal_last = (f.sys_cal, f.gyro_cal, f.accel_cal, f.mag_cal)
			if last_state is None or f.state != last_state:
				transitions.append((
					_fmt_utc(f.utc_seconds, f.utc_ms),
					f.seq,
					state_name(last_state) if last_state is not None else "(start)",
					sname,
					mname,
					pending_reason or "",
				))
				last_state = f.state
				pending_reason = None
		elif rt == RECORD_EVT:
			# EVT records van de Zero zijn rauwe text na de record-byte.
			evt_count += 1
			try:
				txt = payload[1:].decode("utf-8", errors="replace")
			except Exception:  # noqa: BLE001
				txt = repr(payload[1:30])
			text_evts.append((idx, txt))
			# Capteer reden voor volgende transitie als dit een EVT STATE
			# X REASON-record is.
			parts = txt.strip().split()
			if (
				len(parts) >= 4
				and parts[0] == "EVT"
				and parts[1] == "STATE"
			):
				pending_reason = parts[3]
		else:
			# Sommige oudere of toekomstige records hebben geen RECORD_*-byte
			# vooraan en bevatten gewoon een EVT-text payload. Tolerant zijn.
			try:
				txt = payload.decode("utf-8", errors="replace")
				if txt.startswith("EVT "):
					evt_count += 1
					text_evts.append((idx, txt))
					parts = txt.strip().split()
					if (
						len(parts) >= 4
						and parts[0] == "EVT"
						and parts[1] == "STATE"
					):
						pending_reason = parts[3]
				else:
					decode_err += 1
			except Exception:  # noqa: BLE001
				decode_err += 1

	print(
		f"  TLM={tlm_count}  EVT={evt_count}  HDR={hdr_count}  "
		f"decode-err={decode_err}"
	)
	if mode_hist:
		print(f"  modes : {mode_hist}")
	if state_hist:
		print(f"  states: {state_hist}")
	print(f"  seq-gaps: {len(seq_gaps)}" + (f"  first 5: {seq_gaps[:5]}" if seq_gaps else ""))
	if first_tlm and last_tlm:
		dt = max(1, last_tlm.utc_seconds - first_tlm.utc_seconds)
		hz = tlm_count / dt
		print(
			f"  span: {_fmt_utc(first_tlm.utc_seconds, first_tlm.utc_ms)} "
			f"→ {_fmt_utc(last_tlm.utc_seconds, last_tlm.utc_ms)}  "
			f"({dt}s, {tlm_count} frames ⇒ ~{hz:.2f} Hz)"
		)
	if pressures:
		print(
			f"  pressure: {min(pressures):.2f}..{max(pressures):.2f} hPa  "
			f"alt-peak={peak_alt:.2f} m at {peak_alt_t}"
		)
	if peak_g > 0:
		# Markeer een waarschijnlijke int16-clip (32.767 g betekent dat de
		# BNO055 boven zijn lineaire-accel-bereik ging — vaak een echte
		# fysieke impact, geen sensor-hick).
		clip = "  ⚠ int16 clip" if peak_g >= 32.7 else ""
		print(f"  ‖a‖ peak: {peak_g:.3f} g at {peak_g_t}{clip}")
	if cal_last is not None:
		s, g, a, m = cal_last
		print(f"  last cal (sys/gyro/accel/mag): {s}/{g}/{a}/{m}")

	if transitions:
		print("  state transitions:")
		for ts, seq, prev, nxt, mode, reason in transitions:
			tail = f"  reason={reason}" if reason else ""
			print(f"    {ts}  seq={seq:>5}  {mode}: {prev} → {nxt}{tail}")

	if text_evts:
		print(f"  EVT records ({len(text_evts)}):")
		for idx, t in text_evts[:30]:
			print(f"    #{idx}: {t.strip()}")
		if len(text_evts) > 30:
			print(f"    ... +{len(text_evts) - 30} more (gebruik --raw voor alles)")
	return 0


# --- Output mode: csv --------------------------------------------------------


_CSV_HEADER = (
	"file,utc_iso,utc_s,utc_ms,seq,mode,state,"
	"alt_m,pressure_hpa,temp_c,heading_deg,roll_deg,pitch_deg,"
	"ax_g,ay_g,az_g,accel_mag_g,"
	"sys_cal,gyro_cal,accel_cal,mag_cal,"
	"tags"
)


def _csv_field(v: object) -> str:
	if v is None:
		return ""
	if isinstance(v, float):
		# Compact maar precisie-vol formaat.
		return ("%.4f" % v).rstrip("0").rstrip(".")
	return str(v)


def _print_csv(paths: List[Path]) -> int:
	print(_CSV_HEADER)
	for p in paths:
		try:
			records = list(_iter_payloads(p))
		except OSError as e:
			print(f"# {p.name}: {e}", file=sys.stderr)
			continue
		for _off, payload, ok in records:
			if not ok or not payload or payload[0] != RECORD_TLM:
				continue
			try:
				f = unpack_tlm(payload)
			except Exception as e:  # noqa: BLE001
				print(f"# {p.name}: TLM decode err: {e}", file=sys.stderr)
				continue
			tags_str = ";".join(
				"%d@%dx%dx%d:%dmm" % (t.tag_id, t.dx_cm, t.dy_cm, t.dz_cm, t.size_mm)
				for t in f.tags
			)
			fields = [
				p.name,
				_fmt_iso(f.utc_seconds, f.utc_ms),
				str(f.utc_seconds),
				str(f.utc_ms),
				str(f.seq),
				MODE_NAMES.get(f.mode, "M%d" % f.mode),
				state_name(f.state),
				_csv_field(f.alt_m),
				_csv_field(f.pressure_hpa),
				_csv_field(f.temp_c),
				_csv_field(f.heading_deg),
				_csv_field(f.roll_deg),
				_csv_field(f.pitch_deg),
				_csv_field(f.ax_g),
				_csv_field(f.ay_g),
				_csv_field(f.az_g),
				_csv_field(_accel_mag(f)),
				_csv_field(f.sys_cal),
				_csv_field(f.gyro_cal),
				_csv_field(f.accel_cal),
				_csv_field(f.mag_cal),
				tags_str,
			]
			print(",".join(fields))
	return 0


# --- Output mode: raw --------------------------------------------------------


def _print_raw(paths: List[Path]) -> int:
	for p in paths:
		print(f"# {p.name}")
		for off, payload, ok in _iter_payloads(p):
			tag = " " if ok else "!"
			if not payload:
				print(f"{tag} 0x{off:08X} EMPTY")
				continue
			rt = payload[0]
			if rt == RECORD_HDR:
				try:
					hdr = parse_header_payload(payload)
					print(
						f"{tag} 0x{off:08X} HDR ver={hdr['header_version']} "
						f"utc={_fmt_utc(hdr['utc_seconds'], hdr['utc_ms'])} "
						f"host={hdr['hostname']}"
					)
				except Exception as e:  # noqa: BLE001
					print(f"{tag} 0x{off:08X} HDR-ERR {e}")
			elif rt == RECORD_TLM:
				try:
					f = unpack_tlm(payload)
					print(
						f"{tag} 0x{off:08X} TLM seq={f.seq:>5} "
						f"{MODE_NAMES.get(f.mode,'?')}/{state_name(f.state)} "
						f"utc={_fmt_utc(f.utc_seconds, f.utc_ms)} "
						f"alt={_csv_field(f.alt_m)}m "
						f"p={_csv_field(f.pressure_hpa)}hPa "
						f"‖a‖={_accel_mag(f):.3f}g "
						f"cal={_csv_field(f.sys_cal)}/{_csv_field(f.gyro_cal)}/"
						f"{_csv_field(f.accel_cal)}/{_csv_field(f.mag_cal)}"
					)
				except Exception as e:  # noqa: BLE001
					print(f"{tag} 0x{off:08X} TLM-ERR {e}")
			elif rt == RECORD_EVT:
				try:
					txt = payload[1:].decode("utf-8", errors="replace")
				except Exception:  # noqa: BLE001
					txt = repr(payload[1:32])
				print(f"{tag} 0x{off:08X} EVT  {txt.strip()}")
			else:
				try:
					txt = payload.decode("utf-8", errors="replace")
					# EVT-records van de Zero staan vaak zonder RECORD_EVT-byte
					# vooraan in het log (raw text-payload). Toon ze als EVT
					# zodat de output matcht met wat over de radio ging.
					label = "EVT " if txt.startswith("EVT ") else "TXT "
					print(f"{tag} 0x{off:08X} {label} {txt.strip()}")
				except Exception:  # noqa: BLE001
					print(f"{tag} 0x{off:08X} BIN  rt=0x{rt:02X} len={len(payload)}")
	return 0


# --- CLI ---------------------------------------------------------------------


def _expand_paths(args: List[str]) -> List[Path]:
	out: List[Path] = []
	for a in args:
		# Shells als zsh/fish doen globbing voor ons; voor bash met onmatched
		# pattern OF directe API-aanroep doen we het hier zelf.
		matches = sorted(glob.glob(a))
		if matches:
			out.extend(Path(m) for m in matches)
		elif Path(a).exists():
			out.append(Path(a))
		else:
			print(f"warn: geen match voor '{a}'", file=sys.stderr)
	return out


def main(argv: Optional[List[str]] = None) -> int:
	p = argparse.ArgumentParser(
		description=__doc__.split("\n\n", 1)[0],
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	p.add_argument(
		"paths",
		nargs="*",
		help="één of meer .bin bestanden of glob-patterns "
		"(default: zero_logs/latest/*.bin)",
	)
	mode = p.add_mutually_exclusive_group()
	mode.add_argument(
		"--csv",
		action="store_true",
		help="CSV met één regel per TLM-record naar stdout",
	)
	mode.add_argument(
		"--raw",
		action="store_true",
		help="ruwe records (alle types) één per regel",
	)
	args = p.parse_args(argv)

	patterns = args.paths or ["zero_logs/latest/*.bin"]
	paths = _expand_paths(patterns)
	if not paths:
		print("geen .bin bestanden gevonden", file=sys.stderr)
		return 1

	if args.csv:
		return _print_csv(paths)
	if args.raw:
		return _print_raw(paths)

	for p_ in paths:
		try:
			_print_summary(p_)
		except Exception as e:  # noqa: BLE001
			print(f"ERROR {p_}: {e}", file=sys.stderr)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
