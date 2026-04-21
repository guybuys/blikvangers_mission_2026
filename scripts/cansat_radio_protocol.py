#!/usr/bin/env python3
"""
CanSat flight computer — Raspberry Pi Zero 2 W — radio commando-loop (CONFIG / MISSION).

Luistert op RFM69 naar pakketten voor dit node-ID, beantwoordt met dezelfde
tekstregels als de base station op de **Pico** (Thonny: `protocol.py` / README_basestation).

Let op: dit script leest **geen** toetsenbord — commando's met ``!`` zijn alleen voor de
**Pico**-CLI in Thonny. Op de Zero typ je hier niets; alleen Ctrl+C om te stoppen.

Start op de CanSat (Zero 2 W), venv actief, SPI aan:

  python scripts/cansat_radio_protocol.py
  python scripts/cansat_radio_protocol.py --poll 0.5 --verbose
  python scripts/cansat_radio_protocol.py --reply-delay 0      # geen extra pauze vóór antwoord

Stop: **Ctrl+C**, of op afstand (half-duplex) het draad-commando ``STOP RADIO`` (antwoord
``OK STOP RADIO``; daarna stopt het proces — handig bij **systemd**-autostart). Met SSH:
``sudo systemctl stop …`` of ``kill`` op het PID werkt ook.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

os.environ.setdefault("GPIOZERO_PIN_FACTORY", "rpigpio")

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
	sys.path.insert(0, str(_SRC))

_DEFAULT_RUNTIME_PATH = _ROOT / "config" / "radio_runtime.json"


def _load_dotenv(path: Path) -> None:
	"""Mini-loader voor ``.env`` — zet variabelen in ``os.environ`` tenzij al gezet.

	Formaat: ``KEY=VALUE`` per regel, ``#`` voor commentaar, enkele/dubbele quotes
	rond de waarde mogen (worden gestript). Geen shell-expansie. We blijven
	bewust dep-vrij; voor een 'echte' parser: ``python-dotenv``.
	"""
	if not path.is_file():
		return
	try:
		text = path.read_text(encoding="utf-8")
	except OSError:
		return
	for raw in text.splitlines():
		line = raw.strip()
		if not line or line.startswith("#"):
			continue
		if "=" not in line:
			continue
		key, _, value = line.partition("=")
		key = key.strip()
		value = value.strip()
		if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
			value = value[1:-1]
		os.environ.setdefault(key, value)


_load_dotenv(_ROOT / ".env")


def _load_persisted_freq(path: Path) -> Optional[float]:
	try:
		data = json.loads(path.read_text(encoding="utf-8"))
	except (OSError, ValueError):
		return None
	try:
		return float(data.get("freq_mhz"))
	except (TypeError, ValueError):
		return None


def _save_persisted_freq(path: Path, mhz: float) -> Optional[str]:
	try:
		path.parent.mkdir(parents=True, exist_ok=True)
		tmp = path.with_suffix(path.suffix + ".tmp")
		tmp.write_text(json.dumps({"freq_mhz": float(mhz)}) + "\n", encoding="utf-8")
		tmp.replace(path)
		return None
	except OSError as e:
		return str(e)[:80]


def main() -> int:
	p = argparse.ArgumentParser(
		description="CanSat (Zero 2 W) RFM69 wire-protocol loop. "
		"Defaults kunnen via .env (CANSAT_RADIO_KEY/NODE/DEST/FREQ_MHZ) overschreven worden; "
		"CLI-args winnen altijd.",
	)
	p.add_argument(
		"--freq",
		type=float,
		default=float(os.environ.get("CANSAT_RADIO_FREQ_MHZ", "433.0")),
		help="MHz (default: $CANSAT_RADIO_FREQ_MHZ of 433.0; wordt overruled door persisted freq in --runtime-path)",
	)
	p.add_argument(
		"--node",
		type=int,
		default=int(os.environ.get("CANSAT_RADIO_NODE", "120")),
		help="Dit toestel (CanSat) RadioHead-adres (default: $CANSAT_RADIO_NODE of 120)",
	)
	p.add_argument(
		"--dest",
		type=int,
		default=int(os.environ.get("CANSAT_RADIO_DEST", "100")),
		help="Standaard bestemming (basis); replies gaan naar afzender (default: $CANSAT_RADIO_DEST of 100)",
	)
	p.add_argument(
		"--key",
		type=str,
		default=os.environ.get("CANSAT_RADIO_KEY", "CANSAT_2025-2026"),
		help="16-byte UTF-8 AES (default: $CANSAT_RADIO_KEY uit .env of demo-key)",
	)
	p.add_argument("--reset-pin", type=int, default=25)
	p.add_argument(
		"--dio0-pin",
		type=int,
		default=None,
		metavar="BCM",
		help="BCM GPIO voor RFM69 DIO0 (IRQ: PayloadReady/PacketSent); bv. 24 per pinning-doc. "
		"Weglaten = alleen SPI-pollen (hogere CPU-belasting)",
	)
	p.add_argument("--spi-bus", type=int, default=0)
	p.add_argument("--spi-device", type=int, default=0)
	p.add_argument("--tx-power", type=int, default=13)
	p.add_argument("--poll", type=float, default=1.0, help="receive()-timeout in seconden")
	p.add_argument(
		"--reply-delay",
		type=float,
		default=0.08,
		metavar="S",
		help="seconden wachten na verwerken vóór antwoord-TX (half-duplex marge voor de Pico; standaard 0.08, 0=uit)",
	)
	p.add_argument("--verbose", action="store_true")
	p.add_argument("--i2c-bus", type=int, default=1, help="I²C-bus voor sensoren (meestal 1)")
	p.add_argument(
		"--bme280-addr",
		type=lambda x: int(x, 0),
		default=0x76,
		help="BME280 I²C-adres (0x76 of 0x77)",
	)
	p.add_argument(
		"--bme280-os",
		type=int,
		default=16,
		choices=[1, 2, 4, 8, 16],
		help="BME280 oversampling (1/2/4/8/16); hogere waarden = minder ruis (±0.2 Pa bij x16). Default 16 voor vluchtgebruik.",
	)
	p.add_argument(
		"--bme280-iir",
		type=int,
		default=4,
		choices=[0, 2, 4, 8, 16],
		help="BME280 IIR bij boot (CONFIG-preset). 4 = snelle step-response voor !alt / !calground; "
		"wordt bij SET MODE TEST/MISSION automatisch opgetild naar --bme280-iir-mission.",
	)
	p.add_argument(
		"--bme280-iir-mission",
		type=int,
		default=16,
		choices=[0, 2, 4, 8, 16],
		help="BME280 IIR-preset voor TEST/MISSION. Default 16 dempt hoogfrequente ruis tot ~0.3 Pa RMS "
		"(~2 cm); wordt automatisch toegepast op SET MODE TEST/MISSION en teruggerold op END_TEST / "
		"SET MODE CONFIG.",
	)
	p.add_argument(
		"--bme280-alt-prime",
		type=int,
		default=5,
		metavar="N",
		help="Aantal back-to-back BME280-reads per GET ALT (1..32). Vult het IIR-filter zodat één "
		"losse GET ALT meteen accuraat is, ook na lange stilte. 1 = oud gedrag (geen priming). "
		"Default 5 = ~750 ms bij OSP×16; live bij te stellen via SET ALT PRIME N.",
	)
	p.add_argument(
		"--no-bme280",
		action="store_true",
		help="Geen BME280 initialiseren (geen READ BME280 over radio)",
	)
	p.add_argument(
		"--bno055-addr",
		type=lambda x: int(x, 0),
		default=0x28,
		help="BNO055 I²C-adres (0x28 of 0x29)",
	)
	p.add_argument(
		"--no-bno055",
		action="store_true",
		help="Geen BNO055 initialiseren (geen READ BNO055 over radio)",
	)
	p.add_argument(
		"--photo-dir",
		type=str,
		default=str(Path.home() / "photos"),
		help="Foto-/log-map waarvan bestaan+schrijfbaarheid als MISSION-preflight wordt gecheckt",
	)
	p.add_argument(
		"--gimbal-cfg",
		type=str,
		default=str(_ROOT / "config" / "gimbal" / "servo_calibration.json"),
		help="Pad naar servo-calibratie JSON (MISSION-preflight)",
	)
	p.add_argument(
		"--runtime-path",
		type=str,
		default=str(_DEFAULT_RUNTIME_PATH),
		help="JSON-bestand waarin de laatst toegepaste frequentie wordt bewaard (sync met Pico)",
	)
	p.add_argument(
		"--log-dir",
		type=str,
		default=str(Path.home() / "cansat_logs"),
		help="Map voor binary log-files. cansat_continuous.bin staat altijd open; per "
		"MISSION/TEST-sessie komt er cansat_<mode>_<UTC>.bin bij. Default: ~/cansat_logs",
	)
	p.add_argument(
		"--no-log",
		action="store_true",
		help="Schakel binary logging uit (alleen radio-TX, geen .bin op de SD-kaart).",
	)
	p.add_argument(
		"--mission-tlm-interval",
		type=float,
		default=1.0,
		metavar="S",
		help="Periode (s) van de autonome TLM-loop in MISSION mode. Lager = meer frames "
		"(state-machine reageert sneller op hoogte-veranderingen, ook zonder bevel van "
		"de Pico); te laag verzadigt de radio. Default 1.0 s.",
	)
	p.add_argument(
		"--no-servo",
		action="store_true",
		help="Schakel de servo-controller uit (geen pigpio, geen SERVO-cmds). Handig "
		"voor radio-only test-runs zonder gimbal-hardware.",
	)
	p.add_argument(
		"--servo-enable-pin",
		type=int,
		default=6,
		metavar="BCM",
		help="BCM GPIO voor de servo-rail-enable (default 6). 0 = geen rail-control.",
	)
	p.add_argument(
		"--servo-enable-active-low",
		action="store_true",
		help="Servo-rail enable-pin is active-low (rail aan = pin laag).",
	)
	p.add_argument(
		"--gimbal-auto-enable",
		action="store_true",
		help="Zet de gimbal closed-loop al actief bij boot (i.p.v. te wachten op "
		"GIMBAL ON over de radio). De loop regelt pas echt tijdens DEPLOYED; "
		"in andere states blijft de rail uit via de state-policy. Default uit "
		"zodat een verkeerd gemonteerde sensor niet direct aan de servo's trekt.",
	)
	p.add_argument(
		"--gimbal-kx",
		type=float,
		default=200.0,
		help="P-gain servo1 / gx-fout (µs per m/s²). Zelfde tuning-conventie "
		"als scripts/gimbal_level.py; hoger = harder sturen (oscillatie-risico).",
	)
	p.add_argument(
		"--gimbal-ky",
		type=float,
		default=200.0,
		help="P-gain servo2 / gy-fout (µs per m/s²).",
	)
	p.add_argument(
		"--gimbal-kix",
		type=float,
		default=20.0,
		help="I-gain servo1 / gx-fout (µs per geïntegreerde fout·s). 0 = uit.",
	)
	p.add_argument(
		"--gimbal-kiy",
		type=float,
		default=20.0,
		help="I-gain servo2 / gy-fout (idem).",
	)
	p.add_argument(
		"--gimbal-max-us-step",
		type=int,
		default=20,
		help="Max µs per regeltick (≈5 Hz in MISSION/TEST → 100 µs/s). Verhogen "
		"bij trage reactie; verlagen als de gimbal gaat tikken.",
	)
	p.add_argument(
		"--gimbal-swap-axes",
		action="store_true",
		help="Ruil de gx→servo1 / gy→servo2 mapping om. Gebruik als de gimbal "
		"in de verkeerde as corrigeert (makkelijker dan JSON-calibratie herschrijven).",
	)
	p.add_argument(
		"--no-camera",
		action="store_true",
		help="Schakel de camera-thread uit (geen Picamera2/AprilTag, geen tags in TLM). "
		"Handig voor radio-only test-runs zonder CSI-camera of OpenCV.",
	)
	p.add_argument(
		"--tag-registry",
		type=str,
		default=str(_ROOT / "config" / "camera" / "tag_registry.json"),
		help="Pad naar AprilTag-registry JSON (fysieke tag-afmetingen + lens/sensor-parameters).",
	)
	p.add_argument(
		"--camera-detect-width",
		type=int,
		default=1014,
		metavar="PX",
		help="Breedte waarnaar de capture gedownscaled wordt voor AprilTag-detectie. "
		"Op de OV2311 native 1600 px is 1014 ≈ 1.6× downscale (~2-3× speedup); "
		"800 geeft ~4× speedup maar verkleint de detect-range voor kleine tags. "
		"Default 1014.",
	)
	p.add_argument(
		"--camera-fps",
		type=float,
		default=7.0,
		metavar="HZ",
		help="Frames-per-seconde bovengrens voor de camera-thread. Default 7 Hz.",
	)
	p.add_argument(
		"--camera-resolution",
		type=str,
		default="1600x1300",
		help="Capture-resolutie (WxH). Default 1600x1300 = native OV2311-array (Arducam "
		"B0381 PiVariety). Zet naar 4056x3040 voor een IMX477/HQ-camera; een mismatch "
		"geeft warnings van libcamera en een gecropt/geschaald beeld.",
	)
	p.add_argument(
		"--camera-tag-families",
		type=str,
		default="tag36h11",
		help="AprilTag-familie voor de detector. Default tag36h11 (zoals de missie-tags).",
	)
	p.add_argument(
		"--deploy-save-every-n",
		type=int,
		default=7,
		metavar="N",
		help="Sla tijdens DEPLOYED elke N-de frame als JPEG op in --photo-dir "
		"(fallback voor detectie-debug als geen tag in TLM verschijnt). "
		"Bij --camera-fps 7 Hz geeft N=7 ≈ 1 foto/seconde; 0 = uit. Default 7.",
	)
	args = p.parse_args()
	if args.mission_tlm_interval < 0.1:
		print("--mission-tlm-interval moet >= 0.1 s zijn", file=sys.stderr)
		return 1
	if args.reply_delay < 0:
		print("--reply-delay must be >= 0", file=sys.stderr)
		return 1

	key = args.key.encode("utf-8")
	if len(key) != 16:
		print("--key must be exactly 16 UTF-8 bytes", file=sys.stderr)
		return 1
	if args.key == "CANSAT_2025-2026" and not os.environ.get("CANSAT_RADIO_KEY"):
		print(
			"WARN: RFM69 draait met de publieke demo-key. Zet CANSAT_RADIO_KEY in .env "
			"(zie .env.example) voor een privé-sleutel.",
			file=sys.stderr,
		)

	spi_dev = Path(f"/dev/spidev{args.spi_bus}.{args.spi_device}")
	if not spi_dev.exists():
		print("Missing", spi_dev, "— enable SPI", file=sys.stderr)
		return 1

	i2c_dev = Path(f"/dev/i2c-{args.i2c_bus}")
	bme280 = None
	if not args.no_bme280 and i2c_dev.exists():
		try:
			from cansat_hw.sensors.bme280 import BME280

			# Map OSP 1/2/4/8/16 naar Pico-OSAMPLE-codes 1..5.
			_OS_TO_CODE = {1: 1, 2: 2, 4: 3, 8: 4, 16: 5}
			bme280 = BME280(
				args.i2c_bus,
				args.bme280_addr,
				oversampling=_OS_TO_CODE[args.bme280_os],
				iir_filter=args.bme280_iir,
			)
			if bme280.chip_id != 0x60:
				print(
					f"WARN: BME280 chip id 0x{bme280.chip_id:02X} (verwacht 0x60) — READ BME280 uit",
					file=sys.stderr,
				)
				bme280.close()
				bme280 = None
		except Exception as e:  # noqa: BLE001
			print("WARN: BME280 niet beschikbaar:", e, file=sys.stderr)
			if bme280 is not None:
				try:
					bme280.close()
				except Exception:
					pass
				bme280 = None
	elif not args.no_bme280:
		print("WARN: geen", i2c_dev, "— BME280 over radio uit", file=sys.stderr)

	bno055 = None
	if not args.no_bno055 and i2c_dev.exists():
		try:
			from cansat_hw.sensors.bno055 import BNO055

			bno055 = BNO055(args.i2c_bus, args.bno055_addr)
			if bno055.chip_id != 0xA0:
				print(
					f"WARN: BNO055 chip id 0x{bno055.chip_id:02X} (verwacht 0xA0) — READ BNO055 uit",
					file=sys.stderr,
				)
				bno055.close()
				bno055 = None
		except Exception as e:  # noqa: BLE001
			print("WARN: BNO055 niet beschikbaar:", e, file=sys.stderr)
			if bno055 is not None:
				try:
					bno055.close()
				except Exception:
					pass
				bno055 = None
	elif not args.no_bno055:
		print("WARN: geen", i2c_dev, "— BNO055 over radio uit", file=sys.stderr)

	from cansat_hw.radio import RFM69
	from cansat_hw.radio.wire_protocol import (
		RadioRuntimeState,
		TEST_MODE_TLM_INTERVAL_S,
		apply_mode_iir,
		build_telemetry_packet,
		handle_wire_line,
		maybe_advance_flight_state,
		test_mode_advance_tlm,
		test_mode_end,
		test_mode_tick,
	)
	from cansat_hw.sensors.sampler import SensorSampler
	from cansat_hw.servos import (
		GimbalLoop,
		ServoAction,
		ServoController,
		action_for_shutdown,
		action_for_transition,
		make_pigpio_driver,
	)
	from cansat_hw.telemetry import LogManager, state_name
	from cansat_hw.telemetry.codec import STATE_DEPLOYED as _STATE_DEPLOYED

	rfm = RFM69(
		spi_bus=args.spi_bus,
		spi_device=args.spi_device,
		reset_pin=args.reset_pin,
		dio0_pin=args.dio0_pin,
	)
	from cansat_hw.radio.wire_protocol import ALT_PRIME_MAX, ALT_PRIME_MIN

	alt_prime = max(ALT_PRIME_MIN, min(ALT_PRIME_MAX, int(args.bme280_alt_prime)))
	if alt_prime != args.bme280_alt_prime:
		print(
			f"WARN: --bme280-alt-prime geclamped naar {alt_prime} ({ALT_PRIME_MIN}..{ALT_PRIME_MAX})",
			file=sys.stderr,
		)
	state = RadioRuntimeState(
		config_iir=int(args.bme280_iir),
		mission_iir=int(args.bme280_iir_mission),
		alt_prime_samples=alt_prime,
	)
	runtime_path = Path(args.runtime_path).expanduser()
	persisted_freq = _load_persisted_freq(runtime_path)
	if persisted_freq is not None:
		print(f"Geladen freq {persisted_freq} MHz uit {runtime_path}")
		args.freq = persisted_freq
		state.freq_set = True

	log_manager = LogManager(args.log_dir, enabled=not args.no_log)
	if log_manager.enabled and log_manager.continuous_path is not None:
		print(f"Binary log → {log_manager.continuous_path}")

	# Continue sensor-sampler (Fase 7). We tikken hem in elke main-loop-iteratie
	# zodat zowel TLM-builds als handle_wire_line altijd een verse snapshot
	# zien — én zodat de IMU-rolling-windows (peak ‖a‖, freefall, alt-stable)
	# voortdurend bijgewerkt blijven, ook als de Pico tijdelijk uit range is.
	sampler = SensorSampler(bme280=bme280, bno055=bno055)

	# Servo-controller (Fase 12). Wordt over radio bestuurd in CONFIG (SERVO …)
	# en autonoom door de state-policy bij flight-state-overgangen. We laden
	# pigpio enkel als de gebruiker servos wil (--no-servo override + JSON
	# bestaat); op een Mac/dev-machine zonder pigpio of zonder gimbal-cfg
	# loopt de service gewoon door zonder gimbal-functionaliteit.
	servo: Optional[ServoController] = None
	gimbal_cfg_path = Path(args.gimbal_cfg).expanduser()
	if not args.no_servo and gimbal_cfg_path.is_file():
		try:
			import pigpio as _pg  # noqa: F401  (alleen voor connect-check)

			_pi = _pg.pi()
			if not _pi.connected:
				print(
					"WARN: pigpiod niet bereikbaar — servo-controller uit. "
					"Start met: sudo systemctl start pigpiod",
					file=sys.stderr,
				)
			else:
				driver = make_pigpio_driver(
					_pi,
					int(args.servo_enable_pin),
					active_low=bool(args.servo_enable_active_low),
				)
				servo = ServoController(driver, gimbal_cfg_path)
				cal_ok = servo.calibration_complete()
				print(
					"Servo-controller actief — cal=%s rail-pin=%d (%s)"
					% (
						"complete" if cal_ok else "incompleet",
						int(args.servo_enable_pin),
						"active-low" if args.servo_enable_active_low else "active-high",
					)
				)
		except ImportError:
			print(
				"WARN: pigpio niet geïnstalleerd — servo-controller uit. "
				"Installeer met: sudo apt install python3-pigpio",
				file=sys.stderr,
			)
	elif not args.no_servo:
		print(
			"WARN: gimbal-cfg %s ontbreekt — servo-controller uit." % gimbal_cfg_path,
			file=sys.stderr,
		)

	# Gimbal closed-loop-regelaar (Fase 9 active control). Vereist servo-
	# calibratie (beide ``center_us``) én een BNO055 (gravity-reads). Zonder
	# één van beide blijft de loop ``None`` en zijn GIMBAL-commando's
	# onbeschikbaar (``ERR GMB NOHW``). De loop regelt pas echt wanneer (a)
	# hij enabled is en (b) ``flight_state == DEPLOYED``; dat dubbele gate
	# komt terug in de main-loop tick.
	gimbal_loop: Optional[GimbalLoop] = None
	if servo is not None and bno055 is not None:
		cal1 = servo.calibration_for(1)
		cal2 = servo.calibration_for(2)
		if (
			cal1 is not None
			and cal2 is not None
			and cal1.center_us is not None
			and cal2.center_us is not None
		):
			gimbal_loop = GimbalLoop(
				cal1=cal1,
				cal2=cal2,
				kx=float(args.gimbal_kx),
				ky=float(args.gimbal_ky),
				kix=float(args.gimbal_kix),
				kiy=float(args.gimbal_kiy),
				max_us_step=int(args.gimbal_max_us_step),
				swap_control_axes=bool(args.gimbal_swap_axes),
			)
			if args.gimbal_auto_enable:
				gimbal_loop.enable()
			print(
				"Gimbal-loop beschikbaar — kx=%.1f ky=%.1f kix=%.1f kiy=%.1f "
				"step=%d µs/tick (%s)"
				% (
					float(args.gimbal_kx),
					float(args.gimbal_ky),
					float(args.gimbal_kix),
					float(args.gimbal_kiy),
					int(args.gimbal_max_us_step),
					"auto-on" if args.gimbal_auto_enable else "off — !gimbal on om te activeren",
				)
			)
		else:
			print(
				"WARN: gimbal-loop uit — servo-calibratie niet compleet "
				"(center_us ontbreekt). Kalibreer via 'scripts/gimbal/servo_calibration.py'.",
				file=sys.stderr,
			)

	# Camera-thread (Fase 9). Start alleen als --no-camera NIET meegegeven is
	# én picamera2 + apriltag + cv2 allemaal importeerbaar zijn. Registry
	# wordt altijd geladen (ook als camera uit staat) zodat ``--tag-registry``-
	# validatie transparant is; de buffer blijft dan gewoon leeg.
	camera_thread = None
	camera_services = None
	tag_buffer = None
	picam2_handle: Any = None
	tag_registry_obj = None
	from cansat_hw.camera import (
		CameraServices,
		CameraThread,
		CameraUnavailable,
		TagBuffer,
		load_apriltag_detector,
		load_tag_registry,
		make_opencv_jpeg_save_fn,
	)

	tag_registry_path = Path(args.tag_registry).expanduser()
	tag_registry_obj = load_tag_registry(tag_registry_path)
	if not tag_registry_path.is_file():
		print(
			"WARN: tag-registry %s ontbreekt — defaults actief (focal=%.1f mm, default_size=%d mm)"
			% (
				tag_registry_path,
				tag_registry_obj.focal_length_mm,
				tag_registry_obj.default_size_mm,
			),
			file=sys.stderr,
		)

	if not args.no_camera:
		cam_w: Optional[int] = None
		cam_h: Optional[int] = None
		try:
			cam_w_s, cam_h_s = args.camera_resolution.lower().split("x", 1)
			cam_w = int(cam_w_s)
			cam_h = int(cam_h_s)
		except (ValueError, AttributeError):
			print(
				"WARN: --camera-resolution %r ongeldig (verwacht WxH, bv. 4056x3040); camera uit"
				% args.camera_resolution,
				file=sys.stderr,
			)
		if cam_w is not None and cam_h is not None:
			try:
				from cansat_hw.camera.hardware import (
					make_opencv_preprocess_fn,
					make_picamera2_capture_fn,
				)

				picam2_handle, capture_fn = make_picamera2_capture_fn(
					resolution=(cam_w, cam_h)
				)
				preprocess_fn = make_opencv_preprocess_fn()
				detector = load_apriltag_detector(
					families=args.camera_tag_families,
					quad_decimate=2.0,
				)
				tag_buffer = TagBuffer()
				# Shared JPEG-saver: zowel de thread (DEPLOYED fallback-saves
				# elke N frames) als de services (synchrone ``CAM SHOOT`` in
				# CONFIG) gebruiken hetzelfde cv2-pad. Zo is één cv2-import,
				# één quality-setting, één foutpad.
				photo_dir_path = Path(args.photo_dir).expanduser()
				try:
					jpeg_save_fn = make_opencv_jpeg_save_fn(quality=90)
				except CameraUnavailable:
					jpeg_save_fn = None
				save_every = max(0, int(args.deploy_save_every_n))
				camera_thread = CameraThread(
					buffer=tag_buffer,
					registry=tag_registry_obj,
					capture_fn=capture_fn,
					preprocess_fn=preprocess_fn,
					detector=detector,
					target_fps=float(args.camera_fps),
					detect_width=int(args.camera_detect_width),
					save_every_n_frames=save_every if jpeg_save_fn is not None else 0,
					save_dir=photo_dir_path if jpeg_save_fn is not None else None,
					save_fn=jpeg_save_fn,
				)
				camera_thread.start()
				# Services-container voor synchrone CONFIG-commando's
				# (``CAM SHOOT`` / ``CAM DETECT``). Deelt dezelfde capture +
				# detector + registry als de thread; de wire-handler weigert
				# met ``ERR CAM BUSY`` zolang de thread actief is (DEPLOYED),
				# dus er is geen gelijktijdige toegang tot Picamera2.
				camera_services = CameraServices(
					capture_fn=capture_fn,
					preprocess_fn=preprocess_fn,
					detector=detector,
					registry=tag_registry_obj,
					photo_dir=photo_dir_path,
					detect_width=int(args.camera_detect_width),
					save_fn=jpeg_save_fn,
				)
				print(
					"Camera-thread actief — %dx%d capture, %d px detect, %.1f fps cap, "
					"family=%s, save-every=%d (%s)"
					% (
						cam_w,
						cam_h,
						int(args.camera_detect_width),
						float(args.camera_fps),
						args.camera_tag_families,
						save_every,
						"uit" if jpeg_save_fn is None or save_every == 0 else photo_dir_path,
					)
				)
			except CameraUnavailable as e:
				print(
					"WARN: camera-thread uit — %s" % e,
					file=sys.stderr,
				)
				camera_thread = None
				camera_services = None
				tag_buffer = None
				picam2_handle = None
			except Exception as e:  # noqa: BLE001
				print(
					"WARN: camera-thread init mislukte: %s — camera uit" % e,
					file=sys.stderr,
				)
				camera_thread = None
				camera_services = None
				tag_buffer = None
				picam2_handle = None

	def _emit_evt_state_if_changed() -> None:
		"""Stuur ongevraagd ``EVT STATE <NAME> [<REASON>]`` als de state-
		machine sinds de laatste aankondiging een transitie maakte. Wordt
		zowel na elke sampler-tick als na een command-RX aangeroepen zodat
		de operator real-time transities ziet, óók als er geen commando in
		de cache zit. Idempotent — als ``flight_state ==
		last_announced_flight_state`` doet hij niks.
		"""
		if state.flight_state == state.last_announced_flight_state:
			return
		# CONFIG → flight_state=NONE: niet over de lucht aankondigen.
		# We loggen geen "EVT STATE NONE" — dat zou de basisstation-
		# operator alleen verwarren. Wel intern de bookkeeping syncen
		# zodat de eerstvolgende echte transitie (bv. MISSION → PAD_IDLE)
		# opnieuw een delta toont.
		from cansat_hw.telemetry.codec import STATE_NONE as _STATE_NONE
		if state.flight_state == _STATE_NONE:
			state.last_announced_flight_state = int(state.flight_state)
			return
		dest_evt = (
			state.test_dest_node
			if state.test_dest_node is not None
			else args.dest
		)
		name = state_name(state.flight_state)
		reason = state.last_transition_reason
		if reason:
			evt_state = ("EVT STATE %s %s" % (name, reason)).encode("utf-8")
		else:
			evt_state = ("EVT STATE %s" % name).encode("utf-8")
		ok_evt = rfm.send(evt_state, keep_listening=True, destination=dest_evt)
		if args.verbose or not ok_evt:
			print(
				"STATE -> %s%s (dest=%d ok=%s)"
				% (
					name,
					(" %s" % reason) if reason else "",
					dest_evt,
					ok_evt,
				)
			)
		log_manager.write_payload(evt_state)
		state.last_announced_flight_state = int(state.flight_state)

	# Bookkeeping voor de servo-state-policy: vorige (mode, flight_state).
	# Bij elke iteratie vergelijken we dit met de actuele waarde en passen
	# de policy toe (PARK / ENABLE / DISABLE / NONE). Door de hook éénmalig
	# bovenaan de loop te runnen, vangen we transities ongeacht of ze door
	# een Pico-commando, sampler-tick of TEST-end-timer veroorzaakt zijn.
	servo_prev_state: tuple = (state.mode, int(state.flight_state))

	def _apply_servo_policy() -> None:
		"""Pas state-policy toe als (mode, flight_state) sinds vorige iteratie veranderde."""
		nonlocal servo_prev_state
		if servo is None:
			servo_prev_state = (state.mode, int(state.flight_state))
			return
		now_state = (state.mode, int(state.flight_state))
		if now_state == servo_prev_state:
			return
		action = action_for_transition(servo_prev_state, now_state)
		try:
			if action == ServoAction.PARK:
				servo.park_all()
			elif action == ServoAction.HOME:
				servo.home_all()
			elif action == ServoAction.ENABLE:
				servo.enable_rail()
			elif action == ServoAction.DISABLE:
				servo.disable_rail()
		except Exception as e:  # noqa: BLE001
			print(
				"WARN: servo policy %s na %s->%s faalde: %s"
				% (action.value, servo_prev_state, now_state, e),
				file=sys.stderr,
			)
		else:
			if args.verbose and action != ServoAction.NONE:
				print(
					"SERVO: policy %s na transitie %s -> %s"
					% (action.value, servo_prev_state, now_state)
				)
		servo_prev_state = now_state

	try:
		rfm.frequency_mhz = args.freq
		rfm.encryption_key = key
		rfm.node = args.node
		rfm.destination = args.dest
		rfm.tx_power = args.tx_power
		rfm.receive_timeout = min(args.poll, 10.0)

		banner_tail = (
			"Ctrl+C of STOP RADIO om te stoppen — geen !-commando's hier; die zijn voor de Pico base station"
		)
		if args.reply_delay > 0:
			banner_tail += f" — reply-delay {args.reply_delay}s"
		if args.dio0_pin is not None:
			banner_tail += f" — DIO0 IRQ GPIO{args.dio0_pin}"
		if bme280 is not None:
			banner_tail += " — BME280"
		if bno055 is not None:
			banner_tail += " — BNO055"
		print(
			"CanSat (Zero 2 W) radio protocol — node",
			args.node,
			"freq",
			rfm.frequency_mhz,
			"MHz  mode",
			state.mode,
			f"({banner_tail})",
		)
		while True:
			# TEST/MISSION: timer + periodieke telemetrie vóór we verder luisteren.
			# Geen threads, geen locking — pure coöperatieve scheduling tussen
			# twee opeenvolgende receive()-calls. Veilig op de Zero 2 W en
			# voorkomt half-duplex conflicten met inkomende commando's. In
			# MISSION is dit óók de motor van de flight-state-machine: zonder
			# autonome TLM-reads beweegt PAD_IDLE → ASCENT → DEPLOYED → LANDED
			# nooit (ook niet als de Pico tijdelijk uit range is).
			# Eerst de sampler tikken: 1 BME-read + 1 BNO-read per iteratie.
			# In MISSION/TEST is de loop ~5 Hz (rx_timeout 0.2s); in CONFIG
			# ~1 Hz (args.poll). Dat is voldoende voor de IMU-rolling-stats —
			# de echte IMU-triggers komen pas na lift-off in MISSION.
			sampler.tick(ground_hpa=state.ground_hpa)
			# Multi-trigger evaluatie elke tick (≈5 Hz in MISSION/TEST,
			# ≈1 Hz in CONFIG). Cruciaal: korte IMU-pieken tussen TLM-
			# ticks (default 1 Hz) zouden anders gemist worden voor
			# state-overgangen omdat ``build_telemetry_packet`` enkel bij
			# een TLM-tick de evaluatie deed. ``maybe_advance_flight_state``
			# is een no-op buiten MISSION.
			maybe_advance_flight_state(state, sampler.snapshot)
			# Als de state net gewijzigd is, EVT direct uitsturen. Geen
			# wachten op een Pico-commando — anders zie je de transitie
			# nooit aan operator-zijde (Pico zit dan op input()).
			_emit_evt_state_if_changed()
			# Servo-rail policy. Reageer op elke (mode, flight_state)-overgang
			# zodat de gimbal autonoom geënabled/gepark wordt — onafhankelijk
			# van de oorzaak (Pico-commando of state-machine).
			_apply_servo_policy()
			# Camera-policy: alleen tijdens DEPLOYED capturen + detecteren.
			# In PAD_IDLE/ASCENT/LANDED pauzeert de thread (spec Fase 9:
			# CPU + warmte sparen). De ``set_active`` helper is idempotent.
			if camera_thread is not None:
				camera_thread.set_active(
					int(state.flight_state) == int(_STATE_DEPLOYED)
				)
			# Gimbal closed-loop tick. Tweevoudige gate:
			#   (a) loop bestaat + is enabled (via CLI of GIMBAL ON),
			#   (b) rail staat aan.
			# ``rail_on`` is meteen de veiligheidsbarrière: buiten CONFIG
			# kan de operator de rail niet handmatig aanzetten (``SERVO
			# ENABLE`` is CONFIG-only, zie wire_protocol), en de state-
			# policy schakelt hem enkel in bij ``DEPLOYED`` (mission of
			# test). Dus in PAD_IDLE/ASCENT/LANDED schrijft de loop nooit
			# pulses, en in CONFIG werkt hij wél wanneer de operator
			# expliciet ``!servo home`` + ``!gimbal on`` doet — cruciaal
			# voor as-mapping / sign-diagnose zonder hele ``!test``-cyclus.
			if (
				gimbal_loop is not None
				and gimbal_loop.enabled
				and servo is not None
				and servo.rail_on
			):
				try:
					grav = bno055.read_gravity() if bno055 is not None else None
				except Exception:  # noqa: BLE001 — I²C fout mag de main loop niet slopen
					grav = None
				target = gimbal_loop.tick(
					grav,
					now_monotonic=time.monotonic(),
				)
				if target is not None and servo is not None:
					try:
						us1, us2 = target
						g1 = servo.calibration_for(1)
						g2 = servo.calibration_for(2)
						if g1 is not None:
							servo.set_pulse(1, int(us1))
						if g2 is not None:
							servo.set_pulse(2, int(us2))
					except Exception as e:  # noqa: BLE001
						print(
							"WARN: gimbal write faalde: %s" % e,
							file=sys.stderr,
						)
			# Tuning-watchdog: kapt een vergeten SERVO START na 60 s zodat de
			# rail niet onbedoeld aan blijft staan en LiPo leegloopt.
			if servo is not None:
				wd = servo.tick()
				if wd:
					evt_wd = b"EVT SERVO WATCHDOG"
					dest_wd = (
						state.test_dest_node
						if state.test_dest_node is not None
						else args.dest
					)
					rfm.send(evt_wd, keep_listening=True, destination=dest_wd)
					log_manager.write_payload(evt_wd)
					print("SERVO: tuning watchdog kapte sessie -> stopped")
			send_tlm, end_test = test_mode_tick(state)
			if end_test:
				dest = state.test_dest_node if state.test_dest_node is not None else args.dest
				evt = b"EVT MODE CONFIG END_TEST"
				ok_evt = rfm.send(evt, keep_listening=True, destination=dest)
				if args.verbose or not ok_evt:
					print(
						"TEST: timer expired, EVT MODE CONFIG ->",
						dest,
						"ok=",
						ok_evt,
					)
				# Log de EVT als losse payload (Pico/laptop kunnen 'm zo
				# herleiden), dan pas mode_change zodat het nog in de TEST-
				# sessie terechtkomt vóór die sluit.
				log_manager.write_payload(evt)
				old_mode = state.mode
				test_mode_end(state)
				if old_mode != state.mode:
					log_manager.on_mode_change(old_mode, state.mode)
				# Terug naar de responsieve CONFIG-IIR (bv. 4) zodat !alt weer
				# snel reageert; test_mode_end heeft state.mode al op CONFIG gezet.
				apply_mode_iir(state, bme280)
			elif send_tlm:
				dest = state.test_dest_node if state.test_dest_node is not None else args.dest
				tags_for_tlm = (
					tag_buffer.snapshot() if tag_buffer is not None else None
				)
				tlm = build_telemetry_packet(
					state,
					bme280,
					bno055,
					snapshot=sampler.snapshot,
					tags=tags_for_tlm,
				)
				ok_tlm = rfm.send(tlm, keep_listening=True, destination=dest)
				log_manager.write_payload(tlm)
				if args.verbose:
					print(
						"TLM ->",
						dest,
						f"mode={state.mode}",
						f"seq={state.tlm_seq}",
						f"({len(tlm)} B binary)",
						"ok=",
						ok_tlm,
					)
				elif not ok_tlm:
					print("WARN: TLM TX failed (radio timeout)", file=sys.stderr)
				# Kies interval per mode: TEST = vaste 1 Hz dry-run cadence,
				# MISSION = CLI-configureerbaar (default 1.0 s).
				interval = (
					float(args.mission_tlm_interval)
					if state.mode == "MISSION"
					else TEST_MODE_TLM_INTERVAL_S
				)
				test_mode_advance_tlm(state, interval_s=interval)

			# Korte receive-timeout in TEST/MISSION zodat de TLM-scheduler
			# responsief blijft (anders zou args.poll—vaak 1+ s—de cadence dempen).
			rx_timeout = 0.2 if state.mode in ("TEST", "MISSION") else args.poll
			# Gimbal-diagnose in CONFIG: wanneer de loop actief is én de
			# rail aan staat, willen we ook daar op ~5 Hz tikken. Anders
			# zou de regelaar in CONFIG maar args.poll-keer per seconde
			# updaten (≈1 Hz), wat bij ``--gimbal-max-us-step 20`` = 20
			# µs/s oplevert — veel te traag om visueel kantel-diagnose
			# te doen.
			if (
				gimbal_loop is not None
				and gimbal_loop.enabled
				and servo is not None
				and servo.rail_on
			):
				rx_timeout = min(rx_timeout, 0.2)
			# with_header=True: afzender = byte 1 voor reply destination
			# with_ack=False: geen RadioHead-ACK vóór onze tekstantwoord
			pkt = rfm.receive(timeout=rx_timeout, with_header=True, with_ack=False, keep_listening=True)
			if pkt is None:
				continue
			if len(pkt) < 5:
				continue
			from_node = pkt[1]
			mode_before = state.mode
			try:
				line = pkt[4:].decode("utf-8")
			except UnicodeDecodeError:
				reply = b"ERR UTF8"
			else:
				if args.verbose:
					print("RX from", from_node, ":", line.strip())
				reply = handle_wire_line(
					rfm,
					state,
					line,
					bme280=bme280,
					bno055=bno055,
					photo_dir=args.photo_dir,
					gimbal_cfg=args.gimbal_cfg,
					servo=servo,
					camera_services=camera_services,
					camera_thread=camera_thread,
					gimbal_loop=gimbal_loop,
				)
				if args.verbose:
					print("TX to  ", from_node, ":", reply.decode("utf-8", errors="replace"))

			if args.reply_delay > 0:
				time.sleep(args.reply_delay)
			ok = rfm.send(reply, keep_listening=True, destination=from_node)
			if args.verbose:
				print("reply TX ok:", ok, " bytes:", len(reply))
			elif not ok:
				print("WARN: reply TX failed (radio timeout)", file=sys.stderr)
			if args.verbose:
				print("state.mode =", state.mode)

			# Half-duplex marge: gun de Pico een rustig venster (~300 ms)
			# direct na een reply. Anders kan de TLM-scheduler meteen weer
			# zenden terwijl de Pico nog bezig is een vervolgcommando over de
			# lucht te zetten — die clash betekent verloren commando ÉN een
			# pass-through TLM in plaats van het volgende OK-antwoord.
			if ok and state.mode in ("TEST", "MISSION"):
				quiet_until = time.monotonic() + 0.3
				if (
					state.test_next_tlm_monotonic is None
					or state.test_next_tlm_monotonic < quiet_until
				):
					state.test_next_tlm_monotonic = quiet_until

			# Net overgegaan naar TEST of MISSION? Onthoud wie het vroeg
			# zodat unsolicited TLM/EVT naar datzelfde node terug gaan.
			# (``test_dest_node`` is generiek "active session dest"; naam
			# blijft voorlopig zo voor backwards-compat met fase 4-tests.)
			if mode_before != "TEST" and state.mode == "TEST" and ok:
				state.test_dest_node = int(from_node)
				if args.verbose:
					print("TEST: destination set to node", from_node)
			if mode_before != "MISSION" and state.mode == "MISSION" and ok:
				state.test_dest_node = int(from_node)
				if args.verbose:
					print("MISSION: destination set to node", from_node)

			# Mode-overgang doorgeven aan log writer zodat hij eventueel
			# een nieuwe ``cansat_<mode>_<UTC>.bin`` opent of afsluit. We
			# doen dit pas na een geslaagde TX zodat het log de werkelijk
			# bevestigde state weerspiegelt.
			if mode_before != state.mode and ok:
				log_manager.on_mode_change(mode_before, state.mode)

			# Flight-state-overgang? Stuur ongevraagd ``EVT STATE <NAME>
			# [<REASON>]`` zodat het base station meteen kan reageren (UI,
			# log). De helper is idempotent en wordt ook na elke sampler-
			# tick aangeroepen, dus deze call vangt alleen het geval dat
			# de transitie net gebeurde door een command (bv. SET STATE).
			_emit_evt_state_if_changed()

			if state.pending_freq_mhz is not None and ok:
				new_freq = float(state.pending_freq_mhz)
				state.pending_freq_mhz = None
				try:
					rfm.frequency_mhz = new_freq
					print(f"Nieuwe RF-freq toegepast: {new_freq} MHz")
				except Exception as e:  # noqa: BLE001
					print("WARN: nieuwe freq toepassen mislukte:", e, file=sys.stderr)
				err = _save_persisted_freq(runtime_path, new_freq)
				if err:
					print("WARN: persist freq mislukte:", err, file=sys.stderr)
				else:
					print(f"Freq persistent in {runtime_path}")

			if state.exit_after_reply:
				print("STOP RADIO: exiting.")
				break
	except KeyboardInterrupt:
		print("\nStopped.")
		return 0
	finally:
		rfm.close()
		if bme280 is not None:
			try:
				bme280.close()
			except Exception:
				pass
		if bno055 is not None:
			try:
				bno055.close()
			except Exception:
				pass
		# Camera-thread stoppen vóór servo/log — zo stopt de Picamera2-driver
		# schoon (geen stale frames op het socket) en kunnen eventuele laatste
		# log-writes nog mee.
		if camera_thread is not None:
			try:
				camera_thread.stop()
			except Exception as e:  # noqa: BLE001
				print("WARN: camera-thread stop faalde:", e, file=sys.stderr)
		if picam2_handle is not None:
			try:
				picam2_handle.stop()
			except Exception:  # noqa: BLE001
				pass

		# Servo shutdown vóór log_manager.close() zodat een eventuele atexit-
		# park-EVT nog gelogd kan worden. Bij rail-aan park'en we netjes naar
		# stowed; bij actieve tuning kappen we de sessie zonder beweging.
		if servo is not None:
			try:
				action = action_for_shutdown(servo.rail_on)
				if action == ServoAction.PARK:
					print("SERVO: atexit park (rail was aan)")
				servo.shutdown()
			except Exception as e:  # noqa: BLE001
				print("WARN: servo shutdown faalde:", e, file=sys.stderr)
			# pigpio-pi loskoppelen indien we hem zelf opzetten.
			try:
				import pigpio as _pg

				_pi_handle = getattr(servo, "_driver", None)
				_pi_obj = getattr(_pi_handle, "_pi", None)
				if _pi_obj is not None and isinstance(_pi_obj, _pg.pi):
					_pi_obj.stop()
			except Exception:  # noqa: BLE001
				pass
		try:
			log_manager.close()
		except Exception:  # noqa: BLE001
			pass

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
