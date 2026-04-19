#!/usr/bin/env bash
#
# fetch_zero_logs.sh — haal alle logs van de CanSat Zero op naar lokaal.
#
# Wat wordt opgehaald:
#   1. Binary log-files uit ~/cansat_logs/ op de Zero (TLM/EVT/HEADER records).
#   2. systemd-journal van de cansat-radio-protocol service (laatste 24 u).
#
# Layout lokaal (alles onder ./zero_logs/, dat staat in .gitignore):
#   zero_logs/
#     latest/                           ← altijd de meest recente fetch
#       journal.log
#       cansat_*.bin
#       decoded/
#         summary.txt                   ← output van scripts/decode_logs.py
#         flight_<sessie>.csv           ← één CSV per mission/test-sessie
#     archive/
#       2026-04-19T17-14-32/            ← snapshot van vorige `latest/`
#         ...
#
# De vorige `latest/` wordt automatisch verplaatst naar `archive/<timestamp>/`
# zodat je nooit per ongeluk een voorgaande sessie kwijtraakt.
#
# Usage:
#   scripts/fetch_zero_logs.sh                       # default = icw@RPITSM0
#   scripts/fetch_zero_logs.sh user@host             # andere host
#   SKIP_DECODE=1 scripts/fetch_zero_logs.sh         # geen automatische decode
#   JOURNAL_SINCE='2 hours ago' scripts/fetch_zero_logs.sh
#   REMOTE_LOG_DIR=/home/icw/cansat_logs scripts/fetch_zero_logs.sh

set -euo pipefail

TARGET="${1:-icw@RPITSM0}"
REMOTE_LOG_DIR="${REMOTE_LOG_DIR:-/home/icw/cansat_logs}"
JOURNAL_SINCE="${JOURNAL_SINCE:-24 hours ago}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_BASE="${REPO_ROOT}/zero_logs"
LATEST_DIR="${LOCAL_BASE}/latest"
ARCHIVE_DIR="${LOCAL_BASE}/archive"
TIMESTAMP="$(date +%Y-%m-%dT%H-%M-%S)"

mkdir -p "${ARCHIVE_DIR}"

# Stap 1: archiveer vorige fetch (als die bestaat en niet leeg is). We
# verplaatsen i.p.v. kopiëren — sneller en eet geen extra disk. Een leeg
# `latest/` (alleen `decoded/` map) telt als 'niets te archiveren'.
if [[ -d "${LATEST_DIR}" ]]; then
	if find "${LATEST_DIR}" -maxdepth 2 -type f \
			\( -name '*.bin' -o -name 'journal.log' \) \
			-print -quit | grep -q .; then
		echo ">> archiveer vorige fetch → archive/${TIMESTAMP}/"
		mv "${LATEST_DIR}" "${ARCHIVE_DIR}/${TIMESTAMP}"
	else
		# Leeg of alleen verouderde decoded/ → gewoon weggooien.
		rm -rf "${LATEST_DIR}"
	fi
fi
mkdir -p "${LATEST_DIR}"

# Stap 2: rsync binaries. ``--ignore-missing-args`` voorkomt dat een lege
# log-dir het script kraakt; we willen dan alleen de journal nog hebben.
echo ">> rsync ${TARGET}:${REMOTE_LOG_DIR}/ → zero_logs/latest/"
if ! rsync -av --partial --human-readable \
		"${TARGET}:${REMOTE_LOG_DIR}/" "${LATEST_DIR}/"; then
	echo "WARN: rsync van binary logs faalde; ga verder met journal"
fi

# Stap 3: journal van de service. ``--no-pager`` is cruciaal anders blokkeert
# ssh op een interactieve less-prompt. We snijden expliciet op tijd zodat
# logs van weken geleden niet meegaan.
echo ">> journalctl -u cansat-radio-protocol --since='${JOURNAL_SINCE}'"
if ! ssh "${TARGET}" \
		"journalctl -u cansat-radio-protocol --no-pager --since='${JOURNAL_SINCE}'" \
		> "${LATEST_DIR}/journal.log" 2> "${LATEST_DIR}/journal.err"; then
	echo "WARN: journal-fetch faalde; zie ${LATEST_DIR}/journal.err"
else
	rm -f "${LATEST_DIR}/journal.err"
fi

# Stap 4: optioneel decoderen. We doen het standaard, maar laten een uitweg
# voor scripts in CI of als de PYTHONPATH/venv lokaal stuk staat.
if [[ "${SKIP_DECODE:-0}" == "1" ]]; then
	echo ">> klaar; SKIP_DECODE=1 → geen decode"
	exit 0
fi

DECODED_DIR="${LATEST_DIR}/decoded"
mkdir -p "${DECODED_DIR}"

PY="${PYTHON:-python3}"
DECODER="${REPO_ROOT}/scripts/decode_logs.py"

shopt -s nullglob
BIN_FILES=("${LATEST_DIR}"/*.bin)
shopt -u nullglob
if [[ ${#BIN_FILES[@]} -eq 0 ]]; then
	echo ">> geen .bin files opgehaald — sla decode over"
	exit 0
fi

echo ">> decode → ${DECODED_DIR}/summary.txt"
PYTHONPATH="${REPO_ROOT}/src" "${PY}" "${DECODER}" "${BIN_FILES[@]}" \
	> "${DECODED_DIR}/summary.txt" || {
		echo "WARN: decode summary faalde — zie summary.txt"
	}

# Eén CSV per mission/test-bestand. Continuous slaan we standaard over;
# die is enorm en bevat duplicaten van de sessie-files. Wil je 'm toch?
# Voeg cansat_continuous.bin manueel toe aan een aparte aanroep.
for bin in "${BIN_FILES[@]}"; do
	base="$(basename "${bin}" .bin)"
	if [[ "${base}" == "cansat_continuous" ]]; then
		continue
	fi
	out="${DECODED_DIR}/${base}.csv"
	PYTHONPATH="${REPO_ROOT}/src" "${PY}" "${DECODER}" --csv "${bin}" \
		> "${out}" 2> /dev/null || rm -f "${out}"
done

echo ">> klaar — ${LATEST_DIR}"
ls -lh "${LATEST_DIR}" "${DECODED_DIR}" 2>/dev/null || true
