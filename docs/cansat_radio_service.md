# CanSat radio-service (systemd) — bedienings-spiekbriefje

[← Documentatie-index](README.md) · [← Project README](../README.md)

De **Zero 2 W** draait `scripts/cansat_radio_protocol.py` als **systemd-service** zodat er **geen SSH** nodig is om het te starten. Unit-bestand: [`deploy/systemd/cansat-radio-protocol.service`](../deploy/systemd/cansat-radio-protocol.service).

Draait als **`User=root`** zodat het draad-commando `SET TIME <unix_epoch>` de systeemklok kan zetten (`clock_settime`). Stop-gedrag: **`STOP RADIO`** (via de Pico) of **`Ctrl+C`** in een interactieve run exit-en met code **0** → systemd herstart **niet** (we gebruiken bewust `Restart=on-failure`). Pas na `sudo systemctl start …` of een **reboot** (service is `enabled`) komt hij weer op.

## Installatie (eenmalig op de Zero)

```bash
cd ~/cansat_mission_2026
sudo timedatectl set-timezone Europe/Brussels
sudo cp deploy/systemd/cansat-radio-protocol.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cansat-radio-protocol.service
```

Na wijzigingen in de **unit-file**: `sudo systemctl daemon-reload && sudo systemctl restart cansat-radio-protocol`.

## Dagelijkse commando's

| Doel | Commando |
|------|----------|
| Nu starten | `sudo systemctl start cansat-radio-protocol` |
| Stoppen (vanuit SSH) | `sudo systemctl stop cansat-radio-protocol` |
| Stoppen (over radio) | Pico: `BS> STOP RADIO` → `OK STOP RADIO` |
| Herstarten (stop + start) | `sudo systemctl restart cansat-radio-protocol` |
| Status | `systemctl status cansat-radio-protocol --no-pager` |
| Live log volgen | `journalctl -u cansat-radio-protocol -f` |
| Laatste 200 regels log | `journalctl -u cansat-radio-protocol -n 200 --no-pager` |
| Log van deze boot | `journalctl -u cansat-radio-protocol -b --no-pager` |
| Niet bij volgende boot opstarten | `sudo systemctl disable cansat-radio-protocol` |
| Weer bij boot opstarten | `sudo systemctl enable cansat-radio-protocol` |
| Unit-file snel aanpassen (dropin) | `sudo systemctl edit cansat-radio-protocol` |

## Quick health-check vanaf de Pico (Thonny → `basestation_cli.py`)

```
BS> PING           # → OK PING
BS> GET MODE       # → OK MODE CONFIG
BS> !gettime       # → OK TIME <epoch> <YYYY-MM-DDTHH:MM:SS±HH:MM>
BS> !time          # stuurt SET TIME (Pico-klok) — alleen in CONFIG
BS> !calground     # BME280-gemiddelde als grondreferentie
BS> SET FREQ 434.0 # Zero antwoordt op oude freq, schakelt dan door en schrijft config/radio_runtime.json; Pico volgt en schrijft radio_freq.json (persistent aan beide kanten)
BS> !preflight     # → OK PRE ALL … of ERR PRE TIME GND BME IMU DSK LOG FRQ GIM
BS> SET MODE MISSION  # pas toegelaten als preflight slaagt
BS> STOP RADIO     # service stopt na OK STOP RADIO
```

Zie [`docs/mission_states.md`](mission_states.md) voor de betekenis van de preflight-codes.

## Valkuilen

- **Twee processen tegelijk:** zolang de service actief is, kun je niet **ook** handmatig `python scripts/cansat_radio_protocol.py` draaien — beide willen de SPI/RFM69 claimen. Eerst `sudo systemctl stop …`.
- **Tijdzone:** zet Europe/Brussels **eenmalig** (`timedatectl set-timezone …`) zodat foto- en videonamen (`datetime.now()` in de camera-scripts) in lokale tijd lopen.
- **Zonder RTC met batterij:** de systeemklok springt bij herstart terug. Na boot dus opnieuw `!time` / `!timeepoch $(date +%s)` vanaf de Pico, of NTP.
- **`Restart=on-failure`:** `STOP RADIO` is "stop tot ik hem weer start". Wil je dat hij zichzelf altijd ook na nette exit herstart, gebruik dan `systemctl edit` met `Restart=always`.
- **Fotomap (`LOG` in preflight):** de service draait als **root**, dus `~/photos` = `/root/photos`. Gebruik daarom altijd een absoluut pad (`--photo-dir /home/icw/photos`), zoals in de meegeleverde unit. `ExecStartPre=mkdir -p` maakt hem automatisch aan.
- **Freq-persistentie:** `config/radio_runtime.json` op de Zero en `radio_freq.json` op de Pico bewaren de laatst toegepaste frequentie. Bij boot laden beide kanten die vanzelf. Als je van radio wisselt of van begin af aan wil, verwijder je die bestanden of voer je opnieuw `SET FREQ` uit.
