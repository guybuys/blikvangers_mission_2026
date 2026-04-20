"""Thread-safe buffer met de twee grootste AprilTag-detecties (Fase 9).

De camera-thread voegt elke frame ``update()`` detecties in; de radio-loop
(andere thread) leest ze via ``snapshot()``. Voor het TLM-frame passen er
maximaal :data:`cansat_hw.telemetry.codec.NUM_TAGS` = 2 tags, dus de buffer
bewaart telkens de **2 grootste** detecties uit de laatste frame (``size_px``
= ``max_side_px`` descending).

Design-keuzes
-------------

* **Staleness**. Een detectie mag maximaal ``max_age_s`` (default 2 s) oud
  zijn voor de radio-loop hem nog als "actueel" beschouwt. Daarna leest
  ``snapshot()`` een lege lijst en blijft het TLM-frame zonder tags. Zo
  verdwijnen tags netjes uit de telemetrie als de camera stopt of geen
  nieuwe detecties meer vindt (bv. tussen twee frames door, of tijdens
  een frame-drop).
* **Top-2 by max_side_px**. De grootste tag is meestal de dichtste en/of
  de belangrijkste (bv. de 4.5 m landingstag). Door groottesortering
  boven "volgorde van detectie" te kiezen, zien we in de TLM consequent
  de sterkste kandidaten.
* **Geen history / geen smoothing**. Simpele overwrite per frame. Het
  grondstation kan zelf over opeenvolgende TLM-frames smoothen als dat
  nodig is.
"""

from __future__ import annotations

import threading
import time as time_mod
from dataclasses import dataclass, field
from typing import List, Optional

from cansat_hw.telemetry.codec import NUM_TAGS, TagDetection


DEFAULT_MAX_AGE_S = 2.0


@dataclass
class BufferedDetection:
	"""Intern opgeslagen detectie (TagDetection + tijdstempel + pixel-size).

	``max_side_px`` wordt apart bewaard omdat we daarop sorteren (niet op
	``size_mm`` — dat is de fysieke afmeting uit de registry en is gelijk
	voor beide tags als ze dezelfde ID/familie hebben).
	"""

	detection: TagDetection
	max_side_px: float
	captured_at: float  # ``time.monotonic()``


@dataclass
class TagBuffer:
	"""Thread-safe top-2 tag-buffer met staleness-policy.

	Parameters
	----------
	max_age_s
		Hoe lang (monotonic) een detectie nog als actueel geldt. ``<= 0``
		betekent geen staleness-check (detecties blijven tot overschreven).
	now
		Injecteerbare monotonic-clock voor tests. Default
		``time.monotonic``.
	"""

	max_age_s: float = DEFAULT_MAX_AGE_S
	_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
	_items: List[BufferedDetection] = field(default_factory=list, repr=False)
	_total_frames: int = 0
	_total_detections: int = 0

	def update(
		self,
		detections: List[BufferedDetection],
		*,
		now: Optional[float] = None,
	) -> None:
		"""Vervang de buffer-inhoud door de top-2 van ``detections``.

		Aanroeper (detector) levert een lijst ``BufferedDetection`` met
		``max_side_px`` + ``captured_at`` ingevuld. Deze methode sorteert
		descending op pixel-size en bewaart max :data:`NUM_TAGS` items.
		"""

		if now is None:
			now = time_mod.monotonic()
		# Sorteer descending op pixel-size; sla top-N op, negeer de rest.
		sorted_det = sorted(
			(d for d in detections if d.max_side_px > 0),
			key=lambda d: float(d.max_side_px),
			reverse=True,
		)
		top = sorted_det[:NUM_TAGS]
		with self._lock:
			self._items = list(top)
			self._total_frames += 1
			self._total_detections += len(top)

	def clear(self) -> None:
		"""Wis alle detecties (bv. bij state-overgang uit DEPLOYED)."""

		with self._lock:
			self._items = []

	def snapshot(
		self,
		*,
		now: Optional[float] = None,
	) -> List[TagDetection]:
		"""Retourneer de niet-verouderde detecties als :class:`TagDetection`-lijst.

		De lijst is **gesorteerd** descending op pixel-size (grootste eerst)
		zodat slot 0 van het TLM-frame altijd de prominentste tag bevat.
		"""

		if now is None:
			now = time_mod.monotonic()
		max_age = float(self.max_age_s)
		out: List[TagDetection] = []
		with self._lock:
			for item in self._items:
				age = float(now) - float(item.captured_at)
				if max_age > 0 and age > max_age:
					continue
				out.append(item.detection)
		return out

	# -- Introspectie --------------------------------------------------------
	def stats(self) -> dict:
		"""Totalen sinds start (totaal frames verwerkt + detecties doorgegeven)."""

		with self._lock:
			return {
				"frames": int(self._total_frames),
				"detections": int(self._total_detections),
				"current_count": len(self._items),
			}


__all__ = [
	"DEFAULT_MAX_AGE_S",
	"BufferedDetection",
	"TagBuffer",
]
