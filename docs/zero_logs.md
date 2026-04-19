# Zero-logs ophalen, archiveren & decoderen

[← Documentatie-index](README.md) · [← Project README](../README.md)

> **Afkortingen die hieronder vaak voorkomen** (volledige lijst in
> [`glossary.md`](glossary.md)):
> **TLM** = *Telemetry* (60-byte sensor-frame),
> **EVT** = *Event* (tekst-melding zoals `EVT STATE LANDED`),
> **HDR** = *Header* (eerste record per log-bestand),
> **CRC** = *Cyclic Redundancy Check* (corruptie-detectie per record),
> **UTC** = *Coordinated Universal Time* (tijdszone-loze wereldtijd).

De Raspberry Pi Zero schrijft tijdens een sessie twee soorten logs:

1. **Binary `.bin`-bestanden** in `~/cansat_logs/` op de Zero — TLM-frames
   (sensor-data), EVT-records (state-overgangen, mode-wissels) en een HDR-record
   per sessie. Geschreven door [`cansat_hw.telemetry.log_writer`](../src/cansat_hw/telemetry/log_writer.py).
   - `cansat_continuous.bin` — alles, eindeloos doorlopend.
   - `cansat_mission_<UTC>.bin` — één per `SET MODE MISSION`.
   - `cansat_test_<UTC>.bin` — één per `SET MODE TEST`.
2. **`systemd-journal`** van `cansat-radio-protocol.service` — `print()`-output,
   warnings, fouten, service-restarts.

Beide moeten naar je laptop voordat je ze kan analyseren. Doe dat **na elke
testsessie** en **niet pas na de lancering** — dan is er niets meer terug
te halen als de SD-kaart corrupt raakt.

## Lokale layout

Alles onder `zero_logs/` (volledig in `.gitignore`, dus nooit per ongeluk
gecommit):

```text
zero_logs/
├── latest/                          ← altijd de meest recente fetch
│   ├── journal.log                  ← systemd-journal van de service
│   ├── cansat_continuous.bin
│   ├── cansat_mission_*.bin
│   ├── cansat_test_*.bin
│   └── decoded/
│       ├── summary.txt              ← human-readable analyse
│       └── cansat_mission_<UTC>.csv ← één CSV per sessie (niet continuous)
└── archive/
    ├── 2026-04-19T17-14-32/         ← snapshot van vorige `latest/`
    └── 2026-04-19T19-02-10/
```

Iedere fetch verhuist de huidige `latest/` automatisch naar
`archive/<timestamp>/` voordat hij nieuwe data binnenhaalt. Je verliest dus
nooit een sessie doordat je `fetch_zero_logs.sh` twee keer draait.

## Eén commando: fetch + decode

```bash
scripts/fetch_zero_logs.sh
```

Wat het doet (zie [`scripts/fetch_zero_logs.sh`](../scripts/fetch_zero_logs.sh)):

1. **Archiveer** vorige `zero_logs/latest/` → `zero_logs/archive/<timestamp>/`.
2. **`rsync`** `~/cansat_logs/` op de Zero → `zero_logs/latest/`.
3. **`journalctl -u cansat-radio-protocol --since='24 hours ago'`** →
   `zero_logs/latest/journal.log`.
4. **Decode** alle nieuwe `.bin`-files met
   [`scripts/decode_logs.py`](../scripts/decode_logs.py):
   - Eén `summary.txt` met state-transities, peak-altitude, peak-‖a‖, cal-status.
   - Eén `.csv` per mission/test-sessie (continuous slaan we over — die is enorm).

### Opties (env-vars)

| Variabele | Default | Wat doet het |
|---|---|---|
| `${1}` (1e arg) | `icw@RPITSM0` | SSH-target |
| `REMOTE_LOG_DIR` | `/home/icw/cansat_logs` | Pad op de Zero |
| `JOURNAL_SINCE` | `'24 hours ago'` | `--since` voor `journalctl` |
| `SKIP_DECODE` | `0` | Met `1`: alleen kopiëren, geen CSV/summary |
| `PYTHON` | `python3` | Welke python gebruik je voor decoderen |

Voorbeelden:

```bash
# Andere host
scripts/fetch_zero_logs.sh pi@cansat.local

# Alleen sessie van vandaag (snellere journal-fetch)
JOURNAL_SINCE='2 hours ago' scripts/fetch_zero_logs.sh

# Geen automatische decode (handig als je later met andere parameters wil decoderen)
SKIP_DECODE=1 scripts/fetch_zero_logs.sh
```

> **Tip — partial fetch is geen probleem.** Faalt `rsync`, dan halen we tóch
> nog het journal. Faalt het journal, dan blijven de binaries staan. Faalt
> één CSV-decode, dan wordt alleen die ene `.csv` weggegooid; de rest blijft.

## Decoderen handmatig

[`scripts/decode_logs.py`](../scripts/decode_logs.py) heeft drie output-modes:

```bash
# Samenvatting (default) — alles in zero_logs/latest/
PYTHONPATH=src python3 scripts/decode_logs.py

# CSV van één sessie naar bestand (importeerbaar in Excel/pandas/gnuplot)
PYTHONPATH=src python3 scripts/decode_logs.py --csv \
    zero_logs/latest/cansat_mission_20260419T135804Z.bin > flight.csv

# Alle records één per regel (diepe debug, met file-offset en CRC-status)
PYTHONPATH=src python3 scripts/decode_logs.py --raw \
    zero_logs/latest/cansat_continuous.bin | less

# Een gearchiveerde sessie analyseren
PYTHONPATH=src python3 scripts/decode_logs.py \
    zero_logs/archive/2026-04-19T17-14-32/*.bin
```

Wat de **summary** je per file vertelt:

- Aantal TLM / EVT / HDR records, bad-CRC count, decode-fouten.
- Mode- en state-histogram (hoeveel frames in `MISSION/PAD_IDLE`, etc.).
- `seq-gaps`: gaten in de **seq** (sequence-counter, oplopend per TLM-frame).
  **Let op**: in `cansat_continuous.bin` zijn dit meestal sessie-resets (bv.
  `MISSION` herstart → `seq=1`), géén echte packetloss. Voor échte
  loss-detectie kijk je naar de per-mission files; daar hoort `0` te staan.
- `span` + effectieve TLM-rate (frames/seconde).
- Drukbereik en apex (`alt-peak`) met UTC-timestamp.
- Peak-acceleratie ‖a‖ (vector-magnitude `√(ax²+ay²+az²)`).
  **`⚠ int16 clip`** bij ≥32.7 g betekent dat de BNO055 (de IMU) boven zijn
  lineair-accel-bereik ging — meestal een echte impact, geen sensor-bug.
- Laatste BNO055-cal-status (`sys`/`gyro`/`accel`/`mag`, elk 0..3 — 3 = volledig
  gekalibreerd).
- **Alle state-transities** met hun trigger-reden (`ACC` / `ALT` / `FREEFALL` /
  `SHOCK` / `IMPACT` / `STABLE` — zie [`mission_triggers.md`](mission_triggers.md)
  en de [woordenlijst](glossary.md#trigger-redenen-state-overgangen)).
- Eerste 30 EVT-records.

## Snel iets specifieks vinden

```bash
# Alle EVT-records uit de laatste fetch
PYTHONPATH=src python3 scripts/decode_logs.py --raw zero_logs/latest/*.bin \
    | grep ' EVT '

# Alleen state-overgangen (uit de summary)
grep '→' zero_logs/latest/decoded/summary.txt

# Alle warnings/errors uit de service
grep -E 'WARN|ERR|Traceback' zero_logs/latest/journal.log

# Plot apex voor één missie (vereist gnuplot)
PYTHONPATH=src python3 scripts/decode_logs.py --csv \
    zero_logs/latest/cansat_mission_20260419T135804Z.bin \
    | awk -F, 'NR>1 {print $3,$8}' \
    | gnuplot -p -e "set xlabel 'utc_s'; set ylabel 'alt_m'; plot '-' with lines"
```

## Opruimen

`zero_logs/` is compleet in `.gitignore` — git negeert het. Schoonmaken doe
je dus gewoon met `rm`:

```bash
# Verwijder alle archieven ouder dan 30 dagen
find zero_logs/archive -maxdepth 1 -type d -mtime +30 -exec rm -rf {} +

# Begin opnieuw vanaf 0 (na een grote shake-out wil je soms gewoon een schone start)
rm -rf zero_logs/
```

> **Pas op**: dit raakt **alleen** je lokale kopie. De originelen op de Zero
> in `~/cansat_logs/` blijven staan. Wil je die ook leeg? Dat doe je
> bewust en handmatig over SSH:
>
> ```bash
> ssh icw@RPITSM0 'sudo systemctl stop cansat-radio-protocol \
>     && rm -f ~/cansat_logs/*.bin \
>     && sudo systemctl start cansat-radio-protocol'
> ```
>
> De service moet kort stilliggen omdat hij `cansat_continuous.bin` open heeft.

## Veelgestelde valkuilen

- **`Permission denied (publickey)`** bij `rsync` of `ssh` → SSH-key niet
  geladen. Test met `ssh icw@RPITSM0 'echo ok'` voordat je het script draait.
- **`zsh: no matches found: zero_logs/latest/*.bin`** → er staan nog geen
  binaries lokaal. Run eerst `scripts/fetch_zero_logs.sh`.
- **`ModuleNotFoundError: cansat_hw`** bij handmatig decoderen → vergeet
  `PYTHONPATH=src` niet (of activeer de venv waarin `pip install -e .` is
  gebeurd). Het script duwt `src/` zelf vooraan, dus default werkt het.
- **Lege `journal.log`** → de service draait niet of `JOURNAL_SINCE` is te
  kort. Check `ssh icw@RPITSM0 'systemctl status cansat-radio-protocol'`.
- **Nieuw `zero_logs/` of nieuw `zero_journal.log` in `git status`** →
  controleer dat het pad onder `zero_logs/` valt en niet in de project-root
  staat. Alleen `zero_logs/` is genegeerd; een los bestand op project-niveau
  niet.
