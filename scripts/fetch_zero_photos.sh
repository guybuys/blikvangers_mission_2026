#!/usr/bin/env bash
#
# fetch_zero_photos.sh — haal alle JPEG's van de CanSat Zero op naar lokaal.
#
# Wat wordt opgehaald:
#   JPEG's uit ~/photos/ op de Zero. Dat is wat de camera-pipeline schrijft
#   tijdens !shoot, en — afhankelijk van --save-every — ook tijdens
#   DEPLOYED/TEST-runs.
#
# Layout lokaal (alles onder ./zero_photos/, staat in .gitignore):
#   zero_photos/
#     latest/                   ← altijd de meest recente fetch
#       cam_*.jpg
#       ...
#     archive/
#       2026-04-19T17-14-32/    ← snapshot van vorige `latest/`
#         ...
#
# De vorige `latest/` wordt automatisch verplaatst naar `archive/<timestamp>/`
# zodat je nooit per ongeluk foto's kwijtraakt.
#
# Usage:
#   scripts/fetch_zero_photos.sh                         # default = icw@RPITSM0
#   scripts/fetch_zero_photos.sh user@host               # andere host
#   REMOTE_PHOTO_DIR=/home/icw/photos scripts/fetch_zero_photos.sh
#   DELETE_REMOTE=1 scripts/fetch_zero_photos.sh         # wis remote na fetch
#
# DELETE_REMOTE=1 is handig tussen test-sessies om de SD niet te laten
# vollopen. We gebruiken ``rsync --remove-source-files`` zodat er pas iets
# verdwijnt nádat het lokaal gearriveerd is (rsync verifieert de kopie).

set -euo pipefail

TARGET="${1:-icw@RPITSM0}"
REMOTE_PHOTO_DIR="${REMOTE_PHOTO_DIR:-/home/icw/photos}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_BASE="${REPO_ROOT}/zero_photos"
LATEST_DIR="${LOCAL_BASE}/latest"
ARCHIVE_DIR="${LOCAL_BASE}/archive"
TIMESTAMP="$(date +%Y-%m-%dT%H-%M-%S)"

mkdir -p "${ARCHIVE_DIR}"

# Stap 1: archiveer vorige fetch (als die bestaat en niet leeg is).
if [[ -d "${LATEST_DIR}" ]]; then
	if find "${LATEST_DIR}" -maxdepth 2 -type f \
			\( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \) \
			-print -quit | grep -q .; then
		echo ">> archiveer vorige fetch → archive/${TIMESTAMP}/"
		mv "${LATEST_DIR}" "${ARCHIVE_DIR}/${TIMESTAMP}"
	else
		rm -rf "${LATEST_DIR}"
	fi
fi
mkdir -p "${LATEST_DIR}"

# Stap 2: rsync de JPEG's. ``--partial`` laat onderbroken transfers hervatten.
# Met ``DELETE_REMOTE=1`` verwijderen we de bronbestanden pas NA succesvolle
# overdracht (rsync doet dat atomair per file).
RSYNC_OPTS=(-av --partial --human-readable)
if [[ "${DELETE_REMOTE:-0}" == "1" ]]; then
	echo ">> DELETE_REMOTE=1 → remote foto's worden na fetch gewist"
	RSYNC_OPTS+=(--remove-source-files)
fi

echo ">> rsync ${TARGET}:${REMOTE_PHOTO_DIR}/ → zero_photos/latest/"
if ! rsync "${RSYNC_OPTS[@]}" \
		"${TARGET}:${REMOTE_PHOTO_DIR}/" "${LATEST_DIR}/"; then
	echo "WARN: rsync faalde; zie output hierboven"
	exit 1
fi

# Tel en rapporteer. Ruwe grootte is handig om te zien of er wel écht iets is.
shopt -s nullglob
PHOTOS=("${LATEST_DIR}"/*.jpg "${LATEST_DIR}"/*.jpeg "${LATEST_DIR}"/*.png)
shopt -u nullglob
COUNT=${#PHOTOS[@]}

echo ">> klaar — ${COUNT} foto('s) in ${LATEST_DIR}"
if [[ ${COUNT} -gt 0 ]]; then
	du -sh "${LATEST_DIR}" 2>/dev/null || true
	ls -lh "${LATEST_DIR}" 2>/dev/null | head -20
fi
