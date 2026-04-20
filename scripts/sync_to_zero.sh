#!/usr/bin/env bash
#
# sync_to_zero.sh — push de werkkopie naar de CanSat Zero 2 W via rsync.
#
# WAARSCHUWING: gebruik *altijd* dit script en niet rsync direct. Een rsync
# zonder de juiste --exclude's wist op de Zero per ongeluk:
#   - .venv/                 (de Pi-only Python-omgeving — service start niet
#                             meer met status=203/EXEC)
#   - config/radio_runtime.json (persistente RFM69-frequentie — beide kanten
#                                vallen terug op 433.0 MHz default en kunnen
#                                niet meer met elkaar praten)
#   - cansat_logs/, photos/  (sessie-data — nooit lokaal aanwezig)
#
# Na sync herstarten we *automatisch* de systemd-service zodat nieuwe code
# meteen actief is. Skipping kan met SKIP_RESTART=1.
#
# Usage:
#   scripts/sync_to_zero.sh                    # default target = icw@RPITSM0
#   scripts/sync_to_zero.sh user@host          # custom target
#   SKIP_RESTART=1 scripts/sync_to_zero.sh     # alleen bestanden, geen restart
#   DRY_RUN=1 scripts/sync_to_zero.sh          # toon wat er zou gebeuren

set -euo pipefail

TARGET="${1:-icw@RPITSM0}"
REMOTE_DIR="${REMOTE_DIR:-~/cansat_mission_2026/}"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/"

RSYNC_FLAGS=(-av --delete)
if [[ "${DRY_RUN:-0}" == "1" ]]; then
	RSYNC_FLAGS+=(--dry-run)
fi

# Exclude alles wat alleen op de Zero hoort te leven of nooit relevant is om
# te syncen. Volgorde maakt niet uit; --delete wordt door deze patterns dus
# ook geblokkeerd voor matches.
EXCLUDES=(
	'.venv/'
	'.git/'
	'.gitignore'
	'__pycache__/'
	'*.pyc'
	'.pytest_cache/'
	'.mypy_cache/'
	'.ruff_cache/'
	'.DS_Store'
	'.coverage'
	'htmlcov/'

	# Zero-only runtime data — NIET overschrijven of wissen.
	'config/radio_runtime.json'
	# Gimbal-calibratie hoort bij de fysieke hardware (per-servo center/min/max
	# en stow_us). Live-bewerkt op de Zero via SERVO STOW SET / scripts/gimbal/
	# servo_calibration.py. De Mac-kopie in git is een referentie-template; door
	# 'm hier uit te sluiten kan een sync nooit per ongeluk de gekalibreerde
	# stow_us wegvegen of een mechanisch onveilige center_us terugzetten
	# (zie incident 2026-04-19).
	'config/gimbal/servo_calibration.json'
	'cansat_logs/'
	'photos/'

	# Pico-firmware/CLI hoort niet op de Zero.
	'pico_files/'

	# Lokaal analyse-materiaal (fetch_zero_logs.sh + decode_logs.py + plots).
	# ``zero_logs/`` is de Mac-kopie van ~/cansat_logs op de Zero; terug-
	# syncen zou een tweede kopie op een ander pad zetten. De losse CSV/PNG-
	# artifacten komen uit analyse-sessies op de Mac en horen niet thuis op
	# de Zero. Zelfde patronen als in .gitignore — zo blijven git en rsync
	# consistent.
	'zero_logs/'
	'continuous.csv'
	'mission_*.png'
	'mission_*.csv'
	'test_*.png'

	# Lokale dev artifacten.
	'dist/'
	'build/'
	'*.egg-info/'
)

EXCLUDE_FLAGS=()
for pat in "${EXCLUDES[@]}"; do
	EXCLUDE_FLAGS+=(--exclude "$pat")
done

echo ">> rsync ${LOCAL_DIR} -> ${TARGET}:${REMOTE_DIR}"
rsync "${RSYNC_FLAGS[@]}" "${EXCLUDE_FLAGS[@]}" "${LOCAL_DIR}" "${TARGET}:${REMOTE_DIR}"

if [[ "${SKIP_RESTART:-0}" == "1" ]] || [[ "${DRY_RUN:-0}" == "1" ]]; then
	echo ">> sync klaar; service NIET herstart (SKIP_RESTART/DRY_RUN gezet)"
	exit 0
fi

echo ">> systemctl restart cansat-radio-protocol op ${TARGET}"
ssh "${TARGET}" 'sudo systemctl restart cansat-radio-protocol && sleep 2 && sudo systemctl status cansat-radio-protocol --no-pager | head -10'
