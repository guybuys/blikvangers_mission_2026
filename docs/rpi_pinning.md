# Raspberry Pi Zero 2 W — pinning & hardware (CanSat)

[← Documentatie-index](README.md) · [← Project README](../README.md)

> **Afkortingen** (eerste gebruik; volledige lijst in
> [glossary.md](glossary.md)):
> **BCM** = Broadcom-pinnummering voor GPIO (bv. `GPIO2`); niet gelijk
> aan het fysieke headernummer (BOARD).
> **BOARD** = fysieke pinpositie 1–40 op de 40-pins header.
> **GPIO** = General-Purpose I/O — digitale pin die als input of output
> geconfigureerd kan worden.
> **I2C** = Inter-Integrated Circuit, 2-draads bus (SDA + SCL) voor
> sensoren.
> **SPI** = Serial Peripheral Interface, 4-draads hoge-snelheidsbus
> (MOSI/MISO/SCLK/CS) voor bv. de RFM69.
> **UART** = seriële poort (TX/RX); op de Pi hier niet actief gebruikt.

Dit document beschrijft hoe de **fysieke header-pinnen** (1–40 op de connector) zich verhouden tot **BCM/GPIO-nummers** (zoals in de meeste libraries en `config.txt`), en welke verbindingen voor dit project bedoeld zijn.

## Fysiek beschikbare pinnen op de carrier

Op het bord zijn enkel deze **fysieke pinposities** uitgebracht: **1, 2, 3, 4, 5, 6, 15, 16, 19, 20, 21, 22, 23, 24, 31, 32, 33, 34**. Optioneel kunnen **extra pinnen** worden bijgesoldeerd — zie verderop.

- **Pinnummer (BOARD)** = het volgnummer op de 40-pins GPIO-header (tel vanaf pin 1 bij de SD-kaart, zie officiële pinout-diagrammen).
- **GPIO / BCM** = het Broadcom-pinnummer dat je in code ziet (`GPIO2`, `2`, `board.D2` in CircuitPython, enz.). Dit is **niet** hetzelfde als het fysieke pinnummer.

| Fysieke pin (BOARD) | BCM / GPIO | Hoofdfunctie (standaard) |
|--------------------:|-----------:|--------------------------|
| 1 | — | 3V3 voeding |
| 2 | — | 5V voeding |
| 3 | **GPIO2** | I2C1 **SDA** |
| 4 | — | 5V voeding |
| 5 | **GPIO3** | I2C1 **SCL** |
| 6 | — | GND |
| 15 | **GPIO22** | GPIO algemeen |
| 16 | **GPIO23** | GPIO algemeen |
| 19 | **GPIO10** | SPI0 **MOSI** |
| 20 | — | GND |
| 21 | **GPIO9** | SPI0 **MISO** |
| 22 | **GPIO25** | GPIO algemeen |
| 23 | **GPIO11** | SPI0 **SCLK** |
| 24 | **GPIO8** | SPI0 **CE0** (chip select / nSS) |
| 31 | **GPIO6** | GPIO algemeen |
| 32 | **GPIO12** | GPIO / **PWM0** (hardware PWM mogelijk) |
| 33 | **GPIO13** | GPIO / **PWM1** (hardware PWM mogelijk) |
| 34 | — | GND |

---

## Voeding tussen regulator/radio-bord en Pi

Het radio/regulator-bord levert **5 V** naar de Pi; de Pi levert **3V3** terug naar het radio-gedeelte. Gebruik **meerdere GND**-verbindingen (common ground) zoals voorzien.

| Signaal | Fysieke pin(s) | BCM | Opmerking |
|---------|----------------|-----|-----------|
| **5 V in** (van jullie buck naar Pi) | **2** en/of **4** | — | Typisch beide 5V-pinnen zijn intern verbonden; één draad volstaat vaak, tweede voor stroom/impedantie. |
| **GND** | **6**, **20**, **34** | — | Verdeel GND over minstens twee punten indien mogelijk (minder ground bounce). |
| **3V3 uit** (Pi → radio) | **1** en/of **17** | — | Zie ook “3V3 en GND voor sensoren” — **pin 1** en **pin 17** zijn de **enige twee** 3V3-aansluitingen op de 40-pins header (dezelfde 3V3-rail, gemeenschappelijke stroomlimiet van de Pi-regulator). |

**Let op:** LiPo blijft op het regulator-bord; de Pi krijgt **gereguleerde 5 V**, geen ruwe batterijspanning op de 5V-pinnen.

---

## I2C: BME280 + BNO055

Beide modules hebben **3V3** en **GND** nodig, naast **SDA/SCL**.

### Voeding en massa sensoren

Op de 40-pins header zijn er **precies twee** **3V3**-punten: **pin 1** en **pin 17** (zelfde spanning, één interne 3V3-bron — let op de **maximale stroom** die de Pi Zero 2 W kan leveren voor radio + sensoren + evt. pull-ups). Verdeel de last: bijvoorbeeld **pin 1** → radio, **pin 17** → sensorboom (BME280 + BNO055), of beide sensoren vanaf **17** en radio vanaf **1**, afhankelijk van jullie bedradingsboom.

**GND** voor sensoren: **6**, **20**, en bij uitbreiding **25** (als je die bijsoldeert) — meerdere GND-pinnen verminderen spanningsvallen over lange draad.

### Eén bus (standaard)

Beide sensoren kunnen op **I2C1** (GPIO2/3): zelfde **SDA/SCL**, verschillende **adressen** (BME280 meestal `0x76` of `0x77`, BNO055 standaard **`0x28`** (ADR laag) of **`0x29`** — controleer jumpers). Verificatie: `i2cget -y 1 0x77 0xd0` → **`0x60`** (BME280); `i2cget -y 1 0x28 0x00` → **`0xa0`** (BNO055).

| Signaal | Fysieke pin | BCM | Apparaat |
|---------|-------------|-----|----------|
| **SDA** | **3** | **GPIO2** | BME280 **SDA**, BNO055 **SDA** (parallel) |
| **SCL** | **5** | **GPIO3** | BME280 **SCL**, BNO055 **SCL** (parallel) |
| **3V3** | **1** / **17** | — | Zie tekst hierboven |
| **GND** | **6** / **20** / **34** / **25** | — | Common ground met Pi |

Clock stretching (BNO055) werkt op **I2C1** doorgaans goed (vaak **100 kHz** als je voorzichtig wilt); houd draden kort. Pull-ups: meestal al op de breakoutbordjes — niet dubbel onnodig starke pull-ups parallel zetten.

Zorg dat **I2C1** aan staat: `raspi-config` of `dtparam=i2c_arm=on` in `config.txt`.

### Tweede hardware-I2C (I2C0) — pins 27 & 28

Wil je **twee fysieke I2C-connectoren** (bijv. één sensor per bus), dan is de tweede **hardware**-bus op de Pi **I2C0** op **SDA = pin 27 (GPIO0)**, **SCL = pin 28 (GPIO1)**. Die pinnen moet je **bijsoldeert**.

Activeer de bus met o.a. `dtparam=i2c_vc=on` (naam kan per Pi OS-image iets verschillen; controleer `raspi-config` / documentatie — doel is de **VC/auxiliary**-I2C op GPIO0/1).

**Softwarematig:** twee I2C-bussen zijn **normaal en lichtgewicht**. Je krijgt twee device nodes (bv. `/dev/i2c-1` en `/dev/i2c-0`). In code kies je per sensor **welke bus** (busnummer of device path). Geen inherente “straf” op snelheid of stabiliteit; alleen iets meer configuratie en twee keer initialiseren. Let op **unieke adressen** per bus (zelfde chip op beide bussen met hetzelfde adres is geen probleem; twee keer hetzelfde adres **op dezelfde bus** niet).

**Let op:** pins **27/28** worden soms voor **HAT EEPROM** gebruikt; op een eigen carrier zonder HAT is dat meestal geen issue. Zorg dat geen andere overlay deze pins claimt.

---

## SPI: RFM69HCW-CMS

De module gebruikt **SPI0** met hardware **CE0** als **nSS** (chip select).

**Layout op de connector:** Tussen de SPI-lijnen staat fysiek **GND op pin 20** (naast pin 19 — MOSI). Dat is normaal op de Pi; voor een **kabelboom “alleen signaaldraden na elkaar”** zonder GND ertussen is dat lastig. Oplossing: **GND apart** trekken van pin **20** of **25**, en eventueel **DIO0** naar een **bijgesoldeerde** pin verplaatsen — zie onder.

### Aanbevolen toewijzing (met bijgesoldeerde pin 18)

Als **pin 18** beschikbaar is, is dit een logische set: **RESET** terug op **GPIO25** (pin **22**, naast MISO/SCK/CS) en **DIO0** op **GPIO24** (pin **18**).

| RFM69-signaal | Fysieke pin | BCM | Opmerking |
|---------------|-------------|-----|-----------|
| **nSS / CS** | **24** | **GPIO8** | SPI0 CE0 |
| **SCK** | **23** | **GPIO11** | SPI0 SCLK |
| **MOSI** | **19** | **GPIO10** | SPI0 MOSI |
| **MISO** | **21** | **GPIO9** | SPI0 MISO |
| **RESET** | **22** | **GPIO25** | Vrij GPIO; hoog/laag volgens module |
| **DIO0** | **18** | **GPIO24** | IRQ — in Python: ``RFM69(..., dio0_pin=24)`` of ``--dio0-pin 24`` (hybrid DIO0+SPI). **Zonder fysieke draad DIO0→deze GPIO:** laat ``--dio0-pin`` weg (alleen SPI-poll). |
| **3V3 / GND** | **1** / **6** of **20** / **25** / **34** | — | 3V3-logica; GND dicht bij module |

### Alternatief zonder pin 18 (alleen huidige header)

| RFM69-signaal | Fysieke pin | BCM |
|---------------|-------------|-----|
| **RESET** | **16** | **GPIO23** |
| **DIO0** | **22** | **GPIO25** |

Activeer **SPI** in het OS (`dtparam=spi=on`). Controleer of geen andere overlay dezelfde SPI0-pinnen claimt.

**Belangrijk voor pin 26:** **GPIO7** (pin **26**) is **SPI0 CE1**. Met standaard `spi=on` wordt die pin vaak als **tweede chip select** door de SPI-driver gereserveerd — **gebruik pin 26 dus niet als gewone GPIO** tenzij je device tree aanpast zodat CE1 vrij blijft.

---

## Servo’s en motor-voeding (enable)

Twee servogevers en één **enable** die de **LiPo-/accu-spanning naar de servomotoren** als voedingsrail schakelt (stroom besparen wanneer de servo's niet nodig zijn).

**Pin-groepering:** **GPIO6**, **GPIO12**, **GPIO13** en **GND (pin 34)** zitten fysiek **achter elkaar op de connector** (pins **31–34**): één compact blok voor servo-PWM en enable, met **GND** direct naast de laatste signaalpin.

| Functie | Fysieke pin | BCM | Opmerking |
|---------|-------------|-----|-----------|
| **Servo 1 (PWM)** | **32** | **GPIO12** | Hardware **PWM0** — geschikt voor stabiele servo-aansturing (o.a. `pigpio`, of kernel PWM) |
| **Servo 2 (PWM)** | **33** | **GPIO13** | Hardware **PWM1** |
| **Servo-voeding enable** | **31** | **GPIO6** | Schakelt de **motorspanning** (LiPo via jullie schakeling), niet de Pi-5V. **Active-high**; **pull-down** op de gate zodat bij GPIO-input (na `pigpiod`/script-stop) de gate **laag** blijft → rail uit |
| **GND servo's/driver** | **34** | — | Common ground voor servo-aansturing en motorvoedingspad; gebruik eventueel ook **20** voor retour van hoge servostroom volgens layout |

**Software-mapping:** fysiek blijven **pin 32 = BCM12** en **pin 33 = BCM13** de PWM-lijnen; als de **motoraansluitingen** t.o.v. die lijnen zijn omgewisseld, zet in `config/gimbal/servo_calibration.json` bij `servo1`/`servo2` de **`gpio`**-velden op de BCM die effectief naar de juiste motor gaan (in de repo: logische **servo1 → 13**, **servo2 → 12**). `scripts/gimbal/gimbal_test.py --swap-gpio` of `scripts/gimbal_level.py --swap-gpio` doet hetzelfde tijdelijk zonder JSON te wijzigen.

**Pin 15 (GPIO22)** blijft **vrij** als reserve (extra I/O; bit-bang I2C vergt sowieso twee vrije GPIO’s als je dat ooit nodig hebt).

### Aansturing: pigpio (aanbevolen voor servo’s)

Voor **servo’s** is **pigpio** een goede keuze op de Raspberry Pi: de **`pigpiod`**-daemon gebruikt **DMA-getimede pulsen**, waardoor de pulsbreedte **weinig jitter** heeft vergeleken met “busy loop”-PWM in Python. Dat is belangrijk voor een nette gimbal. GPIO **12** en **13** zijn bovendien geschikt voor hardware-PWM; pigpio kan die als gewone GPIO aansturen met vaste servo-pulsen (`set_servo_pulsewidth`).

- **Installatie (Pi OS):** `sudo apt update && sudo apt install -y pigpio python3-pigpio`
- **Daemon:** `sudo systemctl enable --now pigpiod` (of handmatig `sudo pigpiod` tot je zeker weet dat alles werkt)
- **Python:** verbind met `import pigpio; pi = pigpio.pi()` — daarna `pi.set_servo_pulsewidth(<BCM>, …)` met de BCM-nummers uit `config/gimbal/servo_calibration.json` (pulsewidth typisch **500–2500 µs** voor 1–2 ms servo’s; exact volgens datasheet van jullie motoren)
- **Enable (pin 31 / GPIO6):** `pi.write(6, 1)` = voeding aan, `0` = uit (**active-high**). Hardware **pull-down** op de gate: na loslaten van de pin (input) blijft enable veilig uit.
- **Let op:** zolang **pigpiod** draait, worden die GPIO’s door pigpio beheerd — **niet dezelfde pins tegelijk** met gpiozero/RPi.GPIO voor servo’s gebruiken. De **RFM69** zit op andere pins (SPI) en kan naast pigpio draaien zolang er geen pinconflict is.

---

## Samenvatting — alles op één rij

**Doelconfiguratie** (met **pin 17**, **18**, **25** bijgesoldeerd; **I2C0** optioneel op **27/28**):

| Toepassing | Signaal | Fysieke pin (BOARD) | BCM GPIO |
|------------|---------|---------------------|----------|
| Voeding | 5 V in | 2, 4 | — |
| Voeding | 3V3 (radio / sensoren verdelen) | **1**, **17** | — |
| Voeding | GND | 6, 20, **25**, 34 | — |
| I2C1 sensoren | SDA | 3 | 2 |
| I2C1 sensoren | SCL | 5 | 3 |
| I2C0 (optioneel) | SDA / SCL | **27** / **28** | 0 / 1 |
| RFM69 | nSS | 24 | 8 |
| RFM69 | SCK | 23 | 11 |
| RFM69 | MOSI | 19 | 10 |
| RFM69 | MISO | 21 | 9 |
| RFM69 | RESET | **22** | **25** |
| RFM69 | DIO0 | **18** | **24** |
| Servo’s | Servo 1 | 32 | 12 |
| Servo’s | Servo 2 | 33 | 13 |
| Servo-voeding | LiPo→motoren enable | 31 | 6 |
| — | *(vrij / reserve)* | **15**, **16** | **22**, **23** |

**Zonder pin 18:** zet **DIO0** op **22 (GPIO25)** en **RESET** op **16 (GPIO23)** (zoals in de alternatieve tabel bij SPI).

---

## Code: welk nummer gebruiken?

- **RPi.GPIO:** `GPIO.setmode(GPIO.BOARD)` → fysieke pin (1–40); of `GPIO.BCM` → BCM-kolom hierboven.
- **gpiozero:** standaard **BCM**-nummers.
- **lgpio / RPi.GPIO zero / CircuitPython `board`:** meestal **BCM** of benoemde `board.Dx` die op BCM mappen.

De **camera** via flat cable gebruikt **geen** van deze GPIO-pinnen; dat loopt via de CSI-connector.

---

## Reservebord / OS-configuratie (checklist voor nieuwe SD of nieuw carrier)

Gebruik dit als **stappenplan** wanneer je een **reserve-Pi**, **nieuwe SD-kaart** of **tweede carrier** opzet — dan hoef je niet te gissen welke interfaces waar staan.

### Waar de instellingen staan

Op **Raspberry Pi OS Bookworm** staat de firmware-config meestal in:

- **`/boot/firmware/config.txt`** (soms is **`/boot/config.txt`** een symlink daarnaar)

Bewerken: `sudo nano /boot/firmware/config.txt` — daarna **herstarten** als je overlays of `dtparam` wijzigt.

### I2C1 (sensoren op pin 3 / 5)

**Doel:** device **`/dev/i2c-1`** (SDA = GPIO2, SCL = GPIO3).

1. **raspi-config:** *Interface Options* → **I2C** → **Yes**  
   **of** in `config.txt` (één regel volstaat meestal):
   ```ini
   dtparam=i2c_arm=on
   ```
2. Na reboot controleren:
   ```bash
   ls -l /dev/i2c-1
   sudo apt install -y i2c-tools   # eenmalig
   i2cdetect -y 1                  # zou adressen van BME280 / BNO055 kunnen tonen
   ```
3. **Rechten:** gebruiker in groep **`i2c`** (op veel images automatisch na I2C enable):
   ```bash
   sudo usermod -aG i2c $USER
   ```
   Opnieuw inloggen. Zonder `sudo` mag `i2cdetect` dan `/dev/i2c-1` openen.

### I2C0 (optioneel — pins 27 / 28)

Alleen nodig als je **tweede hardware-I2C** fysiek uitgebracht hebt.

1. In `config.txt` typisch:
   ```ini
   dtparam=i2c_vc=on
   ```
   (Exacte naam kan per image verschillen; op oudere docs staat soms `dtparam=i2c0=on` — controleer `raspi-config` of de officiële Pi-documentatie voor jouw OS-versie.)
2. Na reboot: **`/dev/i2c-0`** (vaak) en `i2cdetect -y 0`.

### SPI0 (RFM69 op CE0 — `/dev/spidev0.0`)

**Doel:** NSS op **GPIO8** (pin 24), MOSI/MISO/SCK op 19/21/23.

1. **raspi-config:** *Interface Options* → **SPI** → **Yes**  
   **of:**
   ```ini
   dtparam=spi=on
   ```
2. Na reboot:
   ```bash
   ls -l /dev/spidev0.0
   ```
3. **Rechten:** gebruiker in groep **`spi`**:
   ```bash
   sudo usermod -aG spi,gpio $USER
   ```
   Opnieuw inloggen. (GPIO voor o.a. **reset** en later **DIO0**; SPI-device voor de radio.)

**Let op:** zet **geen tweede overlay** die **SPI0-pinnen** (8–11, 9, 10, 11) opnieuw definieert (bepaalde displays/HATs). Pin **26 (CE1)** blijft voor de standaard SPI-driver vaak “CE1”; gebruik die **niet** als vrije GPIO tenzij je device tree aanpast.

### GPIO algemeen (reset, DIO0, servo-enable)

- Geen aparte `dtparam` nodig voor “gewone” GPIO — wel lid van groep **`gpio`** (vaak al zo op Raspberry Pi OS).
- **DIO0 + venv:** scripts zetten vaak `GPIOZERO_PIN_FACTORY=rpigpio`; dan kan `RPi.GPIO.add_event_detect` op DIO0 falen. De driver probeert eerst **lgpio** (gpiochip) voor DIO0. In een schone venv: `pip install lgpio` of `pip install -e ".[rpi]"` (zie `pyproject.toml`), of `python3 -m venv --system-site-packages .venv` als `python3-lgpio` systeem-breed geïnstalleerd is.
- **pigpio:** na installatie **`pigpiod`** starten (zie servo-sectie); dat is los van I2C/SPI enable.

### Camera (CSI)

Gebruikt **geen** van de getabeleerde GPIO-pinnen; wel apart inschakelen:

- **raspi-config:** *Interface Options* → **Camera** (of legacy / Picamera2 afhankelijk van image)
- Of volg de huidige Pi-documentatie voor **libcamera** / **Picamera2** op Pi Zero 2 W.

### Snelle verificatie na setup

```bash
groups                    # verwacht o.a. i2c, spi, gpio (na herlogin)
ls /dev/i2c-* /dev/spidev* 2>/dev/null
```

---

## Optioneel bij te solderen: pins 17, 18, 25, 26 (+ 27/28 voor I2C0)

| Pin | BCM | Geschikt voor | Opmerking |
|----:|-----|---------------|-----------|
| **17** | — | **3V3** | Tweede 3V3-punt op de header — handig om **BME280** en **BNO055** apart of symmetrisch te voeden naast radio **3V3** op pin **1**. |
| **18** | **GPIO24** | **DIO0** (RFM69), of algemene GPIO | Vrij te gebruiken als input met interrupt; **aanrader** om **RESET op pin 22 (GPIO25)** te houden en **DIO0 hier** te plaatsen. |
| **25** | — | **GND** | Extra massa naast het SPI-blok — handig voor **radio-GND** en nette retour naast pins **19–24**. |
| **26** | **GPIO7** | **SPI0 CE1** — *niet* als vrije GPIO | Met standaard SPI-aan zet is dit **tweede chip select**; voor RFM69 met alleen **CE0** blijft deze pin praktisch **voor de SPI-driver** gereserveerd. **Kies liever 18 voor extra I/O.** |

**Tweede hardware-I2C:** soldeer **27 (SDA / GPIO0)** en **28 (SCL / GPIO1)** en zet `i2c_vc` (of equivalent) aan — zie sectie I2C.

---

## Als er later nog meer GPIO nodig is

Overige pinnen op een volledige header: **7–14**, **29–30**, **35–40** (UART, PWM, extra I/O) — niet allemaal nodig voor dit ontwerp zolang bovenstaande volstaat.
