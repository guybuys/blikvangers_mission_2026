# Servo-tuning via radio (Fase 12)

Volledige handleiding voor het instellen en bedienen van de twee
gimbal-servo's **zonder SSH op de Zero** — alles loopt over de radio
vanaf het base station (de Pico).

> **Afkortingen** (eerste gebruik uit het [glossary](glossary.md)):
> **CanSat** = `Raspberry Pi Zero 2 W` met radio + sensoren + servo's.
> **Pico** = `Raspberry Pi Pico` (grondstation, Thonny).
> **BCM** = Broadcom-nummering voor GPIO-pinnen op de Pi.
> **`us`** = microseconde (µs), eenheid van de PWM-pulsbreedte.
> **`stowed`** = "ingeklapte" servo-positie, veilig tijdens transport
> en voor/na de vlucht.
> **rail** = schakelbare 5 V-voedingslijn naar de servo's (BCM6).

---

## Wanneer welke aanpak?

| Situatie | Pad | Hoe |
|---|---|---|
| Eerste kennismaking, geen radio-link nog, of servo draait niet | **SSH** op de Zero | `python scripts/gimbal/servo_calibration.py` — zelfde letter-mapping als hieronder. |
| Hercalibratie op het terrein / geen laptop bij de CanSat | **Radio** (Fase 12) | Op de Pico: `!servo` opent een sub-REPL met dezelfde letters. |
| Snel motoren op center zetten (visuele check) | Radio | `!home` |
| Alles veilig opbergen vóór transport of MISSION | Radio | `!park` |

Beide paden lezen en schrijven dezelfde
[`config/gimbal/servo_calibration.json`](../config/gimbal/servo_calibration.json).

---

## Het calibratie-bestand

```json
{
  "servo1": { "gpio": 13, "min_us": 1050, "center_us": 1400, "max_us": 1750, "stow_us": 1450 },
  "servo2": { "gpio": 12, "min_us": 1150, "center_us": 1600, "max_us": 1950, "stow_us": 1600 },
  "saved_at": 1775260800
}
```

Per servo hebben we **vier** microseconde-waarden nodig:

| Veld | Betekenis | Wordt gebruikt door |
|---|---|---|
| `min_us` | Pulse voor de ene mechanische aanslag | Clamp tijdens mission-runtime; gimbal-loop (Fase 9). |
| `max_us` | Pulse voor de andere mechanische aanslag | Idem. |
| `center_us` | Pulse voor de logische "nul-positie" | Start van tuning, `SERVO HOME`. |
| `stow_us` | Pulse voor de "ingeklapte" safe-positie | `SERVO STOW`, `SERVO PARK`, autonome rail-policy. |

> De **hardware-cap** is altijd **500 – 2500 µs**. Waarden buiten deze
> range worden stil afgekapt om de servo-driver-IC te beschermen.

Als `stow_us` ontbreekt (of één van de andere drie), blokkeert de
**preflight** op code `SVO` bij `SET MODE MISSION`. Je moet dus minstens
**één keer** door de tuning-flow geweest zijn voordat de CanSat naar
MISSION mag.

---

## Wire-commando's (vanaf de Pico)

Alle `SERVO …`-commando's antwoorden met `OK SVO …` of `ERR SVO …`
(3-letter reply-code past binnen de 60 B RFM69-payload).

### Overal toegelaten (inclusief `MISSION`/`TEST`)

| Commando | Effect |
|---|---|
| `SERVO STATUS` | `OK SVO R=<on\|off> T=<on\|off> SEL=<1\|2\|-> US1=<us> US2=<us> CAL=<yes\|no>`. Tijdens tuning reset dit óók de watchdog (handige refresh). |

### Alleen in `CONFIG`

**Rail-bediening** (direct, geen tuning nodig):

| Commando | Effect |
|---|---|
| `SERVO ENABLE` | Rail aan (voeding), **geen** pulse. Servo's blijven vrij draaibaar tot een HOME/STOW/SET volgt. |
| `SERVO DISABLE` | Pulses op 0, rail uit. Stopt tuning indien actief. |
| `SERVO HOME` | Rail aan + beide servo's naar `center_us`. Rail **blijft aan**. `ERR SVO NOCEN` als `center_us` ontbreekt. |
| `SERVO STOW` | Stuur beide servo's naar `stow_us`. Rail moet al aan staan (`ERR SVO RAILOFF` anders). |
| `SERVO PARK` | Volledige sequence: rail aan → `stow_us` → 800 ms wachten → rail uit. Eén commando voor "alles veilig opbergen". |

**Tuning-sub-state** (stapsgewijs instellen):

| Commando | Effect |
|---|---|
| `SERVO START [1\|2]` | Start tuning-sessie. Rail aan, geselecteerde servo naar `center_us` (of 1500 µs als center onbekend). Reply: `OK SVO …`-status. |
| `SERVO SEL 1\|2` | Wissel welke servo de volgende STEP/SET beweegt. |
| `SERVO STEP <±us>` | Beweeg geselecteerde servo met `±us` (−200..+200). `OK SVO STEP <current_us>`. |
| `SERVO SET <us>` | Zet direct op `us` (500..2500). `OK SVO SET <current_us>`. |
| `SERVO MIN` / `CENTER` / `MAX` | Markeer huidige `us` als MIN / CENTER / MAX van de geselecteerde servo. In-memory tot `SAVE`. |
| `SERVO STOW_MARK` | Markeer huidige `us` als `stow_us`. (Bewust andere naam dan `SERVO STOW` zodat het geen manual-stow-actie wordt.) |
| `SERVO SAVE` | Schrijf alle markers atomair naar `servo_calibration.json`. Tuning blijft actief. |
| `SERVO STOP` | Einde tuning: pulses op 0, rail uit. |

> **Belangrijk**: *tijdens tuning* wordt `cal.min_us`/`cal.max_us` **niet**
> gebruikt als clamp — alleen de hardware-cap (500..2500 µs) blijft
> gelden. Zo kun je een ruimere range dan de vorige calibratie zoeken.
> *Buiten tuning* (HOME/STOW/PARK, mission-runtime SET) blijft cal.clamp
> wél actief, zodat een corrupte JSON nooit de servo voorbij zijn
> mechanische aanslag duwt.

---

## Pico-shortcuts

| Commando op de Pico | Stuurt over de radio | Wanneer |
|---|---|---|
| `!servo` | `SERVO START 1` + open sub-REPL | Calibratie van nul af. |
| `!servo tune` | idem | Alias voor `!servo`. |
| `!servo enable` | `SERVO ENABLE` | Diagnose (voltmeter op de rail). |
| `!servo disable` | `SERVO DISABLE` | Na diagnose, of forceer tuning-stop. |
| `!servo park` / `!park` | `SERVO PARK` | Snelle veilige stow vóór transport/MISSION. |
| `!servo home` / `!home` | `SERVO HOME` | Visuele check: klopt de center? |
| `!servo stow` | `SERVO STOW` | Stow zonder rail uit (vereist `ENABLE` vooraf). |
| `!servo status` | `SERVO STATUS` | Snapshot van rail/tuning/pulse/cal. |

---

## De tuning-sub-REPL

Na `!servo` kom je in:

```
servo> _
```

Letter-mapping (consistent met `scripts/gimbal/servo_calibration.py`):

| Toets | Actie | Wire-commando |
|---|---|---|
| `1` / `2` | Selecteer servo 1 / 2 | `SERVO SEL 1\|2` |
| `a` / `d` | −10 µs / +10 µs (fijn) | `SERVO STEP ∓10` |
| `A` / `D` | −50 µs / +50 µs (grof) | `SERVO STEP ∓50` |
| `N` (getal) | Direct naar `<us>` | `SERVO SET <us>` |
| `z` | Markeer huidige als **MIN** | `SERVO MIN` |
| `c` | Markeer huidige als **CENTER** | `SERVO CENTER` |
| `x` | Markeer huidige als **MAX** | `SERVO MAX` |
| `w` | Markeer huidige als **STOW** | `SERVO STOW_MARK` |
| `p` | Status (rail/tuning/cur-us/cal) + reset watchdog | `SERVO STATUS` |
| `s` | Save JSON | `SERVO SAVE` |
| `q` | Stop tuning + sluit sub-REPL | `SERVO STOP` |
| `?` / `h` / `help` | Toon deze tabel | (lokaal) |

### Standaard tuning-flow

```text
BS> !servo
TX -> SERVO START 1
RX <- OK SVO R=on T=on SEL=1 US1=1500 US2=0 CAL=no

servo> 1           # selecteer servo 1 (al actief, bevestigt)
servo> a a a a a   # naar de lage kant
servo> A A A       # groot omlaag tot fysieke aanslag
servo> D           # één stap terug
servo> z           # → OK SVO MIN 850   (voorbeeld)
servo> D D D D ... # andere kant op
servo> z           # Wacht, dat was MIN → ongedaan door x/d verder of nieuwe z later
servo> x           # → OK SVO MAX 2150
servo> N 1500      # naar ~midden
servo> c           # → OK SVO CENTER 1500
servo> w           # → OK SVO STOW 1500 (hier mag stow = center blijven als er geen gimbal is)
servo> 2           # switch naar servo 2
servo> …           # idem
servo> s           # → OK SVO SAVE servo_calibration.json
servo> q           # → OK SVO STOP, sub-REPL sluit
```

### Na het saven

- `!home` — rail aan, beide servo's op center. Visuele controle.
- `!park` — rail aan → stow → 0,8 s → rail uit. Klaar voor transport.
- `!preflight` — `SVO` moet nu niet meer in de missing-list staan.

---

## Veiligheid

### Watchdog

Tijdens een tuning-sessie loopt een **5 minuten** watchdog: als er 5
min lang geen `SERVO …`-commando binnenkomt, wordt de sessie automatisch
gestopt en de rail afgezet. Dit voorkomt dat een vergeten REPL de
servo's urenlang actief houdt (LiPo leeg + warmte).

| Actie | Reset watchdog? |
|---|---|
| `STEP`, `SET`, `SEL`, `MIN`/`CENTER`/`MAX`/`STOW_MARK`, `SAVE` | ✅ |
| `STATUS` | ✅ (ook vanuit MISSION/TEST — de enige manier om te pollen zonder beweging) |
| Niets typen | ❌ — watchdog telt af |

Als de watchdog toeslaat: rail uit, `T=off` in status. De in-memory
markers gaan verloren (tenzij je vooraf `s` deed). Opnieuw `!servo`
start een nieuwe sessie.

### Autonome rail-policy in MISSION/TEST

Zodra de Zero in `MISSION` of `TEST` staat, laat de autonome
[state-policy](mission_states.md#servo-rail-policy-per-flight-state)
de rail zelf beheren. Handmatige `SERVO ENABLE/DISABLE/HOME/PARK/STOW`
worden geweigerd met `ERR BUSY MISSION` of `ERR BUSY TEST`. Alleen
`SERVO STATUS` blijft werken (read-only).

### Preflight

`SET MODE MISSION` en `SET MODE TEST` voeren een preflight uit. De
`SVO`-check faalt als:

- `pigpiod` niet bereikbaar is (Python `pigpio` kan niet connecten),
- één van de calibratie-velden (min/center/max/stow) ontbreekt voor
  servo 1 of 2,
- er op dat moment een tuning-sessie actief is
  (zou met `SERVO STOP` of via watchdog eerst afgerond moeten zijn).

Reply: `ERR PRE SVO` (eventueel gecombineerd met andere missing-codes,
bv. `ERR PRE TIME GND SVO`).

---

## Closed-loop gimbal (Fase 9)

Zodra de calibratie gezet is én er een BNO055 aan de I²C-bus hangt, kan
de Zero-service een **P+I gimbal-stabilisatie** draaien die tijdens
`DEPLOYED` beide servo's actief horizontaal houdt op basis van de
sensor-zwaartekrachtvector. De regelaar leeft in
[`src/cansat_hw/servos/gimbal_loop.py`](../src/cansat_hw/servos/gimbal_loop.py)
en is een *pure* control-object: de main loop bepaalt de cadence en
roept één keer per iteratie `tick(g)` aan met het laatste `read_gravity()`-
sample. Hij zet pas PWM als:

1. `GIMBAL ON` gezet is (of `--gimbal-auto-enable` bij boot),
2. de `ServoController`-rail aan staat (autonoom via de state-policy),
3. `flight_state == DEPLOYED` (in `PAD_IDLE/ASCENT/LANDED` gebeurt
   niets — zelfde gate als de camera-thread).

### Wire-commando's

| Commando | Mode | Effect |
|---|---|---|
| `GIMBAL ON` | CONFIG, TEST, MISSION | Zet closed-loop aan. Reset de I-term zodat een nieuwe sessie schoon vertrekt. |
| `GIMBAL OFF` | CONFIG, TEST, MISSION | Zet closed-loop uit. Laatste geschreven PWM blijft staan (rail beheert de state-policy). |
| `GIMBAL HOME` | CONFIG | Rail aan + beide servo's naar `center_us` + reset rate-limit-vertrekpunt in de loop. `ERR GMB BUSY` buiten CONFIG. |
| `GET GIMBAL` | overal | `OK GIMBAL E=<on\|off> P=<prim\|cold> T=<ticks> R=<rejected> EX=<cg> EY=<cg> U1=<µs> U2=<µs>`. `EX/EY` = laatste LPF-fout in 1/100 m/s² (cg) om onder 60 B te passen. |

Reply-conventie: `OK GIMBAL …` / `ERR GMB …` (3-letter code zodat alles
in de 60 B RFM69-payload past). `ERR GMB NOHW` betekent ofwel geen
BNO055, ofwel geen `center_us` calibratie — start in beide gevallen bij
de `SERVO`-flow.

### Pico-shortcuts

| Pico-commando | Wire-commando | Wanneer |
|---|---|---|
| `!gimbal on` | `GIMBAL ON` | Vóór `!test` of net na `SET MODE MISSION`. |
| `!gimbal off` | `GIMBAL OFF` | Als de gimbal oscilleert of als je manueel wilt posen. |
| `!gimbal home` | `GIMBAL HOME` | Visuele check in CONFIG — alle servo's naar center. |
| `!gimbal status` | `GET GIMBAL` | Live-status tijdens tuning / dry-run. |

### CLI-tuning op de Zero

De service accepteert deze flags (bijv. in
`/etc/systemd/system/cansat-radio-protocol.service.d/override.conf`):

| Flag | Default | Betekenis |
|---|---|---|
| `--gimbal-auto-enable` | off | Start met `GIMBAL ON` direct bij boot. Default **uit**: een verkeerd gemonteerde sensor zou anders meteen aan de servo's trekken. |
| `--gimbal-kx` | 200.0 | P-gain servo1 / gx-fout (µs per m/s²). |
| `--gimbal-ky` | 200.0 | P-gain servo2 / gy-fout. |
| `--gimbal-kix` | 20.0 | I-gain servo1. 0 = uit. |
| `--gimbal-kiy` | 20.0 | I-gain servo2. 0 = uit. |
| `--gimbal-max-us-step` | 20 | Max PWM-verandering per regeltick (~5 Hz → 100 µs/s). |
| `--gimbal-swap-axes` | off | Ruil `gx→servo1 / gy→servo2` om (makkelijker dan re-kalibreren). |

Tuning-conventie is identiek aan
[`scripts/gimbal_level.py`](../scripts/gimbal_level.py); waardes
kunnen 1-op-1 worden overgenomen van een succesvolle SSH-sessie.

### Veiligheidsgrenzen (intern)

De `GimbalLoop` verwerpt samples die niet vertrouwbaar zijn in plaats
van er blind op te reageren:

- **Norm-check**: `‖g‖` moet tussen 7.0 en 12.5 m/s² liggen
  (filtert freefall + saturatie).
- **Spike-check**: maximaal 2.5 m/s² verandering per tick in één as
  (filtert kabel-glitches). Eerste sample zaait enkel de LPF.
- **LPF**: α=0.85 op de raw-vector (verschuift naar 5 Hz tick-rate).
- **Deadband**: ±0.10 m/s² op de P-term; de I-term integreert wél
  door zodat kleine biassen toch wegregelen.
- **Clamp**: elke PWM gaat door `ServoCal.clamp()` vóór het de rail
  bereikt, net als bij `SERVO SET/HOME/STOW`.
- **Rate-limit**: max `--gimbal-max-us-step` µs per tick, vanaf de
  laatst-geschreven positie (niet vanaf center — zo slaat de gimbal
  niet vol uit wanneer je `GIMBAL ON` terwijl hij ergens anders stond).

`GET GIMBAL` laat de tellers zien (`T`=accepted ticks, `R`=rejected
samples) — als `R` blijft oplopen terwijl `T` stilstaat, zit je sensor
in saturatie of is de sensor-kabel niet geaard.

### Wanneer regelt de loop écht?

Er zijn twee gates in de main loop (beide moeten open staan):

1. **``!gimbal on``** → `gimbal_loop.enabled = True`.
2. **Rail aan** → `servo.rail_on == True`.

De rail-status volgt automatisch uit de [state-policy](mission_states.md#servo-rail-policy-per-flight-state):

| Mode / state | Rail | Loop regelt? |
|---|---|---|
| `CONFIG` (rail uit) | uit | **nee** — operator moet eerst `!servo enable` of `!servo home` |
| `CONFIG` + `!servo home` | **aan** | **ja, als `!gimbal on`** — dit is de diagnose-mode |
| `MISSION/PAD_IDLE` | uit | nee |
| `MISSION/ASCENT` | uit | nee |
| `MISSION/DEPLOYED` | **aan** | **ja** — productie-case |
| `MISSION/LANDED` | uit | nee |
| `TEST/DEPLOYED` | **aan** | **ja** — dry-run |

Omdat `SERVO ENABLE` / `SERVO HOME` enkel in CONFIG mag, kan de loop
*buiten* CONFIG niet per ongeluk actief worden tijdens bv. `PAD_IDLE`
— de rail is daar altijd uit via de state-policy, en handmatige
overrides worden geweigerd met `ERR BUSY MISSION`.

Wanneer de loop in CONFIG actief is, schakelt de service ook de main-
loop-polling op naar ~5 Hz (i.p.v. `--poll`, typisch 1 Hz). Zo geeft
`--gimbal-max-us-step 20` dezelfde ~100 µs/s die je ook in MISSION/TEST
krijgt, en kan je het regelgedrag visueel beoordelen.

### Snelle diagnose: reageert de gimbal in de juiste richting?

Nieuwe servo's, ander frame, of in het verleden ooit PWM-kabels
omgedraaid? Dan is het slim om vóór een `!test` even kort te checken
welke servo welke as stuurt en of het teken klopt. Dat kan volledig via
radio vanuit de Pico-REPL:

```text
# 1. Start veilig — rail uit, geen pulses.
BS> !servo disable
BS> !servo status
    → OK SVO R=off T=off SEL=- US1=0 US2=0 CAL=yes

# 2. Beide servo's naar hun gekalibreerde center. Kijk wat er
#    fysiek gebeurt.
BS> !servo home
    → OK SVO HOME US1=1500 US2=1600       (voorbeeld-getallen)
#   Verwacht: beide servo's in het midden van hun range, gimbal
#   waterpas / neutraal. Eén in een extreme positie? → PWM-stekkers
#   zitten op de verkeerde GPIO, of center_us klopt niet.

# 3. Welke fysieke as stuurt servo 1 vs servo 2?
BS> !servo            # opent sub-REPL
servo> 1              # selecteer servo 1
servo> A A            # -100 µs (grove stap)
#   Observeer: welke as van de gimbal kantelt? Noteer (bv. pitch).
servo> D D            # terug naar center
servo> 2              # zelfde voor servo 2
servo> A A
#   Observeer: welke as? (bv. roll)
servo> q              # sluit sub-REPL, servo's uit

# 4. Terug naar center, dan closed-loop aanzetten.
BS> !servo home
BS> !gimbal on
    → OK GIMBAL ON
BS> !gimbal status
    → OK GIMBAL E=on P=cold T=0 R=0 EX=NA EY=NA U1=1500 U2=1600
#   P=cold = LPF nog niet gezaaid; wacht 1-2 sec. na !gimbal on.

# 5. Kantel de cansat LANGZAAM in één as — bv. pitch omhoog ~15°.
#    Houd hem gekanteld, doe telkens:
BS> !gimbal status
#   Verwacht: EX of EY is niet-nul (in cg = 1/100 m/s²; 15° ≈ 250 cg),
#   en U1 of U2 schuift weg van center richting de "compenserende"
#   kant (de servo probeert de camera waterpas te houden).
```

**Diagnose-matrix** bij stap 5:

| Wat je ziet | Interpretatie | Fix |
|---|---|---|
| `T` stilstaand op 0, `R` groeit | Sensor levert samples buiten `g_min..g_max` of voortdurend spikes | `!servo disable`, check BNO055-bedrading / sensor-mount |
| `EX`/`EY` beweegt, `U1`/`U2` niet | `max_us_step=0`, of clamp (cal.min/max) blokkeert | Check `--gimbal-max-us-step` > 0; kalibreer range breder |
| Servo beweegt de **juiste** fysieke as maar naar de **verkeerde kant** | Sign is omgekeerd (gimbal *versterkt* de kanteling i.p.v. compenseren) | Negatieve `--gimbal-kx` (of `-ky`) in de service-override |
| Pitch-kanteling stuurt de **roll-servo** i.p.v. pitch-servo (of omgekeerd) | As-mapping is geswapped | `--gimbal-swap-axes` aanzetten (1-op-1 zwak dan `kx`↔`ky`) |
| Beide bovenstaande tegelijk (verkeerde as én verkeerde kant) | Waarschijnlijk 1 servo mechanisch omgekeerd gemonteerd + PWM swap | Eerst fysiek checken; beide software-fixes combineren kan tot verwarring leiden |
| `!servo home` = mechanische lock (servo tegen eindstop) | `center_us` hoort bij een andere GPIO — je hebt PWM-kabels omgedraaid zonder kalibratie bij te werken | Óf kabels terug zoals vroeger, óf volledige her-kalibratie (`!servo` vanaf nul) |

Als alles klopt: `!gimbal off` → `!servo disable` → ga over naar
`!test 30` voor de échte DEPLOYED-gates, of `SET MODE MISSION` voor
vlucht.

### Verschil met `scripts/gimbal_level.py`

`gimbal_level.py` blijft bestaan als **SSH-only standalone tool** voor
offline tuning op de bank: het logt CSV, doet een warm-up, en kan op 50
Hz draaien met pigpio direct. De Zero-service gebruikt dezelfde
wiskunde, maar:

* tikt in de main loop op ~5 Hz (gedeeld met RX/TX en state-machine),
* leest gravity alleen wanneer beide gates open staan (`enabled` + `rail_on`),
* laat de rail-beheersing aan de state-policy,
* heeft geen warm-up nodig (zero-target: "waterpas" = `gx=gy=0`).

Gebruik `gimbal_level.py` om nieuwe `kx/ky/kix/kiy`-waardes te vinden,
neem die over als `--gimbal-…`-flags in de service.

---

## Troubleshooting

| Symptoom | Meest waarschijnlijke oorzaak | Fix |
|---|---|---|
| `WARN: pigpio niet geïnstalleerd — servo-controller uit` in de service-log | `pigpio` Python-module niet in de venv | `pip install -e ".[gimbal]"` op de Zero, dan `sudo systemctl restart cansat-radio-protocol.service` |
| `SERVO STATUS` geeft `ERR SVO NOHW` | `pigpiod`-daemon niet actief **of** `config/gimbal/servo_calibration.json` ontbreekt | `sudo systemctl enable --now pigpiod`; bestand moet minstens bestaan (mag lege velden hebben) |
| Pico `servo>`-prompt retries bij elke letter | RFM69 dropped packets (ruis, afstand) | `!timeout 5`, `!gap 0.1`, check antennes |
| Commando's geven `ERR SVO NOTUN` na een pauze | Watchdog (5 min) is afgegaan | `q` → `!servo` om opnieuw te starten; markers zijn kwijt (behalve wat je al gesavet had) |
| Servo staat stil bij `!servo enable` | Dit klopt: `ENABLE` geeft voeding, **geen pulse** → geen torque | Gebruik `!home` of `!servo tune` voor een actieve positie |
| Tuning negeert de MIN/MAX limieten | Gewenst: tuning gebruikt alleen de hardware-cap 500..2500 µs zodat je een ruimere range kunt zoeken | Buiten tuning (HOME/STOW/PARK/mission) blijft de cal-clamp wel actief |
| Servo snap't bij `!home` hard terug | Normaal: HOME stuurt met max torque naar center — niet een bug | Zorg dat er mechanisch niets in de weg zit; doe eerst `!servo status` om de huidige positie te zien |
| `SERVO PARK` reply = `ERR SVO NOSTOW` | `stow_us` ontbreekt voor één of beide servo's | Start `!servo`, druk op `w` bij een veilige positie voor beide servo's, `s` om te saven |
| `!gimbal on` reply = `ERR GMB NOHW` | Geen BNO055 **of** calibratie mist `center_us` | Check service-log: "Gimbal-loop beschikbaar …" moet bij boot staan. Zo niet: `!preflight`, kalibreer servo's opnieuw, of check de BNO055-bedrading |
| `GET GIMBAL` toont `R` oplopend, `T` stil | Gravity-samples worden verworpen (norm of spike) | BNO055-sensor in saturatie (hevige trillingen) of kabel los — `GIMBAL OFF`, debug sensor, daarna opnieuw `ON` |
| Gimbal jaagt / oscilleert in DEPLOYED | `--gimbal-kx` / `--gimbal-ky` te hoog | Begin met `!gimbal off`, halveer de gain in de service-override, reboot de service, test met `!test 30` |
| Gimbal corrigeert in verkeerde as | Sensor-frame ≠ gimbal-frame | Gebruik `--gimbal-swap-axes` (of negatief `kx`/`ky`) i.p.v. kalibratie opnieuw te doen |

---

## Zie ook

- [Mission states](mission_states.md) — wanneer wordt de rail autonoom
  aan/uit gezet door de state-policy?
- [Glossary](glossary.md) — definities van `stowed`, `rail`, `SERVO …`.
- [`scripts/gimbal/README.md`](../scripts/gimbal/README.md) — lokale
  tuning via SSH.
- [`src/cansat_hw/servos/controller.py`](../src/cansat_hw/servos/controller.py)
  — implementatie van rail, pulses, tuning-state, watchdog, JSON I/O.

---

[← Documentatie-index](README.md) · [← Project README](../README.md)
