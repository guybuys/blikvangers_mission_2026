#!/usr/bin/env python3
"""
Afdaling/landing: foto's + BME280 + BNO055 + AprilTag → CSV op SD + ``radio_snapshot.json``.

- Geen MQTT: radio-stack kan periodiek ``radio_snapshot.json`` lezen (beste regel sinds start).
- Optioneel **exposure-bracket**: ``--bracket-us 8000,12000,20000`` roteert vaste sluitertijden (µs).
- AWB uit waar libcamera dat ondersteunt (NoIR mono).

Voorbeeld::

  python scripts/camera/descent_telemetry.py --frames 200 --photo-dir ~/photos --log ~/logs/descent.csv

Zie ``scripts/camera/README.md`` voor dependencies.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
_ROOT = _SCRIPT_DIR.parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
	sys.path.insert(0, str(_SRC))

if str(_SCRIPT_DIR) not in sys.path:
	sys.path.insert(0, str(_SCRIPT_DIR))

from tag_metrics import (
	compute_calibration_k,
	compute_metrics_from_corners,
	compute_metrics_pinhole,
)


def _parse_bracket_us(s: str) -> List[int]:
	out: List[int] = []
	for part in (s or "").split(","):
		part = part.strip()
		if not part:
			continue
		out.append(int(part, 10))
	return out


def _parse_calibration(s: str) -> List[Tuple[float, float]]:
	pairs: List[Tuple[float, float]] = []
	for part in (s or "").split(","):
		part = part.strip()
		if not part or ":" not in part:
			continue
		a, b = part.split(":", 1)
		pairs.append((float(a.strip()), float(b.strip())))
	return pairs


def _radio_score(tags: List[int], detections: List[Dict[str, Any]]) -> float:
	"""Hoger = beter voor uplink-samenvatting."""
	if not detections:
		return float(len(tags))
	max_side = max(float(d.get("max_side_px", 0.0)) for d in detections)
	dists = [float(d.get("distance_m", 1e9)) for d in detections if d.get("distance_m") is not None]
	min_dist = min(dists) if dists else 1e9
	return len(tags) * 1e12 + max_side * 1e6 + max(0.0, 500.0 - min_dist)


class AprilTagWorker:
	def __init__(self) -> None:
		env = os.environ.copy()
		worker = _SCRIPT_DIR / "apriltag_worker.py"
		self.proc = subprocess.Popen(
			[sys.executable, "-u", str(worker)],
			stdin=subprocess.PIPE,
			stdout=subprocess.PIPE,
			stderr=subprocess.STDOUT,
			text=True,
			env=env,
			cwd=str(_SCRIPT_DIR),
		)

	def detect(self, filepath: str) -> Optional[dict]:
		if self.proc.poll() is not None:
			return None
		assert self.proc.stdin is not None
		assert self.proc.stdout is not None
		self.proc.stdin.write(filepath + "\n")
		self.proc.stdin.flush()
		line = self.proc.stdout.readline()
		if not line:
			return None
		try:
			return json.loads(line)
		except json.JSONDecodeError:
			return None

	def close(self) -> None:
		try:
			if self.proc.stdin:
				self.proc.stdin.write("__quit__\n")
				self.proc.stdin.flush()
		except Exception:
			pass
		try:
			self.proc.terminate()
		except Exception:
			pass


def _apply_controls(picam2: Any, bracket_us: List[int], idx: int, analogue_gain: float) -> None:
	"""Stel exposure in; faal stil als deze cam/libcamera een key niet kent."""
	try:
		if bracket_us:
			exp = int(bracket_us[idx % len(bracket_us)])
			ctrl = {
				"AeEnable": False,
				"AwbEnable": False,
				"ExposureTime": exp,
				"AnalogueGain": float(analogue_gain),
			}
		else:
			ctrl = {"AeEnable": True, "AwbEnable": False}
		picam2.set_controls(ctrl)
	except Exception:
		try:
			picam2.set_controls({"AeEnable": True})
		except Exception:
			pass


def main() -> int:
	p = argparse.ArgumentParser(description="Camera + sensoren + AprilTag → CSV + radio_snapshot.json")
	p.add_argument("--photo-dir", type=Path, default=Path.home() / "photos")
	p.add_argument("--log", type=Path, required=True, help="Pad naar .csv (wordt aangevuld)")
	p.add_argument("--radio-snapshot", type=Path, default=None, help="JSON met beste regel (default: naast log)")
	p.add_argument("--frames", type=int, default=0, help="0 = oneindig tot Ctrl+C")
	p.add_argument("--interval", type=float, default=0.0, help="Extra pauze na elke frame (s)")
	p.add_argument("--size", type=str, default="1600x1300", help="WxH still")
	p.add_argument("--bracket-us", type=str, default="", help="Komma-gescheiden exposure-tijden µs, bv. 8000,12000,20000")
	p.add_argument("--analog-gain", type=float, default=2.0, help="Analog gain bij vaste exposure (bracket)")
	p.add_argument("--i2c-bus", type=int, default=1)
	p.add_argument("--bme280-addr", type=lambda x: int(x, 0), default=0x76)
	p.add_argument("--bno055-addr", type=lambda x: int(x, 0), default=0x28)
	p.add_argument("--fusion", choices=("imu", "ndof"), default="imu")
	p.add_argument(
		"--tag-registry",
		type=Path,
		default=None,
		help=(
			"Pad naar config/camera/tag_registry.json. Indien gezet: "
			"pinhole-modus met per-tag size_mm uit de registry "
			"(zelfde formule als de Zero-radio-pijplijn). Anders: "
			"legacy single-size calibratie via --calibration-data."
		),
	)
	p.add_argument(
		"--calibration-data",
		type=str,
		default="195.0:0.80,118.9:1.30,85.5:1.80,78.4:2.00,40.1:3.80,34.0:4.50",
		help=(
			"Legacy modus: tag_pixel_breedte:afstand_m,... voor k in "
			"tag_metrics. Genegeerd als --tag-registry is gezet."
		),
	)
	p.add_argument(
		"--fx",
		type=float,
		default=1000.0,
		help="Horizontale focal length voor offset-math (legacy modus). In registry-modus default = focal_length_px.",
	)
	p.add_argument(
		"--fy",
		type=float,
		default=1000.0,
		help="Verticale focal length voor offset-math (legacy modus). In registry-modus default = focal_length_px.",
	)
	p.add_argument("--no-sensors", action="store_true")
	p.add_argument("--no-apriltag", action="store_true")
	args = p.parse_args()

	ws, hs = args.size.lower().split("x", 1)
	size = (int(ws), int(hs))
	bracket_us = _parse_bracket_us(args.bracket_us)

	registry = None
	focal_px = 0.0
	if args.tag_registry is not None:
		try:
			from cansat_hw.camera.registry import load_tag_registry

			registry = load_tag_registry(args.tag_registry)
			focal_px = float(registry.focal_length_px)
			if focal_px <= 0:
				print(
					f"WARN: tag-registry {args.tag_registry} levert focal_length_px=0 — val terug op legacy --calibration-data",
					file=sys.stderr,
				)
				registry = None
		except Exception as e:
			print(
				f"WARN: kan tag-registry {args.tag_registry} niet laden ({e}) — val terug op legacy --calibration-data",
				file=sys.stderr,
			)
			registry = None

	if registry is not None:
		print(
			f"Pinhole-modus: registry={args.tag_registry}, focal_length_px={focal_px:.1f}, "
			f"sensor_full_res={registry.full_res_px}, default_size_mm={registry.default_size_mm}",
			file=sys.stderr,
		)
		k = 0.0
	else:
		calib_pairs = _parse_calibration(args.calibration_data)
		if not calib_pairs:
			calib_pairs = [(78.4, 2.00)]
		k = compute_calibration_k(calib_pairs)
		print(f"Legacy single-size modus: k={k:.2f} (uit {len(calib_pairs)} datapunten)", file=sys.stderr)

	args.photo_dir.mkdir(parents=True, exist_ok=True)
	args.log.parent.mkdir(parents=True, exist_ok=True)
	snap_path = args.radio_snapshot or args.log.with_suffix(".radio_snapshot.json")

	import picamera2_bootstrap

	picamera2_bootstrap.ensure_apt_picamera2_on_path()
	try:
		from picamera2 import Picamera2
	except ImportError as e:
		print(
			"picamera2 niet importeerbaar. Op de Pi: sudo apt install -y python3-picamera2",
			file=sys.stderr,
		)
		print(
			"  In een venv: dist-packages via picamera2_bootstrap.py; bij numpy/simplejpeg-fout:",
			file=sys.stderr,
		)
		print('  pip install "numpy>=1.22,<2"   # venv mag geen NumPy 2 gebruiken met apt-picamera2', file=sys.stderr)
		print(
			"  alternatief: venv met --system-site-packages of: /usr/bin/python3 scripts/camera/…",
			file=sys.stderr,
		)
		print(e, file=sys.stderr)
		return 1

	from cansat_hw.sensors.bme280 import BME280
	from cansat_hw.sensors.bno055 import BNO055, OPERATION_MODE_IMU, OPERATION_MODE_NDOF

	mode = OPERATION_MODE_NDOF if args.fusion == "ndof" else OPERATION_MODE_IMU
	bme: Optional[BME280] = None
	imu: Optional[BNO055] = None
	if not args.no_sensors:
		i2c_dev = Path(f"/dev/i2c-{args.i2c_bus}")
		if not i2c_dev.exists():
			print(f"Geen {i2c_dev} — gebruik --no-sensors of zet I²C aan.", file=sys.stderr)
			return 2
		try:
			bme = BME280(args.i2c_bus, args.bme280_addr)
			if bme.chip_id != 0x60:
				print("WARN: BME280 chip id onverwacht — sensor uit", file=sys.stderr)
				bme.close()
				bme = None
		except Exception as e:
			print("WARN: BME280:", e, file=sys.stderr)
			bme = None
		try:
			imu = BNO055(args.i2c_bus, args.bno055_addr, mode=mode)
			if imu.chip_id != 0xA0:
				print("WARN: BNO055 chip id onverwacht — IMU uit", file=sys.stderr)
				imu.close()
				imu = None
		except Exception as e:
			print("WARN: BNO055:", e, file=sys.stderr)
			imu = None

	worker: Optional[AprilTagWorker] = None
	if not args.no_apriltag:
		try:
			worker = AprilTagWorker()
		except Exception as e:
			print("WARN: AprilTag-worker start mislukt:", e, file=sys.stderr)
			worker = None

	picam2 = Picamera2()
	cfg = picam2.create_still_configuration(main={"size": size})
	picam2.configure(cfg)
	picam2.start()
	time.sleep(0.2)
	_apply_controls(picam2, bracket_us, 0, args.analog_gain)
	time.sleep(0.1)

	fieldnames = [
		"iso_ts",
		"filename",
		"bracket_idx",
		"exposure_us",
		"analogue_gain",
		"ae_enable",
		"pressure_hpa",
		"temp_c",
		"rh_pct",
		"gx",
		"gy",
		"gz",
		"calib_sys",
		"calib_gyro",
		"calib_accel",
		"calib_mag",
		"detect_s",
		"n_tags",
		"tags",
		"min_dist_m",
		"max_side_px",
		"radio_score",
		"detections_json",
	]

	best_score = -1.0
	best_payload: Optional[Dict[str, Any]] = None
	frame_i = 0
	new_file = not args.log.exists()

	try:
		with args.log.open("a", newline="", encoding="utf-8") as fp:
			wr = csv.DictWriter(fp, fieldnames=fieldnames, extrasaction="ignore")
			if new_file:
				wr.writeheader()

			while args.frames == 0 or frame_i < args.frames:
				bracket_idx = frame_i % max(len(bracket_us), 1) if bracket_us else 0
				if bracket_us:
					_apply_controls(picam2, bracket_us, frame_i, args.analog_gain)
					time.sleep(0.03)

				ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
				fn = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
				fpath = args.photo_dir / fn

				picam2.capture_file(str(fpath))
				meta = picam2.capture_metadata()
				exp_us = int(meta.get("ExposureTime", 0) or 0)
				again = float(meta.get("AnalogueGain", 0.0) or 0.0)
				ae_en = meta.get("AeEnable", "")

				p_hpa, t_c, rh = float("nan"), float("nan"), float("nan")
				if bme is not None:
					try:
						p_hpa, t_c, rh = bme.read()
					except Exception:
						pass

				gx = gy = gz = float("nan")
				cs: Tuple[int, int, int, int] = (-1, -1, -1, -1)
				if imu is not None:
					try:
						gx, gy, gz = imu.read_gravity()
						cs = imu.calibration_status()
					except Exception:
						pass

				tags: List[int] = []
				detections: List[Dict[str, Any]] = []
				detect_s = float("nan")
				det_json = "{}"

				if worker is not None:
					res = worker.detect(str(fpath))
					if isinstance(res, dict) and res.get("ok"):
						tags = [int(t) for t in res.get("tags", []) if t is not None]
						detect_s = float(res.get("detect_s", 0.0) or 0.0)
						img_meta = res.get("image") if isinstance(res.get("image"), dict) else {}
						iw = int(img_meta.get("w", 0) or 0)
						ih = int(img_meta.get("h", 0) or 0)
						raw_dets = res.get("detections") if isinstance(res.get("detections"), list) else []
						for d in raw_dets:
							if not isinstance(d, dict):
								continue
							tid = d.get("tag_id")
							cpx = d.get("corners_px")
							if tid is None or not isinstance(cpx, list) or len(cpx) != 4 or iw <= 0 or ih <= 0:
								continue
							try:
								if registry is not None:
									size_m = registry.size_mm_for(int(tid)) / 1000.0
									m = compute_metrics_pinhole(
										tag_id=int(tid),
										corners_px=cpx,
										image_w=iw,
										image_h=ih,
										focal_length_px=focal_px,
										tag_size_m=size_m,
									)
								else:
									m = compute_metrics_from_corners(
										tag_id=int(tid),
										corners_px=cpx,
										image_w=iw,
										image_h=ih,
										k=k,
										fx=args.fx,
										fy=args.fy,
									)
								detections.append(m)
							except Exception:
								continue
						det_json = json.dumps(detections, separators=(",", ":"))

				min_dist = float("nan")
				max_side = float("nan")
				if detections:
					min_dist = min(float(d["distance_m"]) for d in detections)
					max_side = max(float(d["max_side_px"]) for d in detections)

				score = _radio_score(tags, detections)
				row = {
					"iso_ts": ts,
					"filename": fn,
					"bracket_idx": bracket_idx if bracket_us else "",
					"exposure_us": exp_us,
					"analogue_gain": f"{again:.4f}",
					"ae_enable": ae_en,
					"pressure_hpa": f"{p_hpa:.2f}" if p_hpa == p_hpa else "",
					"temp_c": f"{t_c:.2f}" if t_c == t_c else "",
					"rh_pct": f"{rh:.2f}" if rh == rh else "",
					"gx": f"{gx:.4f}" if gx == gx else "",
					"gy": f"{gy:.4f}" if gy == gy else "",
					"gz": f"{gz:.4f}" if gz == gz else "",
					"calib_sys": cs[0],
					"calib_gyro": cs[1],
					"calib_accel": cs[2],
					"calib_mag": cs[3],
					"detect_s": f"{detect_s:.4f}" if detect_s == detect_s else "",
					"n_tags": len(tags),
					"tags": ";".join(str(t) for t in tags),
					"min_dist_m": f"{min_dist:.4f}" if min_dist == min_dist else "",
					"max_side_px": f"{max_side:.2f}" if max_side == max_side else "",
					"radio_score": f"{score:.2f}",
					"detections_json": det_json,
				}
				wr.writerow(row)
				fp.flush()

				if score > best_score:
					best_score = score
					best_payload = {
						"updated_utc": ts,
						"radio_score": score,
						"filename": fn,
						"tags": tags,
						"n_tags": len(tags),
						"min_dist_m": min_dist if min_dist == min_dist else None,
						"max_side_px": max_side if max_side == max_side else None,
						"pressure_hpa": p_hpa if p_hpa == p_hpa else None,
						"temp_c": t_c if t_c == t_c else None,
						"rh_pct": rh if rh == rh else None,
						"gx": gx if gx == gx else None,
						"gy": gy if gy == gy else None,
						"gz": gz if gz == gz else None,
						"calib": list(cs),
						"detections": detections,
					}
					snap_path.write_text(json.dumps(best_payload, indent=2), encoding="utf-8")

				frame_i += 1
				if args.interval > 0:
					time.sleep(args.interval)

	except KeyboardInterrupt:
		print("Gestopt (Ctrl+C)", file=sys.stderr)
	finally:
		try:
			picam2.stop()
		except Exception:
			pass
		if bme is not None:
			try:
				bme.close()
			except Exception:
				pass
		if imu is not None:
			try:
				imu.close()
			except Exception:
				pass
		if worker is not None:
			worker.close()

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
