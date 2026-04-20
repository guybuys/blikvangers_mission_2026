#!/usr/bin/env python3
"""Converteer een Pico-basestation JSONL-log naar CSV.

De Pico-basestation (``pico_files/Orginele cansat/RadioReceiver/
basestation_cli.py``) schrijft met ``!log on`` een JSON-lines log naar
Pico-flash. Elke regel is één record met onder andere ``dir``, ``text``,
``rssi`` en — voor ge-decodeerde replies — ``parsed`` (dict met de
ontlede velden).

Dit script pakt de **TLM-records** (``dir == "RX"`` én
``parsed.kind == "TLM"``) en schrijft ze als CSV — één rij per TLM-frame,
klaar om direct in Excel/Numbers/Pandas te openen.

Gebruik
=======

    # Default: <basename>.csv naast de input
    python scripts/pico_jsonl_to_csv.py cansat_20260419_135804.jsonl
    # → cansat_20260419_135804.csv

    # Expliciete output-file
    python scripts/pico_jsonl_to_csv.py -o flight.csv session.jsonl

    # Meerdere inputs samenvoegen (kolom ``file`` onderscheidt ze)
    python scripts/pico_jsonl_to_csv.py -o combined.csv *.jsonl

    # Naar stdout (pipe in pandas / gnuplot / jq, …)
    python scripts/pico_jsonl_to_csv.py -o - session.jsonl | column -t -s,

Kolommen
========

De kolomvolgorde volgt ``scripts/decode_logs.py`` (Zero-side ``.bin`` →
CSV) waar er overlap is, met enkele Pico-specifieke velden vooraan:

- ``file``                 bestandsnaam van de JSONL-bron
- ``t``                    ISO-tijd vanaf de Pico-RTC (leeg zonder RTC)
- ``dt_ms``                monotone milliseconden sinds ``!log on``
- ``rssi``                 RX-signaalsterkte in dBm (Pico-only)
- ``utc_iso``              UTC-tijd uit het binary TLM-frame (Zero-RTC)
- ``utc_s`` / ``utc_ms``   idem, rauw
- ``seq``                  TLM-seq (uint16, wraps)
- ``mode`` / ``state``     CONFIG / MISSION / TEST  ×  PAD_IDLE / …
- sensor-velden (``alt_m``, ``pressure_hpa``, ``temp_c``, Euler, accel,
  gyro, BNO-cal 0..3)
- ``accel_mag_g``          ‖(ax,ay,az)‖, diagnostisch (zoals ``decode_logs.py``)
- ``tag_count`` + ``tags`` (``id@dx×dy×dz:size_mm;…``)

Text-TLM (oudere TEST-regel-formaat) mist veel van deze velden —
kolommen die er niet zijn blijven gewoon leeg.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Iterator, List, Mapping, Optional, TextIO, Tuple


COLUMNS: Tuple[str, ...] = (
	"file",
	"t",
	"dt_ms",
	"rssi",
	"utc_iso",
	"utc_s",
	"utc_ms",
	"seq",
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
	"accel_mag_g",
	"gx_dps",
	"gy_dps",
	"gz_dps",
	"sys_cal",
	"gyro_cal",
	"accel_cal",
	"mag_cal",
	"tag_count",
	"tags",
)


def _fmt_field(value: Any) -> str:
	"""Compacte CSV-weergave — matcht ``scripts/decode_logs.py``."""
	if value is None or value == "":
		return ""
	if isinstance(value, float):
		if math.isnan(value) or math.isinf(value):
			return ""
		return ("%.4f" % value).rstrip("0").rstrip(".")
	return str(value)


def _utc_iso_from_parsed(parsed: Mapping[str, Any]) -> str:
	"""Bouw ``YYYY-MM-DDTHH:MM:SS.mmmZ`` uit ``utc_seconds`` + ``utc_ms``."""
	utc_s = parsed.get("utc_seconds")
	utc_ms = parsed.get("utc_ms") or 0
	try:
		utc_s_int = int(utc_s)  # type: ignore[arg-type]
	except (TypeError, ValueError):
		return ""
	if utc_s_int <= 0:
		return ""
	whole = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(utc_s_int))
	try:
		ms = int(utc_ms)
	except (TypeError, ValueError):
		ms = 0
	return "%s.%03dZ" % (whole, max(0, min(999, ms)))


def _accel_mag(parsed: Mapping[str, Any]) -> Optional[float]:
	"""‖(ax, ay, az)‖ in g; None als geen enkele accel-as aanwezig is."""
	have_any = False
	total = 0.0
	for key in ("ax_g", "ay_g", "az_g"):
		raw = parsed.get(key)
		if raw is None:
			continue
		try:
			val = float(raw)
		except (TypeError, ValueError):
			continue
		total += val * val
		have_any = True
	if not have_any:
		return None
	return math.sqrt(total)


def _tags_to_str(tags: Any) -> str:
	"""Serialiseer tag-lijst naar ``id@dx×dy×dz:size_mm;id@…`` (= Zero-formaat)."""
	if not tags:
		return ""
	out: List[str] = []
	for tag in tags:
		if not isinstance(tag, Mapping):
			continue
		try:
			out.append(
				"%d@%dx%dx%d:%dmm"
				% (
					int(tag["id"]),
					int(tag["dx_cm"]),
					int(tag["dy_cm"]),
					int(tag["dz_cm"]),
					int(tag["size_mm"]),
				)
			)
		except (KeyError, TypeError, ValueError):
			continue
	return ";".join(out)


def _row_from_record(rec: Mapping[str, Any], file_label: str) -> Optional[dict]:
	"""Geef een CSV-rij als ``rec`` een TLM-RX-record is; anders ``None``."""
	if rec.get("dir") != "RX":
		return None
	parsed = rec.get("parsed")
	if not isinstance(parsed, Mapping) or parsed.get("kind") != "TLM":
		return None
	return {
		"file": file_label,
		"t": rec.get("t") or "",
		"dt_ms": _fmt_field(rec.get("dt_ms")),
		"rssi": _fmt_field(rec.get("rssi")),
		"utc_iso": _utc_iso_from_parsed(parsed),
		"utc_s": _fmt_field(parsed.get("utc_seconds")),
		"utc_ms": _fmt_field(parsed.get("utc_ms")),
		"seq": _fmt_field(parsed.get("seq")),
		"mode": parsed.get("mode") or rec.get("mode") or "",
		"state": parsed.get("state") or "",
		"alt_m": _fmt_field(parsed.get("alt_m")),
		"pressure_hpa": _fmt_field(parsed.get("pressure_hpa")),
		"temp_c": _fmt_field(parsed.get("temp_c")),
		"heading_deg": _fmt_field(parsed.get("heading_deg")),
		"roll_deg": _fmt_field(parsed.get("roll_deg")),
		"pitch_deg": _fmt_field(parsed.get("pitch_deg")),
		"ax_g": _fmt_field(parsed.get("ax_g")),
		"ay_g": _fmt_field(parsed.get("ay_g")),
		"az_g": _fmt_field(parsed.get("az_g")),
		"accel_mag_g": _fmt_field(_accel_mag(parsed)),
		"gx_dps": _fmt_field(parsed.get("gx_dps")),
		"gy_dps": _fmt_field(parsed.get("gy_dps")),
		"gz_dps": _fmt_field(parsed.get("gz_dps")),
		"sys_cal": _fmt_field(parsed.get("bno_sys_cal")),
		"gyro_cal": _fmt_field(parsed.get("bno_gyro_cal")),
		"accel_cal": _fmt_field(parsed.get("bno_accel_cal")),
		"mag_cal": _fmt_field(parsed.get("bno_mag_cal")),
		"tag_count": _fmt_field(parsed.get("tag_count")),
		"tags": _tags_to_str(parsed.get("tags")),
	}


def iter_tlm_rows(
	source: TextIO,
	file_label: str,
	*,
	on_error: Optional[TextIO] = None,
) -> Iterator[dict]:
	"""Yield één rij per TLM-record in een JSONL-stream.

	Ongeldige JSON-regels worden overgeslagen met een waarschuwing naar
	``on_error`` (default ``sys.stderr``). Lege regels en niet-TLM records
	worden stil genegeerd.
	"""
	if on_error is None:
		on_error = sys.stderr
	for line_no, raw in enumerate(source, start=1):
		line = raw.strip()
		if not line:
			continue
		try:
			rec = json.loads(line)
		except json.JSONDecodeError as e:
			print(
				"# skip %s:%d ongeldige JSON: %s" % (file_label, line_no, e),
				file=on_error,
			)
			continue
		if not isinstance(rec, Mapping):
			continue
		row = _row_from_record(rec, file_label)
		if row is not None:
			yield row


def _default_output_for(input_path: Path) -> Path:
	"""``<name>.csv`` naast de input; blijft in dezelfde map."""
	return input_path.with_suffix(".csv")


def _open_sources(
	specs: List[str],
) -> Iterator[Tuple[TextIO, str, Optional[TextIO]]]:
	"""Yield ``(fh, label, close_target)`` voor elke input-spec."""
	for spec in specs:
		if spec == "-":
			yield sys.stdin, "<stdin>", None
			continue
		path = Path(spec).expanduser()
		if not path.exists():
			print(
				"# skip: '%s' bestaat niet" % spec,
				file=sys.stderr,
			)
			continue
		fh = open(str(path), "r", encoding="utf-8")
		yield fh, path.name, fh


def main(argv: Optional[List[str]] = None) -> int:
	parser = argparse.ArgumentParser(
		description=(
			"Converteer Pico-basestation JSONL-log (van !log on) naar CSV — "
			"één rij per TLM-record."
		),
		formatter_class=argparse.RawDescriptionHelpFormatter,
	)
	parser.add_argument(
		"input",
		nargs="+",
		help=(
			"Pad(en) naar .jsonl-bestand(en); gebruik '-' om stdin te lezen."
		),
	)
	parser.add_argument(
		"-o",
		"--out",
		help=(
			"Output-CSV. Default bij één input: <basename>.csv naast de "
			"input (bv. session.jsonl → session.csv). Met meerdere inputs "
			"of '-' (stdin) is --out verplicht. Gebruik '-' om naar stdout "
			"te schrijven."
		),
	)
	args = parser.parse_args(argv)

	multi_or_stdin = len(args.input) > 1 or args.input[0] == "-"
	if multi_or_stdin and not args.out:
		parser.error(
			"--out is verplicht bij meerdere inputs of bij stdin-input ('-')",
		)

	if args.out:
		out_spec = args.out
	else:
		out_spec = str(_default_output_for(Path(args.input[0]).expanduser()))

	if out_spec == "-":
		out_fh: TextIO = sys.stdout
		close_out = False
	else:
		out_path = Path(out_spec).expanduser()
		out_fh = open(str(out_path), "w", encoding="utf-8", newline="")
		close_out = True

	total = 0
	try:
		writer = csv.DictWriter(out_fh, fieldnames=list(COLUMNS))
		writer.writeheader()
		for fh, label, close_target in _open_sources(args.input):
			try:
				for row in iter_tlm_rows(fh, label):
					writer.writerow(row)
					total += 1
			finally:
				if close_target is not None:
					close_target.close()
	finally:
		if close_out:
			out_fh.close()

	if total == 0:
		print(
			"warning: geen TLM-records gevonden "
			"(zijn er wel 'RX'-regels met parsed.kind=='TLM' in de input?)",
			file=sys.stderr,
		)
	if out_spec != "-":
		print("%d TLM-rijen → %s" % (total, out_spec), file=sys.stderr)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
