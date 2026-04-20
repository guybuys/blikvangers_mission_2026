# Base station (Pico) — CLI + eenvoudig commando-protocol

## Naamgeving (CanSat vs base station)

| Rolverdeling | Board | Typische verbinding |
|--------------|-------|---------------------|
| **CanSat** (flight computer) | **Raspberry Pi Zero 2 W** | SSH, `cansat_radio_protocol.py` |
| **Base station** (grondstation) | **Raspberry Pi Pico** | Thonny USB, `basestation_cli.py` |

Vermijd de term **“Pi”** alleen — die past op **beide** types. Liever **Pico** vs **Zero** of **base station** vs **CanSat**.

Deze map bevat naast `rfm69test_receiver.py` een **interactieve base station** voor Thonny: `basestation_cli.py`.

## Bestanden op de Pico

| Bestand | Rol |
|---------|-----|
| `basestation_cli.py` | Hoofdprogramma: `input("BS> ")` — lokaal (`!…`) of over RFM69 |
| `protocol.py` | Max. payload, defaults, `help_wire_commands()` |
| `rfm69.py` | **Kopiëren** vanuit [`../rfm69.py`](../rfm69.py) in dezelfde map op de Pico |
| `README_basestation.md` | Dit document |

## Thonny

1. Pico verbinden, bestanden naar de Pico kopiëren (of op de flash in dezelfde map).
2. `basestation_cli.py` openen → **Run** (of REPL: `import basestation_cli` werkt niet voor de loop — gebruik Run).
3. In de **Shell** verschijnt `BS> ` — typ commando’s.

## Waarom `input()` / “CLI-achtig”?

- **Eenvoudig** in MicroPython, geen extra webserver of GUI.
- Werkt goed met **USB-serial** in Thonny.
- Nadeel: **blocking** — tijdens wachten op `input()` luistert de Pico niet op de radio. Voor een **basisstation aan een PC** is dat meestal acceptabel: je stuurt een commando, wacht op antwoord, daarna volgende regel.

Wil je **tegelijk** blijven luisteren, gebruik dan het aparte script `rfm69test_receiver.py` of breid later uit met `_thread` / `asyncio` (complexer).

## Belangrijk: `!`-commando’s alleen in Thonny (Pico)

Commando’s die met **`!`** beginnen zijn **lokaal op de Pico** (`!help`, `!timeout`, …).

Op de **CanSat (Zero 2 W)** draait `cansat_radio_protocol.py` **zonder** toetsenbord-loop: daar hebben `!timeout` / `!help` **geen effect**. Als je die in de **SSH-terminal** typt terwijl het script draait, zie je ze hooguit **door elkaar met de log** — ze worden **niet** verwerkt. Instellingen op de Zero: **alleen** via argumenten van het script (`--poll`, `--freq`, …).

## Lokale commando’s (`!…`)

| Commando | Betekenis |
|----------|-----------|
| `!help` | Hulp |
| `!wirehelp` | Lijst **draad**-commando’s voor de CanSat |
| `!freq 433.0` | RF-frequentie op **deze** Pico |
| `!dest 120` | RadioHead-bestemming (CanSat-node op de Zero) |
| `!node 100` | Eigen node (base station) |
| `!timeout 8.0` | Totaal seconden wachten op antwoord na zenden (default **8 s** — verdeelt zich over `MAX_TX_ATTEMPTS=3` pogingen, ≥2.5 s per poging; hoog genoeg voor `CAL GROUND` / `PREFLIGHT` met IIR×16). Verhogen bij zwakke RF-link. |
| `!gap 0.05` | Pauze na eigen TX vóór RX (half-duplex) |
| `!info` | Huidige instellingen |
| `!time` | Stuurt `SET TIME <epoch>` naar de CanSat (MicroPython `time.time()` — voor juiste tijd: Pico-klok syncen vanaf Thonny/laptop, zie hieronder) |
| `!timeepoch N` | Zelfde met vast **Unix-tijd** `N` (op de laptop: `date +%s`) — handig als de Pico geen juiste RTC heeft |
| `!gettime` | Stuurt `GET TIME` naar de CanSat; de Zero antwoordt met `OK TIME <epoch> <ISO local>` (bv. `2026-04-17T23:55:36+02:00`) |
| `!preflight` | Stuurt `PREFLIGHT` — toont missende categorieën of `OK PRE ALL …` met grond-/trigger-info |
| `!calground` | Stuurt `CAL GROUND` — de Zero middelt BME280-druk en bewaart die als grondreferentie |
| `!triggers` | Stuurt `GET TRIGGERS` — compacte altitude-only weergave (ASCENT m [+ hPa-equiv], DEPLOY m-daling vanaf apogee, LAND m) |
| `!state` | Stuurt `GET STATE` — huidige flight-state + reden van laatste overgang (bv. `OK STATE ASCENT ACC`, of `OK STATE PAD_IDLE` zonder reason bij boot). |
| `!servo` / `!servo tune` | Opent een **sub-REPL** voor servo-calibratie (`SERVO START → letters → SAVE/STOP`). Volledige walkthrough: [servo_tuning.md](../../../docs/servo_tuning.md). |
| `!servo enable` / `!servo disable` / `!servo status` | Shortcuts voor `SERVO ENABLE` / `DISABLE` / `STATUS`. |
| `!servo home` / `!home` | Stuurt `SERVO HOME` — beide servo's naar `center_us`, rail blijft aan. |
| `!servo park` / `!park` | Stuurt `SERVO PARK` — rail aan → `stow_us` → 800 ms wachten → rail uit. Veilige stow vóór transport / MISSION. |
| `!servo stow` | Stuurt `SERVO STOW` — alleen naar `stow_us` (rail moet al aan staan). |

> Voor de volledige multi-trigger view (nieuwe IMU+altitude-drempels)
> typ je `GET TRIG ALL` rechtstreeks — er is geen `!trig…`-shortcut op
> de Pico. Reply: `OK TRIG A=5.0m/6.0g D=3.0m/8.0g/1.0s L=5.0m/12.0g/8.0s`.
> Zie [mission_triggers.md](../../../docs/mission_triggers.md) voor
> defaults, ranges en tuning-tips.
| `!alt` | Stuurt `GET ALT` — hoogte boven grondreferentie + actuele druk (werkt ook in MISSION) |
| `!apogee` | Stuurt `GET APOGEE` — hoogste hoogte tot nu toe, bijhorende druk en ouderdom in s |
| `!resetapogee` | Stuurt `RESET APOGEE` — apogee-tracking herbeginnen (alleen CONFIG) |
| `!iir [N]` | Zonder argument: stuurt `GET IIR` (toont chip-waarde + CFG/MIS presets). Met `N` ∈ `{0,2,4,8,16}`: stuurt `SET IIR N` — lagere coëfficient = snellere response bij `!alt`, hogere = stiller signaal maar tragere step-response. |
| `!altprime [N]` | Zonder argument: `GET ALT PRIME` (huidig aantal samples per `!alt`). Met `N` ∈ `[1..32]`: `SET ALT PRIME N` — meer samples = accurater (filter krijgt N samples om bij te benen) maar tragere reply (~150 ms × N bij OSP×16). Default 5. |
| `!listen` | Alleen RX-loop (tot Stop in Thonny) |
| `!test [s]` | Vraag **TEST-mode** op de CanSat (default 10 s, 2..60). Luistert daarna read-only tot het `EVT MODE CONFIG`-event binnenkomt (of tot `s + 3` s om zijn). |
| `!log on [pad]` | Start JSON-lines log op de Pico-flash (default `cansat_<timestamp>.jsonl`) |
| `!log off` | Sluit de actieve log af |
| `!log status` | Toont of er gelogd wordt + pad + laatst gekende MISSION-mode |

### Retry-gedrag en timeouts

De Pico stuurt elk commando **tot `MAX_TX_ATTEMPTS = 3` keer** opnieuw
als er geen antwoord komt binnen een **per-poging-venster** van
`max(2.5 s, REPLY_TIMEOUT_S / MAX_TX_ATTEMPTS)`:

- Default `REPLY_TIMEOUT_S = 8.0 s` → 3 × ~2.67 s wachtvensters.
- `!timeout 10` → ~3.33 s per poging, totaal ~10 s.
- Tussen elke TX en de RX-switch zit `REPLY_GAP_S = 50 ms` (`!gap`).
  Verhoog naar 100 ms bij randgevallen van half-duplex.

Retries worden in de log gelogd als `TX`-records met
`{"attempt": N, "of": 3}`; een echte gegeven-op is een `TIMEOUT`-record
met `{"timeout_s": 8.0, "attempts": 3}`.

Ongevraagde frames (`EVT STATE …`, `EVT MODE …`, `TLM …`, `EVT SERVO
WATCHDOG`) mogen altijd binnenkomen; ze worden niet in de retry-telling
meegenomen en de command-reply-matcher negeert ze, zodat ze je antwoord
op bv. `PING` niet "stelen".

### Flight-state op het base station

Door `OK STATE …` en `EVT STATE …` mee te parsen weet de Pico altijd
welke **flight-state** de CanSat rapporteert, en welke **reden** de
laatste transitie triggerde:

```text
BS>
RX <- EVT STATE ASCENT ACC         (≈ direct na lift-off)
RX <- EVT STATE DEPLOYED FREEFALL  (parachute open / vrije val)
RX <- EVT STATE LANDED IMPACT      (touchdown)
```

- Reason-codes: `ACC`, `ALT`, `FREEFALL`, `SHOCK`, `DESCENT`, `IMPACT`,
  `STABLE` — zie [mission_triggers.md](../../../docs/mission_triggers.md#reason-codes-in-detail).
- De Pico-CLI logt elke EVT in JSONL als
  `{"kind": "EVT_STATE", "state": "ASCENT", "reason": "ACC"}`.
- `!state` geeft op elk moment de huidige state + reason ad-hoc op
  (`GET STATE`).

**Belangrijke side-effect van `SET MODE MISSION`**: de Zero reset
**automatisch** de apogee (`max_alt_m` → `None`) bij elke `CONFIG →
MISSION`-overgang. Je hoeft dus **geen** `!resetapogee` meer te doen
vóór de vlucht — de vorige MISSION of test-sessie kan geen stale apogee
achterlaten. `!resetapogee` blijft nuttig als je tijdens CONFIG een
`!alt`-test hebt gedaan en de teller wil opschonen.

### Log-formaat

Elke TX, RX en TIMEOUT wordt bewaard als één JSON-record per regel (**JSONL**):

```json
{"dt_ms": 0,    "dir": "INFO", "text": "LOG_OPEN", "version": 1, "node": 100, "dest": 120, "freq_mhz": 433.0}
{"dt_ms": 42,   "dir": "TX",   "text": "CAL GROUND"}
{"dt_ms": 520,  "dir": "RX",   "text": "OK GROUND 1019.19", "rssi": -28.0, "parsed": {"kind": "GROUND", "ground_hpa": 1019.19}}
{"dt_ms": 1200, "dir": "TX",   "text": "GET ALT"}
{"dt_ms": 1684, "dir": "RX",   "text": "OK ALT -0.29 1019.23", "rssi": -27.5, "parsed": {"kind": "ALT", "alt_m": -0.29, "pressure_hpa": 1019.23}, "mode": "MISSION"}
```

- `dt_ms` is altijd aanwezig: monotone milliseconden sinds `!log on`. Betrouwbaar ook zonder RTC.
- `t` (ISO-tijd) komt erbij zodra de Pico-RTC plausibel gezet is (jaar ≥ 2020), bv. na `SET TIME` + Thonny-sync.
- `parsed` ontleedt bekende `OK`-replies naar kolom-vriendelijke velden (`alt_m`, `pressure_hpa`, `temp_c`, `humidity_pct`, `ground_hpa`, `freq_mhz`, `epoch`, …) — zo is MISSION-telemetrie direct in `pandas` of een dashboard te gooien. TEST-telemetrie komt binnen als `TLM`-frames met geparste velden `dt_ms`, `alt_m`, `pressure_hpa`, `temp_c`, `heading_deg`, `roll_deg`, `pitch_deg`, `bno_sys_cal`; het afsluitende `EVT MODE CONFIG`-event komt in als `{"kind": "EVT_MODE", "mode": "CONFIG", "reason": "END_TEST"}`.
- `mode` toont de laatst ontvangen `OK MODE …` (of `EVT MODE …`) zodat je CONFIG-, TEST- en MISSION-secties makkelijk kunt filteren.

Analyseren later (op de laptop):

```python
import pandas as pd
df = pd.read_json("cansat_20260418_123456.jsonl", lines=True)
mission = df[df["mode"] == "MISSION"]
alt = pd.json_normalize(mission["parsed"])
```

**Let op Pico-flash:** een log-bestand van ~200 KB (≈ een uur vliegen met `!alt` elke seconde) past prima; elke write `flush()`t direct, zodat je bij een reset geen regels verliest. Schakel `!log off` aan het eind van een sessie zodat de file netjes wordt gesloten. Voor lange sessies: regelmatig nieuwe file (`!log off` + `!log on`) — blokken van ~1 MB blijven handelbaar.

### Servo-tuning / park / home vanaf de Pico (snelkaart)

Voor alle details zie [servo_tuning.md](../../../docs/servo_tuning.md).
Korte versie:

```text
BS> !servo           # opent sub-REPL  (TX SERVO START 1)
servo> a a a a       # fijne stappen (-10us elk)
servo> A             # grove stap (-50us)
servo> z             # markeer MIN op huidige us
servo> D D D … x     # andere kant op; markeer MAX
servo> N 1500 c      # naar 1500us; markeer CENTER
servo> w             # markeer STOW (mag = CENTER bij testen zonder gimbal)
servo> 2             # switch naar servo 2, herhaal
servo> s             # SAVE naar servo_calibration.json
servo> q             # STOP (rail uit)

BS> !home            # rail aan + beide servo's naar center (actieve hold)
BS> !park            # rail aan → stow → 0.8s → rail uit  (veilige transport)
BS> !servo status    # snapshot zonder beweging (ook in MISSION toegelaten)
```

De sub-REPL heeft een **5 min watchdog**: na 5 min zonder commando stopt
tuning automatisch (rail uit). `!servo status` reset de watchdog.

## Draad-protocol (naar CanSat over RFM69)

Max. **60 bytes** UTF-8 per pakket, één regel zonder newline.

Voorbeelden:

- `PING` — alive-check; verwacht antwoord `OK PING`.
- `GET MODE` / `SET MODE CONFIG` / `SET MODE MISSION` (oude alias: `SET MODE LAUNCH` → zelfde modus, antwoord `OK MODE MISSION`)
- `GET FREQ` / `SET FREQ 433.0` — **persistent op beide kanten**. De Zero antwoordt nog op de **oude** freq, schakelt dan door en schrijft `config/radio_runtime.json`. De Pico-CLI detecteert `OK FREQ …` en past zijn eigen `frequency_mhz` aan + slaat op in `radio_freq.json` op de flash. Bij volgende boot laden beide dus vanzelf de laatst gebruikte waarde → **één commando, beide in sync, ook na reboot**.
- `READ BME280` of kort `BME280` — `OK BME280 …` als BME280 actief op de Zero; anders `ERR NO BME280`
- `READ BNO055` of kort `BNO055` — `OK BNO055 …` (heading/roll/pitch + calibratie 0–3); anders `ERR NO BNO055`
- `SET TIME <unix_epoch>` — alleen als de Zero in **CONFIG** staat; zet de **systeemklok** (`OK TIME` of `ERR TIME …`). Op de Zero meestal **root** nodig (bv. systemd-service `User=root`) of `timedatectl` met passende rechten.
- `GET TIME` — vraagt de huidige **systeemklok** van de Zero op (CONFIG én MISSION toegestaan). Antwoord: `OK TIME <epoch> <ISO local>` bv. `OK TIME 1776462936.401 2026-04-17T23:55:36+02:00`.
- **MISSION-preflight (alleen CONFIG):**
  - `CAL GROUND` — eerst `ALT PRIME` warm-up reads om het IIR-filter bij te benen, daarna 4 reads middelen voor de grondreferentie (`OK GROUND <hPa>`). Default = 5 + 4 = 9 reads ≈ 1.4 s bij OSP×16; zorg dat `!timeout` ≥ 4 s staat. Oude gedrag zonder warm-up gaf systematisch een te lage grondreferentie wanneer het filter lang stilgelegen had.
  - `SET GROUND <hPa>` — grondreferentie handmatig.
  - `GET GROUND` — huidige referentie of `OK GROUND NONE`.
  - `SET TRIGGER ASCENT <m>` / `DEPLOY <m_daling>` / `LAND <m>` — drempels zetten. `ASCENT` = **stijging in meters** t.o.v. grond; `DEPLOY` = **daling in meters vanaf apogee** (fysisch: "zijn we het hoogste punt voorbij?"); `LAND` = meters boven grond voor landing.
  - `GET TRIGGERS` — bv. `OK TRIG ASC=5.0m/0.60hPa DEP=3.0m LND=5.0m` (hPa-equivalent voor ASC wordt toegevoegd zodra `ground_hpa` bekend is).
  - `GET ALT` / `ALT` — `OK ALT <m_boven_grond> <hPa>` (toegelaten in CONFIG én MISSION; werkt alleen als BME280 + grond gekend zijn).
  - `GET APOGEE` — `OK APOGEE <m> <hPa> <age_s>` of `OK APOGEE NONE` (toegelaten in CONFIG én MISSION).
  - `RESET APOGEE` — `OK APOGEE RESET` (alleen CONFIG).
  - `PREFLIGHT` — toont `ERR PRE TIME GND BME IMU DSK LOG FRQ GIM` (alleen wat ontbreekt) of `OK PRE ALL GND=… ASC=… DEP=… LND=…`.
  - `SET MODE MISSION` voert deze check automatisch uit; zolang iets ontbreekt krijg je `ERR PRE …` en **blijft de Zero in CONFIG**.
- **BME280 IIR-filter:**
  - `GET IIR` → `OK IIR <huidige_coef> CFG=<preset> MIS=<preset>`. De huidige waarde is wat er **nu** op de chip staat; `CFG` is wat er in `CONFIG` gebruikt wordt, `MIS` in `TEST`/`MISSION`.
  - `SET IIR <0|2|4|8|16>` — werkt alleen in `CONFIG` (`ERR BUSY` / `ERR BUSY TEST|MISSION` anders). Stelt zowel de chip als het `CFG`-preset bij. Vuistregel: **lagere** coëfficient ⇒ `!alt` reageert sneller op handmatige hoogteveranderingen; **hogere** coëfficient ⇒ minder ruis maar trager (IIR×16 heeft ~10–12 s nodig om 99 % van een nieuwe waarde te bereiken).
  - Auto-switch: bij `SET MODE TEST` en `SET MODE MISSION` wordt de `MIS`-preset (default 16) automatisch toegepast; bij einde-TEST (`EVT MODE CONFIG END_TEST`) en `SET MODE CONFIG` rolt de Zero terug naar de `CFG`-preset (default 4). De defaults zijn instelbaar via `--bme280-iir` (CFG) en `--bme280-iir-mission` (MIS) op de Zero.
- **GET ALT priming:**
  - De BME280 staat in **forced mode**: tussen twee `GET ALT`-calls sampelt de chip niet, dus het IIR-filter staat stil. Eén losse `!alt` na lange stilte zou anders maar `1/IIR` van een echte hoogteverandering "zien".
  - Daarom doet de Zero per `GET ALT` standaard **5 back-to-back reads** (≈750 ms bij OSP×16) en rapporteert de laatste. Het filter is daardoor altijd "ingehaald" wanneer je het antwoord ziet.
  - `GET ALT PRIME` → `OK ALT PRIME <n>`; `SET ALT PRIME <1..32>` (alleen CONFIG) past het aantal samples live aan. Meer = accurater na lange stilte, trager antwoord. `SET ALT PRIME 1` = oud gedrag (1 read).
  - Default instelbaar bij opstart via `--bme280-alt-prime` op de Zero.
- **TEST-mode (dry-run van DEPLOYED):**
  - `SET MODE TEST [seconds]` — start een dry-run van `DEPLOYED` voor `seconds` seconden (default 10, klem 2..60). Eerst loopt een **minimale preflight** (`TIME`, `GND`, `BME`); bij een tekort: `ERR PRE …` en Zero blijft in `CONFIG`. Slaagt de check: `OK MODE TEST <seconds>`, Zero schakelt naar `TEST`.
  - Tijdens `TEST` pusht de Zero elke seconde ongevraagd één `TLM <dt_ms> <alt_m> <p_hpa> <T_c> <heading> <roll> <pitch> <sys_cal>`-regel naar het base station (ontbrekende sensoren → `NA`).
  - Alle commando's worden geweigerd met `ERR BUSY TEST` behalve `PING`, `GET MODE`, `GET TIME`. Ook `SET MODE CONFIG` en `STOP RADIO` zijn geblokkeerd — de timer is bewust niet te aborteren.
  - Na afloop stuurt de Zero éénmalig `EVT MODE CONFIG END_TEST` en herstelt intern naar `CONFIG`. Base station: gebruik `!test [seconds]` om dit in één beweging af te handelen.
- **Flight-state & triggers (Fase 8/8b):**
  - `GET STATE` → `OK STATE <NAME> [<REASON>]` (NAME = NONE / PAD\_IDLE / ASCENT / DEPLOYED / LANDED; REASON = `ACC` / `ALT` / `FREEFALL` / `SHOCK` / `DESCENT` / `IMPACT` / `STABLE` — zie [mission_triggers.md](../../../docs/mission_triggers.md)).
  - `SET STATE <NAME>` — handmatig forceren; alleen in `CONFIG` (wordt in MISSION geweigerd met `ERR BUSY`).
  - `SET TRIG <ST> <FIELD> <VAL>` — nieuwe multi-trigger setter: `ASC HEIGHT|ACC`, `DEP DESCENT|SHOCK|FREEFALL`, `LND ALT|IMPACT|STABLE`. Bv. `SET TRIG ASC ACC 5.0` → `OK TRIG ASC ACC 5.00g`. Alleen in CONFIG.
  - `SET TRIGGER ASCENT|DEPLOY|LAND <m>` — legacy-alias voor de altitude-backup-velden (ASCENT HEIGHT, DEPLOY DESCENT, LAND ALT).
  - `GET TRIG ALL` → `OK TRIG A=<m>/<g> D=<m>/<g>/<s> L=<m>/<g>/<s>` (volledig; past binnen 60 B).
  - **Side-effect `SET MODE MISSION`:** apogee wordt atomair gereset (`max_alt_m` → `None`) zodat een vorige sessie `DESCENT` niet meteen kan fire-n.
- **Servo-rail (Fase 12):**
  - `SERVO STATUS` — altijd toegelaten (ook in MISSION/TEST). Reply: `OK SVO R=<on|off> T=<on|off> SEL=<1|2|-> US1=<us> US2=<us> CAL=<yes|no>`.
  - `SERVO ENABLE` / `DISABLE` / `HOME` / `STOW` / `PARK` / `START [1|2]` / `SEL <1|2>` / `STEP <±us>` / `SET <us>` / `MIN` / `CENTER` / `MAX` / `STOW_MARK` / `SAVE` / `STOP` — alleen in CONFIG. Volledige beschrijving: [servo_tuning.md](../../../docs/servo_tuning.md). Tijdens een tuning-sessie loopt een **5 min watchdog**; bij timeout pusht de Zero `EVT SERVO WATCHDOG` en zet rail uit.
- `STOP RADIO` — beëindigt `cansat_radio_protocol.py` **na** het antwoord `OK STOP RADIO` (werkt in CONFIG en MISSION, **niet** in TEST). Handig bij autostart via **systemd**; alternatief: `sudo systemctl stop …` of SSH/`kill`.

Vrije tekst zonder prefix wordt ook verstuurd (handig om te debuggen).

Antwoorden (conventie op de CanSat): prefix `OK ` of `ERR ` + uitleg.

## CanSat (Raspberry Pi Zero 2 W)

Op de **Zero** (SSH, zelfde repo, venv actief):

```bash
python scripts/cansat_radio_protocol.py
python scripts/cansat_radio_protocol.py --verbose --poll 0.5
python scripts/cansat_radio_protocol.py --verbose --poll 0.5 --reply-delay 0.08
python scripts/cansat_radio_protocol.py --bme280-addr 0x77   # als de sensor op 0x77 zit
python scripts/bme280_test.py --chip-id
python scripts/bme280_test.py --samples 50 --interval 0 --os 1
python scripts/bno055_test.py --chip-id
python scripts/bno055_test.py --samples 20 --interval 0.1
```

`--reply-delay` (seconden): pauze op de Zero **na** ontvangen/verwerken **vóór** de antwoord-TX — geeft de Pico na eigen TX tijd om stabiel in RX te gaan (half-duplex). **Standaard in het script: 0.08 s**; zet op `0` als je geen extra wachttijd wilt.

**BME280:** op de Zero `pip install smbus2` of `pip install -e ".[sensors]"`. Zonder I²C of met `--no-bme280` reageert `READ BME280` met `ERR NO BME280`.

Dat draait `cansat_hw.radio.wire_protocol` (zelfde tekstregels als hierboven). Standaard **node 120**, freq **433.0 MHz**, key `CANSAT_2025-2026` — gelijk houden met de Pico `!freq` / `!dest` / `!node`.

Zonder `cansat_radio_protocol.py` op de **Zero** zie je op de Pico na een TX: *(geen antwoord binnen … s)*.

### Autostart op de Zero (systemd)

Zie in de repo **`deploy/systemd/cansat-radio-protocol.service`**: pas `WorkingDirectory` en `ExecStart` (pad naar venv-Python en eventuele `--freq` / `--node`) aan, kopieer naar `/etc/systemd/system/`, `daemon-reload`, `enable --now`. Zo hoeft niemand met SSH het script handmatig te starten. **Tijdzone** eenmalig instellen (bv. `timedatectl set-timezone Europe/Brussels`) als foto-/videonamen in lokale tijd moeten lopen.

## Radio-instellingen

Moeten **exact** matchen: **AES-key 16 bytes**, **zelfde MHz**, **node 100** (base station) ↔ **120** (CanSat), zoals in `rfm69test_emitter.py` / `radio_rfm69_test.py`.

**Sleutel niet in git**: leg je eigen `RADIO_KEY` in `secrets.py` naast dit bestand
(template: `secrets.example.py`). Op de Zero: `CANSAT_RADIO_KEY` in `.env` aan de
repo-root (template: `.env.example`). Zonder eigen key draaien beide kanten met
de publiek bekende demo-sleutel en zie je een `WARN`. Zie
[`docs/secrets.md`](../../../docs/secrets.md).

## Troubleshooting: Pico ziet geen antwoord, Zero wel `RX` / `TX` in `--verbose`

Dan heeft de **CanSat** het antwoord **over RF verstuurd** (op de Zero: `reply TX ok: True`). Als de Pico toch “geen antwoord” meldt:

1. **Start `cansat_radio_protocol.py` op de Zero vóór** je in Thonny `PING` stuurt.
2. **Half-duplex:** korte **`!gap`** (standaard 50 ms) geeft de CanSat tijd om naar RX te gaan; bij problemen **`!timeout 5`**. Een **`clear_fifo()`** vóór `receive()` is **niet** meer nodig — dat kon juist een al binnengekomen antwoord wissen (STDBY→RX leegt de FIFO).
3. **`!info`** op de Pico: zelfde **freq** en **dest 120** als `--node` / `--freq` op de Zero.
4. **RF-pad:** antennes, afstand, stoorbronnen; asymmetrische path (één richting werkt) komt voor bij zwakke RX.

Zie `basestation_cli.py` — `REPLY_GAP_S`, `REPLY_TIMEOUT_S`.
