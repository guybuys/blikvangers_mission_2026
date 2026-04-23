# Missie-checklist — leidraad op lanceerdag

Print dit of houd het naast de Thonny-sessie. Volgorde is **ongeveer** chronologisch; pas aan als jullie workflow anders is. Uitgebreide uitleg bij triggers: [mission_triggers.md](mission_triggers.md); states en preflight: [mission_states.md](mission_states.md).

---

## 0 — Voor je vertrekt (hardware & SD)

- [ ] **Batterij**: genoeg capaciteit voor jullie eis (incl. wachttijd op de pad); connectoren stevig.
- [ ] **Zero**: `cansat-radio-protocol` draait (of startprocedure gekend), SD niet vol, **≥ 500 MB vrij** op `/` (preflight `DSK`).
- [ ] **Fotomap op de Zero** bestaat en is schrijfbaar (typisch `/home/icw/photos` — preflight `LOG`).
- [ ] **Pico**: juiste `basestation_cli.py` + `protocol.py` + `rfm69.py` op flash; USB-kabel mee.
- [ ] **Gimbal**: servo's mechanisch in **stow** vóór transport (`!servo park` in CONFIG als de radio al werkt).
- [ ] **Documentatie**: frequentie / node-ID / call-sheet bij de hand.

---

## 1 — Radio-link & basisinstellingen (CONFIG)

Thonny starten → `BS>` prompt. Eerst lokaal op de Pico:

- [ ] **`!freq <MHz>`** — zelfde band als de CanSat (en als opgeslagen in `config/radio_runtime.json` op de Zero).
- [ ] **`!dest` / `!node`** — bestemming en eigen node kloppen met jullie netwerk.
- [ ] **`!timeout`** — bij trage antwoorden of drukke omgeving eventueel verhogen (default 8 s; `PREFLIGHT` met BME IIR×16 kan traag aanvoelen).

Dan naar de CanSat (elke regel **zonder** `!` is een draad-commando; zie `!wirehelp`):

- [ ] **`PING`** — antwoord `OK PING` (link werkt).
- [ ] **`SET TIME …` of `!time` / `!timeepoch $(date +%s)`** — klok redelijk (preflight `TIME`).
- [ ] **`CAL GROUND` of `!calground`** — grondluchtdruk vastgelegd na minuten stabilisatie buiten de raket (preflight `GND`). *Niet* schudden tijdens cal.
- [ ] **`READ BME280`** / **`READ BNO055`** — plausibele waarden; BNO-cal zie §3.
- [ ] **`GET TRIGGERS`** of **`!triggers`** — bevestig drempels (zie §5). Voor **alle** IMU + alt parameters: typ regel **`GET TRIG ALL`** (geen `!`-shortcut op de Pico).

---

## 2 — JSONL-log op de Pico (**niet overslaan**)

Zonder actieve log verdwijnen TLM/EVT en antwoorden uit jullie latere analyse.

- [ ] **`!log on`** (optioneel pad: `!log on cansat_2026-04-23.jsonl`) — vóór of direct na de eerste serieuze tests die dag.
- [ ] **`!log status`** — bevestigen dat er gelogd wordt.
- [ ] Na de sessie: **`!log off`** (bestand netjes sluiten) en JSONL veiligstellen / converteren (zie [README_basestation](../pico_files/Orginele%20cansat/RadioReceiver/README_basestation.md)).

---

## 3 — BNO055-calibratie (preflight `IMU`)

`READ BNO055` eindigt met **sys / gyr / acc / mag** (elk 0–3).

- [ ] CanSat **rustig laten liggen** → gyro en accel komen meestal snel goed.
- [ ] **`sys`** blijft < 3 zolang **mag < 3** (normaal).
- [ ] Preflight vereist minimaal **sys ≥ 1, acc ≥ 2, mag ≥ 2**. Is **mag = 0** na reboot: kort **figuur-8** in de vrije lucht, weg van metaal, tot `mag` minstens 2 — anders **`PREFLIGHT`/`SET MODE MISSION` blokkeert** op `IMU`.
- [ ] Als jullie gimbal alleen op **zwaartekracht** baseert: acc/gyr zijn het belangrijkst; mag is vooral voor heading en voor deze drempel.

---

## 4 — Optioneel: droogloop zonder echte vlucht

- [ ] **`!test [seconden]`** (2–60) — Zero in `TEST`, `DEPLOYED` gedrag + TLM, daarna automatisch terug naar CONFIG. Handig voor gimbal/demo; **geen** echte state-triggers.
- [ ] In `TEST`: **`!gimbal on`** / **`!gimbal off`** mag (regelaar actief in `DEPLOYED`-achtige fase); zie [mission_states.md](mission_states.md).

---

## 5 — Triggerdrempels (kort)

**Regel:** per overgang geldt **OR-logica** — **één** waar trigger volstaat; de **eerste** getroffen bepaalt de **reden** in `EVT STATE …` (bv. `ACC`, `FREEFALL`, `IMPACT`).

| Fase-overgang | Trigger | Wat het ongeveer betekent | Default (indicatie) | Instellen (voorbeeld) |
|---------------|---------|---------------------------|----------------------|-------------------------|
| **PAD_IDLE → ASCENT** | **ACC** | Piekversnelling langs IMU (raketmotor / worp) ≥ drempel | 6 g | `SET TRIG ASC ACC 6.0` |
| | **ALT** (backup) | Hoogte boven grond ≥ drempel (BME trager) | 5 m | `SET TRIG ASC HEIGHT 5.0` |
| **ASCENT → DEPLOYED** | **FREEFALL** | Aaneengesloten tijd met “geen gewicht” (‖a‖ laag) ≥ drempel | 1,0 s | `SET TRIG DEP FREEFALL 1.0` |
| | **SHOCK** | Korte hoge piek-**g** (parachute/hard open) | 8 g | `SET TRIG DEP SHOCK 8.0` |
| | **DESCENT** (backup) | Gevallen vanaf **apogee** ≥ drempel (druk/hoogte) | 3 m | `SET TRIG DEP DESCENT 3.0` |
| **DEPLOYED → LANDED** | **IMPACT** | Harde landingsschok | 12 g | `SET TRIG LND IMPACT 12.0` |
| | **STABLE** | Hoogte “rustig” binnen ruis gedurende ≥ drempel | 8 s | `SET TRIG LND STABLE 8.0` |
| | **ALT** (backup) | Hoogte ≤ drempel boven grond (bijna terug op maaiveld) | 5 m | `SET TRIG LND ALT 5.0` |

**Praktisch:**

- **ACC te laag** → valse start (trillen op de pad). **Te hoog** → missen van lancering.
- **ALT-backup** helpt als IMU even haper; **gevoelig** voor wind/thermiek op de druk — afstemmen op jullie testdata.
- **FREEFALL** — kort genoeg om echte deploy te vangen, lang genoeg om ruis te negeren.
- **DESCENT** — vangt uitrol onder parachute als IMU-signalen zacht zijn.
- **STABLE** — kan “LANDED” geven op rustige hang / lage snelheid; combineer met **IMPACT** voor harde landing.

Oude korte vormen blijven werken: `SET TRIGGER ASCENT <m>`, `DEPLOY`, `LAND` (alleen de hoogte-backups).

---

## 6 — Laatste check vóór MISSION

- [ ] **`PREFLIGHT`** of **`!preflight`** — `OK PRE ALL …` (geen `ERR PRE …`). Herstel ontbrekende codes (zie tabel hieronder).
- [ ] Nog eens **`GET TRIG ALL`** of **`GET TRIGGERS`** — waarden zijn de bedoeling.
- [ ] **Gimbal in raket**: servo's **stowed**; in software volgt o.a. `PAD_IDLE` met rail uit — fysiek al veilig in de buis.

**Preflight-codes (compact)**

| Code | Probleem | Typische fix |
|------|-----------|--------------|
| `TIME` | Klok niet gezet | `!time` / `SET TIME` |
| `GND` | Geen gronddruk | `!calground` |
| `BME` | Sensor/Range | I²C, omgeving |
| `IMU` | Cal te laag | BNO beweging / figuur-8 voor mag |
| `DSK` | Te weinig ruimte | Opruimen op Zero |
| `LOG` | Geen schrijfbare fotomap | `mkdir`, rechten, `--photo-dir` |
| `FRQ` | Freq niet gezet/geladen | `SET FREQ` / persistentie |
| `GIM` | Geen servo-cal JSON | [servo_tuning](servo_tuning.md) / `scripts/gimbal/servo_calibration.py` |

---

## 7 — Missie starten

- [ ] **`SET MODE MISSION`** (geen `!`; antwoord `OK MODE MISSION`) — Zero begint in **`PAD_IDLE`**, apogee wordt gereset.
- [ ] **`!listen`** — de Pico-CLI blokkeert anders op `input()` en **ontvangt geen TLM/EVT** tussen commando’s. Na MISSION-start: **`!listen`** voor continu luisteren (liefst met **`!log on`** al actief). **Ctrl+C** stopt alleen listen en geeft **`BS>`** terug (geen script-herstart nodig); **Thonny Stop** stopt het hele base-station-script.
- [ ] In **`PAD_IDLE`**: lage autonomie-telemetrie (o.a. **~5 s beacon**-interval in de huidige code — geen continue 1 Hz); radio kan zwak ogenblikken — dat is by design t.o.v. batterij.
- [ ] **Niet** rekenen op tussentijdse `SET STATE` in MISSION: autonome overgangen alleen (tenzij jullie firmware dat expliciet toevoegt).

**Verwachte events (voorbeelden)**

- `EVT STATE ASCENT ACC` of `… ALT` — lancering herkend.
- `EVT STATE DEPLOYED FREEFALL` of `… SHOCK` of `… DESCENT` — uit de raket / rust modus.
- `EVT STATE LANDED IMPACT` of `… STABLE` of `… ALT` — op de grond / rust.

Zie **`!state`** of `GET STATE` als je twijfelt.

---

## 8 — Na landing (recovery)

- [ ] **Locate**: zwakke periodic beacon in **`LANDED`** mogelijk; richtantenne / loop naar laatste positie.
- [ ] **Servo’s**: in `LANDED` geen autonome stow — **`!servo park`** wanneer veilig te pakken.
- [ ] **Logs**: `!log off` op de Pico, JSONL + eventuele Zero-logs / foto’s ophalen ([zero_logs](zero_logs.md), `fetch_zero_photos.sh`).

---

## 9 — Spiekbrief: veelgebruikte Pico-commando’s

| Commando | Actie |
|----------|--------|
| `!preflight` | `PREFLIGHT` naar Zero |
| `!calground` | `CAL GROUND` |
| `!triggers` | `GET TRIGGERS` |
| `!state` / `!alt` / `!apogee` | Status / hoogte / apogee |
| `!log on` / `!log off` | JSONL-sessie |
| `!listen` | Continu RX (TLM/EVT); na `SET MODE MISSION` essentieel; Ctrl+C → `BS>` |
| `!test [s]` | Dry-run `DEPLOYED` |
| `!gimbal on` / `off` / `status` | Gimbal in MISSION/TEST + status |

Ruwe draadregels: `PING`, `PREFLIGHT`, `SET MODE MISSION`, `GET TRIG ALL`, `SET TRIG …`.

---

*Laatste tip: vink **`!log on`** bewust aan in §2 vóór druk oefenen — gisteren vergeten is vandaag geen data.*
