"""Interactieve servo-calibratie (pigpio). Standaard-JSON: ``config/gimbal/servo_calibration.json``."""

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional

import pigpio

_REPO = Path(__file__).resolve().parents[2]
_src = _REPO / "src"
if _src.is_dir() and str(_src) not in sys.path:
	sys.path.insert(0, str(_src))

_DEFAULT_JSON = _REPO / "config" / "gimbal" / "servo_calibration.json"

from cansat_hw.servos.power_enable import servo_rail_configure, servo_rail_set


@dataclass
class ServoCalibration:
	gpio: int
	min_us: Optional[int] = None
	center_us: Optional[int] = None
	max_us: Optional[int] = None

	def clamp(self, us: int) -> int:
		if self.min_us is not None:
			us = max(us, self.min_us)
		if self.max_us is not None:
			us = min(us, self.max_us)
		return us


def _print_help() -> None:
	msg = """
Controls
  q                quit (turns off servo pulses)
  1 / 2            select servo 1 / servo 2
  a / d            -step / +step (small)
  A / D            -bigstep / +bigstep
  z                set current as MIN for selected servo
  x                set current as CENTER for selected servo
  c                set current as MAX for selected servo
  p                print current state
  s                save calibration JSON
  o                turn OFF pulses for selected servo
  O                turn OFF pulses for BOTH servos

Notes
  - pigpio uses BCM GPIO numbering.
  - If you previously used RPi.GPIO with GPIO.BOARD 32/33, those are typically BCM12/BCM13.
  - Defaults --gpio1 13 / --gpio2 12 match this repo's crossed-PWM layout; use 12/13 if your motors follow physical pin 32/33 without swap.
  - Typical servo range is around 1000..2000 us, center ~1500 us. Start conservative.
""".strip("\n")
	print(msg)


def _status_line(
	selected: int,
	current_us: Dict[int, int],
	cal: Dict[int, ServoCalibration],
) -> str:
	def fmt(i: int) -> str:
		c = cal[i]
		return (
			f"S{i} gpio={c.gpio} us={current_us[i]} "
			f"[min={c.min_us} center={c.center_us} max={c.max_us}]"
		)

	return f"selected=S{selected} | {fmt(1)} | {fmt(2)}"


def _load_existing(path: Path) -> Dict[str, object]:
	if not path.exists():
		return {}
	try:
		return json.loads(path.read_text(encoding="utf-8"))
	except Exception:
		return {}


def main() -> int:
	parser = argparse.ArgumentParser(add_help=True)
	parser.add_argument(
		"--gpio1",
		type=int,
		default=13,
		help="Servo 1 BCM GPIO (default 13 — repo matcht omgewisselde PWM t.o.v. pin 32/33)",
	)
	parser.add_argument(
		"--gpio2",
		type=int,
		default=12,
		help="Servo 2 BCM GPIO (default 12 — zie --gpio1)",
	)
	parser.add_argument("--start-us", type=int, default=1500, help="Start pulsewidth in microseconds")
	parser.add_argument("--step", type=int, default=10, help="Small step in microseconds")
	parser.add_argument("--bigstep", type=int, default=50, help="Big step in microseconds")
	parser.add_argument(
		"--json",
		type=Path,
		default=_DEFAULT_JSON,
		help="Pad voor servo_calibration.json (standaard repo config/gimbal/)",
	)
	parser.add_argument(
		"--enable-pin",
		type=int,
		default=6,
		help="BCM GPIO voor servo-motorvoeding (0 = niet gebruiken, oude setup)",
	)
	parser.add_argument(
		"--enable-active-low",
		action="store_true",
		help="Als gezet: laag = voeding aan (active-low driver)",
	)
	args = parser.parse_args()

	_enable_pin = int(args.enable_pin)
	_enable_al = bool(args.enable_active_low)

	pi = pigpio.pi()
	if not pi.connected:
		print(
			"Could not connect to pigpiod. Start it with: sudo systemctl start pigpiod",
			file=sys.stderr,
		)
		return 2

	cal: Dict[int, ServoCalibration] = {
		1: ServoCalibration(gpio=args.gpio1),
		2: ServoCalibration(gpio=args.gpio2),
	}

	existing = _load_existing(args.json)
	if isinstance(existing, dict):
		for idx, key in [(1, "servo1"), (2, "servo2")]:
			val = existing.get(key)
			if isinstance(val, dict):
				for field in ["min_us", "center_us", "max_us"]:
					if field in val and isinstance(val[field], int):
						setattr(cal[idx], field, val[field])

	current_us: Dict[int, int] = {
		1: cal[1].clamp(int(args.start_us)),
		2: cal[2].clamp(int(args.start_us)),
	}

	selected = 1

	try:
		servo_rail_configure(pi, _enable_pin, active_low=_enable_al)
		servo_rail_set(pi, _enable_pin, True, active_low=_enable_al)

		for i in [1, 2]:
			pi.set_mode(cal[i].gpio, pigpio.OUTPUT)
			pi.set_servo_pulsewidth(cal[i].gpio, current_us[i])

		_print_help()
		print(_status_line(selected, current_us, cal))

		while True:
			cmd = input("> ").strip()
			if not cmd:
				continue

			if cmd == "q":
				break

			if cmd == "1":
				selected = 1
				print(_status_line(selected, current_us, cal))
				continue
			if cmd == "2":
				selected = 2
				print(_status_line(selected, current_us, cal))
				continue

			if cmd in {"a", "d", "A", "D"}:
				delta = args.step if cmd in {"a", "d"} else args.bigstep
				if cmd in {"a", "A"}:
					delta = -delta
				new_us = current_us[selected] + int(delta)
				new_us = cal[selected].clamp(new_us)
				current_us[selected] = new_us
				pi.set_servo_pulsewidth(cal[selected].gpio, new_us)
				print(_status_line(selected, current_us, cal))
				continue

			if cmd == "z":
				cal[selected].min_us = current_us[selected]
				print(_status_line(selected, current_us, cal))
				continue
			if cmd == "x":
				cal[selected].center_us = current_us[selected]
				print(_status_line(selected, current_us, cal))
				continue
			if cmd == "c":
				cal[selected].max_us = current_us[selected]
				print(_status_line(selected, current_us, cal))
				continue

			if cmd == "o":
				pi.set_servo_pulsewidth(cal[selected].gpio, 0)
				print(f"S{selected} OFF")
				continue
			if cmd == "O":
				pi.set_servo_pulsewidth(cal[1].gpio, 0)
				pi.set_servo_pulsewidth(cal[2].gpio, 0)
				print("BOTH OFF")
				continue

			if cmd == "p":
				print(_status_line(selected, current_us, cal))
				continue

			if cmd == "s":
				data = {
					"servo1": asdict(cal[1]),
					"servo2": asdict(cal[2]),
					"saved_at": int(time.time()),
				}
				args.json.parent.mkdir(parents=True, exist_ok=True)
				args.json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
				print(f"Saved to {args.json}")
				continue

			if cmd in {"h", "help", "?"}:
				_print_help()
				continue

			print("Unknown command. Type 'h' for help.")

	finally:
		try:
			pi.set_servo_pulsewidth(cal[1].gpio, 0)
			pi.set_servo_pulsewidth(cal[2].gpio, 0)
			servo_rail_set(pi, _enable_pin, False, active_low=_enable_al)
		finally:
			pi.stop()

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
