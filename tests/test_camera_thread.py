"""Unit tests voor :mod:`cansat_hw.camera.thread` (Fase 9).

We vermijden elke afhankelijkheid van Picamera2/OpenCV/apriltag door de
CameraThread met fake capture/preprocess/detector in te pluggen, en per test
``run_once()`` aan te roepen i.p.v. de achtergrond-thread te starten.
"""

from __future__ import annotations

import threading
import time
import unittest
from typing import Any, List, Tuple

from cansat_hw.camera.buffer import TagBuffer
from cansat_hw.camera.registry import TagRegistry
from cansat_hw.camera.thread import CameraThread


class FakeDetector:
	"""Returnt een scripted lijst van (tag_id, corners_px) per ``detect()``."""

	def __init__(self, scripted: List[List[Tuple[int, List[Tuple[float, float]]]]]):
		self._scripted = list(scripted)
		self.calls = 0

	def detect(self, grey: Any):
		self.calls += 1
		if not self._scripted:
			return []
		return self._scripted.pop(0)


def _identity_capture(frame: Any, w: int, h: int):
	def _cap():
		return frame, (w, h)

	return _cap


def _identity_preprocess(frame: Any, target_w: int):
	# scale=1.0, no resize. Return frame as "grey".
	return frame, 1.0


def _square_corners(cx: float, cy: float, side: float):
	h = side / 2.0
	return [
		(cx - h, cy - h),
		(cx + h, cy - h),
		(cx + h, cy + h),
		(cx - h, cy + h),
	]


class TestCameraThread(unittest.TestCase):
	def setUp(self) -> None:
		self.buf = TagBuffer(max_age_s=10.0)
		self.reg = TagRegistry(
			focal_length_mm=1.0,
			pixel_pitch_um=1.0,
			tags={},
			default_size_mm=1000,
		)

	def _make(self, detector: FakeDetector, image_wh=(4056, 3040)) -> CameraThread:
		frame = object()  # placeholder; preprocess/detect zijn fake
		return CameraThread(
			buffer=self.buf,
			registry=self.reg,
			capture_fn=_identity_capture(frame, image_wh[0], image_wh[1]),
			preprocess_fn=_identity_preprocess,
			detector=detector,
			target_fps=100.0,
		)

	def test_run_once_publishes_top_two(self) -> None:
		det = FakeDetector(
			[
				[
					(1, _square_corners(2028.0, 1520.0, 50.0)),
					(2, _square_corners(2028.0, 1520.0, 200.0)),
					(3, _square_corners(2028.0, 1520.0, 100.0)),
				]
			]
		)
		cam = self._make(det)
		cam.run_once()
		snap = self.buf.snapshot()
		self.assertEqual([t.tag_id for t in snap], [2, 3])

	def test_no_detections_clears_current(self) -> None:
		det = FakeDetector([[(1, _square_corners(2028.0, 1520.0, 50.0))], []])
		cam = self._make(det)
		cam.run_once()
		self.assertEqual(len(self.buf.snapshot()), 1)
		cam.run_once()
		self.assertEqual(len(self.buf.snapshot()), 0)

	def test_set_active_clears_buffer(self) -> None:
		det = FakeDetector([[(1, _square_corners(2028.0, 1520.0, 50.0))]])
		cam = self._make(det)
		# Activate eerst (anders is False->False een no-op en wordt er niet gecleared).
		cam.set_active(True)
		cam.run_once()
		self.assertEqual(len(self.buf.snapshot()), 1)
		cam.set_active(False)
		self.assertEqual(self.buf.snapshot(), [])
		self.assertFalse(cam.is_active())
		cam.set_active(True)
		self.assertTrue(cam.is_active())

	def test_errors_counted_without_crash(self) -> None:
		class BadDetector:
			def detect(self, grey: Any):
				raise RuntimeError("oops")

		cam = CameraThread(
			buffer=self.buf,
			registry=self.reg,
			capture_fn=_identity_capture(object(), 4056, 3040),
			preprocess_fn=_identity_preprocess,
			detector=BadDetector(),  # type: ignore[arg-type]
			target_fps=100.0,
		)
		# We gebruiken de achtergrond-thread hier specifiek om de
		# exception-handler in _run te exerceren.
		cam.start()
		cam.set_active(True)
		# Wacht tot de thread minstens één error heeft geregistreerd.
		deadline = time.monotonic() + 2.0
		while time.monotonic() < deadline and cam.stats()["errors"] == 0:
			time.sleep(0.02)
		cam.stop()
		self.assertGreaterEqual(cam.stats()["errors"], 1)

	def test_thread_lifecycle_start_stop(self) -> None:
		det = FakeDetector(
			[
				[(1, _square_corners(2028.0, 1520.0, 100.0))]
				for _ in range(50)
			]
		)
		cam = self._make(det)
		cam.start()
		cam.set_active(True)
		# Wacht op minstens één frame.
		deadline = time.monotonic() + 2.0
		while time.monotonic() < deadline and cam.stats()["frames"] == 0:
			time.sleep(0.02)
		self.assertGreaterEqual(cam.stats()["frames"], 1)
		cam.stop()
		self.assertIsNotNone(cam._thread)
		self.assertFalse(cam._thread.is_alive() if cam._thread else False)

	def test_save_every_n_writes_frames_with_tag_suffix(self) -> None:
		"""Elke N-de frame → save_fn wordt met frame+path aangeroepen."""

		import tempfile

		det = FakeDetector(
			[[(1, _square_corners(2028.0, 1520.0, 100.0))] for _ in range(5)]
		)
		sentinel_frame = object()
		saved: list = []

		def save_fn(frame: Any, path) -> None:
			saved.append((frame, path))

		with tempfile.TemporaryDirectory() as td:
			from pathlib import Path as _P

			cam = CameraThread(
				buffer=self.buf,
				registry=self.reg,
				capture_fn=_identity_capture(sentinel_frame, 4056, 3040),
				preprocess_fn=_identity_preprocess,
				detector=det,
				target_fps=100.0,
				save_every_n_frames=2,
				save_dir=_P(td),
				save_fn=save_fn,
			)
			# 4 iteraties → frames 2 en 4 worden gesaved (elke 2de).
			for _ in range(4):
				cam.run_once()
			self.assertEqual(len(saved), 2)
			for frame, path in saved:
				self.assertIs(frame, sentinel_frame)
				self.assertTrue(path.name.endswith(".jpg"))
				self.assertTrue(path.name.startswith("deploy_"))
				self.assertIn("_tags-1", path.name)
			self.assertEqual(cam.stats()["saved"], 2)
			self.assertEqual(cam.stats()["save_errors"], 0)

	def test_save_off_when_every_n_zero(self) -> None:
		import tempfile

		det = FakeDetector([[(1, _square_corners(2028.0, 1520.0, 100.0))]] * 3)
		saved: list = []

		def save_fn(frame: Any, path) -> None:
			saved.append(path)

		with tempfile.TemporaryDirectory() as td:
			from pathlib import Path as _P

			cam = CameraThread(
				buffer=self.buf,
				registry=self.reg,
				capture_fn=_identity_capture(object(), 4056, 3040),
				preprocess_fn=_identity_preprocess,
				detector=det,
				target_fps=100.0,
				save_every_n_frames=0,
				save_dir=_P(td),
				save_fn=save_fn,
			)
			for _ in range(3):
				cam.run_once()
			self.assertEqual(saved, [])
			self.assertEqual(cam.stats()["saved"], 0)

	def test_save_errors_counted_without_stopping_detect(self) -> None:
		import tempfile

		det = FakeDetector([[(1, _square_corners(2028.0, 1520.0, 100.0))]] * 2)

		def bad_save(frame: Any, path) -> None:
			raise OSError("disk full")

		with tempfile.TemporaryDirectory() as td:
			from pathlib import Path as _P

			cam = CameraThread(
				buffer=self.buf,
				registry=self.reg,
				capture_fn=_identity_capture(object(), 4056, 3040),
				preprocess_fn=_identity_preprocess,
				detector=det,
				target_fps=100.0,
				save_every_n_frames=1,
				save_dir=_P(td),
				save_fn=bad_save,
			)
			for _ in range(2):
				cam.run_once()
			# Detectie + buffer-publish moet nog steeds gelukt zijn.
			self.assertEqual(cam.stats()["frames"], 2)
			self.assertEqual(cam.stats()["saved"], 0)
			self.assertEqual(cam.stats()["save_errors"], 2)

	def test_inactive_pauses_loop(self) -> None:
		"""Inactive thread moet niet tick'en."""

		det = FakeDetector(
			[[(1, _square_corners(2028.0, 1520.0, 100.0))] for _ in range(100)]
		)
		cam = self._make(det)
		cam.start()
		# Start als inactive.
		self.assertFalse(cam.is_active())
		time.sleep(0.1)
		self.assertEqual(cam.stats()["frames"], 0)
		cam.set_active(True)
		deadline = time.monotonic() + 2.0
		while time.monotonic() < deadline and cam.stats()["frames"] == 0:
			time.sleep(0.02)
		self.assertGreaterEqual(cam.stats()["frames"], 1)
		cam.stop()


if __name__ == "__main__":
	unittest.main()
