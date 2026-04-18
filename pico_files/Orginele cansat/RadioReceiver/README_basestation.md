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
| `!timeout 2.0` | Seconden wachten op antwoord na zenden |
| `!gap 0.05` | Pauze na eigen TX vóór RX (half-duplex) |
| `!info` | Huidige instellingen |
| `!time` | Stuurt `SET TIME <epoch>` naar de CanSat (MicroPython `time.time()` — voor juiste tijd: Pico-klok syncen vanaf Thonny/laptop, zie hieronder) |
| `!timeepoch N` | Zelfde met vast **Unix-tijd** `N` (op de laptop: `date +%s`) — handig als de Pico geen juiste RTC heeft |
| `!gettime` | Stuurt `GET TIME` naar de CanSat; de Zero antwoordt met `OK TIME <epoch> <ISO local>` (bv. `2026-04-17T23:55:36+02:00`) |
| `!preflight` | Stuurt `PREFLIGHT` — toont missende categorieën of `OK PRE ALL …` met grond-/trigger-info |
| `!calground` | Stuurt `CAL GROUND` — de Zero middelt BME280-druk en bewaart die als grondreferentie |
| `!triggers` | Stuurt `GET TRIGGERS` — huidige drempels (ASCENT m [+ hPa-equiv], DEPLOY m-daling vanaf apogee, LAND m) |
| `!alt` | Stuurt `GET ALT` — hoogte boven grondreferentie + actuele druk (werkt ook in MISSION) |
| `!apogee` | Stuurt `GET APOGEE` — hoogste hoogte tot nu toe, bijhorende druk en ouderdom in s |
| `!resetapogee` | Stuurt `RESET APOGEE` — apogee-tracking herbeginnen (alleen CONFIG) |
| `!listen` | Alleen RX-loop (tot Stop in Thonny) |
| `!log on [pad]` | Start JSON-lines log op de Pico-flash (default `cansat_<timestamp>.jsonl`) |
| `!log off` | Sluit de actieve log af |
| `!log status` | Toont of er gelogd wordt + pad + laatst gekende MISSION-mode |

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
- `parsed` ontleedt bekende `OK`-replies naar kolom-vriendelijke velden (`alt_m`, `pressure_hpa`, `temp_c`, `humidity_pct`, `ground_hpa`, `freq_mhz`, `epoch`, …) — zo is MISSION-telemetrie direct in `pandas` of een dashboard te gooien.
- `mode` toont de laatst ontvangen `OK MODE …` zodat je CONFIG- en MISSION-secties makkelijk kunt filteren.

Analyseren later (op de laptop):

```python
import pandas as pd
df = pd.read_json("cansat_20260418_123456.jsonl", lines=True)
mission = df[df["mode"] == "MISSION"]
alt = pd.json_normalize(mission["parsed"])
```

**Let op Pico-flash:** een log-bestand van ~200 KB (≈ een uur vliegen met `!alt` elke seconde) past prima; elke write `flush()`t direct, zodat je bij een reset geen regels verliest. Schakel `!log off` aan het eind van een sessie zodat de file netjes wordt gesloten. Voor lange sessies: regelmatig nieuwe file (`!log off` + `!log on`) — blokken van ~1 MB blijven handelbaar.

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
  - `CAL GROUND` — BME280-gemiddelde (16 samples) als grondreferentie (`OK GROUND <hPa>`).
  - `SET GROUND <hPa>` — grondreferentie handmatig.
  - `GET GROUND` — huidige referentie of `OK GROUND NONE`.
  - `SET TRIGGER ASCENT <m>` / `DEPLOY <m_daling>` / `LAND <m>` — drempels zetten. `ASCENT` = **stijging in meters** t.o.v. grond; `DEPLOY` = **daling in meters vanaf apogee** (fysisch: "zijn we het hoogste punt voorbij?"); `LAND` = meters boven grond voor landing.
  - `GET TRIGGERS` — bv. `OK TRIG ASC=5.0m/0.60hPa DEP=3.0m LND=5.0m` (hPa-equivalent voor ASC wordt toegevoegd zodra `ground_hpa` bekend is).
  - `GET ALT` / `ALT` — `OK ALT <m_boven_grond> <hPa>` (toegelaten in CONFIG én MISSION; werkt alleen als BME280 + grond gekend zijn).
  - `GET APOGEE` — `OK APOGEE <m> <hPa> <age_s>` of `OK APOGEE NONE` (toegelaten in CONFIG én MISSION).
  - `RESET APOGEE` — `OK APOGEE RESET` (alleen CONFIG).
  - `PREFLIGHT` — toont `ERR PRE TIME GND BME IMU DSK LOG FRQ GIM` (alleen wat ontbreekt) of `OK PRE ALL GND=… ASC=… DEP=… LND=…`.
  - `SET MODE MISSION` voert deze check automatisch uit; zolang iets ontbreekt krijg je `ERR PRE …` en **blijft de Zero in CONFIG**.
- `STOP RADIO` — beëindigt `cansat_radio_protocol.py` **na** het antwoord `OK STOP RADIO` (werkt in CONFIG en MISSION). Handig bij autostart via **systemd**; alternatief: `sudo systemctl stop …` of SSH/`kill`.

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
