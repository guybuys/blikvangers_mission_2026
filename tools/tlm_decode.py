#!/usr/bin/env python3
"""Decoder & live-monitor voor de binary log-bestanden van de CanSat Zero.

Leest één of meerdere ``.bin``-files (geproduceerd door
``cansat_hw.telemetry.log_writer.LogManager``) en print de inhoud naar
``stdout`` in een leesbaar of machine-vriendelijk formaat.

Gebruik
=======

	# Eén shot, mens-leesbaar
	python tools/tlm_decode.py ~/cansat_logs/cansat_test_*.bin

	# CSV (kolommen voor TLM-velden); HDR/EVT komen als '#'-comments
	python tools/tlm_decode.py --format csv cansat_test_*.bin > flight.csv

	# JSON Lines: één object per record
	python tools/tlm_decode.py --format jsonl cansat_test_*.bin > flight.jsonl

	# Live volgen (tail -f) op het continuous-bestand:
	python tools/tlm_decode.py --tail ~/cansat_logs/cansat_continuous.bin

	# Stdin als input (handig voor pipes / SCP):
	scp pi:~/cansat_logs/cansat_test_X.bin /dev/stdout | python tools/tlm_decode.py -

CSV-tip
=======
CSV bevat alleen TLM-records als data-rijen; HDR/EVT verschijnen als
``#``-prefixed metadata-regels zodat het bestand met ``pandas.read_csv(..., comment='#')``
direct leesbaar is.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
from pathlib import Path
from typing import IO, Iterator, List, Optional, Tuple

# Maak ``cansat_hw`` importeerbaar zonder ``pip install -e .`` (handig op een
# laptop die alleen logs analyseert en geen sensors aan boord heeft).
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
	sys.path.insert(0, str(_SRC))

from cansat_hw.telemetry.codec import (  # noqa: E402
	BINARY_FIRST_BYTE_MAX,
	RECORD_HDR,
	RECORD_TLM,
	mode_name,
	state_name,
	unpack_mode_state,
	unpack_tlm,
)
from cansat_hw.telemetry.log_writer import (  # noqa: E402
	iter_records,
	parse_header_payload,
)

# --- Decoded record helpers --------------------------------------------------
TLM_CSV_COLUMNS: Tuple[str, ...] = (
	"file",
	"offset",
	"seq",
	"utc",
	"utc_seconds",
	"utc_ms",
	"mode",
	"state",
	"alt_m",
	"pressure_hpa",
	"temp_c",
	"heading_deg",
	"roll_deg",
	"pitch_deg",
	"ax_g",
	"ay_g",
	"az_g",
	"gx_dps",
	"gy_dps",
	"gz_dps",
	"bno_sys_cal",
	"bno_gyro_cal",
	"bno_accel_cal",
	"bno_mag_cal",
	"tag_count",
	"crc_ok",
)


def _utc_iso(seconds: int, ms: int) -> str:
	"""Vorm één UTC-ISO-string uit (seconds, ms)."""
	whole = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(int(seconds)))
	return "%s.%03dZ" % (whole, max(0, min(999, int(ms))))


def _classify(payload: bytes) -> str:
	"""Bepaal het record-type aan de hand van de eerste byte."""
	if not payload:
		return "EMPTY"
	b0 = payload[0]
	if b0 == RECORD_TLM:
		return "TLM"
	if b0 == RECORD_HDR:
		return "HDR"
	if b0 <= BINARY_FIRST_BYTE_MAX:
		return "BIN_OTHER"
	# Alles >= 0x20 = ASCII-payload (huidige EVT MODE … is daar voorbeeld van).
	return "ASCII"


# --- Output renderers --------------------------------------------------------
class HumanRenderer:
	def __init__(self, out: IO[str]) -> None:
		self.out = out

	def header(self, files: List[str]) -> None:
		print(
			"# tlm_decode — %d bestand(en) — %s"
			% (len(files), ", ".join(files) or "<stdin>"),
			file=self.out,
		)

	def render(
		self, file_label: str, offset: int, payload: bytes, crc_ok: bool
	) -> None:
		kind = _classify(payload)
		ok_mark = "" if crc_ok else " [BAD CRC]"
		prefix = "[%s @%6d]%s" % (file_label, offset, ok_mark)
		if kind == "HDR":
			try:
				h = parse_header_payload(payload)
			except ValueError as e:
				print("%s HDR  invalid: %s" % (prefix, e), file=self.out)
				return
			mode_v, state_v = unpack_mode_state(h["mode_state"])
			print(
				"%s HDR  ver=%d %s mode=%s/%s frame_size=%d host=%s fmt=%s"
				% (
					prefix,
					h["header_version"],
					_utc_iso(h["utc_seconds"], h["utc_ms"]),
					mode_name(mode_v),
					state_name(state_v),
					h["frame_size"],
					h["hostname"] or "<none>",
					h["frame_format"],
				),
				file=self.out,
			)
			return
		if kind == "TLM":
			try:
				f = unpack_tlm(payload)
			except ValueError as e:
				print("%s TLM  invalid: %s" % (prefix, e), file=self.out)
				return
			print(
				"%s TLM  seq=%d %s %s/%s alt=%s p=%s T=%s hdg=%s r=%s p=%s "
				"ax=%s ay=%s az=%s cal=%s tags=%d"
				% (
					prefix,
					f.seq,
					_utc_iso(f.utc_seconds, f.utc_ms),
					f.mode_label,
					f.state_label,
					_fmt_num(f.alt_m, "%.2fm"),
					_fmt_num(f.pressure_hpa, "%.2fhPa"),
					_fmt_num(f.temp_c, "%.1fC"),
					_fmt_num(f.heading_deg, "%.1f"),
					_fmt_num(f.roll_deg, "%.1f"),
					_fmt_num(f.pitch_deg, "%.1f"),
					_fmt_num(f.ax_g, "%.3fg"),
					_fmt_num(f.ay_g, "%.3fg"),
					_fmt_num(f.az_g, "%.3fg"),
					_fmt_cal(f.sys_cal, f.gyro_cal, f.accel_cal, f.mag_cal),
					len(f.tags),
				),
				file=self.out,
			)
			return
		if kind == "ASCII":
			text = payload.decode("utf-8", errors="replace").rstrip()
			print("%s TXT  %s" % (prefix, text), file=self.out)
			return
		# BIN_OTHER / EMPTY
		print(
			"%s %-3s %d B: %s"
			% (prefix, kind, len(payload), payload[:32].hex(" ") + ("…" if len(payload) > 32 else "")),
			file=self.out,
		)

	def close(self) -> None:
		self.out.flush()


class JsonlRenderer:
	def __init__(self, out: IO[str]) -> None:
		self.out = out

	def header(self, files: List[str]) -> None:
		# JSONL: geen header-regel; consumers verwachten 1 record/regel.
		_ = files

	def render(
		self, file_label: str, offset: int, payload: bytes, crc_ok: bool
	) -> None:
		kind = _classify(payload)
		obj: dict = {
			"file": file_label,
			"offset": offset,
			"crc_ok": crc_ok,
			"kind": kind,
		}
		if kind == "HDR":
			try:
				h = parse_header_payload(payload)
				mode_v, state_v = unpack_mode_state(h["mode_state"])
				obj.update(h)
				obj["mode"] = mode_name(mode_v)
				obj["state"] = state_name(state_v)
			except ValueError as e:
				obj["error"] = str(e)
		elif kind == "TLM":
			try:
				f = unpack_tlm(payload)
				d = f.to_dict()
				# Vlak maken: tags als sublijst blijft; rest mergen.
				obj.update(d)
			except ValueError as e:
				obj["error"] = str(e)
		elif kind == "ASCII":
			obj["text"] = payload.decode("utf-8", errors="replace").rstrip()
		else:
			obj["hex"] = payload.hex()
		print(json.dumps(obj, separators=(",", ":")), file=self.out)

	def close(self) -> None:
		self.out.flush()


class CsvRenderer:
	def __init__(self, out: IO[str]) -> None:
		self.out = out
		self._writer = csv.writer(out)
		self._wrote_header = False

	def header(self, files: List[str]) -> None:
		print(
			"# tlm_decode CSV — %d bestand(en); HDR/EVT/ASCII komen als '#'-comments"
			% len(files),
			file=self.out,
		)

	def render(
		self, file_label: str, offset: int, payload: bytes, crc_ok: bool
	) -> None:
		kind = _classify(payload)
		if kind == "HDR":
			try:
				h = parse_header_payload(payload)
				mode_v, state_v = unpack_mode_state(h["mode_state"])
				print(
					"# HDR file=%s offset=%d utc=%s mode=%s/%s host=%s fmt=%s frame_size=%d"
					% (
						file_label,
						offset,
						_utc_iso(h["utc_seconds"], h["utc_ms"]),
						mode_name(mode_v),
						state_name(state_v),
						h["hostname"],
						h["frame_format"],
						h["frame_size"],
					),
					file=self.out,
				)
			except ValueError as e:
				print("# HDR invalid file=%s offset=%d err=%s" % (file_label, offset, e), file=self.out)
			return
		if kind == "ASCII":
			text = payload.decode("utf-8", errors="replace").rstrip()
			print("# TXT file=%s offset=%d %s" % (file_label, offset, text), file=self.out)
			return
		if kind != "TLM":
			print(
				"# %s file=%s offset=%d hex=%s" % (kind, file_label, offset, payload[:32].hex()),
				file=self.out,
			)
			return
		# TLM → data-rij. Header pas nu schrijven (na eventuele HDR/EVT comments).
		try:
			f = unpack_tlm(payload)
		except ValueError as e:
			print("# TLM invalid file=%s offset=%d err=%s" % (file_label, offset, e), file=self.out)
			return
		if not self._wrote_header:
			self._writer.writerow(TLM_CSV_COLUMNS)
			self._wrote_header = True
		row = [
			file_label,
			offset,
			f.seq,
			_utc_iso(f.utc_seconds, f.utc_ms),
			f.utc_seconds,
			f.utc_ms,
			f.mode_label,
			f.state_label,
			_csv_num(f.alt_m),
			_csv_num(f.pressure_hpa),
			_csv_num(f.temp_c),
			_csv_num(f.heading_deg),
			_csv_num(f.roll_deg),
			_csv_num(f.pitch_deg),
			_csv_num(f.ax_g),
			_csv_num(f.ay_g),
			_csv_num(f.az_g),
			_csv_num(f.gx_dps),
			_csv_num(f.gy_dps),
			_csv_num(f.gz_dps),
			_csv_int(f.sys_cal),
			_csv_int(f.gyro_cal),
			_csv_int(f.accel_cal),
			_csv_int(f.mag_cal),
			len(f.tags),
			"1" if crc_ok else "0",
		]
		self._writer.writerow(row)

	def close(self) -> None:
		self.out.flush()


def _fmt_num(v: Optional[float], fmt: str) -> str:
	return "n/a" if v is None else (fmt % float(v))


def _fmt_cal(*vals: Optional[int]) -> str:
	if any(v is None for v in vals):
		return "n/a"
	return "/".join("%d" % int(v) for v in vals)  # type: ignore[arg-type]


def _csv_num(v: Optional[float]) -> str:
	return "" if v is None else ("%g" % float(v))


def _csv_int(v: Optional[int]) -> str:
	return "" if v is None else ("%d" % int(v))


# --- Source iterators --------------------------------------------------------
def _iter_file_oneshot(
	path: Path, *, label: str
) -> Iterator[Tuple[str, int, bytes, bool]]:
	"""Itereer alle records in ``path`` in één keer (geen tail)."""
	with open(str(path), "rb") as fh:
		for offset, payload, ok in iter_records(fh):
			yield label, offset, payload, ok


def _iter_file_tail(
	path: Path,
	*,
	label: str,
	poll_s: float,
	stop_after_idle_s: Optional[float] = None,
) -> Iterator[Tuple[str, int, bytes, bool]]:
	"""Tail-modus: blijf wachten op nieuwe records.

	We bewaren de positie zelf en heropenen de file niet; dat is robuust voor
	het LogManager-gedrag (append-only, geen rotate). Bij EOF sleepen we
	``poll_s``; ``stop_after_idle_s`` (alleen voor tests) breekt na N s stilte.
	"""
	pos = 0
	idle = 0.0
	with open(str(path), "rb") as fh:
		while True:
			fh.seek(pos)
			progressed = False
			for offset, payload, ok in iter_records(fh):
				yield label, offset, payload, ok
				pos = fh.tell()
				progressed = True
				idle = 0.0
			if not progressed:
				if stop_after_idle_s is not None and idle >= stop_after_idle_s:
					return
				time.sleep(poll_s)
				idle += poll_s


def _iter_stdin() -> Iterator[Tuple[str, int, bytes, bool]]:
	"""Lees stdin volledig in geheugen en itereer (geen tail-support)."""
	data = sys.stdin.buffer.read()
	buf = io.BytesIO(data)
	for offset, payload, ok in iter_records(buf):
		yield "<stdin>", offset, payload, ok


# --- Main --------------------------------------------------------------------
def _build_renderer(name: str, out: IO[str]):
	if name == "human":
		return HumanRenderer(out)
	if name == "csv":
		return CsvRenderer(out)
	if name == "jsonl":
		return JsonlRenderer(out)
	raise ValueError("unknown format: %s" % name)


def main(argv: Optional[List[str]] = None) -> int:
	p = argparse.ArgumentParser(
		description="Decoder voor CanSat binary log-files (.bin).",
	)
	p.add_argument(
		"files",
		nargs="+",
		help="Pad(en) naar .bin-bestand(en); gebruik '-' om stdin te lezen.",
	)
	p.add_argument(
		"--format",
		choices=["human", "csv", "jsonl"],
		default="human",
		help="Output formaat (default: human).",
	)
	p.add_argument(
		"--tail",
		action="store_true",
		help="Volg het bestand live (poll voor nieuwe records). Werkt alleen "
		"bij EXACT 1 file en niet voor stdin.",
	)
	p.add_argument(
		"--poll",
		type=float,
		default=0.5,
		metavar="S",
		help="Poll-interval in seconden voor --tail (default: 0.5)",
	)
	p.add_argument(
		"--skip-bad-crc",
		action="store_true",
		help="Sla records met foute CRC stil over i.p.v. ze te tonen/markeren.",
	)
	p.add_argument(
		"--no-header",
		action="store_true",
		help="Onderdruk HDR-records in de output.",
	)
	args = p.parse_args(argv)

	if args.tail and (len(args.files) != 1 or args.files[0] == "-"):
		print(
			"--tail werkt alleen op exact één bestand (niet stdin).",
			file=sys.stderr,
		)
		return 2

	renderer = _build_renderer(args.format, sys.stdout)
	renderer.header(args.files)

	def _emit(label: str, offset: int, payload: bytes, ok: bool) -> None:
		if not ok and args.skip_bad_crc:
			return
		if args.no_header and _classify(payload) == "HDR":
			return
		renderer.render(label, offset, payload, ok)

	try:
		if args.tail:
			path = Path(args.files[0]).expanduser()
			for rec in _iter_file_tail(path, label=path.name, poll_s=args.poll):
				_emit(*rec)
		else:
			for f in args.files:
				if f == "-":
					for rec in _iter_stdin():
						_emit(*rec)
					continue
				path = Path(f).expanduser()
				for rec in _iter_file_oneshot(path, label=path.name):
					_emit(*rec)
	except KeyboardInterrupt:
		pass
	finally:
		renderer.close()
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
