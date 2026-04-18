# Mission triggers — drempelwaarden voor `PAD_IDLE → ASCENT → DEPLOYED → LANDED`

Dit document legt uit **welke gebeurtenissen** de Zero tijdens `MISSION` probeert te detecteren, **welke sensor-drempel** daar in de code bij hoort, hoe je die **instelt vanaf het base station**, en wat zinvolle **defaults** zijn.

> **Belangrijk:** de wire-commando's om de triggers te **configureren** (en de hoogte/apogee op te vragen) werken al. De vluchtlogica die ze vervolgens **gebruikt** om van `PAD_IDLE` naar `ASCENT` enz. te springen, zit nog niet in productie — de runtime-state is geschreven zodat die loop straks alleen `state.trig_*` en `state.max_alt_m` hoeft te lezen.

Zie ook: [Missie-states](mission_states.md) voor de bredere state-machine.

---

## Overzicht

| Trigger | Gebeurtenis | Sensor | Eenheid | Default | Range | Instelcommando |
|---------|-------------|--------|---------|---------|-------|----------------|
| **`ASCENT`** | Lancering: CanSat is voldoende ver **boven** het grondniveau gestegen | BME280 (druk → hoogte) | **m** (stijging) | **5.0 m** | 0.5 — 1000 m | `SET TRIGGER ASCENT <m>` |
| **`DEPLOY`** | Apogee is voorbij: CanSat is **`DEP` meter gedaald vanaf de hoogste hoogte** die tot nu toe gezien is | BME280 + apogee-tracking (`max_alt_m`) | **m** (daling vanaf piek) | **3.0 m** | 0.5 — 100 m | `SET TRIGGER DEPLOY <m>` |
| **`LAND`** | Teruggevallen tot op grondniveau (landing) | BME280 (hoogte t.o.v. `ground_hpa`) | **m** (boven grond) | **5.0 m** | 0.5 — 500 m | `SET TRIGGER LAND <m>` |

Huidige waarden bekijken: `!triggers` op de Pico → bv. `OK TRIG ASC=5.0m/0.60hPa DEP=3.0m LND=5.0m`.
Alle waarden vind je ook in `PREFLIGHT`-OK: `OK PRE ALL GND=1019.1 ASC=5.0m DEP=3.0m LND=5.0m`.

---

## `ASCENT` — "zijn we gelanceerd?"

**Vraag die de Zero zichzelf stelt (in `PAD_IDLE`):** "Ben ik nu minstens `ASC` meter hoger dan de grondreferentie?"

- **Bron:** BME280-druk, vergeleken met `ground_hpa` (vastgelegd via `CAL GROUND` of `SET GROUND` tijdens CONFIG).
- **Interne omrekening:** `ASC` (meters) wordt via de ISA-formule omgerekend naar een drukdaling in hPa. Rond zeeniveau geldt ongeveer **8,3 m per hPa**, dus 5 m ≈ 0,60 hPa — precies wat je terugziet in `GET TRIGGERS` zodra grond gekalibreerd is.
- **Effect van de drempel:**
  - **Te laag** (bv. 1 m): ruis op de BME280 of wind kan de trigger per ongeluk afschieten voor de raket vertrekt.
  - **Te hoog** (bv. 50 m): we missen het begin van de ascent (of pieken te kort om het te zien) — camera en fast-log starten te laat.
  - **Praktisch goed:** **3 — 10 m** voor een CanSat-opstelling. Start met de default **5 m**.
- **Zelf instellen:** `SET TRIGGER ASCENT 5` → antwoord `OK TRIG ASCENT 5.00m`.
- **Hoe checken zonder vlucht?** In CONFIG kan je `BME280` herhaaldelijk opvragen of `scripts/bme280_test.py --samples 50` draaien; het verschil tussen samples toont je de ruis en geeft een realistische ondergrens voor `ASC`.

> **Waarom in meters, niet hPa?** Meters zijn voor iedereen intuïtief ("5 m hoog"); de omzetting naar hPa hangt af van het actuele weer. Door intern pas bij preflight/detectie om te rekenen naar hPa **met de huidige `ground_hpa`**, klopt de drempel ongeacht of het vandaag 1013 of 1020 hPa is.

---

## `DEPLOY` — "is de parachute eruit? / dalen we?"

**Vraag (in `ASCENT`):** "Is de huidige hoogte minstens `DEP` meter lager dan de **hoogste hoogte** die we tot nu toe gezien hebben?"

- **Bron:** BME280-druk omgezet naar hoogte (zelfde ISA-formule als `ASCENT`), vergeleken met `state.max_alt_m`. Die **apogee** wordt bij elke BME280-lezing automatisch bijgewerkt — zowel via de MISSION-loop als via `GET ALT`/`BME280`-requests tijdens CONFIG-tests.
- **Waarom niet op tijd?** Een tijd-drempel ("2 seconden na ASCENT") neemt niet mee hoe lang de motor brandt of hoe hoog de raket komt. Een **daling vanaf apogee** is fysisch het juiste criterium: je weet zeker dat je niet meer stijgt.
- **Effect van de drempel:**
  - **Te klein** (bv. 0,5 m): ruis op de BME280 of een windvlaag kan DEPLOY per ongeluk triggeren vóór het echte hoogtepunt.
  - **Te groot** (bv. 20 m): we detecteren pas heel laat; relevant log wordt gemist.
  - **Praktisch:** **2 — 5 m** voor een CanSat. Start met de default **3 m**.
- **Zelf instellen:** `SET TRIGGER DEPLOY 3.0` → antwoord `OK TRIG DEPLOY 3.00m`.
- **Apogee inspecteren / resetten:** `!apogee` (→ `OK APOGEE <m> <hPa> <age_s>`), `!resetapogee` (→ `OK APOGEE RESET`). Voor een test kan je `!resetapogee` gebruiken voor je opnieuw begint.

---

## `LAND` — "liggen we op de grond?"

**Vraag (in `DEPLOYED`):** "Zijn we teruggevallen tot binnen `LND` meter van de grondreferentie, en is de beweging 'rustig'?"

- **Bron:** BME280-hoogte (drukdaling hersteld naar bijna grond) + BNO055 die weinig beweging meldt.
- **Effect:**
  - **Te klein** (bv. 1 m): door ruis of windschokken denkt de CanSat dat hij nog niet geland is → radio blijft onnodig aan.
  - **Te groot** (bv. 50 m): de CanSat schakelt al naar `LANDED` terwijl hij nog stevig zweeft.
  - **Praktisch:** **3 — 10 m**. Start met de default **5 m**.
- **Zelf instellen:** `SET TRIGGER LAND 10` → antwoord `OK TRIG LAND 10.00m`.

---

## Hoogte live uitlezen

Voor debuggen en om gevoel te krijgen voor realistische drempels:

- **`!alt`** → `OK ALT <m_boven_grond> <hPa>`; mag ook tijdens MISSION. Vereist actieve BME280 en een gekalibreerde grond (`!calground` of `SET GROUND`). Elke aanroep **werkt ook de apogee bij**.
- **`!apogee`** → `OK APOGEE <m> <hPa> <age_s>` (age = secondes sinds het piekmoment gemeten werd) of `OK APOGEE NONE` als er nog niks is opgemeten.
- **`!resetapogee`** → zet de tracking terug op nul (alleen in CONFIG).

Apogee wordt automatisch gevolgd bij elke BME280-lezing: `GET ALT`, `BME280`/`READ BME280` en binnenkort de MISSION-loop zelf. Je hoeft dus niets speciaals aan te zetten — zolang grond gekalibreerd is, loopt de teller.

---

## Workflow samengevat

1. **`!time`** — zet de systeemklok (correcte foto-/lognamen en log-timestamps).
2. **`!calground`** — gemiddelde BME280-druk wordt `ground_hpa` (grondreferentie).
3. **`!alt`** / **`!apogee`** — sanity-check: hoogte ≈ 0 m net boven de tafel, apogee begint op `NONE`.
4. **`!triggers`** — check de defaults. Niet tevreden? Pas aan met `SET TRIGGER …`.
5. **`SET FREQ <mhz>`** (éénmalig) — persistent aan beide kanten.
6. **`!resetapogee`** — zet de apogee-teller op nul vlak vóór je in MISSION gaat.
7. **`!preflight`** — moet `OK PRE ALL …` teruggeven.
8. **`SET MODE MISSION`** — Zero gaat naar `PAD_IDLE` met de **op dat moment actieve** trigger-waarden. Na deze stap worden triggers en grondreferentie niet meer aangepast over de radio (CONFIG-only). `!alt` en `!apogee` blijven wél werken voor telemetrie.

---

## Defaults in één overzicht

| Constante (code) | Waarde | Betekenis |
|------------------|--------|-----------|
| `DEFAULT_ASCENT_HEIGHT_M` | 5.0 | stijging m voor `ASCENT`-detectie |
| `DEFAULT_DEPLOY_DESCENT_M` | 3.0 | daling vanaf apogee vóór `DEPLOY` triggert |
| `DEFAULT_LAND_HZ_M` | 5.0 | terugval tot binnen deze hoogte boven grond ⇒ `LANDED` |
| `PREFLIGHT_BNO_SYS_MIN` | 1 | BNO055 systeem-calibratie minimum (van 0..3) |
| `GROUND_CAL_SAMPLES` | 16 | aantal BME280-samples voor `CAL GROUND` |

Deze defaults staan in `src/cansat_hw/radio/wire_protocol.py` — aanpassen vereist wel een deploy van de Zero-code. Voor dagelijks tunen gebruik je `SET TRIGGER …` vanaf de Pico; die keuze blijft in RAM tot reboot of `SET MODE CONFIG → MISSION` cyclus.

---

[← Documentatie-index](README.md) · [← Missie-states](mission_states.md) · [← Project README](../README.md)
