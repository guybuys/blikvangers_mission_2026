# Documentatie — CanSat 2026

Uitgebreidere notities en hardware-informatie. De **projectintro en installatie** staan in de [README op repository-niveau](../README.md).

## Hardware & pinning

- **[Raspberry Pi Zero 2 W — pinning & hardware](rpi_pinning.md)**  
  Fysieke pin nummers vs BCM/GPIO, voeding, I2C (BME280, BNO055), SPI (RFM69HCW), servo’s (o.a. **pigpio**), **reservebord-checklist** (`config.txt`, groepen, `/dev/i2c-*` / `spidev`), optioneel bij te solderen pinnen, tweede I2C-bus.

## Radio — base station (Pico) & commando-protocol

- **[Pico base station CLI + draad-protocol](../../pico_files/Orginele%20cansat/RadioReceiver/README_basestation.md)**  
  Thonny, lokale `!`-commando’s, tekstregels naar de CanSat.
- **CanSat (Zero 2 W):** `python scripts/cansat_radio_protocol.py` vanuit de repo-root (zie [project-README](../README.md)).

## Later uit te breiden

Hier kunnen o.a. komen:

- Radioprotocol op de CanSat / Zero (config vs launch), parameterbestanden  
- Sensor-integratie (BME280, BNO055)  
- Camera (CSI) en gimbal-servo’s  
- Stroomverbruik en testprocedures  

Pull requests en issues via GitHub zoals gebruikelijk voor jullie team.
