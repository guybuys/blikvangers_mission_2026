# Mission triggers — drempelwaarden voor `PAD_IDLE → ASCENT → DEPLOYED → LANDED`

Dit document legt uit **welke gebeurtenissen** de CanSat tijdens
`MISSION` probeert te detecteren, **welke sensoren** daarvoor gecombineerd
worden, hoe je elke drempel **instelt vanaf het base station**, en wat
zinvolle **defaults** zijn.

> **Afkortingen** (eerste gebruik; zie [glossary](glossary.md) voor de
> volledige lijst):
> **CanSat** = flight-software op de Raspberry Pi Zero 2 W.
> **Pico** = base station (Raspberry Pi Pico met Thonny).
> **IMU** = BNO055 9-DoF sensor — levert oa. lineaire acceleratie
> `‖a_lin‖`.
> **BME280** = luchtdruk-/temperatuursensor — levert via ISA-formule een
> hoogte in m boven `ground_hpa`.
> **ISA** = International Standard Atmosphere, het omrekenmodel
> `h ≈ 44330·(1 − (p/p₀)^0.19)`.
> **EVT** = "event"-record dat de CanSat ongevraagd over de radio duwt
> bij een state-transitie of mode-wissel.
> **TLM** = "telemetry"-frame; in MISSION 1 Hz binaire frames van 60 B.
> **`‖a_lin‖`** = norm van de gravity-compensated acceleratie-vector,
> uitgedrukt in `g`.

Zie ook: [Missie-states](mission_states.md) voor het grotere
state-machine-plaatje.

---

## Filosofie: **OR-logica met IMU-primair, altitude-backup**

Per transitie (`PAD_IDLE→ASCENT`, `ASCENT→DEPLOYED`, `DEPLOYED→LANDED`)
evalueert de CanSat **meerdere triggers** in vaste volgorde. Zodra
**één** trigger waar is, gaat de state vooruit. De eerste die vuurt,
bepaalt ook de **`reason`-code** die in de `EVT STATE …` en het
binary-log komt.

Waarom niet één enkele trigger? Een echte raket-launch is zo'n
zeldzaam moment dat we er drie kansen willen hebben om 'm niet te
missen:

| Trigger-type | Sterkte | Zwakte |
|---|---|---|
| **IMU (g-waarde / freefall / stable)** | Snel (<100 ms), onafhankelijk van weer en grondreferentie | BNO055 kan een hick-up hebben; calibratie moet voldoende zijn |
| **Altitude (BME280 + ISA)** | Robuust, langetermijn-drift laag na `CAL GROUND` | Traag (~1 s door IIR-filter); gevoelig voor drukverandering door weer |

De altitude-backups zorgen dat we **toch** in DEPLOYED geraken als de
IMU bv. even in een NaN hangt — kritisch want **we hebben maar één
lancering**.

---

## Overzicht van alle drempels

| State-transitie | Reason | Trigger | Sensor | Eenheid | Default | Range | SET-commando |
|-----------------|--------|---------|--------|---------|---------|-------|--------------|
| `PAD_IDLE → ASCENT` | **`ACC`** | Piek `‖a_lin‖` ≥ drempel | IMU | **g** | **6.0 g** | 0.5 – 20 g | `SET TRIG ASC ACC <g>` |
|                    | **`ALT`** (backup) | Hoogte ≥ drempel boven grond | BME280 | **m** | **5.0 m** | 0.5 – 1000 m | `SET TRIG ASC HEIGHT <m>` |
| `ASCENT → DEPLOYED` | **`FREEFALL`** | Aaneensluitende vrije val ≥ drempel | IMU | **s** | **1.0 s** | 0.05 – 10 s | `SET TRIG DEP FREEFALL <s>` |
|                     | **`SHOCK`** | Piek `‖a‖` ≥ drempel (parachute-snap) | IMU | **g** | **8.0 g** | 1 – 20 g | `SET TRIG DEP SHOCK <g>` |
|                     | **`DESCENT`** (backup) | `max_alt − current_alt` ≥ drempel | BME280 + apogee | **m** | **3.0 m** | 0.5 – 100 m | `SET TRIG DEP DESCENT <m>` |
| `DEPLOYED → LANDED` | **`IMPACT`** | Piek `‖a‖` ≥ drempel (touchdown-schok) | IMU | **g** | **12.0 g** | 1 – 30 g | `SET TRIG LND IMPACT <g>` |
|                     | **`STABLE`** | Hoogte stabiel binnen ruis voor ≥ drempel | BME280 | **s** | **8.0 s** | 1 – 60 s | `SET TRIG LND STABLE <s>` |
|                     | **`ALT`** (backup) | Hoogte ≤ drempel boven grond | BME280 | **m** | **5.0 m** | 0.5 – 500 m | `SET TRIG LND ALT <m>` |

> **Legacy-alias**: `SET TRIGGER ASCENT <m>`, `SET TRIGGER DEPLOY <m>`,
> `SET TRIGGER LAND <m>` blijven werken voor back-compat (oude
> operator-tools). Ze zetten dezelfde altitude-backup-velden als hun
> nieuwe `SET TRIG …`-tegenhanger.

Alle drempels live ophalen:

| Commando | Reply-voorbeeld | Wat zie je |
|---|---|---|
| `GET TRIGGERS` | `OK TRIG ASC=5.0m/0.60hPa DEP=3.0m LND=5.0m` | Compacte **altitude-only** weergave (oudere tooling-compat). hPa-equivalent alleen zichtbaar als `ground_hpa` al gekalibreerd is. |
| `GET TRIG ALL` | `OK TRIG A=5.0m/6.0g D=3.0m/8.0g/1.0s L=5.0m/12.0g/8.0s` | **Volledige** multi-trigger view; past net binnen 60 B. |
| `PREFLIGHT` (als OK) | `OK PRE ALL GND=1019.1 ASC=5.0m DEP=3.0m LND=5.0m` | Handig als confirmatie net vóór `SET MODE MISSION`. |

---

## Reason-codes in detail

De flight-state machine vuurt in de volgorde uit de tabel; de **eerste
match** wint en belandt als `reason` in `EVT STATE <NAME> <REASON>` en
in het binary-log.

```text
PAD_IDLE → ASCENT
  1. ACC       peak ‖a_lin‖ ≥ trig_ascent_accel_g           (IMU, primair)
  2. ALT       alt_m ≥ trig_ascent_height_m                 (BME280, backup)

ASCENT → DEPLOYED
  1. FREEFALL  freefall_for_s ≥ trig_deploy_freefall_s      (IMU, primair)
  2. SHOCK     peak ‖a‖ ≥ trig_deploy_shock_g               (IMU, parachute-snap)
  3. DESCENT   max_alt − alt ≥ trig_deploy_descent_m        (BME280, backup)

DEPLOYED → LANDED
  1. IMPACT    peak ‖a‖ ≥ trig_land_impact_g                (IMU, touchdown)
  2. STABLE    alt_stable_for_s ≥ trig_land_stable_s        (BME280, rust)
  3. ALT       alt_m ≤ trig_land_hz_m                       (BME280, backup)
```

Een transitie verschijnt ook live op het base station:

```text
BS>
RX <- EVT STATE ASCENT ACC           (direct na launch)
RX <- EVT STATE DEPLOYED FREEFALL    (parachute-deploy)
RX <- EVT STATE LANDED IMPACT        (touchdown)
```

Zie [Missie-states §Autonome state-advance](mission_states.md#autonome-state-advance)
voor hoe vaak deze evaluatie loopt (≈5 Hz, gedreven door de
sensor-sampler, onafhankelijk van Pico-commando's).

---

## Per trigger: motivatie + tuning-tips

### `PAD_IDLE → ASCENT`

#### `ACC` — motor-burn-piek (IMU, primair)

- **Bron**: BNO055 lineaire acceleratie (gravity verwijderd). Norm over
  x/y/z, gerapporteerd als "peak-over-laatste-window" door de sampler.
- **Wat verwacht?** Hobby-raket-launch: 5 – 15 g gedurende ~0,5 s.
- **Default 6.0 g**: genoeg marge boven alles wat je met de hand doet
  (oppakken = 3 – 5 g, laten vallen op tafel = 3 – 4 g), net onder een
  minimale motor-burn.
- **Te laag (bv. 3 g)**: indoor-test triggert per ongeluk door
  oppakken / wegleggen.
- **Te hoog (bv. 12 g)**: zwakke booster haalt 'm niet → we vallen
  terug op de `ALT`-backup (trager, maar werkt nog).

#### `ALT` — hoogte-backup (BME280)

- **Bron**: BME280-druk, via ISA-formule omgerekend naar m boven
  `ground_hpa`.
- **Default 5.0 m** ≈ **0,60 hPa** (rond zeeniveau: ~8,3 m/hPa). Bij een
  gekalibreerde grond zie je dat exact in `GET TRIGGERS`:
  `ASC=5.0m/0.60hPa`.
- **Te laag (1 m)**: BME280-ruis of windvlaag schiet 'm af vóór vertrek.
- **Te hoog (50 m)**: we missen het begin van de ascent en de camera /
  fast-log starten laat.
- **Praktisch goed**: 3 – 10 m. Start met de default.

### `ASCENT → DEPLOYED`

#### `FREEFALL` — motor uit, nog vóór parachute (IMU)

- **Bron**: sampler telt **aaneensluitende** secondes waarin
  `‖a_lin‖ ≤ 0,3 g` (tijdelijke drempel in de sampler; niet radio-
  configureerbaar want fundamenteel fysisch).
- **Default 1.0 s** ≈ valdiepte van ~5 m. Kort genoeg om voor deployment
  nog parachute-tijd te hebben, lang genoeg om één BNO055-hickup te
  overleven.
- **Te klein (0,1 s)**: elke korte shake triggert.
- **Te groot (3 s)**: we vallen meter(s) dieper voor detectie.

#### `SHOCK` — parachute-snap (IMU)

- **Bron**: piek `‖a‖` (**niet** gravity-verwijderd, want de snap is zo
  kort dat lineair filteren 'm eet).
- **Default 8.0 g**: parachute-snap is typisch 5 – 20 g.
- Handig als 2e lijn naast `FREEFALL` — een snelle, harde chute-open
  gaat vaak door `FREEFALL` heen vóór die z'n secondes heeft volgemaakt.

#### `DESCENT` — apogee-backup (BME280)

- **Bron**: `state.max_alt_m` wordt bijgewerkt **bij elke** BME280-read
  (ook tijdens CONFIG-tests via `GET ALT`). Vergelijking:
  `max_alt − current_alt ≥ drempel`.
- **Default 3.0 m**: filterruis ≪ drempel (σ ≈ 2 cm bij IIR×16, OSP×16).
- **Waarom niet op tijd?** Een "X seconden na ASCENT"-drempel werkt niet
  voor verschillende motoren; fysisch is "we dalen vanaf het hoogste
  punt" de juiste definitie.
- **Apogee inspecteren / resetten**: `!apogee` → `OK APOGEE <m> <hPa>
  <age_s>`; `!resetapogee` (CONFIG-only) voor een clean start.

> **Belangrijk**: `!apogee` wordt **automatisch gereset** bij elke
> `SET MODE MISSION`. Je hoeft er dus in de preflight-checklist geen
> extra `!resetapogee` meer voor te doen — de CanSat ziet zelf de
> overgang CONFIG→MISSION en zet `max_alt_m` op `None`.

### `DEPLOYED → LANDED`

#### `IMPACT` — touchdown-schok (IMU)

- **Bron**: piek `‖a‖` (raw, gravity niet weggehaald).
- **Default 12.0 g**: gras ~5 – 10 g, asfalt ~10 – 30 g. Op gras krijg
  je soms een "zachte" landing die de drempel niet haalt → `STABLE`
  vangt dat op.

#### `STABLE` — aanhoudende rust (BME280)

- **Bron**: sampler telt `alt_stable_for_s` — secondes waarin de
  hoogte binnen ruis (~σ·2) rond z'n gemiddelde blijft.
- **Default 8.0 s**: lang genoeg om wind-turbulentie onder parachute
  te overleven, kort genoeg dat een zachte landing niet onopgemerkt
  doorstaat.

#### `ALT` — backup onder drempel-hoogte (BME280)

- **Bron**: huidige hoogte ≤ `trig_land_hz_m` (default 5.0 m boven
  grond).
- Defensief: als IMPACT én STABLE falen (bv. continue lichte
  trillingen doordat de CanSat tegen een boom tikt), dan nog geraken
  we in LANDED zodra we op grondniveau hangen.

---

## Hoogte live uitlezen

Voor debuggen en om gevoel te krijgen voor realistische drempels:

- **`!alt`** → `OK ALT <m_boven_grond> <hPa>`; mag ook tijdens MISSION.
  Vereist actieve BME280 + gekalibreerde grond (`!calground` of
  `SET GROUND`). Werkt ook de apogee bij.
- **`!apogee`** → `OK APOGEE <m> <hPa> <age_s>` (age = secondes sinds
  het piekmoment gemeten werd), of `OK APOGEE NONE` als er nog niks
  is opgemeten.
- **`!resetapogee`** → zet de tracking terug op nul (alleen CONFIG).
- **`!triggers`** en **`!trigall`** (→ `GET TRIGGERS` resp. `GET TRIG
  ALL`) tonen de huidige drempels.

---

## Workflow-samenvatting vóór een vlucht

1. **`!time`** / **`!timeepoch …`** — systeemklok (correcte foto-/
   lognamen en log-timestamps).
2. **`!calground`** — gemiddelde BME280-druk wordt `ground_hpa`.
3. **`!alt`** — sanity-check: hoogte ≈ 0 m net boven de tafel.
4. **`!trigall`** — bekijk alle drempels. Niet tevreden? Pas aan met
   `SET TRIG …` (zie tabel). Je mag ook de legacy
   `SET TRIGGER ASCENT/DEPLOY/LAND <m>` gebruiken.
5. **`SET FREQ <mhz>`** (éénmalig, persistent op beide kanten).
6. **`!preflight`** — moet `OK PRE ALL …` teruggeven. `SVO` moet dan
   ook in orde zijn (zie [servo_tuning.md](servo_tuning.md)).
7. **`SET MODE MISSION`** — Zero voert zelf apogee-reset uit en gaat
   naar `PAD_IDLE` met de op dat moment actieve trigger-waarden. Vanaf
   hier zijn `SET TRIGGER` / `SET TRIG` / `SET GROUND` geblokkeerd
   (`ERR BUSY MISSION`); `!alt` en `!apogee` blijven wél werken voor
   telemetrie.

---

## Defaults in één oogopslag

| Constante (code) | Waarde | Betekenis |
|------------------|--------|-----------|
| `DEFAULT_ASCENT_HEIGHT_M` | **5.0** | Stijging (m) voor `ASCENT`-backup |
| `DEFAULT_ASCENT_ACC_G` | **6.0** | Peak `‖a_lin‖` (g) voor `ASCENT` primair |
| `DEFAULT_DEPLOY_DESCENT_M` | **3.0** | Daling vanaf apogee (m) voor `DEPLOYED`-backup |
| `DEFAULT_DEPLOY_FREEFALL_S` | **1.0** | Vrije val (s) voor `DEPLOYED` primair |
| `DEFAULT_DEPLOY_SHOCK_G` | **8.0** | Chute-snap piek (g) voor `DEPLOYED` 2e IMU |
| `DEFAULT_LAND_HZ_M` | **5.0** | Hoogte ≤ … (m) voor `LANDED`-backup |
| `DEFAULT_LAND_IMPACT_G` | **12.0** | Touchdown-piek (g) voor `LANDED` primair |
| `DEFAULT_LAND_STABLE_S` | **8.0** | Rust-duur (s) voor `LANDED` 2e BME280 |
| `PREFLIGHT_BNO_SYS_MIN` | **1** | BNO055 sys-cal minimum (0 – 3) voor preflight |
| `GROUND_CAL_SAMPLES` | **4** | BME280-samples gemiddeld na priming-burst voor `CAL GROUND` |

Alles staat in `src/cansat_hw/radio/wire_protocol.py`. Aanpassen vereist
een deploy van de Zero-code; voor dagelijks tunen gebruik je
`SET TRIG …` vanaf de Pico — dat blijft in RAM tot reboot of
`SET MODE CONFIG → MISSION`-cyclus.

---

## BME280-ruis & IIR-filter (kort)

Oversampling ×16 + IIR ×16 geeft volgens de datasheet ~0,3 Pa RMS ≈
**σ ≈ 2 cm** per sample. Meer dan genoeg voor een 3 m
`DEPLOY DESCENT`-drempel.

De filter-coëfficient wordt automatisch per mode geschakeld: in `CONFIG`
IIR×4 (responsief voor handmatige `!alt`), in `TEST`/`MISSION` IIR×16
(stil signaal voor apogee- en deploy-detectie). Zie
[mission_states.md](mission_states.md#bme280-iir-filter-per-mode) voor
details en override-opties.

---

[← Documentatie-index](README.md) · [← Missie-states](mission_states.md) · [← Project README](../README.md)
