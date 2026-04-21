"""
Minimale MicroPython-decoder voor het binary 60-byte TLM-frame.

DEZE FILE MOET LAYOUT-IDENTIEK BLIJVEN MET:
    src/cansat_hw/telemetry/codec.py

Bij elke wijziging in de Zero-codec moet hetzelfde hier gebeuren, anders
kan de Pico binary frames niet meer lezen. De wire-format is bewust
"versieloos" in de header (record_type-byte volstaat); incompatibele
wijzigingen vragen een nieuw record_type.

Geen dataclasses, geen typing, geen exceptions met message-formatting via
f-strings — allemaal voor max-compatibiliteit met MicroPython.
"""

import struct

# --- Record types -------------------------------------------------------------
RECORD_TLM = 0x01
RECORD_EVT = 0x02
RECORD_HDR = 0xF0

BINARY_FIRST_BYTE_MAX = 0x1F

# --- Mode / state nibbles -----------------------------------------------------
MODE_NAMES = {
	0x0: "CONFIG",
	0x1: "MISSION",
	0x2: "TEST",
	0xF: "UNKNOWN",
}
STATE_NAMES = {
	0x0: "NONE",
	0x1: "PAD_IDLE",
	0x2: "ASCENT",
	0x3: "DEPLOYED",
	0x4: "LANDED",
	0xF: "UNKNOWN",
}

# --- Sentinels ----------------------------------------------------------------
INT16_NA = -0x8000
UINT16_NA = 0xFFFF
# tag_id is u16 in het frame zodat IDs > 255 (tag36h11 gaat tot 586,
# bv. de missie-tags 317 en 536) zonder bit-loss kunnen worden gedecodeerd.
TAG_ID_NA = 0xFFFF

# --- Schaalfactoren -----------------------------------------------------------
SCALE_ALT_CM = 100.0
SCALE_HPA_X10 = 10.0
SCALE_T_X10 = 10.0
SCALE_DEG_X10 = 10.0
SCALE_ACCEL_MG = 1000.0
SCALE_GYRO_X10 = 10.0

NUM_TAGS = 2
# id(2) + dx(2) + dy(2) + dz(2) + size(2) = 10 B per tag. Reserved
# slinkt van 6 → 4 zodat het frame 60 B blijft. Zie Zero-codec voor
# achtergrond.
_TAG_FORMAT = "HhhhH"
_RESERVED_BYTES = 4

# Volledige frame layout — moet EXACT matchen met de Zero-codec.
FRAME_FORMAT = (
	"<BBHIHhHhHhhhhhhhhBB" + (_TAG_FORMAT * NUM_TAGS) + ("%ds" % _RESERVED_BYTES)
)
FRAME_SIZE = struct.calcsize(FRAME_FORMAT)


def is_binary_packet(b0):
	"""``True`` als de eerste byte van een radio-pakket een binary frame is."""
	try:
		v = int(b0)
	except (TypeError, ValueError):
		return False
	return 0 <= v <= BINARY_FIRST_BYTE_MAX


def mode_name(mode_int):
	return MODE_NAMES.get(int(mode_int) & 0x0F, "UNKNOWN")


def state_name(state_int):
	return STATE_NAMES.get(int(state_int) & 0x0F, "UNKNOWN")


def _i16_or_none(raw, scale):
	if raw == INT16_NA:
		return None
	return raw / scale


def _u16_or_none(raw, scale):
	if raw == UINT16_NA:
		return None
	return raw / scale


def decode_tlm(payload):
	"""
	Decodeer een 60-byte binary TLM-frame -> dict met sleutels:

	  kind         "TLM" of "BIN_UNKNOWN" als record_type onbekend is
	  record_type  raw 1-byte int
	  mode         "CONFIG" / "MISSION" / "TEST" / "UNKNOWN"
	  state        "NONE" / "PAD_IDLE" / "ASCENT" / "DEPLOYED" / "LANDED"
	  seq          int  (uint16; wraps na 65535)
	  utc          float (utc_seconds + utc_ms/1000)
	  utc_seconds  int
	  utc_ms       int
	  alt_m, pressure_hpa, temp_c                 floats of None
	  heading_deg, roll_deg, pitch_deg            floats of None
	  ax_g, ay_g, az_g                            floats of None
	  gx_dps, gy_dps, gz_dps                      floats of None
	  bno_sys_cal, bno_gyro_cal, bno_accel_cal,
	  bno_mag_cal                                 ints (0..3) of None (geen BNO)
	  tag_count    int (0..NUM_TAGS)
	  tags         list of dicts (id, dx_cm, dy_cm, dz_cm, size_mm)

	Werpt ``ValueError`` als de payload korter is dan ``FRAME_SIZE``.
	"""
	if len(payload) < FRAME_SIZE:
		raise ValueError("payload too short")
	parts = struct.unpack(FRAME_FORMAT, bytes(payload[:FRAME_SIZE]))
	(
		rec_type,
		mode_state_byte,
		seq,
		utc_s,
		utc_ms,
		alt_cm,
		p_x10,
		t_x10,
		hd_x10,
		rl_x10,
		pt_x10,
		ax_mg,
		ay_mg,
		az_mg,
		gx_x10,
		gy_x10,
		gz_x10,
		cal_b,
		tag_count,
		t0_id,
		t0_dx,
		t0_dy,
		t0_dz,
		t0_sz,
		t1_id,
		t1_dx,
		t1_dy,
		t1_dz,
		t1_sz,
		_reserved,
	) = parts

	mode_int = (mode_state_byte >> 4) & 0x0F
	state_int = mode_state_byte & 0x0F

	# BNO-cal: als alle Euler-velden sentinel zijn én cal=0 -> "geen BNO".
	hd_missing = hd_x10 == UINT16_NA
	rl_missing = rl_x10 == INT16_NA
	pt_missing = pt_x10 == INT16_NA
	if cal_b == 0 and hd_missing and rl_missing and pt_missing:
		sys_cal = gyro_cal = accel_cal = mag_cal = None
	else:
		sys_cal = (cal_b >> 6) & 0x3
		gyro_cal = (cal_b >> 4) & 0x3
		accel_cal = (cal_b >> 2) & 0x3
		mag_cal = cal_b & 0x3

	tags = []
	for tid, dx, dy, dz, sz in (
		(t0_id, t0_dx, t0_dy, t0_dz, t0_sz),
		(t1_id, t1_dx, t1_dy, t1_dz, t1_sz),
	):
		if tid != TAG_ID_NA:
			tags.append(
				{
					"id": int(tid),
					"dx_cm": int(dx),
					"dy_cm": int(dy),
					"dz_cm": int(dz),
					"size_mm": int(sz),
				}
			)

	out = {
		"kind": "TLM" if rec_type == RECORD_TLM else "BIN_UNKNOWN",
		"record_type": int(rec_type),
		"mode": mode_name(mode_int),
		"state": state_name(state_int),
		"seq": int(seq),
		"utc_seconds": int(utc_s),
		"utc_ms": int(utc_ms),
		"utc": float(utc_s) + float(utc_ms) / 1000.0,
		"alt_m": _i16_or_none(alt_cm, SCALE_ALT_CM),
		"pressure_hpa": _u16_or_none(p_x10, SCALE_HPA_X10),
		"temp_c": _i16_or_none(t_x10, SCALE_T_X10),
		"heading_deg": _u16_or_none(hd_x10, SCALE_DEG_X10),
		"roll_deg": _i16_or_none(rl_x10, SCALE_DEG_X10),
		"pitch_deg": _i16_or_none(pt_x10, SCALE_DEG_X10),
		"ax_g": _i16_or_none(ax_mg, SCALE_ACCEL_MG),
		"ay_g": _i16_or_none(ay_mg, SCALE_ACCEL_MG),
		"az_g": _i16_or_none(az_mg, SCALE_ACCEL_MG),
		"gx_dps": _i16_or_none(gx_x10, SCALE_GYRO_X10),
		"gy_dps": _i16_or_none(gy_x10, SCALE_GYRO_X10),
		"gz_dps": _i16_or_none(gz_x10, SCALE_GYRO_X10),
		"bno_sys_cal": sys_cal,
		"bno_gyro_cal": gyro_cal,
		"bno_accel_cal": accel_cal,
		"bno_mag_cal": mag_cal,
		"tag_count": len(tags),
		"tags": tags,
	}
	return out


def _fmt(v, fmt):
	if v is None:
		return "NA"
	return fmt % v


def _format_tag_brief(tag):
	"""Compacte weergave van één tag-detectie: ``<id>@<dz>m`` (dz = optische as).

	De Zero vult de tags-slots in **max_side_px-descending**-volgorde (grootste
	tag eerst, zie :class:`cansat_hw.camera.buffer.TagBuffer`), dus de eerste
	entry is automatisch de "meest prominente" tag.
	"""
	tid = tag.get("id")
	dz = tag.get("dz_cm")
	if dz is None:
		return "%s@NAm" % (tid,)
	return "%d@%.1fm" % (int(tid), float(dz) / 100.0)


def format_tlm_short(d):
	"""Eén-regelweergave voor de CLI tijdens TEST/MISSION-listen.

	Toont naast ``tags=<N>`` ook de top-2 tags (ID + afstand in meter) zodat de
	operator tijdens de missie direct ziet wélke tag gedetecteerd wordt en op
	welke afstand — bv. ``tags=2 [26@12.3m 4@45.6m]``. De Zero garandeert dat
	de lijst descending gesorteerd is op pixel-size (grootste eerst).
	"""
	base = (
		"TLM seq=%d %s/%s utc=%d.%03d "
		"alt=%s p=%s T=%s "
		"hdg=%s r=%s p=%s "
		"ax=%s ay=%s az=%s "
		"cal=%s/%s/%s/%s tags=%d"
	) % (
		d["seq"],
		d["mode"],
		d["state"],
		d["utc_seconds"],
		d["utc_ms"],
		_fmt(d["alt_m"], "%.2fm"),
		_fmt(d["pressure_hpa"], "%.2fhPa"),
		_fmt(d["temp_c"], "%.1fC"),
		_fmt(d["heading_deg"], "%.1f"),
		_fmt(d["roll_deg"], "%.1f"),
		_fmt(d["pitch_deg"], "%.1f"),
		_fmt(d["ax_g"], "%.3fg"),
		_fmt(d["ay_g"], "%.3fg"),
		_fmt(d["az_g"], "%.3fg"),
		"NA" if d["bno_sys_cal"] is None else str(d["bno_sys_cal"]),
		"NA" if d["bno_gyro_cal"] is None else str(d["bno_gyro_cal"]),
		"NA" if d["bno_accel_cal"] is None else str(d["bno_accel_cal"]),
		"NA" if d["bno_mag_cal"] is None else str(d["bno_mag_cal"]),
		d["tag_count"],
	)
	tags = d.get("tags") or []
	if tags:
		parts = [_format_tag_brief(t) for t in tags[:2]]
		base += " [" + " ".join(parts) + "]"
	return base
