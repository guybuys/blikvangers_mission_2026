"""Unit tests voor :mod:`cansat_hw.camera.buffer` (Fase 9)."""

from __future__ import annotations

import threading
import time
import unittest

from cansat_hw.camera.buffer import BufferedDetection, TagBuffer
from cansat_hw.telemetry.codec import TagDetection


def _bd(tag_id: int, side_px: float, captured_at: float = 100.0) -> BufferedDetection:
	return BufferedDetection(
		detection=TagDetection(tag_id=tag_id, dx_cm=0, dy_cm=0, dz_cm=0, size_mm=100),
		max_side_px=float(side_px),
		captured_at=float(captured_at),
	)


class TestTagBuffer(unittest.TestCase):
	def test_empty_snapshot(self) -> None:
		buf = TagBuffer()
		self.assertEqual(buf.snapshot(now=100.0), [])

	def test_update_keeps_top_two_by_size(self) -> None:
		buf = TagBuffer(max_age_s=10.0)
		buf.update(
			[
				_bd(1, 50.0, 100.0),
				_bd(2, 200.0, 100.0),
				_bd(3, 100.0, 100.0),
				_bd(4, 10.0, 100.0),
			],
			now=100.0,
		)
		snap = buf.snapshot(now=100.0)
		self.assertEqual(len(snap), 2)
		self.assertEqual(snap[0].tag_id, 2)
		self.assertEqual(snap[1].tag_id, 3)

	def test_staleness_filters_out_old_items(self) -> None:
		buf = TagBuffer(max_age_s=1.0)
		buf.update([_bd(7, 100.0, captured_at=100.0)], now=100.0)
		self.assertEqual(len(buf.snapshot(now=100.5)), 1)
		self.assertEqual(buf.snapshot(now=102.0), [])

	def test_clear_drops_items(self) -> None:
		buf = TagBuffer(max_age_s=10.0)
		buf.update([_bd(1, 100.0, 100.0)], now=100.0)
		self.assertEqual(len(buf.snapshot(now=100.0)), 1)
		buf.clear()
		self.assertEqual(buf.snapshot(now=100.0), [])

	def test_ignores_nonpositive_side(self) -> None:
		buf = TagBuffer(max_age_s=10.0)
		buf.update([_bd(1, 0.0, 100.0), _bd(2, -5.0, 100.0)], now=100.0)
		self.assertEqual(buf.snapshot(now=100.0), [])

	def test_max_age_zero_disables_staleness(self) -> None:
		buf = TagBuffer(max_age_s=0.0)
		buf.update([_bd(1, 100.0, captured_at=100.0)], now=100.0)
		self.assertEqual(len(buf.snapshot(now=10_000.0)), 1)

	def test_stats(self) -> None:
		buf = TagBuffer(max_age_s=10.0)
		buf.update([_bd(1, 100.0, 100.0), _bd(2, 50.0, 100.0)], now=100.0)
		buf.update([_bd(3, 30.0, 100.0)], now=100.0)
		s = buf.stats()
		self.assertEqual(s["frames"], 2)
		self.assertEqual(s["detections"], 3)
		self.assertEqual(s["current_count"], 1)

	def test_thread_safety_does_not_deadlock(self) -> None:
		"""Ruwe smoke-test: writer-thread en reader-thread beuken op de buffer."""

		buf = TagBuffer(max_age_s=10.0)
		stop = False

		def writer() -> None:
			i = 0
			while not stop:
				buf.update([_bd(i % 10, float(i % 100 + 1), time.monotonic())])
				i += 1

		def reader() -> None:
			while not stop:
				buf.snapshot()

		t_w = threading.Thread(target=writer)
		t_r = threading.Thread(target=reader)
		t_w.start()
		t_r.start()
		time.sleep(0.05)
		stop = True
		t_w.join(timeout=1.0)
		t_r.join(timeout=1.0)
		self.assertFalse(t_w.is_alive())
		self.assertFalse(t_r.is_alive())


if __name__ == "__main__":
	unittest.main()
