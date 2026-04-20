"""Servo-rail policy: tabel-test voor :func:`action_for_transition` (Fase 12)."""

from __future__ import annotations

import unittest

from cansat_hw.servos.state_policy import (
	STATE_ASCENT,
	STATE_DEPLOYED,
	STATE_LANDED,
	STATE_NONE,
	STATE_PAD_IDLE,
	ServoAction,
	action_for_shutdown,
	action_for_transition,
)


class StatePolicyTest(unittest.TestCase):
	def test_identical_transition_is_none(self) -> None:
		prev = ("CONFIG", STATE_NONE)
		self.assertEqual(action_for_transition(prev, prev), ServoAction.NONE)

	def test_config_to_mission_parks(self) -> None:
		out = action_for_transition(
			("CONFIG", STATE_NONE), ("MISSION", STATE_PAD_IDLE)
		)
		self.assertEqual(out, ServoAction.PARK)

	def test_config_to_test_homes(self) -> None:
		# TEST = DEPLOYED-dry-run: zelfde policy als ASCENT→DEPLOYED, dus HOME
		# (rail aan + center_us) i.p.v. een "slap" ENABLE zonder pulse.
		out = action_for_transition(
			("CONFIG", STATE_NONE), ("TEST", STATE_DEPLOYED)
		)
		self.assertEqual(out, ServoAction.HOME)

	def test_test_to_config_disables_without_park(self) -> None:
		# Na TEST willen we de gimbal in home-positie houden voor inspectie;
		# geen stow-beweging meer. Alleen rail uit.
		out = action_for_transition(
			("TEST", STATE_DEPLOYED), ("CONFIG", STATE_NONE)
		)
		self.assertEqual(out, ServoAction.DISABLE)

	def test_pad_idle_to_ascent_does_nothing(self) -> None:
		out = action_for_transition(
			("MISSION", STATE_PAD_IDLE), ("MISSION", STATE_ASCENT)
		)
		self.assertEqual(out, ServoAction.NONE)

	def test_ascent_to_deployed_homes(self) -> None:
		# Parachute open → gimbal actief naar center_us (HOME), niet enkel
		# rail aan zonder pulse. Zo vertrekt de control-loop vanaf een
		# bekende neutrale positie.
		out = action_for_transition(
			("MISSION", STATE_ASCENT), ("MISSION", STATE_DEPLOYED)
		)
		self.assertEqual(out, ServoAction.HOME)

	def test_deployed_to_landed_disables_without_move(self) -> None:
		# Na impact op de grond: NIET meer stowen (gimbal kan in gras/puin
		# staan). Enkel rail uit; operator kan ``!servo park`` sturen na
		# recovery.
		out = action_for_transition(
			("MISSION", STATE_DEPLOYED), ("MISSION", STATE_LANDED)
		)
		self.assertEqual(out, ServoAction.DISABLE)

	def test_mission_to_config_no_autonomous_change(self) -> None:
		# Operator forceerde abort; rail-policy doet niets autonoom.
		out = action_for_transition(
			("MISSION", STATE_PAD_IDLE), ("CONFIG", STATE_NONE)
		)
		self.assertEqual(out, ServoAction.NONE)

	def test_manual_jump_to_landed_disables(self) -> None:
		# SET STATE LANDED vanuit PAD_IDLE — niet realistisch maar moet veilig.
		out = action_for_transition(
			("MISSION", STATE_PAD_IDLE), ("MISSION", STATE_LANDED)
		)
		self.assertEqual(out, ServoAction.DISABLE)

	def test_manual_jump_to_deployed_homes(self) -> None:
		out = action_for_transition(
			("MISSION", STATE_PAD_IDLE), ("MISSION", STATE_DEPLOYED)
		)
		self.assertEqual(out, ServoAction.HOME)

	def test_shutdown_with_rail_on_parks(self) -> None:
		self.assertEqual(action_for_shutdown(True), ServoAction.PARK)

	def test_shutdown_with_rail_off_does_nothing(self) -> None:
		self.assertEqual(action_for_shutdown(False), ServoAction.NONE)


if __name__ == "__main__":
	unittest.main()
