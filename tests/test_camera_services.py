"""Unit tests voor :mod:`cansat_hw.camera.services` (CONFIG-mode synchrone
shoot/detect).

We gebruiken fakes voor capture/preprocess/detector zodat er geen
Picamera2/OpenCV/apriltag vereist is. De save-fn is een stub die naar een
in-memory dict schrijft; zo vermijden we echte disk-writes in tests.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple

from cansat_hw.camera.registry import TagRegistry
from cansat_hw.camera.services import CameraServices


class _ScriptedDetector:
	def __init__(self, scripted: List[List[Tuple[int, List[Tuple[float, float]]]]]):
		self._scripted = list(scripted)
		self.calls = 0

	def detect(self, grey: Any):
		self.calls += 1
		if not self._scripted:
			return []
		return self._scripted.pop(0)


def _capture(frame: Any, w: int, h: int):
	def _cap():
		return frame, (w, h)

	return _cap


def _identity_preprocess(frame: Any, target_w: int):
	return frame, 1.0


def _square(cx: float, cy: float, side: float):
	h = side / 2.0
	return [
		(cx - h, cy - h),
		(cx + h, cy - h),
		(cx + h, cy + h),
		(cx - h, cy + h),
	]


class CameraServicesTest(unittest.TestCase):
	def setUp(self) -> None:
		self.reg = TagRegistry(
			focal_length_mm=1.0,
			pixel_pitch_um=1.0,
			tags={},
			default_size_mm=1000,
		)
		self.frame = object()
		self.saved: Dict[Path, Any] = {}

		def save_fn(frame: Any, path: Path) -> None:
			self.saved[path] = frame

		self.save_fn = save_fn
		self.tmp = Path(self.id().replace(".", "_")).resolve()
		# We wissen self.tmp niet fysiek — save_fn schrijft nergens heen.
		# Maar _reserve_filename() kan path.exists() checken; zolang de
		# directory niet bestaat blijft dat False (geen collision).

	def _make(
		self,
		detector: _ScriptedDetector,
		*,
		image_wh: Tuple[int, int] = (1600, 1300),
	) -> CameraServices:
		return CameraServices(
			capture_fn=_capture(self.frame, image_wh[0], image_wh[1]),
			preprocess_fn=_identity_preprocess,
			detector=detector,
			registry=self.reg,
			photo_dir=self.tmp,
			save_fn=self.save_fn,
		)

	def test_detect_once_returns_metrics_without_saving(self) -> None:
		det = _ScriptedDetector(
			[
				[
					(1, _square(800.0, 650.0, 100.0)),
					(2, _square(800.0, 650.0, 200.0)),
				]
			]
		)
		svc = self._make(det)
		metrics, (w, h) = svc.detect_once()
		self.assertEqual(w, 1600)
		self.assertEqual(h, 1300)
		self.assertEqual(len(metrics), 2)
		self.assertEqual(self.saved, {})  # niks gesaved
		self.assertEqual(svc.stats()["detects"], 1)
		self.assertEqual(svc.stats()["shoots"], 0)

	def test_shoot_and_detect_save_true_writes_jpeg(self) -> None:
		# We gebruiken een echte tmp-dir zodat mkdir(parents, exist_ok) op
		# ``photo_dir`` werkelijk lukt.
		import tempfile

		with tempfile.TemporaryDirectory() as td:
			svc = CameraServices(
				capture_fn=_capture(self.frame, 1600, 1300),
				preprocess_fn=_identity_preprocess,
				detector=_ScriptedDetector([[(1, _square(800.0, 650.0, 100.0))]]),
				registry=self.reg,
				photo_dir=Path(td),
				save_fn=self.save_fn,
			)
			result = svc.shoot_and_detect(save=True)
			self.assertIsNotNone(result.path)
			self.assertEqual(result.image_wh, (1600, 1300))
			self.assertEqual(len(result.detections), 1)
			self.assertIn(result.path, self.saved)
			self.assertIs(self.saved[result.path], self.frame)
			self.assertEqual(svc.stats()["shoots"], 1)

	def test_shoot_and_detect_save_false_skips_save(self) -> None:
		svc = self._make(_ScriptedDetector([[(1, _square(800.0, 650.0, 100.0))]]))
		result = svc.shoot_and_detect(save=False)
		self.assertIsNone(result.path)
		self.assertEqual(self.saved, {})
		self.assertEqual(svc.stats()["shoots"], 1)

	def test_capture_failure_raises_runtime_error_and_counts(self) -> None:
		def bad_cap():
			raise IOError("picamera gone")

		svc = CameraServices(
			capture_fn=bad_cap,
			preprocess_fn=_identity_preprocess,
			detector=_ScriptedDetector([]),
			registry=self.reg,
			photo_dir=self.tmp,
			save_fn=self.save_fn,
		)
		with self.assertRaises(RuntimeError):
			svc.detect_once()
		self.assertEqual(svc.stats()["errors"], 1)
		self.assertIsNotNone(svc.stats()["last_error"])

	def test_filename_collision_appends_suffix(self) -> None:
		import tempfile

		with tempfile.TemporaryDirectory() as td:
			p = Path(td)
			svc = CameraServices(
				capture_fn=_capture(self.frame, 1600, 1300),
				preprocess_fn=_identity_preprocess,
				detector=_ScriptedDetector([[], [], []]),
				registry=self.reg,
				photo_dir=p,
				save_fn=self.save_fn,
			)
			# Forceer drie shots in dezelfde "seconde" door _reserve_filename
			# heen en weer te laten itereren. save_fn stub → path.exists()
			# werkt niet als save_fn niks op disk zet; dus we creëren het
			# bestand zelf.
			def save_and_touch(frame: Any, path: Path) -> None:
				self.saved[path] = frame
				path.write_bytes(b"X")

			svc.save_fn = save_and_touch
			r1 = svc.shoot_and_detect(save=True)
			r2 = svc.shoot_and_detect(save=True)
			r3 = svc.shoot_and_detect(save=True)
			self.assertIsNotNone(r1.path)
			self.assertIsNotNone(r2.path)
			self.assertIsNotNone(r3.path)
			self.assertNotEqual(r1.path, r2.path)
			self.assertNotEqual(r2.path, r3.path)
			# Minstens 1 moet een ``_1``/``_2`` suffix hebben.
			suffixes = {p.stem.split("_")[-1] for p in (r1.path, r2.path, r3.path)}
			self.assertTrue(any(s in {"1", "2"} for s in suffixes))


if __name__ == "__main__":
	unittest.main()
