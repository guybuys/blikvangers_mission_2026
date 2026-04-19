"""Binary telemetry codec — 60-byte TLM-frame voor de RFM69 link.

Frame-layout (little-endian, exact 60 bytes):

  offset  size  type   veld                  eenheid          opmerking
  ------  ----  -----  --------------------  ---------------  --------------
    0      1   u8     record_type           1=TLM, 2=EVT     >= 0x20 = ASCII
    1      1   u8     mode_state            high nib=mode    low nib=state
    2      2   u16    seq                   #                wraps @ 65535
    4      4   u32    utc_seconds           s                Unix epoch
    8      2   u16    utc_ms                ms               0..999
   10      2   i16    alt_cm                cm               +-327 m
   12      2   u16    p_hpa_x10             0.1 hPa          0..6553.5
   14      2   i16    T_x10                 0.1 °C
   16      2   u16    heading_x10           0.1°             0..359.9
   18      2   i16    roll_x10              0.1°
   20      2   i16    pitch_x10             0.1°
   22      2   i16    ax_mg                 0.001 g          +-32 g
   24      2   i16    ay_mg
   26      2   i16    az_mg
   28      2   i16    gx_dps_x10            0.1 °/s
   30      2   i16    gy_dps_x10
   32      2   i16    gz_dps_x10
   34      1   u8     cal_pack              2b sys|gy|ac|mg  altijd geldig*
   35      1   u8     tag_count             0..2
   36      9   ...    tag[0]                BhhhH            id+dx+dy+dz+sz
   45      9   ...    tag[1]                BhhhH
   54      6   bytes  reserved              0xFF*6           toekomst (GPS/bat)
                                                              = 60 bytes

Sentinels voor "ontbrekend":
  i16:  -32768 (0x8000)        u16: 65535 (0xFFFF)
  u8:   0xFF (tag_id)

(*) cal_pack heeft géén sentinel: alle 4 velden zijn 0..3 en de packing
    (s<<6)|(g<<4)|(a<<2)|m kan elke byte 0x00..0xFF produceren. "Geen
    BNO055 info" wordt afgeleid uit de overige BNO-velden (Euler/accel
    = ``None``); de cal_pack-byte staat dan op 0x00.

De **eerste byte** van een radio-pakket bepaalt of het binary is: alle
record-types liggen in 0x00..0x1F (record < 0x20). ASCII-replies beginnen
met 'O' (OK), 'E' (ERR), 'T' (TLM, oude tekst-variant), … — dus >= 0x40.
Zo kan de Pico zonder extra header onderscheiden, en blijft de wire-API
volledig backward-compatible voor bestaande tekst-commando's.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# --- Record types (1ste byte) -------------------------------------------------
RECORD_TLM = 0x01
RECORD_EVT = 0x02
RECORD_HDR = 0xF0  # alleen voor log-bestanden, gaat NIET over de radio.

# Alles met eerste byte < 0x20 wordt als binary frame behandeld; >= 0x20 = ASCII.
BINARY_FIRST_BYTE_MAX = 0x1F

# --- System mode (high nibble van mode_state) --------------------------------
MODE_CONFIG = 0x0
MODE_MISSION = 0x1
MODE_TEST = 0x2
MODE_UNKNOWN = 0xF

MODE_NAMES = {
	MODE_CONFIG: "CONFIG",
	MODE_MISSION: "MISSION",
	MODE_TEST: "TEST",
	MODE_UNKNOWN: "UNKNOWN",
}

# Inverse lookup: tekst-naam (zoals in ``RadioRuntimeState.mode``) -> waarde.
_MODE_BY_NAME = {v: k for k, v in MODE_NAMES.items()}

# --- Flight state (low nibble van mode_state) ---------------------------------
STATE_NONE = 0x0
STATE_PAD_IDLE = 0x1
STATE_ASCENT = 0x2
STATE_DEPLOYED = 0x3
STATE_LANDED = 0x4
STATE_UNKNOWN = 0xF

STATE_NAMES = {
	STATE_NONE: "NONE",
	STATE_PAD_IDLE: "PAD_IDLE",
	STATE_ASCENT: "ASCENT",
	STATE_DEPLOYED: "DEPLOYED",
	STATE_LANDED: "LANDED",
	STATE_UNKNOWN: "UNKNOWN",
}

# --- Sentinels ----------------------------------------------------------------
INT16_NA = -0x8000  # -32768
UINT16_NA = 0xFFFF  # 65535
UINT8_NA = 0xFF
TAG_ID_NA = 0xFF

# --- Schaalfactoren (waarde * factor -> integer dat we packen) ----------------
SCALE_ALT_CM = 100.0  # 1.23 m -> 123 cm
SCALE_HPA_X10 = 10.0  # 1019.32 hPa -> 10193 (0.1 hPa precisie)
SCALE_T_X10 = 10.0  # 21.5 °C -> 215
SCALE_DEG_X10 = 10.0  # 123.4° -> 1234
SCALE_ACCEL_MG = 1000.0  # 1.234 g -> 1234 mg
SCALE_GYRO_X10 = 10.0  # 12.3 °/s -> 123

# --- Tags ---------------------------------------------------------------------
NUM_TAGS = 2
_TAG_FORMAT = "BhhhH"  # id(1) + dx(2) + dy(2) + dz(2) + size(2) = 9 B
_RESERVED_BYTES = 6

# Volledige frame layout. Match dit EXACT op de Pico-decoder
# (``pico_files/Orginele cansat/RadioReceiver/tlm_decode.py``).
FRAME_FORMAT = (
	"<BBHIHhHhHhhhhhhhhBB"
	+ (_TAG_FORMAT * NUM_TAGS)
	+ ("%ds" % _RESERVED_BYTES)
)
FRAME_SIZE = struct.calcsize(FRAME_FORMAT)
assert FRAME_SIZE == 60, "FRAME_SIZE = %d, verwacht 60" % FRAME_SIZE


# --- Helpers: mode/state -----------------------------------------------------
def pack_mode_state(mode: int, state: int) -> int:
	"""High nibble = mode, low nibble = flight state."""
	return ((int(mode) & 0x0F) << 4) | (int(state) & 0x0F)


def unpack_mode_state(byte: int) -> Tuple[int, int]:
	"""Inverse van :func:`pack_mode_state` -> ``(mode, state)``."""
	b = int(byte) & 0xFF
	return (b >> 4) & 0x0F, b & 0x0F


def mode_name(mode: int) -> str:
	return MODE_NAMES.get(int(mode) & 0x0F, "UNKNOWN")


def state_name(state: int) -> str:
	return STATE_NAMES.get(int(state) & 0x0F, "UNKNOWN")


def mode_for_string(mode_str: str) -> int:
	"""Zet ``RadioRuntimeState.mode`` (str) om naar de mode-nibble."""
	return _MODE_BY_NAME.get((mode_str or "").upper(), MODE_UNKNOWN)


def state_for_mode_string(mode_str: str) -> int:
	"""Default-flight-state per system mode (zolang Fase 8 niet live is).

	- ``CONFIG`` -> ``NONE``
	- ``MISSION`` -> ``PAD_IDLE`` (placeholder; echte tracking volgt later)
	- ``TEST`` -> ``DEPLOYED`` (TEST is een dry-run van DEPLOYED)
	"""
	m = (mode_str or "").upper()
	if m == "TEST":
		return STATE_DEPLOYED
	if m == "MISSION":
		return STATE_PAD_IDLE
	return STATE_NONE


# --- Helpers: BNO055 calibratie pakken in 1 byte ------------------------------
# Geen sentinel: 4 velden van 0..3 vullen exact 8 bits, dus elke byte 0x00..0xFF
# kan een legitieme waarde voorstellen. "Geen BNO055" -> 0x00 byte; de aanroeper
# (bv. ``build_telemetry_packet``) zet de overige BNO-velden dan op ``None`` en
# de Pico kan zo onderscheid maken tussen "alles op nul" en "geen sensor".
def pack_cal(
	sys_cal: Optional[int],
	gyro_cal: Optional[int] = None,
	accel_cal: Optional[int] = None,
	mag_cal: Optional[int] = None,
) -> int:
	"""Pak vier 0..3-waarden in 1 byte. ``None`` wordt als 0 verpakt."""
	s = max(0, min(3, int(sys_cal or 0)))
	g = max(0, min(3, int(gyro_cal or 0)))
	a = max(0, min(3, int(accel_cal or 0)))
	m = max(0, min(3, int(mag_cal or 0)))
	return (s << 6) | (g << 4) | (a << 2) | m


def unpack_cal(b: int) -> Tuple[int, int, int, int]:
	"""Inverse: ``(sys, gyro, accel, mag)`` elk 0..3."""
	bb = int(b) & 0xFF
	return (bb >> 6) & 0x3, (bb >> 4) & 0x3, (bb >> 2) & 0x3, bb & 0x3


# --- Helpers: scalar packing met sentinel-behandeling -------------------------
def _scale_int16(v: Optional[float], scale: float) -> int:
	if v is None:
		return INT16_NA
	try:
		x = int(round(float(v) * scale))
	except (TypeError, ValueError):
		return INT16_NA
	if x <= -0x8000:
		return -0x7FFF  # net binnen bereik; voorkom collision met INT16_NA
	if x >= 0x7FFF:
		return 0x7FFF
	return x


def _scale_uint16(v: Optional[float], scale: float) -> int:
	if v is None:
		return UINT16_NA
	try:
		x = int(round(float(v) * scale))
	except (TypeError, ValueError):
		return UINT16_NA
	if x < 0:
		return 0
	if x >= 0xFFFF:
		return 0xFFFE  # voorkom collision met UINT16_NA
	return x


def _decode_int16(raw: int, scale: float) -> Optional[float]:
	if int(raw) == INT16_NA:
		return None
	return float(raw) / scale


def _decode_uint16(raw: int, scale: float) -> Optional[float]:
	if int(raw) == UINT16_NA:
		return None
	return float(raw) / scale


def is_binary_packet(b0: int) -> bool:
	"""``True`` als de eerste byte van een pakket op een binary frame wijst."""
	try:
		return 0 <= int(b0) <= BINARY_FIRST_BYTE_MAX
	except (TypeError, ValueError):
		return False


# --- Tags --------------------------------------------------------------------
@dataclass
class TagDetection:
	"""Eén AprilTag-detectie zoals doorgegeven aan :func:`pack_tlm`."""

	tag_id: int
	dx_cm: int = 0
	dy_cm: int = 0
	dz_cm: int = 0
	size_mm: int = 0


# --- Decoded frame ------------------------------------------------------------
@dataclass
class TelemetryFrame:
	"""Mens-leesbare weergave van een gedecodeerd TLM-frame."""

	record_type: int
	mode: int
	state: int
	seq: int
	utc_seconds: int
	utc_ms: int
	alt_m: Optional[float]
	pressure_hpa: Optional[float]
	temp_c: Optional[float]
	heading_deg: Optional[float]
	roll_deg: Optional[float]
	pitch_deg: Optional[float]
	ax_g: Optional[float]
	ay_g: Optional[float]
	az_g: Optional[float]
	gx_dps: Optional[float]
	gy_dps: Optional[float]
	gz_dps: Optional[float]
	sys_cal: Optional[int]
	gyro_cal: Optional[int]
	accel_cal: Optional[int]
	mag_cal: Optional[int]
	tags: List[TagDetection] = field(default_factory=list)

	@property
	def mode_label(self) -> str:
		return mode_name(self.mode)

	@property
	def state_label(self) -> str:
		return state_name(self.state)

	@property
	def utc(self) -> float:
		"""Unix epoch als float (seconden + ms/1000)."""
		return float(self.utc_seconds) + float(self.utc_ms) / 1000.0

	def to_dict(self) -> dict:
		out = {
			"kind": "TLM",
			"record_type": self.record_type,
			"mode": self.mode_label,
			"state": self.state_label,
			"seq": self.seq,
			"utc": self.utc,
			"utc_seconds": self.utc_seconds,
			"utc_ms": self.utc_ms,
			"alt_m": self.alt_m,
			"pressure_hpa": self.pressure_hpa,
			"temp_c": self.temp_c,
			"heading_deg": self.heading_deg,
			"roll_deg": self.roll_deg,
			"pitch_deg": self.pitch_deg,
			"ax_g": self.ax_g,
			"ay_g": self.ay_g,
			"az_g": self.az_g,
			"gx_dps": self.gx_dps,
			"gy_dps": self.gy_dps,
			"gz_dps": self.gz_dps,
			"bno_sys_cal": self.sys_cal,
			"bno_gyro_cal": self.gyro_cal,
			"bno_accel_cal": self.accel_cal,
			"bno_mag_cal": self.mag_cal,
			"tag_count": len(self.tags),
		}
		if self.tags:
			out["tags"] = [
				{
					"id": t.tag_id,
					"dx_cm": t.dx_cm,
					"dy_cm": t.dy_cm,
					"dz_cm": t.dz_cm,
					"size_mm": t.size_mm,
				}
				for t in self.tags
			]
		return out


# --- Pack / unpack ------------------------------------------------------------
def pack_tlm(
	*,
	mode: int,
	state: int,
	seq: int,
	utc_seconds: int,
	utc_ms: int,
	alt_m: Optional[float] = None,
	pressure_hpa: Optional[float] = None,
	temp_c: Optional[float] = None,
	heading_deg: Optional[float] = None,
	roll_deg: Optional[float] = None,
	pitch_deg: Optional[float] = None,
	ax_g: Optional[float] = None,
	ay_g: Optional[float] = None,
	az_g: Optional[float] = None,
	gx_dps: Optional[float] = None,
	gy_dps: Optional[float] = None,
	gz_dps: Optional[float] = None,
	sys_cal: Optional[int] = None,
	gyro_cal: Optional[int] = None,
	accel_cal: Optional[int] = None,
	mag_cal: Optional[int] = None,
	tags: Optional[List[TagDetection]] = None,
) -> bytes:
	"""Bouw één 60-byte binary TLM-frame.

	Ontbrekende waarden mogen ``None`` zijn; die krijgen het juiste sentinel-
	patroon mee zodat de decoder ze terug als ``None`` kan rapporteren.
	"""
	tags_use = list(tags or [])[:NUM_TAGS]

	values = [
		RECORD_TLM,
		pack_mode_state(mode, state),
		int(seq) & 0xFFFF,
		int(utc_seconds) & 0xFFFFFFFF,
		max(0, min(999, int(utc_ms))),
		_scale_int16(alt_m, SCALE_ALT_CM),
		_scale_uint16(pressure_hpa, SCALE_HPA_X10),
		_scale_int16(temp_c, SCALE_T_X10),
		_scale_uint16(heading_deg, SCALE_DEG_X10),
		_scale_int16(roll_deg, SCALE_DEG_X10),
		_scale_int16(pitch_deg, SCALE_DEG_X10),
		_scale_int16(ax_g, SCALE_ACCEL_MG),
		_scale_int16(ay_g, SCALE_ACCEL_MG),
		_scale_int16(az_g, SCALE_ACCEL_MG),
		_scale_int16(gx_dps, SCALE_GYRO_X10),
		_scale_int16(gy_dps, SCALE_GYRO_X10),
		_scale_int16(gz_dps, SCALE_GYRO_X10),
		pack_cal(sys_cal, gyro_cal, accel_cal, mag_cal),
		len(tags_use) & 0xFF,
	]
	for i in range(NUM_TAGS):
		if i < len(tags_use):
			t = tags_use[i]
			values.extend(
				[
					int(t.tag_id) & 0xFF,
					max(-0x8000, min(0x7FFF, int(t.dx_cm))),
					max(-0x8000, min(0x7FFF, int(t.dy_cm))),
					max(-0x8000, min(0x7FFF, int(t.dz_cm))),
					max(0, min(0xFFFF, int(t.size_mm))),
				]
			)
		else:
			values.extend([TAG_ID_NA, INT16_NA, INT16_NA, INT16_NA, UINT16_NA])
	values.append(b"\xff" * _RESERVED_BYTES)

	return struct.pack(FRAME_FORMAT, *values)


def unpack_tlm(payload: bytes) -> TelemetryFrame:
	"""Decodeer een 60-byte binary TLM-frame naar :class:`TelemetryFrame`.

	Werpt :class:`ValueError` als de payload te kort is. Bytes na de eerste
	60 worden genegeerd zodat optionele framing/CRC in log-bestanden geen
	probleem geeft.
	"""
	if len(payload) < FRAME_SIZE:
		raise ValueError(
			"payload too short: %d < %d" % (len(payload), FRAME_SIZE)
		)
	parts = struct.unpack(FRAME_FORMAT, payload[:FRAME_SIZE])
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
	mode_v, state_v = unpack_mode_state(mode_state_byte)
	sys_cal_v, gyro_cal_v, accel_cal_v, mag_cal_v = unpack_cal(cal_b)
	# BNO055 ontbreekt = euler-velden allemaal sentinel én cal_pack == 0;
	# dan rapporteren we de cal-waarden ook als ``None`` zodat de Pico/log
	# duidelijk ziet "geen BNO" i.p.v. "alles op 0".
	hd_missing = int(hd_x10) == UINT16_NA
	rl_missing = int(rl_x10) == INT16_NA
	pt_missing = int(pt_x10) == INT16_NA
	if cal_b == 0 and hd_missing and rl_missing and pt_missing:
		sys_cal = gyro_cal = accel_cal = mag_cal = None
	else:
		sys_cal = sys_cal_v
		gyro_cal = gyro_cal_v
		accel_cal = accel_cal_v
		mag_cal = mag_cal_v

	tags: List[TagDetection] = []
	for tid, dx, dy, dz, sz in (
		(t0_id, t0_dx, t0_dy, t0_dz, t0_sz),
		(t1_id, t1_dx, t1_dy, t1_dz, t1_sz),
	):
		if int(tid) != TAG_ID_NA:
			tags.append(
				TagDetection(
					tag_id=int(tid),
					dx_cm=int(dx),
					dy_cm=int(dy),
					dz_cm=int(dz),
					size_mm=int(sz),
				)
			)
	# tag_count uit het pakket is informatief; we reconstrueren via tags-list.
	_ = int(tag_count)

	return TelemetryFrame(
		record_type=int(rec_type),
		mode=int(mode_v),
		state=int(state_v),
		seq=int(seq),
		utc_seconds=int(utc_s),
		utc_ms=int(utc_ms),
		alt_m=_decode_int16(alt_cm, SCALE_ALT_CM),
		pressure_hpa=_decode_uint16(p_x10, SCALE_HPA_X10),
		temp_c=_decode_int16(t_x10, SCALE_T_X10),
		heading_deg=_decode_uint16(hd_x10, SCALE_DEG_X10),
		roll_deg=_decode_int16(rl_x10, SCALE_DEG_X10),
		pitch_deg=_decode_int16(pt_x10, SCALE_DEG_X10),
		ax_g=_decode_int16(ax_mg, SCALE_ACCEL_MG),
		ay_g=_decode_int16(ay_mg, SCALE_ACCEL_MG),
		az_g=_decode_int16(az_mg, SCALE_ACCEL_MG),
		gx_dps=_decode_int16(gx_x10, SCALE_GYRO_X10),
		gy_dps=_decode_int16(gy_x10, SCALE_GYRO_X10),
		gz_dps=_decode_int16(gz_x10, SCALE_GYRO_X10),
		sys_cal=sys_cal,
		gyro_cal=gyro_cal,
		accel_cal=accel_cal,
		mag_cal=mag_cal,
		tags=tags,
	)
