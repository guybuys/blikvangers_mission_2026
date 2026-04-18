# Documentatie — CanSat 2026

Uitgebreidere notities en hardware-informatie. De **projectintro en installatie** staan in de [README op repository-niveau](../README.md).

## Hardware & pinning

- **[Raspberry Pi Zero 2 W — pinning & hardware](rpi_pinning.md)**  
  Fysieke pin nummers vs BCM/GPIO, voeding, I2C (BME280, BNO055), SPI (RFM69HCW), servo’s (o.a. **pigpio**), **reservebord-checklist** (`config.txt`, groepen, `/dev/i2c-*` / `spidev`), optioneel bij te solderen pinnen, tweede I2C-bus.

## Radio — base station (Pico) & commando-protocol

- **[Pico base station CLI + draad-protocol](../../pico_files/Orginele%20cansat/RadioReceiver/README_basestation.md)**  
  Thonny, lokale `!`-commando’s, tekstregels naar de CanSat.
- **CanSat (Zero 2 W):** `python scripts/cansat_radio_protocol.py` vanuit de repo-root (zie [project-README](../README.md)).
- **[Radio-service (systemd) — bedienings-spiekbriefje](cansat_radio_service.md)** — start/stop, log volgen, herstarten, `STOP RADIO` vs `systemctl`, valkuilen bij autostart op de Zero.
- **BME280 / BNO055 (I²C):** `python scripts/bme280_test.py` / `python scripts/bno055_test.py` (`pip install smbus2` of `pip install -e ".[sensors]"`). Over de radio in CONFIG: `BME280` / `BNO055` (zie base station README).
- **Gimbal (pigpio + calibratie-JSON):** [`scripts/gimbal/`](../scripts/gimbal/README.md) — calibratie in `config/gimbal/`; niveauregeling o.a. `scripts/gimbal_level.py` (BNO055 via smbus2).
- **Camera (Picamera2 + AprilTag + logging):** [`scripts/camera/`](../scripts/camera/README.md) — `descent_telemetry.py`, `focus_preview.py`; demo-referentie in `zero_files/camera_project/`.

## Missie & vluchtstates (concept)

- **[Missie-states — uitleg voor leerlingen](mission_states.md)**  
  Twee lagen (Pico `CONFIG` / `MISSION` vs Zero-substates `PAD_IDLE`, `ASCENT`, `DEPLOYED`, `LANDED`), Nederlandse uitleg bij de Engelse namen, overgangen, frequentie persistentie, WiFi-kanttekening, link met `wire_protocol.py`.
- **[Mission triggers — drempelwaarden voor de overgangen](mission_triggers.md)**  
  Wat detecteert `ASCENT` / `DEPLOY` / `LAND` precies, welke sensor, wat zijn zinvolle waarden en hoe stel je ze in vanaf de Pico (`SET TRIGGER …`, `GET TRIGGERS`, `PREFLIGHT`).
- **[Geheimen & lokale configuratie](secrets.md)**  
  `.env` op de Zero, `secrets.py` op de Pico, `.env.example` / `secrets.example.py` als template. Wat is écht geheim en hoe hou je Pico en Zero op dezelfde AES-sleutel.

## Later uit te breiden

Hier kunnen o.a. komen:

- Radioprotocol op de CanSat / Zero (config vs mission), parameterbestanden — zie ook [mission_states.md](mission_states.md)  
- Verdere sensor-integratie (BNO055)  
- Camera (CSI): basisscripts in [`scripts/camera/`](../scripts/camera/README.md); diepere integratie in flight-loop  
- Gimbal-servo’s  
- Stroomverbruik en testprocedures  

Pull requests en issues via GitHub zoals gebruikelijk voor jullie team.
