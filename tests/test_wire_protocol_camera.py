"""Wire-protocol camera-commando's: ``CAM SHOOT``, ``CAM DETECT``, ``GET CAMSTATS``.

We injecteren een dummy :class:`CameraServices` + dummy camera-thread
via ``handle_wire_line(..., camera_services=..., camera_thread=...)``.
Daardoor testen we de bytes-replies én de CONFIG-only / ERR-paden zonder
ook maar één echte frame te capturen.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, List, Tuple
from unittest.mock import MagicMock

from cansat_hw.camera.detector import DetectorMetrics
from cansat_hw.camera.services import ShootResult
from cansat_hw.radio.wire_protocol import RadioRuntimeState, handle_wire_line


def _make_metric(tag_id: int, distance_m: float, max_side_px: float) -> DetectorMetrics:
	return DetectorMetrics(
		tag_id=tag_id,
		corners_px_full=[(0.0, 0.0)] * 4,
		center_px_full=(0.0, 0.0),
		max_side_px=max_side_px,
		distance_m=distance_m,
		dx_m=0.0,
		dy_m=0.0,
		size_mm=1100,
	)


class FakeServices:
	def __init__(
		self,
		*,
		shoot_result: ShootResult,
		detect_result: Tuple[List[DetectorMetrics], Tuple[int, int]],
	):
		self._shoot = shoot_result
		self._detect = detect_result
		self.shoots = 0
		self.detects = 0

	def shoot_and_detect(self, *, save: bool) -> ShootResult:
		self.shoots += 1
		return self._shoot

	def detect_once(self):
		self.detects += 1
		return self._detect

	def stats(self) -> dict:
		return {
			"shoots": self.shoots,
			"detects": self.detects,
			"errors": 0,
			"last_error": None,
		}


class FakeThread:
	def __init__(self, *, active: bool, frames: int = 0, saved: int = 0, errors: int = 0):
		self._active = active
		self._frames = frames
		self._saved = saved
		self._errors = errors

	def is_active(self) -> bool:
		return self._active

	def stats(self) -> dict:
		return {
			"active": self._active,
			"frames": self._frames,
			"errors": self._errors,
			"last_error": None,
			"saved": self._saved,
			"save_errors": 0,
		}


class CamShootTests(unittest.TestCase):
	def _call(
		self,
		line: str,
		*,
		services: Any = None,
		thread: Any = None,
		mode: str = "CONFIG",
	) -> bytes:
		st = RadioRuntimeState(mode=mode)
		return handle_wire_line(
			MagicMock(frequency_mhz=433.0),
			st,
			line,
			camera_services=services,
			camera_thread=thread,
		)

	def test_cam_shoot_returns_ok_shoot_with_filename_and_tags(self) -> None:
		svc = FakeServices(
			shoot_result=ShootResult(
				path=Path("/tmp/cam_214530Z.jpg"),
				detections=[
					_make_metric(1, 12.34, 150.0),
					_make_metric(3, 23.45, 80.0),
				],
				image_wh=(1600, 1300),
			),
			detect_result=([], (1600, 1300)),
		)
		out = self._call("CAM SHOOT", services=svc, thread=FakeThread(active=False))
		self.assertTrue(out.startswith(b"OK SHOOT "), out)
		self.assertIn(b"cam_214530Z.jpg", out)
		self.assertIn(b"1600x1300", out)
		self.assertIn(b"T=2", out)
		# Grootste tag zit vooraan: tag 1 (max_side=150) vóór tag 3.
		self.assertIn(b"1=1234", out)
		self.assertIn(b"3=2345", out)
		self.assertLessEqual(len(out), 60)
		self.assertEqual(svc.shoots, 1)

	def test_cam_shoot_no_tags_reports_T_zero(self) -> None:
		svc = FakeServices(
			shoot_result=ShootResult(
				path=Path("/tmp/cam_214530Z.jpg"),
				detections=[],
				image_wh=(1600, 1300),
			),
			detect_result=([], (1600, 1300)),
		)
		out = self._call("CAM SHOOT", services=svc, thread=FakeThread(active=False))
		self.assertIn(b"T=0", out)

	def test_cam_detect_no_save_reply_format(self) -> None:
		svc = FakeServices(
			shoot_result=ShootResult(path=None, detections=[], image_wh=(1600, 1300)),
			detect_result=([_make_metric(5, 5.00, 300.0)], (1600, 1300)),
		)
		out = self._call("CAM DETECT", services=svc, thread=FakeThread(active=False))
		self.assertTrue(out.startswith(b"OK DETECT "), out)
		self.assertIn(b"1600x1300", out)
		self.assertIn(b"T=1", out)
		self.assertIn(b"5=500", out)
		self.assertEqual(svc.detects, 1)
		self.assertEqual(svc.shoots, 0)

	def test_cam_shoot_blocked_when_thread_active(self) -> None:
		svc = FakeServices(
			shoot_result=ShootResult(path=None, detections=[], image_wh=(1600, 1300)),
			detect_result=([], (1600, 1300)),
		)
		out = self._call("CAM SHOOT", services=svc, thread=FakeThread(active=True))
		self.assertEqual(out, b"ERR CAM BUSY")
		self.assertEqual(svc.shoots, 0)

	def test_cam_shoot_blocked_when_not_in_config(self) -> None:
		svc = FakeServices(
			shoot_result=ShootResult(path=None, detections=[], image_wh=(1600, 1300)),
			detect_result=([], (1600, 1300)),
		)
		# MISSION-mode: ``CAM SHOOT`` is NIET in de allowlist → ``ERR BUSY MISSION``.
		out = self._call(
			"CAM SHOOT", services=svc, thread=FakeThread(active=False), mode="MISSION"
		)
		self.assertTrue(out.startswith(b"ERR BUSY MISSION"))

	def test_cam_shoot_without_services_errors(self) -> None:
		out = self._call("CAM SHOOT", services=None, thread=FakeThread(active=False))
		self.assertEqual(out, b"ERR CAM NOHW")

	def test_cam_shoot_capture_failure_returns_err_cam(self) -> None:
		class BadServices(FakeServices):
			def shoot_and_detect(self, *, save: bool) -> ShootResult:
				raise RuntimeError("imx gone")

		svc = BadServices(
			shoot_result=ShootResult(path=None, detections=[], image_wh=(0, 0)),
			detect_result=([], (0, 0)),
		)
		out = self._call("CAM SHOOT", services=svc, thread=FakeThread(active=False))
		self.assertTrue(out.startswith(b"ERR CAM "), out)

	def test_cam_detect_capture_failure_returns_err_cam(self) -> None:
		class BadServices(FakeServices):
			def detect_once(self):
				raise RuntimeError("imx gone")

		svc = BadServices(
			shoot_result=ShootResult(path=None, detections=[], image_wh=(0, 0)),
			detect_result=([], (0, 0)),
		)
		out = self._call("CAM DETECT", services=svc, thread=FakeThread(active=False))
		self.assertTrue(out.startswith(b"ERR CAM "), out)


class CamStatsTests(unittest.TestCase):
	def test_camstats_reports_active_and_counters(self) -> None:
		svc = FakeServices(
			shoot_result=ShootResult(path=None, detections=[], image_wh=(0, 0)),
			detect_result=([], (0, 0)),
		)
		thr = FakeThread(active=True, frames=42, saved=6, errors=1)
		st = RadioRuntimeState(mode="CONFIG")
		out = handle_wire_line(
			MagicMock(frequency_mhz=433.0),
			st,
			"GET CAMSTATS",
			camera_services=svc,
			camera_thread=thr,
		)
		self.assertTrue(out.startswith(b"OK CAMSTATS "), out)
		self.assertIn(b"A=on", out)
		self.assertIn(b"F=42", out)
		self.assertIn(b"S=6", out)
		self.assertIn(b"E=1", out)

	def test_camstats_no_hardware_returns_err(self) -> None:
		st = RadioRuntimeState(mode="CONFIG")
		out = handle_wire_line(
			MagicMock(frequency_mhz=433.0),
			st,
			"GET CAMSTATS",
			camera_services=None,
			camera_thread=None,
		)
		self.assertEqual(out, b"ERR CAM NOHW")

	def test_camstats_allowed_in_mission(self) -> None:
		"""``GET CAMSTATS`` is alleen-lezen → moet ook in MISSION werken."""

		svc = FakeServices(
			shoot_result=ShootResult(path=None, detections=[], image_wh=(0, 0)),
			detect_result=([], (0, 0)),
		)
		thr = FakeThread(active=False, frames=0)
		st = RadioRuntimeState(mode="MISSION")
		out = handle_wire_line(
			MagicMock(frequency_mhz=433.0),
			st,
			"GET CAMSTATS",
			camera_services=svc,
			camera_thread=thr,
		)
		self.assertTrue(out.startswith(b"OK CAMSTATS "), out)


if __name__ == "__main__":
	unittest.main()
