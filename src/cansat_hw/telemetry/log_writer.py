"""Binary log writer voor CanSat-telemetrie op de Zero (SD-kaart).

Schrijft elk binair payload-frame (TLM, EVT, of toekomstige record-types) als
zelf-beschrijvend record naar één of meerdere ``.bin``-bestanden:

    +----------+------------+-----------------+----------+
    | magic(2) | length(2)  | payload(len B)  |  crc(2)  |
    +----------+------------+-----------------+----------+
       0xA5 0x5A   little    de payload zelf      CRC-16
                   endian                          CCITT

Strategie (twee parallelle outputs):

* ``cansat_continuous.bin`` blijft permanent open; alle frames (CONFIG-bursts,
  TLM uit TEST/MISSION, EVT, …) gaan erdoor. Werkt als veilig "tape recorder"-
  vangnet: zelfs als een sessie-file beschadigd raakt, vinden we er nog alles in.
* Per **MISSION** of **TEST**-sessie wordt een aparte file geopend
  (``cansat_test_<UTC>.bin`` / ``cansat_mission_<UTC>.bin``); zo is één
  vlucht/dryrun makkelijk te isoleren in tools/decoder.

Elk bestand begint met een **HEADER-record** (record_type 0xF0, 64 B payload)
dat de ``FRAME_FORMAT``-string en hostname meeschrijft, zodat een latere
analyser het bestand kan decoderen zonder de codebase ernaast te hoeven.

Op een disk-full of permission error wordt de manager **eenmalig** in een
"disabled" state gezet (logt nog 1× een WARN naar ``stderr``); de hoofdlus van
de Zero blijft gewoon doorlopen en de radio-TX gaat door — logging is een
backup, geen mission-critical pad.
"""

from __future__ import annotations

import os
import socket
import struct
import sys
import time as time_mod
from pathlib import Path
from typing import IO, Iterator, Optional, Tuple, Union

from cansat_hw.telemetry.codec import (
	FRAME_FORMAT,
	FRAME_SIZE,
	RECORD_HDR,
	mode_for_string,
	state_for_mode_string,
)

# --- On-disk framing ----------------------------------------------------------
# Magic gekozen zodat het NIET kan botsen met de eerste byte van een payload
# (records starten met 0x01..0x1F of 0xF0; magic is 0xA5). Synchronisatie/
# resync na corruptie is daardoor makkelijk: zoek volgende ``LOG_MAGIC``.
LOG_MAGIC = b"\xa5\x5a"
LOG_MAGIC_SIZE = len(LOG_MAGIC)
LOG_LEN_FORMAT = "<H"
LOG_LEN_SIZE = struct.calcsize(LOG_LEN_FORMAT)
LOG_CRC_FORMAT = "<H"
LOG_CRC_SIZE = struct.calcsize(LOG_CRC_FORMAT)
LOG_OVERHEAD = LOG_MAGIC_SIZE + LOG_LEN_SIZE + LOG_CRC_SIZE  # 6 bytes per record

# Maximaal 64 KiB payload past in u16; ruim genoeg voor onze 60 B TLM en de
# 64 B HEADER. Toekomstige bulk-records (bv. AprilTag-stitch / foto-meta)
# kunnen probleemloos enkele KiB worden.
LOG_PAYLOAD_MAX = 0xFFFF

# --- HEADER (eerste record in elke file) --------------------------------------
HEADER_VERSION = 1
HEADER_FORMAT_STR_LEN = 40
HEADER_HOSTNAME_LEN = 14
HEADER_FORMAT = "<BBBBIH%ds%ds" % (HEADER_FORMAT_STR_LEN, HEADER_HOSTNAME_LEN)
HEADER_PAYLOAD_SIZE = struct.calcsize(HEADER_FORMAT)
assert HEADER_PAYLOAD_SIZE == 64, (
	"HEADER_PAYLOAD_SIZE = %d, verwacht 64" % HEADER_PAYLOAD_SIZE
)


def crc16_ccitt(data: bytes, *, init: int = 0xFFFF) -> int:
	"""CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, geen reflectie/XOR-out).

	Snel, draagbaar en standaard genoeg dat een buitenstaander hetzelfde
	resultaat reproduceert. Bewust geen ``binascii``-variant: die is afwezig
	op MicroPython en we willen de Pico-decoder identiek kunnen houden.
	"""
	crc = int(init) & 0xFFFF
	for byte in data:
		crc ^= (int(byte) & 0xFF) << 8
		for _ in range(8):
			if crc & 0x8000:
				crc = ((crc << 1) ^ 0x1021) & 0xFFFF
			else:
				crc = (crc << 1) & 0xFFFF
	return crc


def pack_log_record(payload: bytes) -> bytes:
	"""Wikkel ``payload`` in magic+len+payload+crc en retourneer de bytes."""
	if not isinstance(payload, (bytes, bytearray, memoryview)):
		raise TypeError("payload must be bytes-like")
	pl = bytes(payload)
	if len(pl) > LOG_PAYLOAD_MAX:
		raise ValueError(
			"payload too large: %d > %d" % (len(pl), LOG_PAYLOAD_MAX)
		)
	header = LOG_MAGIC + struct.pack(LOG_LEN_FORMAT, len(pl))
	crc = crc16_ccitt(pl)
	return header + pl + struct.pack(LOG_CRC_FORMAT, crc)


def _utc_split(now_wall: Optional[float] = None) -> Tuple[int, int]:
	"""Split ``time.time()`` (of meegegeven waarde) in (epoch_s, ms 0..999)."""
	if now_wall is None:
		now_wall = time_mod.time()
	utc_seconds = int(now_wall)
	utc_ms = int(round((now_wall - utc_seconds) * 1000.0))
	if utc_ms >= 1000:
		utc_seconds += 1
		utc_ms = 0
	if utc_ms < 0:
		utc_ms = 0
	return utc_seconds, utc_ms


def build_header_payload(
	*,
	mode: str = "CONFIG",
	now_wall: Optional[float] = None,
	hostname: Optional[str] = None,
) -> bytes:
	"""Bouw één 64-byte HEADER-payload (record_type 0xF0).

	De ``frame_format`` en ``frame_size`` worden meegeschreven zodat een
	off-line decoder geen versie-hardcode hoeft te hebben — als wij later het
	frame uitbreiden, weet die welke layout dit bestand heeft.
	"""
	utc_s, utc_ms = _utc_split(now_wall)
	hn_raw = (
		hostname if hostname is not None else (socket.gethostname() or "unknown")
	)
	mode_state_byte = (
		(mode_for_string(mode) & 0x0F) << 4
	) | (state_for_mode_string(mode) & 0x0F)
	# encode + truncate naar de vaste velden (struct '%ds' nullt automatisch).
	fmt_bytes = FRAME_FORMAT.encode("ascii", errors="replace")[:HEADER_FORMAT_STR_LEN]
	hn_bytes = hn_raw.encode("ascii", errors="replace")[:HEADER_HOSTNAME_LEN]
	return struct.pack(
		HEADER_FORMAT,
		RECORD_HDR,
		HEADER_VERSION,
		mode_state_byte & 0xFF,
		FRAME_SIZE & 0xFF,
		utc_s & 0xFFFFFFFF,
		utc_ms & 0xFFFF,
		fmt_bytes,
		hn_bytes,
	)


def parse_header_payload(payload: bytes) -> dict:
	"""Inverse van :func:`build_header_payload`. Werpt ValueError op rommel."""
	if len(payload) < HEADER_PAYLOAD_SIZE:
		raise ValueError(
			"header too short: %d < %d" % (len(payload), HEADER_PAYLOAD_SIZE)
		)
	(
		rec_type,
		ver,
		mode_state,
		frame_size,
		utc_s,
		utc_ms,
		fmt_b,
		hn_b,
	) = struct.unpack(HEADER_FORMAT, payload[:HEADER_PAYLOAD_SIZE])
	if rec_type != RECORD_HDR:
		raise ValueError("not a HEADER record (rt=0x%02X)" % rec_type)
	return {
		"record_type": int(rec_type),
		"header_version": int(ver),
		"mode_state": int(mode_state),
		"frame_size": int(frame_size),
		"utc_seconds": int(utc_s),
		"utc_ms": int(utc_ms),
		"frame_format": fmt_b.rstrip(b"\x00").decode("ascii", errors="replace"),
		"hostname": hn_b.rstrip(b"\x00").decode("ascii", errors="replace"),
	}


def iter_records(stream: IO[bytes]) -> Iterator[Tuple[int, bytes, bool]]:
	"""Itereer log-records uit een binary stream.

	Yields ``(offset, payload, ok)`` per gevonden record. ``ok`` is False als de
	CRC niet klopt (we yielden 'm wel zodat tooling kan beslissen om door te
	gaan of te stoppen). Synchroniseert opnieuw bij rommel: skipt 1 byte en
	zoekt verder naar :data:`LOG_MAGIC`.
	"""
	# 1-byte buffer + glij door het bestand. We lezen de magic+len in één keer
	# om I/O calls te beperken; bij mismatch sliden we 1 byte verder.
	while True:
		offset = stream.tell()
		head = stream.read(LOG_MAGIC_SIZE + LOG_LEN_SIZE)
		if len(head) < LOG_MAGIC_SIZE + LOG_LEN_SIZE:
			return  # EOF
		if head[:LOG_MAGIC_SIZE] != LOG_MAGIC:
			# Resync: 1 byte vooruit en opnieuw proberen.
			stream.seek(offset + 1)
			continue
		(length,) = struct.unpack(
			LOG_LEN_FORMAT, head[LOG_MAGIC_SIZE : LOG_MAGIC_SIZE + LOG_LEN_SIZE]
		)
		if length > LOG_PAYLOAD_MAX:
			stream.seek(offset + 1)
			continue
		body = stream.read(length + LOG_CRC_SIZE)
		if len(body) < length + LOG_CRC_SIZE:
			return  # truncated tail
		payload = body[:length]
		(crc_disk,) = struct.unpack(LOG_CRC_FORMAT, body[length : length + LOG_CRC_SIZE])
		ok = crc_disk == crc16_ccitt(payload)
		yield offset, payload, ok


# --- Per-bestand sessie -------------------------------------------------------
class LogSession:
	"""Eén open ``.bin``-bestand met magic+len+payload+crc-records."""

	def __init__(
		self,
		path: Union[str, Path],
		*,
		header_payload: Optional[bytes] = None,
		flush_each: bool = True,
	) -> None:
		self.path = Path(path)
		self.flush_each = bool(flush_each)
		self._fp: Optional[IO[bytes]] = None
		self._bytes_written = 0
		self._records_written = 0
		self.path.parent.mkdir(parents=True, exist_ok=True)
		# Append-mode zodat een herstart niets overschrijft; nieuwe sessie =
		# nieuwe naam dus dit komt vooral van pas voor ``cansat_continuous.bin``.
		self._fp = open(str(self.path), "ab")
		if header_payload is not None and self._fp.tell() == 0:
			self._write_raw(pack_log_record(header_payload))

	@property
	def open(self) -> bool:
		return self._fp is not None

	@property
	def bytes_written(self) -> int:
		return self._bytes_written

	@property
	def records_written(self) -> int:
		return self._records_written

	def _write_raw(self, data: bytes) -> int:
		assert self._fp is not None
		n = self._fp.write(data)
		self._bytes_written += n or 0
		self._records_written += 1
		if self.flush_each:
			self._fp.flush()
		return n or 0

	def append(self, payload: bytes) -> int:
		"""Schrijf één frame; werpt OSError door naar de caller bij disk-full."""
		if self._fp is None:
			raise OSError("session closed")
		return self._write_raw(pack_log_record(payload))

	def close(self) -> None:
		if self._fp is None:
			return
		try:
			self._fp.flush()
			try:
				os.fsync(self._fp.fileno())
			except (OSError, AttributeError, ValueError):
				pass
		finally:
			try:
				self._fp.close()
			except OSError:
				pass
			self._fp = None


# --- Multi-file orchestrator --------------------------------------------------
def _ts_for_filename(now_wall: Optional[float] = None) -> str:
	"""``20260419T084941Z`` (UTC, tekenset veilig in alle FS)."""
	if now_wall is None:
		now_wall = time_mod.time()
	return time_mod.strftime("%Y%m%dT%H%M%SZ", time_mod.gmtime(now_wall))


class LogManager:
	"""Beheert het continuous-bestand + één sessie-bestand per MISSION/TEST.

	De caller (radio-loop) hoeft alleen te roepen:

	* :meth:`on_mode_change` bij elke mode-overgang
	* :meth:`write_payload` per binary frame dat de Zero genereert
	* :meth:`close` bij shutdown

	Disk-full / I/O-fout zet ``self.enabled = False`` zodat verdere calls
	stilletjes no-ops zijn — de radio-loop loopt door alsof er niets gebeurd is.
	"""

	def __init__(
		self,
		log_dir: Union[str, Path],
		*,
		hostname: Optional[str] = None,
		enabled: bool = True,
		flush_each: bool = True,
	) -> None:
		self.log_dir = Path(log_dir).expanduser()
		self.hostname = hostname or (socket.gethostname() or "unknown")
		self.enabled = bool(enabled)
		self.flush_each = bool(flush_each)
		self._continuous: Optional[LogSession] = None
		self._session: Optional[LogSession] = None
		self._session_mode: Optional[str] = None
		self._warned_disk_full = False
		if not self.enabled:
			return
		try:
			self.log_dir.mkdir(parents=True, exist_ok=True)
			self._continuous = self._open_session(
				self.log_dir / "cansat_continuous.bin",
				mode="CONFIG",
			)
		except OSError as e:
			self._disable("could not init log dir: %s" % e)

	@property
	def session_path(self) -> Optional[Path]:
		return None if self._session is None else self._session.path

	@property
	def continuous_path(self) -> Optional[Path]:
		return None if self._continuous is None else self._continuous.path

	def _open_session(self, path: Path, *, mode: str) -> LogSession:
		header = build_header_payload(mode=mode, hostname=self.hostname)
		return LogSession(path, header_payload=header, flush_each=self.flush_each)

	def _disable(self, reason: str) -> None:
		if not self._warned_disk_full:
			print("WARN: log writer disabled (%s)" % reason, file=sys.stderr)
			self._warned_disk_full = True
		self.enabled = False
		# Sessies veilig sluiten zodat een latere ``close()`` niet meer probeert.
		try:
			if self._continuous is not None:
				self._continuous.close()
		except OSError:
			pass
		try:
			if self._session is not None:
				self._session.close()
		except OSError:
			pass
		self._continuous = None
		self._session = None
		self._session_mode = None

	def on_mode_change(self, old_mode: str, new_mode: str) -> None:
		"""Open/sluit het sessie-bestand bij een mode-overgang.

		Open een nieuwe sessie bij overgang **naar** TEST of MISSION; sluit
		een lopende sessie zodra we naar CONFIG (of een andere niet-loggende
		state) terug gaan. Het continuous-bestand blijft altijd open.
		"""
		if not self.enabled:
			return
		old = (old_mode or "").upper()
		new = (new_mode or "").upper()
		if old == new:
			return
		if new in ("TEST", "MISSION"):
			# Sluit een eventuele oude sessie eerst — defensive (zou niet mogen
			# gebeuren; TEST→MISSION zonder tussen-CONFIG is verboden).
			self._close_session_safe()
			ts = _ts_for_filename()
			fname = "cansat_%s_%s.bin" % (new.lower(), ts)
			try:
				self._session = self._open_session(self.log_dir / fname, mode=new)
				self._session_mode = new
			except OSError as e:
				self._disable("could not open session %s: %s" % (fname, e))
		else:
			# CONFIG (of UNKNOWN) — sluit lopende sessie.
			self._close_session_safe()

	def _close_session_safe(self) -> None:
		if self._session is None:
			return
		try:
			self._session.close()
		except OSError as e:
			# We disablen niet bij een sluit-fout; logging kan vrolijk verder.
			print("WARN: log session close failed: %s" % e, file=sys.stderr)
		self._session = None
		self._session_mode = None

	def write_payload(self, payload: bytes) -> bool:
		"""Schrijf ``payload`` naar continuous + actieve sessie.

		Retourneert True als het minstens één keer succesvol weggeschreven
		is. Op OSError tijdens write wordt de manager gedisabled (eenmalige
		WARN); volgende calls zijn no-ops.
		"""
		if not self.enabled or not payload:
			return False
		wrote_any = False
		if self._continuous is not None:
			try:
				self._continuous.append(payload)
				wrote_any = True
			except OSError as e:
				self._disable("continuous write failed: %s" % e)
				return False
		if self._session is not None:
			try:
				self._session.append(payload)
				wrote_any = True
			except OSError as e:
				# Sessie kapot maar continuous werkt nog → sluit alleen sessie.
				print("WARN: log session write failed: %s" % e, file=sys.stderr)
				try:
					self._session.close()
				except OSError:
					pass
				self._session = None
				self._session_mode = None
		return wrote_any

	def close(self) -> None:
		self._close_session_safe()
		if self._continuous is not None:
			try:
				self._continuous.close()
			except OSError:
				pass
			self._continuous = None
		self.enabled = False
