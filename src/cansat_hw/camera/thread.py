"""CameraThread — achtergrond-thread voor Picamera2 + AprilTag (Fase 9).

Draait los van de radio-loop en schrijft detecties naar een
:class:`TagBuffer`. Start/stop-policy is **flight-state-driven**: de
radio-loop roept :meth:`CameraThread.set_active` aan bij elke iteratie; de
thread capturet alleen zolang ``active=True`` (spec: alleen in
``DEPLOYED``). In alle andere states slaapt hij op een conditie-variabele,
zodat CPU + warmte gespaard blijven.

Dependency-injection laat ons dit zonder hardware unit-testen:

* ``capture_fn`` — callable ``() -> (image_ndarray, (w, h))`` die in tests
  een vaste numpy-array teruggeeft. Op de Zero gebruiken we een wrapper
  rond ``Picamera2.capture_array("main")``.
* ``preprocess_fn`` — callable ``(image_ndarray, target_w) -> (grey_ndarray,
  scale)``. Tests kunnen hier een identity-functie inpluggen; productie
  gebruikt OpenCV (``cvtColor`` + ``resize``).
* ``detector`` — :class:`AprilTagDetector` (of een dummy met ``detect``).

De thread loopt met een **fps-cap** (``target_fps``); als de detectie
sneller dan dat is wacht hij. Dat voorkomt dat de Zero 100% CPU trekt voor
niets en dat de thread de RFM69 SPI-timing verstoort.
"""

from __future__ import annotations

import threading
import time as time_mod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from .buffer import BufferedDetection, TagBuffer
from .detector import (
	AprilTagDetector,
	compute_metrics,
	metrics_to_buffered,
)
from .registry import TagRegistry


# Default frames-per-second van de capture-loop. 7 Hz is de door de
# originele camera-code empirisch bepaalde bovengrens op een Zero 2 W bij
# ~1000 px detectie-breedte. In productie komt de loop hier zelden aan —
# de AprilTag-detectie op full-res is typisch de bottleneck.
DEFAULT_TARGET_FPS = 7.0

# Default detectie-breedte (pixels). De full-res frames worden voor de
# detector downscaled naar deze breedte; dat versnelt detectie ~16×
# t.o.v. 4056 px, zonder dat kleine tags volledig verdwijnen (we houden
# nog steeds meer dan genoeg pixels voor tag36h11 op onze missie-afmeten).
DEFAULT_DETECT_WIDTH = 1014


CaptureFn = Callable[[], Tuple[Any, Tuple[int, int]]]
PreprocessFn = Callable[[Any, int], Tuple[Any, float]]
# ``(frame, path) -> None`` — schrijft één frame als JPEG. Zelfde signatuur
# als :data:`cansat_hw.camera.services.SaveFn`; lokaal gedefinieerd om een
# circulaire import te vermijden (``services`` importeert uit ``thread``).
SaveFn = Callable[[Any, Path], None]


@dataclass
class CameraThread:
	"""Achtergrond-thread die frames capture't en tags publiceert.

	Parameters
	----------
	buffer
		:class:`TagBuffer` waarin we detecties schrijven.
	registry
		:class:`TagRegistry` voor focal length + tag sizes.
	capture_fn
		Functie die één frame capturet + de (w, h) van dat frame teruggeeft
		(op full-res).
	preprocess_fn
		Functie ``(frame, target_width) -> (grey, scale)`` die het frame
		naar greyscale + gedownscalede versie omzet.
	detector
		Object met een ``detect(grey) -> list[(tag_id, corners_px)]`` API.
	target_fps
		Bovengrens op de loop-frequentie. Default 7 Hz (zie module-docstring).
	detect_width
		Breedte (px) waarnaar ``preprocess_fn`` mag downscalen voor detectie.
	"""

	buffer: TagBuffer
	registry: TagRegistry
	capture_fn: CaptureFn
	preprocess_fn: PreprocessFn
	detector: AprilTagDetector
	target_fps: float = DEFAULT_TARGET_FPS
	detect_width: int = DEFAULT_DETECT_WIDTH
	name: str = "camera-thread"
	# -- optionele fallback: sla in DEPLOYED elke N-de frame als JPEG op
	# zodat we achteraf kunnen debuggen waarom detectie faalde. ``0`` (default)
	# = saves uit. ``save_dir`` moet dan ook gezet zijn én ``save_fn`` moet
	# geinjecteerd zijn (typisch via
	# :func:`cansat_hw.camera.services.make_opencv_jpeg_save_fn`).
	save_every_n_frames: int = 0
	save_dir: Optional[Path] = None
	save_fn: Optional[SaveFn] = None
	save_filename_prefix: str = "deploy_"

	# -- interne state (niet door aanroeper instellen) -----------------------
	_active: bool = field(default=False, init=False)
	_stop: bool = field(default=False, init=False)
	_cond: threading.Condition = field(default_factory=threading.Condition, init=False, repr=False)
	_thread: Optional[threading.Thread] = field(default=None, init=False, repr=False)
	_frames: int = field(default=0, init=False)
	_errors: int = field(default=0, init=False)
	_last_error: Optional[str] = field(default=None, init=False)
	_saved: int = field(default=0, init=False)
	_save_errors: int = field(default=0, init=False)

	# -- lifecycle -----------------------------------------------------------
	def start(self) -> None:
		"""Start de achtergrond-thread (idempotent)."""

		if self._thread is not None and self._thread.is_alive():
			return
		self._stop = False
		t = threading.Thread(target=self._run, name=self.name, daemon=True)
		self._thread = t
		t.start()

	def stop(self, *, join_timeout_s: float = 2.0) -> None:
		"""Signaleer stop + wacht max ``join_timeout_s`` op de thread."""

		with self._cond:
			self._stop = True
			self._active = False
			self._cond.notify_all()
		t = self._thread
		if t is not None and t.is_alive():
			t.join(timeout=float(join_timeout_s))

	def set_active(self, active: bool) -> None:
		"""Activeer of pauzeer de capture-loop.

		``True`` → loop draait (capture + detect). ``False`` → loop slaapt
		op de conditie-variabele tot hij weer geactiveerd wordt (of
		``stop()`` binnenkomt). Roep bij elke flight-state-transitie aan.
		"""

		new_state = bool(active)
		with self._cond:
			if self._active == new_state:
				return
			self._active = new_state
			if not new_state:
				# Bij deactivatie: buffer leegmaken zodat de radio-loop geen
				# verouderde tags meer ziet. De staleness-check in
				# :class:`TagBuffer` zou dit op termijn ook doen, maar expliciet
				# clearen voorkomt dat er nog één "schaduw"-frame meelift.
				pass
			self._cond.notify_all()
		if not new_state:
			self.buffer.clear()

	def is_active(self) -> bool:
		with self._cond:
			return bool(self._active)

	def stats(self) -> dict:
		"""Diagnose-counter snapshot."""

		with self._cond:
			return {
				"frames": int(self._frames),
				"errors": int(self._errors),
				"last_error": self._last_error,
				"active": bool(self._active),
				"saved": int(self._saved),
				"save_errors": int(self._save_errors),
			}

	# -- main loop -----------------------------------------------------------
	def _run(self) -> None:
		period = 1.0 / float(self.target_fps) if self.target_fps > 0 else 0.0
		while True:
			with self._cond:
				# Pauzeer tot active of stop.
				while not self._active and not self._stop:
					self._cond.wait(timeout=1.0)
				if self._stop:
					return

			t0 = time_mod.monotonic()
			try:
				self._run_once()
			except Exception as e:  # noqa: BLE001 — thread mag nooit crashen
				with self._cond:
					self._errors += 1
					self._last_error = str(e)

			# FPS-cap: slaap het restant van de periode, maar wordt
			# onmiddellijk wakker als er een stop/activate-signaal komt.
			if period > 0:
				elapsed = time_mod.monotonic() - t0
				sleep_for = period - elapsed
				if sleep_for > 0:
					with self._cond:
						if self._stop:
							return
						self._cond.wait(timeout=sleep_for)

	def _run_once(self) -> None:
		"""Eén capture + detect + buffer.update. Publiek voor tests."""

		frame, (image_w, image_h) = self.capture_fn()
		grey, scale = self.preprocess_fn(frame, int(self.detect_width))
		dets = self.detector.detect(grey)
		now = time_mod.monotonic()
		buffered: List[BufferedDetection] = []
		for tag_id, corners in dets:
			metrics = compute_metrics(
				tag_id=tag_id,
				corners_px=corners,
				image_w=int(image_w),
				image_h=int(image_h),
				registry=self.registry,
				detection_scale=float(scale),
			)
			if metrics is None:
				continue
			buffered.append(metrics_to_buffered(metrics, captured_at=now))
		self.buffer.update(buffered, now=now)
		with self._cond:
			self._frames += 1
			frame_idx = self._frames
		# Save-fallback: elke N-de frame (vanaf de 1ste) wegschrijven zodat
		# we achteraf in ``photos/`` kunnen kijken waarom detectie faalde.
		# Saven gebeurt nooit in de hot-path: staat uit tenzij de radio-
		# service ``save_every_n_frames > 0`` én ``save_dir`` + ``save_fn``
		# injecteert.
		if (
			self.save_every_n_frames > 0
			and self.save_dir is not None
			and self.save_fn is not None
			and frame_idx % int(self.save_every_n_frames) == 0
		):
			try:
				self.save_dir.mkdir(parents=True, exist_ok=True)
				tag_suffix = (
					"_tags-" + "-".join(str(b.detection.tag_id) for b in buffered)
					if buffered
					else ""
				)
				fname = self._build_save_filename(frame_idx, tag_suffix)
				self.save_fn(frame, self.save_dir / fname)
				with self._cond:
					self._saved += 1
			except Exception as e:  # noqa: BLE001 — save mag de detect-loop niet stoppen
				with self._cond:
					self._save_errors += 1
					self._last_error = ("SAVE " + str(e))[:80]

	def _build_save_filename(self, frame_idx: int, tag_suffix: str) -> str:
		"""``deploy_<UTC>_<fidx>[_tags-..].jpg`` — compact + chronologisch.

		Gebruikt UTC zodat de alfabetische volgorde = tijdsvolgorde,
		handig bij rsync-fetch en ls.
		"""

		t = time_mod.gmtime()
		return "%s%04d%02d%02dT%02d%02d%02dZ_%04d%s.jpg" % (
			self.save_filename_prefix,
			t.tm_year,
			t.tm_mon,
			t.tm_mday,
			t.tm_hour,
			t.tm_min,
			t.tm_sec,
			int(frame_idx) % 10000,
			tag_suffix,
		)

	# Publieke alias zodat tests zonder threading één iteratie kunnen draaien.
	def run_once(self) -> None:
		self._run_once()


__all__ = [
	"DEFAULT_TARGET_FPS",
	"DEFAULT_DETECT_WIDTH",
	"CameraThread",
	"CaptureFn",
	"PreprocessFn",
	"SaveFn",
]
