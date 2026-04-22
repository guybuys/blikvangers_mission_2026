# Documentatie — CanSat 2026

Uitgebreidere notities en hardware-informatie. De **projectintro en installatie** staan in de [README op repository-niveau](../README.md).

> **Onbekende afkorting tegengekomen?** Kijk eerst in de
> **[woordenlijst & afkortingen](glossary.md)** — TLM, EVT, IMU, IIR,
> `ground_hpa`, trigger-redenen, enz.

## Hardware & pinning

- **[Raspberry Pi Zero 2 W — pinning & hardware](rpi_pinning.md)**  
  Fysieke pin nummers vs BCM/GPIO, voeding, I2C (BME280, BNO055), SPI (RFM69HCW), servo’s (o.a. **pigpio**), **reservebord-checklist** (`config.txt`, groepen, `/dev/i2c-*` / `spidev`), optioneel bij te solderen pinnen, tweede I2C-bus.

## Radio — base station (Pico) & commando-protocol

- **[Pico base station CLI + draad-protocol](../../pico_files/Orginele%20cansat/RadioReceiver/README_basestation.md)**  
  Thonny, lokale `!`-commando’s, tekstregels naar de CanSat. JSONL-log via `!log on` — met [`scripts/pico_jsonl_to_csv.py`](../scripts/pico_jsonl_to_csv.py) kan je die ná een sessie in één stap naar CSV (Excel) converteren.
- **CanSat (Zero 2 W):** `python scripts/cansat_radio_protocol.py` vanuit de repo-root (zie [project-README](../README.md)).
- **[Radio-service (systemd) — bedienings-spiekbriefje](cansat_radio_service.md)** — start/stop, log volgen, herstarten, `STOP RADIO` vs `systemctl`, valkuilen bij autostart op de Zero.
- **[Zero-logs ophalen, archiveren & decoderen](zero_logs.md)** — `scripts/fetch_zero_logs.sh` (rsync + journal + automatische decode), `scripts/decode_logs.py` (summary / CSV / raw), lokale layout `zero_logs/latest/` + `archive/<timestamp>/`, en hoe je snel state-transities, peak-altitude en peak-‖a‖ uit een sessie haalt.
- **Quick-look grafieken (matplotlib)** — `python scripts/plot_test_csv.py zero_logs/latest/decoded/cansat_test_*.csv` tekent alt, ‖a‖, roll/pitch en gyro in één figuur en schrijft `.png` naast de CSV. Handig voor drop-tests en descent-analyse; `--no-show` voor batch, `--out` voor expliciet pad.
- **Foto's ophalen van de Zero** — `scripts/fetch_zero_photos.sh` haalt `/home/icw/photos/*.jpg` naar `zero_photos/latest/` met automatische archivering van de vorige fetch onder `zero_photos/archive/<timestamp>/`. Met `DELETE_REMOTE=1 scripts/fetch_zero_photos.sh` ruim je de Zero mee op (handig tussen test-sessies).
- **BME280 / BNO055 (I²C):** `python scripts/bme280_test.py` / `python scripts/bno055_test.py` (`pip install smbus2` of `pip install -e ".[sensors]"`). Over de radio in CONFIG: `BME280` / `BNO055` (zie base station README).
- **Gimbal (pigpio + calibratie-JSON):** [`scripts/gimbal/`](../scripts/gimbal/README.md) — calibratie in `config/gimbal/`; niveauregeling o.a. `scripts/gimbal_level.py` (BNO055 via smbus2).
- **Camera (Picamera2 + AprilTag + logging):** [`scripts/camera/`](../scripts/camera/README.md) — `descent_telemetry.py`, `focus_preview.py`; demo-referentie in `zero_files/camera_project/`.
- **[Camera + AprilTag-pijplijn in de radio-service (Fase 9)](camera.md)** — `TagBuffer`, `CameraThread`, afstandsformule `d = f_px × size_m / max_side_px`, `config/camera/tag_registry.json`, CLI-flags (`--no-camera`, `--tag-registry`, `--camera-detect-width`, `--camera-fps`, `--camera-resolution`, `--camera-tag-families`) en troubleshooting.

## Missie & vluchtstates (concept)

- **[Missie-states — uitleg voor leerlingen](mission_states.md)**  
  Twee lagen (Pico `CONFIG` / `MISSION` vs Zero-substates `PAD_IDLE`, `ASCENT`, `DEPLOYED`, `LANDED`), Nederlandse uitleg bij de Engelse namen, overgangen, frequentie persistentie, WiFi-kanttekening, link met `wire_protocol.py`.
- **[Mission triggers — drempelwaarden voor de overgangen](mission_triggers.md)**  
  Wat detecteert `ASCENT` / `DEPLOY` / `LAND` precies, welke sensor, wat zijn zinvolle waarden en hoe stel je ze in vanaf de Pico (`SET TRIGGER …`, `GET TRIGGERS`, `PREFLIGHT`).
- **[Missie-checklist (lanceerdag + trigger-spiek)](mission_checklist.md)**  
  Stappen in volgorde, `!log on`, BNO-preflight, tabel met alle instelbare triggers, `PREFLIGHT`-codes, recovery — bedoeld om af te vinken naast de Pico-CLI.
- **[Geheimen & lokale configuratie](secrets.md)**  
  `.env` op de Zero, `secrets.py` op de Pico, `.env.example` / `secrets.example.py` als template. Wat is écht geheim en hoe hou je Pico en Zero op dezelfde AES-sleutel.

## Referentie

- **[Planning & roadmap](planning.md)** — wat is af, wat ligt op de plank, wat staat eerstvolgend op de agenda. **Bij te werken in dezelfde commit als feature-merges**, anders loopt het uit de pas met de werkelijkheid.
- **[Woordenlijst & afkortingen](glossary.md)** — centrale glossary voor TLM/EVT/HDR/CRC, `mode_state`, sensor-termen (BME280/BNO055/IMU/IIR), trigger-redenen, en alle andere jargon die in de losse documenten terugkomt.

## Later uit te breiden

Hier kunnen o.a. komen:

- Radioprotocol op de CanSat / Zero (config vs mission), parameterbestanden — zie ook [mission_states.md](mission_states.md)  
- Verdere sensor-integratie (BNO055)  
- Camera (CSI): basisscripts in [`scripts/camera/`](../scripts/camera/README.md); diepere integratie in flight-loop  
- Gimbal-servo’s  
- Stroomverbruik en testprocedures  

Pull requests en issues via GitHub zoals gebruikelijk voor jullie team.
