# CanSat mission 2026 — flight software

Software en hardware-documentatie voor de **CanSat** (**Raspberry Pi Zero 2 W**) en voor het **base station** op de **Raspberry Pi Pico** (grondstation / Thonny).

**Naamgeving:** we spreken van **CanSat** of **Zero (2 W)** voor het flight computer-board, en **base station** of **Pico** voor het grondstation — niet van “de Pi” als dat beide boards zou kunnen bedoelen.

## Repository-layout

| Pad | Inhoud |
|-----|--------|
| [`src/cansat_hw/`](src/cansat_hw/) | Python-packages per onderdeel: `radio`, `sensors`, `camera`, `servos` |
| [`scripts/`](scripts/) | Handige scripts (o.a. radio smoke-test) |
| [`docs/`](docs/) | Uitgebreidere documentatie — [overzicht](docs/README.md) |
| [`pico_files/`](pico_files/) | MicroPython / Pico-referentie (MCHobby CanSat RFM69-voorbeelden) |

## Vereisten (CanSat — Zero 2 W)

- **Python** ≥ 3.9  
- **SPI** ingeschakeld (`raspi-config` of `dtparam=spi=on` in `/boot/firmware/config.txt`)  
- Gebruiker in groepen **`spi`** en **`gpio`** (na `usermod`: opnieuw inloggen)

## Snelstart op de CanSat (Zero 2 W)

```bash
cd cansat_mission_2026
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
# Aanbevolen op de Zero (minder gpiozero-warnings):
pip install -e ".[rpi]"
# Gimbal / BNO055 over I²C + pigpio in dezelfde venv (gebruik altijd dezelfde Python als voor scripts):
python -m pip install -e ".[gimbal]"
python -c "import pigpio; print('pigpio ok')"
```

**Opnieuw dezelfde venv gebruiken** (geen `python3 -m venv` meer — die maakt een lege omgeving zonder `pigpio` / `smbus2`):

```bash
source .venv/bin/activate
cd cansat_mission_2026
```

Alleen als je de map **`.venv` verwijdert** of op een **nieuwe machine** begint: opnieuw `python3 -m venv .venv`, dan `source .venv/bin/activate` en **opnieuw** `pip install -e .` en de extras die je nodig hebt (`.[rpi]`, `.[gimbal]`, `.[sensors]`, …).

Radio-test (NSS op **SPI CE0**, reset standaard **BCM 25** — zie documentatie):

```bash
python scripts/radio_rfm69_test.py --version-only
python scripts/radio_rfm69_test.py --listen
python scripts/radio_rfm69_test.py --send "hello"
```

Meer opties: `python scripts/radio_rfm69_test.py --help`

**Radio commando-protocol (CanSat = Zero 2 W via SSH; base station = Pico via Thonny):** na `pip install -e .` op de **Zero**:

```bash
source .venv/bin/activate
python scripts/cansat_radio_protocol.py --verbose
```

Zelfde tekstregels als in `pico_files/.../RadioReceiver/README_basestation.md`. Stop met **Ctrl+C** of het draad-commando **`STOP RADIO`** (antwoord `OK STOP RADIO`; daarna stopt het proces).

**Tijd over de radio (alleen CONFIG):** `SET TIME <unix_epoch>` zet de systeemklok op de Zero (Unix-seconden; meestal root of systemd `User=root`, zie unit hieronder). Op de Pico: `!time` (Pico-klok — sync via Thonny indien nodig) of `!timeepoch <unix>` met `date +%s` vanaf de laptop. Zet op de Zero eenmalig **`timedatectl set-timezone Europe/Brussels`** als je overal lokale tijd wilt (bestandsnamen foto/video).

**Autostart (geen SSH om de listener te starten):** voorbeeld-unit [`deploy/systemd/cansat-radio-protocol.service`](deploy/systemd/cansat-radio-protocol.service) — `WorkingDirectory` en `ExecStart` naar jullie pad/user aanpassen, daarna `sudo systemctl enable --now cansat-radio-protocol`. Stoppen: **`STOP RADIO`** over RF, of `sudo systemctl stop cansat-radio-protocol`, of via SSH het proces killen.

**Camera (afdaling / AprilTag):** [`scripts/camera/README.md`](scripts/camera/README.md) — o.a. `pip install -e ".[camera]"` of meerdere extras **in één haakje**: `pip install -e ".[sensors,camera]"` (niet twee keer `-e ".[…]"` achter elkaar — dan klaagt pip over “conflicting dependencies”). De extra pinnt **`numpy<2`** (Picamera2 + apt-`simplejpeg`); daarnaast: `python3-picamera2` (apt) + `pupil-apriltag` op de Zero.

Files en folders kopiëren naar de raspberry pi zero 2 w

```bash
rsync -avz --exclude '.venv' --exclude '__pycache__' --exclude '*.egg-info' \
  /Users/guybuys/dev/python-projects/cansat_mission_2026/ \
  icw@RPITSM0:~/cansat_mission_2026/
```

## Documentatie

- **[Documentatie-index](docs/README.md)** — pinning, uitbreidingen, enz.  
- **[GPIO- en radio-pinning (CanSat / Zero 2 W)](docs/rpi_pinning.md)** — fysieke pins, SPI/I2C, RFM69, servo’s

## Licentie / bronnen

De Pico-`rfm69`-driver in `pico_files/` is gebaseerd op MCHobby / Adafruit / LowPowerLab-stijl code; zie commentaar in die bestanden voor licenties en links.
