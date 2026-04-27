#!/usr/bin/env python3
"""Quick-look plots voor een decoded TLM-CSV uit ``scripts/decode_logs.py``.

Bedoeld voor snelle post-test inspectie van drop-tests, camera-shoots en
missie-samples. Plot vier panelen boven elkaar met gedeelde tijds-as:

1. Altitude (m, vanaf eerste sample).
2. Linear-acceleration-magnitude ``‖a‖`` (g), met piek-markering.
3. Roll & pitch (°) — tuimel-indicatie tijdens val / descent.
4. Gyro x/y/z (°/s) — rotatiesnelheid per as.

State-overgangen (PAD_IDLE → ASCENT → DEPLOYED → LANDED of TEST/DEPLOYED)
worden als verticale lijnen getekend zodat je de mission-fase snel leest.

De CSV-layout die we verwachten staat boven in ``scripts/decode_logs.py``
(kolomnamen ``utc_iso``, ``alt_m``, ``ax_g``, ``ay_g``, ``az_g``,
``accel_mag_g``, ``roll_deg``, ``pitch_deg``, ``gx_dps``/``gy_dps``/
``gz_dps`` optioneel, ``state``). Ontbrekende kolommen worden overgeslagen
i.p.v. een crash.

Usage:

    python scripts/plot_test_csv.py zero_logs/latest/decoded/cansat_test_20260421T093444Z.csv
    python scripts/plot_test_csv.py --no-show zero_logs/.../cansat_mission_*.csv  # batch
    python scripts/plot_test_csv.py --out /tmp/drop.png <csv>                      # expliciete output
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable, List, Optional

# Matplotlib in non-interactive mode als er geen DISPLAY is (CI/headless);
# anders gebruikt ``matplotlib.pyplot`` de default backend. ``MPLCONFIGDIR``
# voorkomt waarschuwingen in sandbox-omgevingen waar ``~/.matplotlib`` niet
# schrijfbaar is (zie Mac-setup).
os.environ.setdefault("MPLCONFIGDIR", str(Path.home() / ".cache" / "matplotlib"))

import matplotlib  # noqa: E402

if not os.environ.get("DISPLAY") and sys.platform != "darwin":
	matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402


# Kleurschema: consistent binnen één figuur, net genoeg onderscheid voor de
# 3-assige panels (roll/pitch, gyro) zonder afhankelijkheid van Matplotlib's
# cycler (die kan tussen versies veranderen).
_COLOR_ALT = "#1f77b4"
_COLOR_MAG = "#d62728"
_COLOR_ROLL = "#2ca02c"
_COLOR_PITCH = "#9467bd"
_COLOR_GX = "#ff7f0e"
_COLOR_GY = "#2ca02c"
_COLOR_GZ = "#9467bd"
_COLOR_STATE = "#808080"


def _parse_time(df: pd.DataFrame) -> pd.DatetimeIndex:
	"""Kies de beste beschikbare tijds-as.

	``utc_iso`` is de standaard-output van ``decode_logs.py`` (ISO8601). Voor
	oudere CSV-varianten gebruiken we ``utc_s`` + ``utc_ms`` als fallback.
	Resultaat is altijd een timezone-naive :class:`DatetimeIndex` in UTC.
	"""
	if "utc_iso" in df.columns:
		return pd.to_datetime(df["utc_iso"], utc=True).dt.tz_convert(None)
	if "utc_s" in df.columns and "utc_ms" in df.columns:
		return pd.to_datetime(
			df["utc_s"] * 1_000 + df["utc_ms"],
			unit="ms",
			utc=True,
		).dt.tz_convert(None)
	raise SystemExit("CSV heeft geen utc_iso of utc_s/utc_ms kolom")


def _state_transitions(df: pd.DataFrame) -> List[tuple]:
	"""Lijst van ``(timestamp, state)`` voor iedere substate-wissel.

	De eerste sample wordt altijd opgenomen (zo staat de startstate op de
	plot, handig bij TEST-runs die al in DEPLOYED beginnen).
	"""
	if "state" not in df.columns:
		return []
	shifts = df["state"].ne(df["state"].shift())
	return list(zip(df.index[shifts], df.loc[shifts, "state"]))


def _add_state_markers(ax, transitions: Iterable[tuple]) -> None:
	# Stagger labels verticaal (0.98 → 0.80 → …) zodat opeenvolgende
	# transitions in een dichte cluster (typisch ASCENT→DEPLOYED binnen
	# enkele seconden) elkaar niet overschrijven.
	y_cycle = (0.97, 0.85, 0.73)
	y_min, y_max = ax.get_ylim()
	for i, (t, state) in enumerate(transitions):
		ax.axvline(t, color=_COLOR_STATE, linestyle="--", linewidth=0.8, alpha=0.6)
		frac = y_cycle[i % len(y_cycle)]
		y = y_min + frac * (y_max - y_min)
		ax.text(
			t,
			y,
			" " + str(state),
			color=_COLOR_STATE,
			fontsize=8,
			va="top",
			ha="left",
		)


def _mark_peak(ax, times: pd.DatetimeIndex, values: pd.Series, unit: str) -> None:
	"""Zet piek-waarde als rode stip + label boven op een grafiek."""
	if values.empty or values.isna().all():
		return
	# ``idxmax`` geeft een label uit de index (Timestamp in ons geval);
	# we hebben de integer-positie nodig om ``times[i]`` te indexeren.
	i = int(values.abs().to_numpy().argmax())
	t = times[i]
	v = float(values.iloc[i])
	ax.plot(t, v, "o", color=_COLOR_MAG, markersize=5)
	ax.annotate(
		"%.2f %s @ %s" % (v, unit, t.strftime("%H:%M:%S")),
		(t, v),
		textcoords="offset points",
		xytext=(5, 10),
		fontsize=8,
		color=_COLOR_MAG,
	)


def _plot_file(csv_path: Path, out: Optional[Path], show: bool) -> Optional[Path]:
	df = pd.read_csv(csv_path)
	if df.empty:
		print("WARN: %s is leeg" % csv_path, file=sys.stderr)
		return None

	df.index = _parse_time(df)
	transitions = _state_transitions(df)

	fig, axes = plt.subplots(
		4, 1, figsize=(12, 10), sharex=True, constrained_layout=True
	)
	fig.suptitle(
		"CanSat TLM — %s  (%d rows, %.1fs, %.2f Hz)"
		% (
			csv_path.name,
			len(df),
			(df.index[-1] - df.index[0]).total_seconds(),
			len(df) / max(1e-3, (df.index[-1] - df.index[0]).total_seconds()),
		),
		fontsize=11,
	)

	# Panel 1: altitude. Peak-markering zodat je apogee meteen ziet.
	ax = axes[0]
	if "alt_m" in df.columns:
		ax.plot(df.index, df["alt_m"], color=_COLOR_ALT, linewidth=1.2)
		_mark_peak(ax, df.index, df["alt_m"], "m")
	ax.set_ylabel("alt (m)")
	ax.grid(True, linestyle=":", alpha=0.4)

	# Panel 2: ‖a‖. Rol zelf als fallback als de CSV geen accel_mag_g heeft
	# (oude decoder-versies). Markeer de piek expliciet — dit is typisch
	# waar de operator wil inzoomen (impact, opening, tumble).
	ax = axes[1]
	if "accel_mag_g" in df.columns:
		mag = df["accel_mag_g"]
	else:
		mag = (df.get("ax_g", 0) ** 2 + df.get("ay_g", 0) ** 2 + df.get("az_g", 0) ** 2) ** 0.5
	ax.plot(df.index, mag, color=_COLOR_MAG, linewidth=1.2, label="‖a_lin‖")
	ax.axhline(4.0, color="#888888", linestyle=":", linewidth=0.8)
	ax.text(
		df.index[0], 4.05, " BNO055 NDOF-limiet ±4g",
		color="#888888", fontsize=7, va="bottom",
	)
	_mark_peak(ax, df.index, mag, "g")
	ax.set_ylabel("‖a_lin‖ (g)")
	ax.grid(True, linestyle=":", alpha=0.4)

	# Panel 3: roll/pitch. Met zero-lijn want oriëntatie-drift is vooral
	# zichtbaar als "is het nog in rust?" — symmetrisch rond 0°.
	ax = axes[2]
	if "roll_deg" in df.columns:
		ax.plot(df.index, df["roll_deg"], color=_COLOR_ROLL, linewidth=1.0, label="roll")
	if "pitch_deg" in df.columns:
		ax.plot(df.index, df["pitch_deg"], color=_COLOR_PITCH, linewidth=1.0, label="pitch")
	ax.axhline(0, color="#888888", linewidth=0.5)
	ax.set_ylabel("roll / pitch (°)")
	ax.legend(loc="upper right", fontsize=8)
	ax.grid(True, linestyle=":", alpha=0.4)

	# Panel 4: gyro. Vaak leeg in huidige decoder (niet alle velden worden
	# meegegeven), dus check per kolom.
	ax = axes[3]
	drew_gyro = False
	for col, color, label in (
		("gx_dps", _COLOR_GX, "gx"),
		("gy_dps", _COLOR_GY, "gy"),
		("gz_dps", _COLOR_GZ, "gz"),
	):
		if col in df.columns and df[col].notna().any():
			ax.plot(df.index, df[col], color=color, linewidth=1.0, label=label)
			drew_gyro = True
	ax.axhline(0, color="#888888", linewidth=0.5)
	ax.set_ylabel("gyro (°/s)")
	if drew_gyro:
		ax.legend(loc="upper right", fontsize=8)
	else:
		ax.text(
			0.5, 0.5, "(geen gyro-kolommen in CSV)",
			transform=ax.transAxes, ha="center", va="center",
			color="#888888", fontsize=9,
		)
	ax.grid(True, linestyle=":", alpha=0.4)

	# State-markers bovenop alle panels: de verticale lijnen tekenen we pas
	# nu alle ylimits stabiel zijn, anders zouden de labels hun posities
	# herbereken bij elke nieuwe plot.
	for ax in axes:
		_add_state_markers(ax, transitions)
		ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
		ax.set_xlabel("UTC")
		ax.tick_params(axis="x", which="both", labelbottom=True)

	if out is None:
		out = csv_path.with_suffix(".png")
	fig.savefig(out, dpi=120)
	print("plot → %s" % out)

	if show:
		plt.show()
	else:
		plt.close(fig)
	return out


def main(argv: Optional[List[str]] = None) -> int:
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument("csv", nargs="+", type=Path, help="CSV-bestand(en) uit decode_logs.py")
	parser.add_argument(
		"--out",
		type=Path,
		default=None,
		help="Output PNG-pad (alleen geldig met 1 CSV); default = <csv>.png naast de input",
	)
	parser.add_argument(
		"--no-show",
		action="store_true",
		help="Toon het venster niet (batch-mode; schrijft wel PNG)",
	)
	args = parser.parse_args(argv)

	if args.out is not None and len(args.csv) > 1:
		parser.error("--out werkt niet samen met meerdere CSV's")

	for csv_path in args.csv:
		if not csv_path.is_file():
			print("ERR: niet gevonden: %s" % csv_path, file=sys.stderr)
			return 2
		_plot_file(csv_path, args.out, show=not args.no_show)

	return 0


if __name__ == "__main__":
	sys.exit(main())
