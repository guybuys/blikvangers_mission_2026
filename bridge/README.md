# Bridge — laptop-kant (voorbereiding)

Deze map is **voorbereiding** voor een laptop-script dat de Pico-basestation aan
MQTT koppelt. Nog niet geïmplementeerd; alleen de configuratie staat al klaar
zodat de credentials ergens veilig zitten terwijl we verder werken aan de
software op de Pico en de Zero.

## Doel (toekomstig)

Op de laptop die met de Pico verbonden is:

- `basestation_bridge.py` (nog te schrijven) opent de USB-serial poort naar de
  Pico met `pyserial`.
- Alles wat de Pico uitstuurt (TX/RX-regels, sensor-antwoorden, mode-overgangen)
  wordt geparsed en gepubliceerd via MQTT op onderwerpen zoals
  `cansat/state`, `cansat/alt`, `cansat/bme280`, `cansat/radio`, `cansat/raw`.
- De **display-laptop** subscribet daarop en toont de missie live.
- Optioneel: een `--replay <jsonl>` modus om een oude Pico-log af te spelen.

Thonny en de bridge kunnen **niet tegelijk** dezelfde USB-poort openen — voor
veldgebruik sluit je Thonny af (of zet je de CLI als `main.py` op de Pico).

## Tweewegcommunicatie over USB

Python op de laptop kan de Pico aansturen via USB-CDC. Kleine sanity-test
(alvorens het echte bridge-script bestaat):

```python
import serial
ser = serial.Serial("/dev/tty.usbmodem…", 115200, timeout=0.1)
ser.write(b"PING\n")
while True:
    line = ser.readline().decode("utf-8", "replace")
    if line:
        print(line, end="")
```

Belangrijk: elke commando-regel moet eindigen op `\n`, anders blijft
`input("BS> ")` in de Pico wachten.

## Credentials hier, niet in de root

- `bridge/.env` — jouw echte MQTT-creds (rechten `600`; staat in `.gitignore`).
- `bridge/.env.example` — template om te committen.

De root-`.env` blijft puur voor de **radio** (RFM69-sleutel + node-IDs +
frequentie). Zo komen MQTT-gegevens niet per ongeluk op de Zero terecht — die
draait sowieso offline.

## Dependencies (pas installeren als we de bridge bouwen)

```bash
pip install pyserial paho-mqtt
```

Later voegen we een `[bridge]` extra toe aan `pyproject.toml` zodat het samen
met de rest van de repo beheerd wordt.

## Plaats in het project

| Onderdeel | Locatie | Gebruikt |
|-----------|---------|----------|
| CanSat firmware | `scripts/cansat_radio_protocol.py`, `src/cansat_hw/` | Zero 2 W |
| Base station CLI | `pico_files/Orginele cansat/RadioReceiver/basestation_cli.py` | Pico (MicroPython, Thonny) |
| **Bridge (laptop)** | **`bridge/`** | Laptop die de Pico via USB aanstuurt |
| Display (laptop #2) | extern (niet in deze repo) | MQTT-subscriber, dashboard |
