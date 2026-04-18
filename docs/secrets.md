# Geheimen & lokale configuratie

Sommige waarden horen **niet in git**: de belangrijkste is de **16-byte AES-sleutel**
die de RFM69 gebruikt om radioverkeer te versleutelen. We volgen de gangbare
Python-conventies — op twee kanten een ander bestand omdat de Pico in MicroPython
geen `.env` heeft.

| Kant                   | Bestand                                                               | Gecommit?           |
|------------------------|------------------------------------------------------------------------|---------------------|
| Zero 2 W (CPython)     | `.env` in repo-root (radio)                                            | **nee**             |
| idem — template        | `.env.example`                                                         | ja                  |
| Pico (MicroPython)     | `secrets.py` naast `basestation_cli.py` (op de Pico-flash)             | **nee**             |
| idem — template        | `secrets.example.py` (zelfde map)                                      | ja                  |
| Laptop-bridge (MQTT)   | `bridge/.env`                                                          | **nee**             |
| idem — template        | `bridge/.env.example`                                                  | ja                  |

Alle "echte" bestanden zijn in `.gitignore` uitgesloten; de `*.example`-varianten
zijn dat uitdrukkelijk níét, zodat het team ziet wat er in moet.

## Wat is écht geheim?

Twee dingen:

1. **AES-sleutel** voor de radio (`CANSAT_RADIO_KEY` in root-`.env` /
   `RADIO_KEY` in `secrets.py`). Wie die heeft kan jullie radioverkeer
   meeluisteren of vervalsen. Relevant voor Pico én Zero.
2. **MQTT-wachtwoord** voor de laptop-bridge die telemetrie naar een display
   pusht (`CANSAT_MQTT_PASS` in `bridge/.env`). De Zero draait offline en heeft
   deze niet nodig — het staat alleen op de laptop waar de Pico op zit.

Node-IDs, radiofrequentie, MQTT-broker/port en topic-namen zijn *configuratie*,
geen geheim. We nemen ze wel mee in dezelfde files om de lokale setup op één
plek te hebben.

## Zero 2 W — `.env` aanmaken

```bash
cd ~/cansat_mission_2026
cp .env.example .env
nano .env            # zet CANSAT_RADIO_KEY op een eigen 16-byte waarde
```

`scripts/cansat_radio_protocol.py` leest het bestand automatisch bij opstart
(het zit in een kleine loader boven in het script — géén externe dependency).
De CLI-argumenten winnen nog altijd over `.env`; `.env` wint over de
ingebouwde demo-defaults. Bij de demo-key (publiek bekend) drukt het script
een `WARN` naar stderr.

Het **systemd-unit** (`deploy/systemd/cansat-radio-protocol.service`) start het
script vanuit de repo-root, dus `.env` daar volstaat. Wél belangrijk: de service
draait als `root`. Zet `.env` op rechten `600` (`chmod 600 .env`) zodat andere
gebruikers hem niet kunnen lezen.

Na het aanpassen van `.env`: `sudo systemctl restart cansat-radio-protocol`.

## Pico — `secrets.py` aanmaken

Open Thonny, plaats `secrets.example.py` als `secrets.py` op de Pico
(Save copy → This Computer/MicroPython device, zelfde map als
`basestation_cli.py`), en pas `RADIO_KEY` aan. Bij reboot meldt `basestation_cli.py`
welke waarden uit `secrets.py` geladen zijn; zonder `secrets.py` valt hij terug
op de demo-key en print een `WARN`.

## Key-synchronisatie tussen Pico en Zero

De waarde van `RADIO_KEY` op de Pico **moet byte-voor-byte gelijk** zijn aan
`CANSAT_RADIO_KEY` op de Zero. Anders krijgt de ontvanger enkel onleesbare
versleutelde pakketten — je ziet op de Pico-CLI *geen antwoord binnen X s*
terwijl de radio zelf prima werkt.

Tip: precies 16 ASCII-tekens typen (géén emoji / ü / ß — die zijn multi-byte in
UTF-8 en dan klopt de lengte niet meer).

## Voorbeeld: eigen missie-sleutel kiezen

```bash
python3 - <<'PY'
import secrets; print(secrets.token_urlsafe(12)[:16])
PY
```

Zet die ene regel als waarde op beide kanten. Werkt de radio na restart nog?
Dan heb je je eigen privé-kanaal. Werkt het niet: Pico en Zero staan niet op
dezelfde sleutel — herbekijk lengte en typefouten.

## MQTT-configuratie (laptop-side)

De laptop die met de Pico verbonden is kan telemetrie via MQTT naar een
display-laptop pushen (toekomstig, zie [`bridge/README.md`](../bridge/README.md)).
Creds staan in **`bridge/.env`** — niet in de root-`.env`, want de Zero heeft ze
niet nodig:

```bash
cp bridge/.env.example bridge/.env
chmod 600 bridge/.env
nano bridge/.env           # broker + user/pass invullen
```

De legacy-scripts onder `zero_files/camera_project/` (nu niet meer actief
gebruikt — Zero draait offline) lezen dezelfde `CANSAT_MQTT_*`-env-vars. Draai
je ze toch ooit weer: `set -a && . bridge/.env && set +a` vóór het script, of
gebruik systemd `EnvironmentFile=bridge/.env`.

## Andere soorten geheimen

Heb je later WiFi-credentials, API-sleutels (bv. ground station) of SSH-keys?
Leg die volgens hetzelfde patroon neer (`.env` of `secrets.py`), nooit rechtstreeks
in de code. Het globale ignore-pattern `secrets.py` dekt ze op de Pico, `.env*`
op de Zero.
