"""BNO055 calibratie-profiel ⇄ JSON.

Gebruik dit samen met :meth:`cansat_hw.sensors.bno055.BNO055.read_calibration_profile`
en :meth:`write_calibration_profile` om de 22-byte offset-/radius-blob persistent te
maken tussen reboots. De BNO055 vergeet z'n kalibratie bij elke power-cycle, dus
we bewaren na een geslaagde kalibratie-sessie de bytes in
``config/bno055_calibration.json`` op de Zero (per-hardware, buiten git) en laden
die bij boot terug in de chip.

Schema (versie 1) — human-readable, zodat we in een oogopslag kunnen zien of de
offsets plausibel zijn i.p.v. een base64-blob:

.. code-block:: json

    {
        "schema": 1,
        "saved_at": "2026-04-21T19:42:30+00:00",
        "calibration_at_save": "sys=3 gyr=3 acc=3 mag=3",
        "accel_offset": [17, -4, 11],
        "mag_offset":   [-128, 94, -210],
        "gyro_offset":  [0, 1, -1],
        "accel_radius": 1000,
        "mag_radius":   657,
        "raw_hex": "110…6a"
    }

``raw_hex`` (22 bytes ⇒ 44 hex-chars) is de **ground truth**: als de schaal-
integers zijn aangepast of het schema verandert, kan je hier altijd direct de
originele blob terughalen. De gedecodeerde integer-velden zijn puur voor
menselijke inspectie en debugging.
"""

from __future__ import annotations

import json
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, Union

from cansat_hw.sensors.bno055.device import BNO055_CALIB_PROFILE_LENGTH

PROFILE_SCHEMA_VERSION = 1
PROFILE_LENGTH = BNO055_CALIB_PROFILE_LENGTH  # re-export voor callers


def _unpack_profile(data: bytes) -> Tuple[
	Tuple[int, int, int],
	Tuple[int, int, int],
	Tuple[int, int, int],
	int,
	int,
]:
	"""Splits het 22-byte blob in ``(accel_off, mag_off, gyro_off, accel_r, mag_r)``."""
	if len(data) != PROFILE_LENGTH:
		raise ValueError(
			f"profile-blob moet {PROFILE_LENGTH} B zijn, kreeg {len(data)} B"
		)
	# Alle 22 bytes zijn 11 klein-endian int16's: 3 accel + 3 mag + 3 gyro +
	# accel_radius + mag_radius. Één struct.unpack houdt de parse leesbaar en
	# voorkomt sign-bugs in handmatige byte-combines.
	a0, a1, a2, m0, m1, m2, g0, g1, g2, ar, mr = struct.unpack("<11h", data)
	return (a0, a1, a2), (m0, m1, m2), (g0, g1, g2), ar, mr


def profile_to_dict(
	data: bytes,
	*,
	calibration_status: Optional[Tuple[int, int, int, int]] = None,
	saved_at: Optional[datetime] = None,
) -> dict:
	"""Zet een ruwe 22-byte blob om in het JSON-schema (zie module-docstring).

	``calibration_status`` is optioneel: als je de ``(sys, gyr, acc, mag)``-
	tuple van vóór de save aanlevert, landt die als ``calibration_at_save``
	in de JSON (handig bij latere triage — was dit een "in-gloei" save of een
	verse 3/3/3/3 save?).
	"""
	accel_off, mag_off, gyro_off, accel_r, mag_r = _unpack_profile(data)
	ts = (saved_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
	doc = {
		"schema": PROFILE_SCHEMA_VERSION,
		"saved_at": ts.isoformat(timespec="seconds"),
		"accel_offset": list(accel_off),
		"mag_offset": list(mag_off),
		"gyro_offset": list(gyro_off),
		"accel_radius": accel_r,
		"mag_radius": mag_r,
		"raw_hex": data.hex(),
	}
	if calibration_status is not None:
		s, g, a, m = calibration_status
		doc["calibration_at_save"] = f"sys={s} gyr={g} acc={a} mag={m}"
	return doc


def profile_from_dict(doc: dict) -> bytes:
	"""Omgekeerde richting van :func:`profile_to_dict`.

	We vertrouwen in eerste instantie op ``raw_hex`` (ground truth). Als dat
	veld ontbreekt of corrupt is, reconstrueren we de blob uit de integer-
	velden — zodat handmatig-aangepaste JSONs óók werken (typisch nut:
	operator vult de default-blob in met "typische" waarden zonder eerst een
	blob te dumpen).
	"""
	schema = int(doc.get("schema", PROFILE_SCHEMA_VERSION))
	if schema != PROFILE_SCHEMA_VERSION:
		raise ValueError(
			f"BNO055 profile schema {schema} niet ondersteund (verwacht "
			f"{PROFILE_SCHEMA_VERSION}); update je code of regenereer de JSON"
		)

	raw_hex = doc.get("raw_hex")
	if isinstance(raw_hex, str) and raw_hex.strip():
		try:
			data = bytes.fromhex(raw_hex.strip())
		except ValueError as e:
			raise ValueError(f"BNO055 profile raw_hex ongeldig: {e}") from e
		if len(data) == PROFILE_LENGTH:
			return data

	# Fallback: uit integer-velden opbouwen. Handig voor handgeschreven default-
	# profielen maar ook robuust als raw_hex sneuvelde bij een merge-conflict.
	def _triple(key: str) -> Tuple[int, int, int]:
		v = doc.get(key)
		if not isinstance(v, (list, tuple)) or len(v) != 3:
			raise ValueError(f"BNO055 profile veld {key!r} moet lijst van 3 zijn")
		return int(v[0]), int(v[1]), int(v[2])

	accel = _triple("accel_offset")
	mag = _triple("mag_offset")
	gyro = _triple("gyro_offset")
	accel_r = int(doc.get("accel_radius", 0))
	mag_r = int(doc.get("mag_radius", 0))
	return struct.pack("<11h", *accel, *mag, *gyro, accel_r, mag_r)


def save_profile_file(
	path: Union[str, Path],
	data: bytes,
	*,
	calibration_status: Optional[Tuple[int, int, int, int]] = None,
) -> Path:
	"""Schrijf profiel naar ``path`` (JSON, UTF-8, 2-space indent).

	Geeft ``Path(path)`` terug; parent-dirs worden waar nodig aangemaakt zodat
	een verse ``config/``-layout gewoon werkt. Een bestaande file wordt
	overschreven (atomisch via een temp-bestand in dezelfde map zodat we nooit
	een half-geschreven JSON achterlaten).
	"""
	p = Path(path).expanduser()
	p.parent.mkdir(parents=True, exist_ok=True)
	doc = profile_to_dict(data, calibration_status=calibration_status)
	tmp = p.with_suffix(p.suffix + ".tmp")
	tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
	tmp.replace(p)
	return p


def load_profile_file(path: Union[str, Path]) -> Optional[bytes]:
	"""Lees ``path`` en geef de 22-byte blob terug; ``None`` als de file ontbreekt.

	Parse-fouten (corrupte JSON, fout schema, ongeldige raw_hex) laten we
	expliciet door — de caller kan dan loggen en desgewenst fallbacken op een
	default-profile. We slikken alleen het ``FileNotFoundError`` geval, zodat
	een vers-geïnstalleerde Zero zonder JSON geruisloos aan live calibration
	begint.
	"""
	p = Path(path).expanduser()
	try:
		raw = p.read_text(encoding="utf-8")
	except FileNotFoundError:
		return None
	doc = json.loads(raw)
	return profile_from_dict(doc)


__all__ = [
	"PROFILE_SCHEMA_VERSION",
	"PROFILE_LENGTH",
	"profile_to_dict",
	"profile_from_dict",
	"save_profile_file",
	"load_profile_file",
]
