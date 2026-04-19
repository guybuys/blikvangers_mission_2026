# Planning & roadmap — CanSat 2026

[← Documentatie-index](README.md) · [← Project README](../README.md)

Centrale plek voor wat **af is**, wat **eerstvolgend** komt en wat er nog
**op de stapel** ligt. Bij elke fase die je afsluit: status hier bijwerken
in **dezelfde commit** als de code-wijziging — anders loopt deze pagina uit
de pas met de werkelijkheid en wordt hij waardeloos.

> **Conventie**: de fase-nummers zijn historisch gegroeid in chats en zijn
> géén strikte volgorde. Volgorde van uitvoering staat onder
> [Voorgestelde volgorde](#voorgestelde-volgorde).

## Status-legenda

| Symbool | Betekenis |
|---|---|
| ✅ | Afgerond, in productie, met tests waar relevant |
| 🚧 | Bezig (zie *In voorbereiding*) |
| 📋 | Gepland (specificatie helder, nog niet gestart) |
| 💭 | Idee / nice-to-have (specificatie nog open) |

---

## Afgerond

### Fase 1–4 — Basis-infrastructuur ✅

| Onderdeel | Belangrijkste files |
|---|---|
| Wire-protocol Pico ↔ Zero (text-based, AES, freq-persistentie) | [`src/cansat_hw/radio/wire_protocol.py`](../src/cansat_hw/radio/wire_protocol.py), [`src/cansat_hw/radio/rfm69.py`](../src/cansat_hw/radio/rfm69.py) |
| Pico base-station CLI (`!`-commando's, JSONL-log) | [`pico_files/Orginele cansat/RadioReceiver/basestation_cli.py`](../pico_files/Orginele%20cansat/RadioReceiver/basestation_cli.py) |
| BME280 + BNO055-drivers + smoke-tests | [`src/cansat_hw/sensors/`](../src/cansat_hw/sensors/), `scripts/bme280_test.py`, `scripts/bno055_test.py` |
| Lokale servo/gimbal-tooling (interactieve REPL via SSH) | [`scripts/gimbal/`](../scripts/gimbal/) |
| Systemd-service op de Zero, deployment-scripts | [`deploy/systemd/`](../deploy/systemd/), [`scripts/sync_to_zero.sh`](../scripts/sync_to_zero.sh) |

### Fase 6 — Binary telemetrie + on-board logging ✅

| Onderdeel | Belangrijkste files |
|---|---|
| 60-byte binary TLM-frame (UTC-stempel, `mode_state`-byte, sensoren, AprilTag-slots) | [`src/cansat_hw/telemetry/codec.py`](../src/cansat_hw/telemetry/codec.py) |
| HEADER + TLM + EVT-records met CRC, ringbuffer-files op de Zero | [`src/cansat_hw/telemetry/log_writer.py`](../src/cansat_hw/telemetry/log_writer.py) |
| Continuous + per-sessie binary logs in `~/cansat_logs/` op de Zero | (zie service-config) |

### Fase 7 — Continuous sensor sampler ✅

| Onderdeel | Belangrijkste files |
|---|---|
| Pull-based, cooperative sampler (geen threads) met rolling-window stats (peak-‖a‖, freefall, alt-stable) | [`src/cansat_hw/sensors/sampler.py`](../src/cansat_hw/sensors/sampler.py) |
| Adaptieve IIR (CONFIG ×4, MISSION/TEST ×16) + ALT-priming-burst voor `CAL GROUND` / `GET ALT` | (sampler + BME280-driver) |

### Fase 8 + 8b — Flight state machine + multi-trigger ✅

| Onderdeel | Belangrijkste files |
|---|---|
| State machine `PAD_IDLE → ASCENT → DEPLOYED → LANDED` | [`src/cansat_hw/radio/wire_protocol.py`](../src/cansat_hw/radio/wire_protocol.py) (`evaluate_flight_state`, `maybe_advance_flight_state`) |
| Multi-trigger overgangen: IMU primair (`ACC` / `FREEFALL` / `SHOCK` / `IMPACT` / `STABLE`) + altitude backup (`ALT`) | (idem) |
| `EVT STATE <name> <reason>` over de radio + in het log; `OK STATE <name> <reason>` op `GET STATE` | (idem) |
| Continue TLM-loop in `MISSION` (autonoom samplen + state advance, ook tussen Pico-commando's door) | [`scripts/cansat_radio_protocol.py`](../scripts/cansat_radio_protocol.py) |
| Pico-CLI: retry-logic + 8s/2.5s timeouts + `OK STATE … REASON` parsing | [`basestation_cli.py`](../pico_files/Orginele%20cansat/RadioReceiver/basestation_cli.py) |

### Tooling rond logs ✅

| Onderdeel | Belangrijkste files |
|---|---|
| Decoder (summary / CSV / raw) | [`scripts/decode_logs.py`](../scripts/decode_logs.py) |
| Fetch + archive + automatische decode | [`scripts/fetch_zero_logs.sh`](../scripts/fetch_zero_logs.sh) |
| Handleiding | [`docs/zero_logs.md`](zero_logs.md) |

### Documentatie-conventies ✅

| Onderdeel | Belangrijkste files |
|---|---|
| Centrale woordenlijst + uitleg-bij-eerste-gebruik conventie | [`docs/glossary.md`](glossary.md) |

---

## In voorbereiding

### Fase 10 — Documentatie-update 🚧

**Doel**: documentatie synchroniseren met wat de code sinds Fase 6–8b
daadwerkelijk doet, en de glossary-conventie uitrollen.

**Concrete acties**:

- [ ] [`docs/mission_states.md`](mission_states.md) — TLM-cadans (1 Hz CONFIG, 5 Hz MISSION/TEST), continuous sensor-sampler, EVT-records, autonome state-advance buiten Pico-commando's.
- [ ] [`docs/mission_triggers.md`](mission_triggers.md) — nieuwe defaults (6.0g / 1.0s / 8.0g / 12.0g / 8.0s), multi-trigger OR-logica, alle `reason`-codes.
- [ ] [`pico_files/Orginele cansat/RadioReceiver/README_basestation.md`](../pico_files/Orginele%20cansat/RadioReceiver/README_basestation.md) — `OK STATE … REASON` parsing, `!apogee` reset op `SET MODE MISSION`, retry-logic, nieuwe timeouts.
- [ ] Glossary-conventie uitrollen naar de overige docs (`mission_states.md`, `mission_triggers.md`, `cansat_radio_service.md`, `secrets.md`, `rpi_pinning.md`) — afkortingen bij eerste gebruik uitschrijven, linken naar [`glossary.md`](glossary.md).
- [ ] `src/cansat_hw/radio/protocol.py` (docstring) — verwijzen naar binary codec.

**Acceptatie**: een nieuwe lezer (leerling die het project nog niet kent) kan na lezen van `docs/README.md` + de drie kerndocumenten een mission opzetten zonder source code te lezen.

---

## Backlog (gespecificeerd, nog niet gestart)

### Fase 9 — Camera + AprilTag in TLM-flow 📋

**Doel**: tijdens `DEPLOYED` de top-2 grootste AprilTags (id + relatieve
positie + grootte) meesturen in elk TLM-frame.

**Specificatie**:
- Aparte process/thread op de Zero, ≈7 Hz Picamera2-loop.
- AprilTag-detectie (`pupil_apriltags` of `apriltag`-binding).
- Rolling buffer met de **2 grootste** tags (`size_mm` desc).
- Sampler leest deze buffer; codec heeft al ruimte (`tags`-veld in `TelemetryFrame`).
- **Geen** AprilTag-werk in `PAD_IDLE`/`ASCENT`/`LANDED` (CPU + warmte sparen).

**Belangrijkste files**:
[`src/cansat_hw/camera/`](../src/cansat_hw/camera/),
[`src/cansat_hw/telemetry/codec.py`](../src/cansat_hw/telemetry/codec.py) (codec is klaar),
[`scripts/cansat_radio_protocol.py`](../scripts/cansat_radio_protocol.py) (integratie).

**Open vragen**:
- Process of thread? Process geeft betere CPU-isolatie maar IPC-overhead.
- Camera-resolutie vs detectie-snelheid (640×480 of 1280×720).

### Fase 12 — Servo-tuning via radio 📋

**Doel**: gimbal-servo-calibratie kunnen doen vanaf het base station,
zonder SSH op de Zero. Equivalent van de bestaande
[`scripts/gimbal/servo_calibration.py`](../scripts/gimbal/servo_calibration.py)
maar bedienbaar via de Pico-CLI.

**Aanpak (gekozen)**: **sub-state binnen `CONFIG`** + sub-REPL op de Pico
met dezelfde letters als het lokale script.

**Wire-commando's (Zero, alleen toegelaten in `CONFIG`)**:

| Commando | Reply | Effect |
|---|---|---|
| `SERVO START [1\|2]` | `OK SERVO START <s> <us>` | Rail aan, selecteer servo, ga in tuning-state |
| `SERVO STEP <±N>` | `OK SERVO STEP <s> <us>` | Relatief in µs, geclamped op min/max |
| `SERVO SET <us>` | `OK SERVO SET <s> <us>` | Absoluut |
| `SERVO MIN\|CENTER\|MAX` | `OK SERVO <kind> <s> <us>` | Markeer huidige als grenswaarde |
| `SERVO SAVE` | `OK SERVO SAVE` | Schrijf `config/gimbal/servo_calibration.json` |
| `SERVO STOP [SAVE\|DISCARD]` | `OK SERVO STOP` | Verlaat tuning-state, rail uit |
| `SERVO STATUS` | `OK SERVO <s> us=… min=… cen=… max=…` | Polling |
| `SERVO SEL <1\|2>` | `OK SERVO SEL <s>` | (optioneel — Pico kan dit ook lokaal) |

**Pico CLI**:

```text
BS> !servo                ← stuurt SERVO START, opent sub-REPL
servo[S1 1500]> d         ← +step → SERVO STEP +10
servo[S1 1510]> D         ← +bigstep → SERVO STEP +50
servo[S1 1560]> 2         ← lokaal: switch UI naar S2 (geen TX nodig als Zero al weet)
servo[S2 1500]> x         ← SERVO CENTER
servo[S2 1500]> s         ← SERVO SAVE
servo[S2 1500]> q         ← SERVO STOP, terug naar BS>
```

Letters identiek aan `scripts/gimbal/servo_calibration.py` zodat leerlingen
die het lokaal kennen niets nieuws hoeven te leren.

**Veiligheid**:
- **Watchdog op de Zero**: 60 s zonder `SERVO`-commando ⇒ automatisch
  `SERVO STOP DISCARD`, rail uit. Voorkomt dat servo's blijven trekken als
  de Pico crasht of buiten radio-bereik raakt.
- **Buiten `CONFIG` geweigerd**: `ERR SERVO BUSY <mode>` — geen tuning
  tijdens `MISSION`/`TEST`.
- **Geen invloed op flight-state**: tuning is een puur CONFIG-feature, de
  state-machine merkt er niets van.

**Belangrijkste files**:
- Nieuw: `src/cansat_hw/servos/tuner.py` (state + watchdog + JSON I/O).
- [`src/cansat_hw/radio/wire_protocol.py`](../src/cansat_hw/radio/wire_protocol.py) (`SERVO …`-handler).
- [`basestation_cli.py`](../pico_files/Orginele%20cansat/RadioReceiver/basestation_cli.py) (`!servo` sub-REPL).
- Tests: round-trip wire-commando's + watchdog-fire-na-60s.
- Docs: nieuwe sectie in [`scripts/gimbal/README.md`](../scripts/gimbal/README.md) over de twee gelijkwaardige tooling-paden (lokaal vs radio), beide schrijven dezelfde JSON.

**Geschat werk**: ≈ 200–300 regels code + tests + docs.

### Fase 5 — Pico binary log naar LittleFS 💭

**Doel**: backup-log op de Pico-flash, ringbuffer 4 × 256 KB, voor het
geval de SD-kaart op de Zero faalt of er geen laptop bij is.

**Status**: niet kritisch zolang `cansat_logs/` op de Zero werkt en
[`scripts/fetch_zero_logs.sh`](../scripts/fetch_zero_logs.sh) probleemloos
draait. Lage prioriteit.

### Fase 11 — Power management in `PAD_IDLE` 💭

**Doel**: radio uit in `PAD_IDLE` (RFM69 ≈ 750 mW besparing) met
periodieke heartbeat, en eventueel sensor-cadans verlagen.

**Status**: door jou bewust uitgesteld (CanSat-organisatie meldt dat
radio in de raket vaak toch niet werkt; tussen plaatsen en lancering
kan een uur zitten). Pas inplannen als de rest stabiel vliegt.

---

## Kleinere taken

| ID | Taak | Status |
|---|---|---|
| **fix-bme280-conversion-sleep** | `_conversion_sleep_us` overschat met factor ~2 → workaround zit nu Pico-side (8 s/2.5 s timeouts). Driver-fix is upstream-code, riskant. | 💭 nice-to-have |
| **calhint-pico-cli** | `!calhint`-commando dat live BNO055-cal pollt + figure-of-eight en 6-oriëntatie instructies print. Voorkomt dat operators `ERR PRE IMU` als dood-eind ervaren. | 💭 nice-to-have |
| **rfm69-todo** | Losse `TODO` in [`src/cansat_hw/radio/rfm69.py`](../src/cansat_hw/radio/rfm69.py) opzoeken en afhandelen of expliciet afkeuren. | 💭 |
| **archive-cleanup-policy** | `zero_logs/archive/` groeit. Een eenvoudige `cleanup_archive.sh` (default: > 30 dagen weg) zou geen kwaad kunnen. | 💭 |

---

## Voorgestelde volgorde

1. **Fase 10 (docs)** — kort, ruimt achterstand op, maakt het project navigeerbaar voor leerlingen.
2. **Fase 12 (servo via radio)** — vervangt een ergonomisch zwak punt (SSH nodig voor calibratie). Geen blocker maar wel veel waarde voor de hardware-uren.
3. **Fase 9 (camera + AprilTag)** — hét grote ontbrekende stuk vóór een echte vlucht. Plan ruim.
4. **Fase 5 (Pico-log)** of **Fase 11 (power)** afhankelijk van wat er bij echte testvluchten als pijnpunt naar boven komt.

---

## Bijhouden

Bij elke afgeronde fase: hierboven verplaatsen van *Backlog* / *In
voorbereiding* naar *Afgerond*, en de bijhorende files-tabel invullen.
Doe het in **dezelfde commit** als de feature-merge — anders weet niemand
nog wat de werkelijke status is.

Voor losse ideeën die nog niet specifiek genoeg zijn voor een fase: gewoon
in *Kleinere taken* zetten met status 💭 en korte beschrijving. Beter een
ruwe regel hier dan een fragment in een chat-historie.
