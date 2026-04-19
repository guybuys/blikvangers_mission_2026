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

### Fase 12 — Servo-tuning + park/stow via radio ✅

| Onderdeel | Belangrijkste files |
|---|---|
| Pure helper voor rail-policy bij flight-state-overgangen (PARK / ENABLE / DISABLE / NONE) | [`src/cansat_hw/servos/state_policy.py`](../src/cansat_hw/servos/state_policy.py) |
| `ServoController` met rail/pulse/stow/park, tuning sub-state, watchdog (60 s), JSON I/O incl. `stow_us`, dependency-injected driver | [`src/cansat_hw/servos/controller.py`](../src/cansat_hw/servos/controller.py) |
| `SERVO …`-dispatcher in het wire-protocol + `SVO`-preflight check + MISSION-allowlist voor `SERVO STATUS` | [`src/cansat_hw/radio/wire_protocol.py`](../src/cansat_hw/radio/wire_protocol.py) |
| Autonome rail-policy hook in de main loop + watchdog-tick + atexit-park | [`scripts/cansat_radio_protocol.py`](../scripts/cansat_radio_protocol.py) |
| Pico CLI: `!servo` sub-REPL + `!park` + `!servo enable/disable/status/tune` | [`basestation_cli.py`](../pico_files/Orginele%20cansat/RadioReceiver/basestation_cli.py) |
| Tests: state-policy table, controller (rail / stow / park / tuning / watchdog / JSON), wire-roundtrip, SVO-preflight | [`tests/test_servo_state_policy.py`](../tests/test_servo_state_policy.py), [`tests/test_servo_controller.py`](../tests/test_servo_controller.py), [`tests/test_servo_wire.py`](../tests/test_servo_wire.py) |

**Wire-conventie**: replies zijn `OK SVO …` / `ERR SVO …` (3-letter code
zodat het binnen 60 B past, gelijk aan de preflight `SVO`-code).

**Wat er anders is dan de oorspronkelijke spec**:

- `SERVO STOW` als calibratie-marker (binnen tuning) heet `SERVO STOW_MARK` in
  het wire-protocol, zodat `SERVO STOW` zelf eenduidig de **manual-stow-actie**
  is (rail al aan ⇒ stuur stow-pulse).
- `SERVO STATUS` is óók in `MISSION`/`TEST` toegestaan (read-only), zodat de
  operator tijdens een vlucht kan zien of de rail aan/uit staat.
- `SERVO DISABLE` tijdens actieve tuning rondt eerst de tuning af (reply
  `OK SVO DISABLE TUNING_STOPPED`) — voorkomt verweesde watchdog-firings.
- Rail-actie bij `CONFIG → TEST` is `ENABLE` (niet `NONE`); TEST is een
  dry-run van `DEPLOYED`, dus de gimbal hoort actief te zijn.
- Op `MISSION → CONFIG` doet de policy **niets autonoom**: operator forceerde
  abort, dus geen ongewenste extra beweging.

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

### Fase 12 — Servo-tuning + park/stow via radio (uitgewerkte spec, ✅ geïmplementeerd)

> Verplaatst naar **[Afgerond](#fase-12--servo-tuning--parkstow-via-radio-)**.
> Onderstaande spec blijft staan als architectuur-referentie en
> motivatie-archief voor wie de implementatie wil herzien.

**Doel**: gimbal-servo's volledig bedienbaar via het base station — zowel
**calibreren** (equivalent van [`scripts/gimbal/servo_calibration.py`](../scripts/gimbal/servo_calibration.py))
als **veilig opbergen** vóór en na de vlucht — zonder SSH op de Zero.

**Aanpak (gekozen)**: **tuning-sub-state binnen `CONFIG`** + sub-REPL op
de Pico met dezelfde letters als het lokale script. **Rail-policy gekoppeld
aan flight-state** in `MISSION`/`TEST` (Zero bepaalt autonoom wanneer
servo's stroom krijgen). In `CONFIG` beslist de operator volledig.

#### Calibration-JSON (uitbreiding)

Per servo één extra veld erbij naast `min_us` / `center_us` / `max_us`:

| Veld | Betekenis |
|---|---|
| `stow_us` | Veilige "ingeklapte" positie. Gebruikt voor in-rocket en post-landing — zie [glossary: stowed](glossary.md#hardware). |

Eén positie volstaat: in beide gevallen wil je dezelfde mechanische
toestand (gimbal compact + geen stroom).

#### Wire-commando's

**Tuning** (alleen in `CONFIG`):

| Commando | Reply | Effect |
|---|---|---|
| `SERVO START [1\|2]` | `OK SERVO START <s> <us>` | Rail aan, selecteer servo, ga in tuning-state, pulse = laatste of `center_us` |
| `SERVO SEL <1\|2>` | `OK SERVO SEL <s>` | (optioneel — Pico kan dit ook lokaal in de UI) |
| `SERVO STEP <±N>` | `OK SERVO STEP <s> <us>` | Relatief in µs, geclamped op `[min_us, max_us]` |
| `SERVO SET <us>` | `OK SERVO SET <s> <us>` | Absoluut, geclamped |
| `SERVO MIN\|CENTER\|MAX\|STOW` | `OK SERVO <kind> <s> <us>` | Markeer huidige pulsewidth als die grenswaarde |
| `SERVO SAVE` | `OK SERVO SAVE` | Schrijf `config/gimbal/servo_calibration.json` |
| `SERVO STOP` | `OK SERVO STOP` | Verlaat tuning-state. Rail blijft staan — operator stuurt zelf `SERVO DISABLE` als gewenst |
| `SERVO STATUS` | `OK SERVO <s> us=… min=… cen=… max=… stow=… rail=ON\|OFF` | Polling |

**Park / stow** (alleen in `CONFIG`, los van tuning):

| Commando | Reply | Effect |
|---|---|---|
| `SERVO ENABLE` | `OK SERVO ENABLE` | Rail aan (BCM6 via [`servo_rail_set`](../src/cansat_hw/servos/power_enable.py)). Géén pulse — servo's los maar onder spanning |
| `SERVO DISABLE` | `OK SERVO DISABLE` | PWM=0 op beide, rail uit. Onmiddellijk; servo die nog beweegt valt vrij |
| `SERVO STOW [1\|2\|BOTH]` | `OK SERVO STOW <s> <us>` | Stuur stow-pulse. **Géén wait** — operator wacht visueel en stuurt zelf `SERVO DISABLE` als hij stroom wil afkappen |
| `SERVO PARK` | `OK SERVO PARK` | Samengesteld convenience: `ENABLE` → `STOW BOTH` → wacht intern 800 ms → `DISABLE`. Eén round-trip voor "alles veilig opbergen" |

**Waarom "geen `wait_ms` in `SERVO STOW`"?** Hobby-servo's hebben geen
encoder-feedback. In CONFIG kan de operator visueel checken of de servo is
aangekomen vóór `SERVO DISABLE` te sturen — veel betrouwbaarder dan een
geraden timeout. `SERVO PARK` gebruikt voor de autonome use-case wél een
interne 800 ms (datasheet: ~0,17 s/60° onbelast → ×2 onder belasting ⇒
~600 ms full-sweep + marge), hard-coded zodat een verkeerde Pico-call
nooit lange blokkades kan veroorzaken op de Zero.

#### Autonome rail-policy in `MISSION` / `TEST`

In `MISSION` en `TEST` beslist de Zero zelf, **niet** de operator. Alle
`SERVO ...`-commando's vanaf de Pico worden geweigerd: `ERR SERVO BUSY
<mode>`.

| Transitie | Servo-actie |
|---|---|
| `CONFIG` → `MISSION` (preflight ok, state = `PAD_IDLE`) | `ENABLE` → `STOW BOTH` → 800 ms wait → `DISABLE`. Servo's mechanisch in stow, geen stroom |
| `PAD_IDLE` → `ASCENT` | (niets — rail blijft uit; gimbal niet nodig tijdens boost) |
| `ASCENT` → `DEPLOYED` | (in Fase 12: niets. Fase 9 zal hier `ENABLE` toevoegen + gimbal-loop starten) |
| `DEPLOYED` → `LANDED` | (in Fase 12: niets actief; als Fase 9 de rail aan had: `STOW BOTH` → 800 ms → `DISABLE`) |
| `END_TEST` (Zero → `CONFIG`) | Idem als hierboven: stow + disable als rail aan was |
| Service shutdown (`STOP RADIO`, SIGTERM) | atexit: `STOW BOTH` → 800 ms → `DISABLE` |

#### Preflight `SVO`

Bij `SET MODE MISSION`/`SET MODE TEST` checkt de Zero:

- `stow_us` is gekalibreerd voor beide servo's — anders `ERR PRE SVO STOW`
- Geen tuning-state actief — anders `ERR PRE SVO BUSY`
- pigpio-daemon bereikbaar — anders `ERR PRE SVO PIGPIO`

Faalt iets → blijven in `CONFIG`, geen rail-acties.

#### Pico CLI

**`!servo` sub-REPL** (zelfde letters als [`scripts/gimbal/servo_calibration.py`](../scripts/gimbal/servo_calibration.py)):

```text
BS> !servo                  ← stuurt SERVO START, opent sub-REPL
servo[S1 1500 rail=ON]> d   ← +step → SERVO STEP +10
servo[S1 1510 rail=ON]> D   ← +bigstep → SERVO STEP +50
servo[S1 1560 rail=ON]> 2   ← lokaal: switch UI naar S2 + SERVO SEL 2
servo[S2 1500 rail=ON]> x   ← SERVO CENTER (markeer huidige als center)
servo[S2 1500 rail=ON]> w   ← SERVO STOW (nieuwe letter — markeer huidige als stowed)
servo[S2 1500 rail=ON]> s   ← SERVO SAVE
servo[S2 1500 rail=ON]> q   ← SERVO STOP — rail blijft staan, operator beslist
```

**Top-level commando's** (los van de sub-REPL):

```text
BS> !park                   ← stuurt SERVO PARK (alles veilig opbergen — vóór MISSION-start)
BS> !servo enable           ← rail aan zonder de sub-REPL te openen
BS> !servo disable          ← rail uit
BS> !servo status           ← één-shot SERVO STATUS, geen sub-REPL
```

#### Veiligheid

- **Watchdog op de Zero**: 60 s zonder `SERVO`-commando tijdens
  tuning-state ⇒ automatisch `SERVO STOP` + `SERVO DISABLE`. Voorkomt
  vastzittende rails als de Pico crasht of buiten radio-bereik raakt.
- **`SERVO PARK` interne wait hard-cap**: 800 ms vast in code; geen
  wire-parameter zodat een verkeerde Pico-call nooit lange blokkades
  veroorzaakt op de Zero.
- **Buiten `CONFIG` geweigerd**: `ERR SERVO BUSY <mode>` — geen tuning
  tijdens `MISSION`/`TEST`.
- **Service-shutdown atexit**: `STOW + DISABLE` ook bij SIGTERM/CTRL-C.

#### Belangrijkste files

- Nieuw: `src/cansat_hw/servos/tuner.py` — tuning-state, watchdog, rail-controle, JSON I/O.
- Nieuw: `src/cansat_hw/servos/state_policy.py` — kleine pure helper die voor een gegeven flight-state-transitie de gewenste rail-actie teruggeeft (eenvoudig te unit-testen).
- [`src/cansat_hw/servos/power_enable.py`](../src/cansat_hw/servos/power_enable.py) — bestaand, geen wijziging.
- [`src/cansat_hw/radio/wire_protocol.py`](../src/cansat_hw/radio/wire_protocol.py) — `SERVO …`-dispatcher.
- [`scripts/cansat_radio_protocol.py`](../scripts/cansat_radio_protocol.py) — autonome rail-actie hooken aan bestaand `_emit_evt_state_if_changed()`.
- [`basestation_cli.py`](../pico_files/Orginele%20cansat/RadioReceiver/basestation_cli.py) — `!servo` sub-REPL + `!park` + `!servo enable/disable/status`.
- `config/gimbal/servo_calibration.json` — `stow_us` per servo (additive, oude files blijven werken).
- Tests:
  - Round-trip wire-commando's (`SERVO START`/`STEP`/`STOW`/`PARK`/`DISABLE`).
  - Watchdog-fire-na-60s.
  - State-policy table (PAD_IDLE → rail off, etc.) als pure function.
  - Preflight `SVO`-check.
- Docs:
  - [`scripts/gimbal/README.md`](../scripts/gimbal/README.md) — sectie over de twee gelijkwaardige tooling-paden (SSH-script vs radio), beide schrijven dezelfde JSON.
  - [`docs/glossary.md`](glossary.md) — `stowed`, `SERVO`-commando's, rail-policy entries.
  - [`docs/mission_states.md`](mission_states.md) — rail-policy per flight-state.

**Geschat werk**: ≈ 350–450 regels code + tests + docs (iets meer dan
oorspronkelijke schatting door park/stow + state-policy).

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
2. **Fase 9 (camera + AprilTag)** — hét grote ontbrekende stuk vóór een echte vlucht. Plan ruim.
3. **Fase 5 (Pico-log)** of **Fase 11 (power)** afhankelijk van wat er bij echte testvluchten als pijnpunt naar boven komt.

---

## Bijhouden

Bij elke afgeronde fase: hierboven verplaatsen van *Backlog* / *In
voorbereiding* naar *Afgerond*, en de bijhorende files-tabel invullen.
Doe het in **dezelfde commit** als de feature-merge — anders weet niemand
nog wat de werkelijke status is.

Voor losse ideeën die nog niet specifiek genoeg zijn voor een fase: gewoon
in *Kleinere taken* zetten met status 💭 en korte beschrijving. Beter een
ruwe regel hier dan een fragment in een chat-historie.
