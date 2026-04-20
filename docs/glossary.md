# Woordenlijst & afkortingen

[← Documentatie-index](README.md) · [← Project README](../README.md)

Eén centrale plek voor alle terugkerende afkortingen, jargon en codenamen
in deze repo. Andere documenten linken hier naartoe bij eerste gebruik.

> **Conventie**: in de losse documenten schrijven we een afkorting bij het
> eerste gebruik **één keer** uit (bv. *"TLM (Telemetry — sensormeetwaarden)"*),
> en linken eventueel hierheen voor de volledige uitleg.

## Hardware

| Term | Betekenis | Toelichting |
|---|---|---|
| **Pico** | Raspberry Pi Pico W | Het **base station / grondstation**. MicroPython, RFM69-radio, runt [`basestation_cli.py`](../pico_files/Orginele%20cansat/RadioReceiver/basestation_cli.py). |
| **Zero** | Raspberry Pi Zero 2 W | De **CanSat-payload zelf**. CPython 3, sensoren + camera + gimbal + RFM69. |
| **RFM69** | RFM69HCW transceiver | 433/868 MHz radiomodule. Half-duplex (kan niet tegelijk zenden + ontvangen), max 60 byte payload, 250 kbit/s. |
| **BME280** | Bosch omgevingssensor | Druk (hPa), temperatuur (°C), luchtvochtigheid (%RH). Via I²C. Levert hoogte via barometrische formule (zie ISA). |
| **BNO055** | Bosch 9-DOF IMU | Versnelling, gyroscoop, magnetometer + sensor-fusion → Euler-hoeken (heading/roll/pitch). Via I²C. |
| **IMU** | Inertial Measurement Unit | Verzamelterm voor accelerometer + gyroscoop (+ magnetometer). Bij ons = BNO055. |
| **AprilTag** | Visueel fiducial-marker | Zwart/wit vierkant, vergelijkbaar met QR-code; door de camera op de Zero gedetecteerd voor doel-localisatie tijdens `DEPLOYED`. Familie `tag36h11` voor de 2026-missie. Zie [`camera.md`](camera.md). |
| **tag-registry** | `config/camera/tag_registry.json` | Per-ID fysieke afmeting (`size_mm`) + lens- en sensor-parameters (`focal_length_mm`, `pixel_pitch_um`). Input voor de afstandsberekening in de camera-pipeline. |
| **`focal_length_px`** | Brandpuntafstand in pixels | Afgeleid uit `focal_length_mm × 1000 / pixel_pitch_um`. Voor 25 mm telelens + OV2311 (Arducam B0381 PiVariety, 3,0 µm pitch) = **~8 333 px** op volle resolutie. Ter vergelijking: dezelfde lens op een IMX477 (1,55 µm) zou ~16 129 px geven — dus controleer altijd dat `tag_registry.json` de werkelijk gemonteerde sensor reflecteert. Gebruikt in de pinhole-afstand-formule `d = f_px × tag_size_m / max_side_px`. |
| **`max_side_px`** | Langste zijde van de AprilTag-vierhoek in pixels | Uitgedrukt in full-res pixels (corners worden na detectie op downscaled frame teruggeschaald). Input voor de afstandsberekening. |
| **Gimbal** | 2-as servo-platform | Houdt camera horizontaal tijdens descent, aangestuurd via `pigpio` op basis van BNO055-Euler. |
| **stowed** | "Ingeklapte" servo-positie | Veilige mechanische rust-stand voor in-rocket en post-landing. Gekalibreerd per servo (`stow_us` in `config/gimbal/servo_calibration.json`). Gebruikt door `SERVO STOW` / `SERVO PARK` en autonoom bij `MISSION`-entry, `LANDED`, `END_TEST` en service-shutdown. |
| **rail (servo-rail)** | Voedingslijn naar de servo's | Schakelbaar via BCM6 (`servo_rail_set`). "Rail aan" = stroom op servo's; "rail uit" = vrij draaibaar, geen verbruik. Policy: in `CONFIG` operator-controlled, in `MISSION`/`TEST` automatisch bepaald door flight-state. |
| **CSI** | Camera Serial Interface | Lint-connector voor de Pi Camera Module. |
| **OV2311** | OmniVision OV2311 sensor | Mono **global-shutter** beeldsensor in de [Arducam B0381 PiVariety](https://www.arducam.com/) module (NoIR variant). 1600×1300 active array, 3,0 µm pixel pitch. Gekozen voor de 2026-missie omdat global shutter geen rolling-shutter "jelly" geeft tijdens descent, en mono vermijdt een demosaic-stap in de AprilTag-pijplijn. Vereist een PiVariety-tuning-file in `libcamera`; zie [`camera.md`](camera.md#libcamera-tuning-file-ontbreekt). |
| **I²C / SPI** | Bus-protocollen | I²C voor BME280/BNO055 (twee draden), SPI voor RFM69 (vier draden). |
| **GPIO / BCM** | General-Purpose I/O / Broadcom-pinnummering | Zie [`rpi_pinning.md`](rpi_pinning.md). BCM-nummering is wat Python-libraries gebruiken; fysieke pin-nummers zijn de "1..40" van de header. |

## Vluchtfases & modi

Volledige uitleg in [`mission_states.md`](mission_states.md) en
[`mission_triggers.md`](mission_triggers.md).

| Term | Betekenis |
|---|---|
| **`CONFIG`** | Pico-modus: opstellen, kalibreren, parameters zetten. Alle commando's mogen. |
| **`MISSION`** | Pico-modus: vlucht-software actief. Zero loopt door zijn substates. |
| **`TEST`** | Pico-modus: dry-run van `DEPLOYED` met een timer (default 10 s). |
| **`PAD_IDLE`** | Zero-substate: wachten op lancering (op het platform of in de raket). |
| **`ASCENT`** | Zero-substate: opstijgen — boost gedetecteerd. |
| **`DEPLOYED`** | Zero-substate: parachute uit, descent met camera + AprilTag-detectie. |
| **`LANDED`** | Zero-substate: stilstand gedetecteerd na impact. |

## Radio-protocol & telemetrie

Wire-protocol-details: [`pico_files/Orginele cansat/RadioReceiver/README_basestation.md`](../pico_files/Orginele%20cansat/RadioReceiver/README_basestation.md)
en [`src/cansat_hw/radio/wire_protocol.py`](../src/cansat_hw/radio/wire_protocol.py).

| Term | Betekenis | Toelichting |
|---|---|---|
| **TLM** | Telemetry record | 60-byte binary frame met sensormeetwaarden + tijdstempel + mode/state. **TLM-push-cadans over de radio**: 1 Hz in `MISSION` (`--mission-tlm-interval`) én `TEST`. Intern draait de [sensor-sampler](#) ~5 Hz in `MISSION`/`TEST` zodat korte IMU-pieken tussen twee TLM-frames niet gemist worden voor de state-machine. Zie [mission_states.md](mission_states.md#tlm-cadans-binary-frames). |
| **sensor-sampler** | `SensorSampler` in `cansat_hw.sensors.sampler` | Gedeelde pull-based sampler die per tick één BME280- + één BNO055-read doet en rolling-window afgeleiden bijhoudt: `peak_accel_g`, `freefall_for_s`, `alt_stable_for_s`. Input voor de [multi-trigger evaluatie](mission_triggers.md). |
| **EVT** | Event record | Tekstuele gebeurtenis-melding zoals `EVT STATE LANDED IMPACT` of `EVT MODE CONFIG END_TEST`. Wordt **direct** verstuurd, niet op de TLM-cadans. |
| **HDR / HEADER** | Header record | Eén keer per log-bestand vooraan: versie, mode, frame-grootte, hostname, UTC-starttijd. Type-byte `0xF0`. Gaat **niet** over de radio, alleen in `.bin`-files. |
| **CRC** | Cyclic Redundancy Check | Checksum op elk record. `bad-CRC` in een decode-summary betekent een corrupt frame in het log-bestand (bv. SD-kaart-glitch, niet packetloss op de radio). |
| **seq** | Sequence counter | Oplopend nummer per TLM-frame (16-bit, wrapt op 0xFFFF). Gat = packetloss (per-mission file) of sessie-reset (continuous file). |
| **`mode_state`** | 1-byte combinatie | Hoge nibble = mode (CONFIG/MISSION/TEST), lage nibble = flight-state (PAD_IDLE/ASCENT/...). |
| **PING / OK / ERR** | Wire-commando-replies | Pico stuurt `PING`, Zero antwoordt `OK PING`. Algemene patroon: `OK <cmd> [args]` of `ERR <reden>`. |
| **`SERVO ...`** | Wire-commando-familie | Servo-tuning + park/stow/home vanaf het base station. Calibratie (sub-state binnen `CONFIG`): `SERVO START/SEL/STEP/SET/MIN/CENTER/MAX/STOW_MARK/SAVE/STOP`. Rail-bediening: `SERVO ENABLE/DISABLE/STOW/PARK/HOME`. Read-only: `SERVO STATUS` (ook in `MISSION`/`TEST`; reset tijdens tuning de watchdog). Replies = `OK SVO …` / `ERR SVO …`. Zie [planning Fase 12](planning.md#fase-12--servo-tuning--parkstow-via-radio-). |
| **PREFLIGHT** | Pre-launch check | Zero controleert `TIME`/`GND`/`BME`/`IMU`/`DSK`/`LOG`/`FRQ`/`GIM` voor mode-wissel. Falen → `ERR PRE <welke>`. |
| **`STOP RADIO`** | Wire-commando | Stopt de service op de Zero. Exit-code 0 → systemd herstart **niet** (`Restart=on-failure`). |

## Sensor-output & calibratie

| Term | Betekenis | Toelichting |
|---|---|---|
| **`alt_m`** | Altitude (meter) | Berekend uit BME280-druk + ground-druk (`ground_hpa`) via ISA-barometrische formule. |
| **`pressure_hpa`** | Druk in hectopascal | 1 hPa = 1 mbar = 100 Pa. Zeeniveau ≈ 1013,25 hPa. |
| **ISA** | International Standard Atmosphere | Standaard atmosfeer-model dat we gebruiken om druk → hoogte om te zetten. |
| **IIR** | Infinite Impulse Response filter | Hardware-filter in BME280. Hoger = gladder maar trager. CONFIG: ×4, MISSION/TEST: ×16. Instelbaar met `SET IIR`. |
| **OS / oversampling** | Multi-sample averaging | BME280 gemiddelde over N reads voor lagere ruis. |
| **CAL GROUND** | Ground-zero calibratie | Zero meet huidige druk → wordt `ground_hpa` → `alt_m=0` op die plek. |
| **`sys_cal` / `gyro_cal` / `accel_cal` / `mag_cal`** | BNO055-calibratiestatus | 0..3 per subsysteem. 3 = volledig gekalibreerd. `sys_cal=3` vereist een goed-gekalibreerd `mag_cal` (figure-of-eight beweging) en `accel_cal` (6 oriëntaties). |
| **`heading` / `roll` / `pitch`** | Euler-hoeken (graden) | BNO055 sensor-fusion output. `heading` = kompas (0..360°, NoO=0). |
| **`ax_g` / `ay_g` / `az_g`** | Lineaire versnelling in g | BNO055 met zwaartekracht eruit gerekend. 1 g ≈ 9,81 m/s². Bereik ±32 g (clipt op 32.767 g — int16-limit). |
| **‖a‖** | Vector-magnitude `√(ax²+ay²+az²)` | Totale versnelling, oriëntatie-onafhankelijk. Gebruikt voor SHOCK/IMPACT-triggers. |

## Trigger-redenen (state-overgangen)

Zie [`mission_triggers.md`](mission_triggers.md) voor drempelwaarden.

| Reason | Wanneer |
|---|---|
| **`ACC`** | Aanhoudende versnelling > drempel — primaire `ASCENT`-trigger. |
| **`ALT`** | Hoogte boven/onder drempel — backup-trigger voor alle overgangen. |
| **`FREEFALL`** | ‖a‖ ≈ 0 g voor X seconden — `DEPLOYED`-trigger (apex bereikt). |
| **`SHOCK`** | Korte piek in ‖a‖ — backup `DEPLOYED`-trigger (parachute opent). |
| **`DESCENT`** | Hoogte daalt onder apex − marge — backup `DEPLOYED`-trigger. |
| **`IMPACT`** | Grote piek in ‖a‖ — primaire `LANDED`-trigger. |
| **`STABLE`** | Stilstand voor X seconden — backup `LANDED`-trigger. |

## Software & deployment

| Term | Betekenis |
|---|---|
| **systemd** | Linux service-manager. `cansat-radio-protocol.service` op de Zero. Zie [`cansat_radio_service.md`](cansat_radio_service.md). |
| **`journalctl`** | Linux-tool om systemd-logs te lezen. `-u <service>` filtert op service. |
| **`rsync`** | Bestand-sync tool. Bij ons altijd via [`scripts/sync_to_zero.sh`](../scripts/sync_to_zero.sh) — nooit direct, want `--delete` zonder excludes wist de venv. |
| **venv / `.venv/`** | Python virtual environment. Zero-only: nooit syncen, nooit committen. |
| **AES** | Advanced Encryption Standard | Symmetrische versleuteling van radio-payloads. Sleutel in `secrets.py` (Pico) en `.env` (Zero) — moet identiek zijn aan beide kanten. Zie [`secrets.md`](secrets.md). |
| **JSONL** | JSON Lines | Eén JSON-object per regel. Gebruikt door de Pico-CLI als `!log on` aanstaat. |
| **CLI** | Command-Line Interface | Bij ons: de interactieve `BS>` prompt op de Pico (`basestation_cli.py`). |

## Tijd & getalformaten

| Term | Betekenis |
|---|---|
| **UTC** | Coordinated Universal Time | "Wereldtijd" zonder zomertijd. Alle log-timestamps zijn in UTC; lokale tijd in België = UTC+1 (winter) of UTC+2 (zomer). |
| **Unix epoch** | Seconden sinds 1970-01-01 00:00:00 UTC | TLM gebruikt 4 bytes seconden + 2 bytes ms voor de absolute timestamp. |
| **LSB / MSB** | Least/Most Significant Byte | Bij multi-byte velden in het wire-formaat. Wij gebruiken little-endian (LSB eerst) tenzij anders vermeld. |
| **g** | Standaard zwaartekrachtversnelling | 1 g = 9,80665 m/s². Versnellingen rapporteren we **in g**, niet in m/s². |
| **hPa** | Hectopascal | Druk-eenheid; 1 hPa = 100 Pa = 1 mbar. |
