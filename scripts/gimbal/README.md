# Gimbal (Pi)

**Venv:** vanaf repo-root met `.venv` actief: `python -m pip install -e ".[gimbal]"` (installeert `pigpio` + `smbus2` in **deze** interpreter). Test: `python -c "import pigpio"`. Gebruik liever `python -m pip` dan losse `pip`, dan sluit pip altijd aan op dezelfde venv-Python. Daarnaast op het systeem: `sudo apt install pigpio` en `sudo systemctl enable --now pigpiod` (of `sudo pigpiod`).

| Bestand | Rol |
|---------|-----|
| `servo_calibration.py` | Interactieve calibratie (**pigpio**). Standaard schrijft/leest `--json` → `config/gimbal/servo_calibration.json`. |
| `gimbal_test.py` | Testregelaar: **CircuitPython BNO055** (Blinka) + pigpio. |
| `../gimbal_level.py` | **cansat_hw BNO055** (smbus2). Standaard regelt **gx→0, gy→0** (`--level-target zero`); `--level-target warmup` = oude gx0/gy0 uit warm-up. P+I: defaults `--kx`/`--ky`=200, `--kix`/`--kiy`=20 (`--kix 0`/`--kiy 0` = geen I); `--integral-max`, `--max-us-step` bij grote gains. Settle + `--loop-max-dg` zie script-help. |

**Calibratie-JSON:** repository-pad `config/gimbal/servo_calibration.json`
(`gpio` + `min_us` / `center_us` / `max_us` / `stow_us` per motor).
Overschrijf met `--cal` / `--json` naar een eigen pad indien gewenst.

> **`stow_us`** (Fase 12) = de "ingeklapte" / veilige park-positie. Wordt
> gebruikt door `SERVO PARK` over de radio en door de autonome rail-policy
> bij `CONFIG → MISSION` en `DEPLOYED → LANDED`. Calibreer via
> `!servo` op de Pico (letter `w` markeert de huidige us als stow) of in
> de SSH-REPL hieronder (idem-sleutels worden uitgebreid).

**Enable:** standaard BCM **6** (servo-rail); `--enable-pin 0` schakelt uit.

Zie `docs/rpi_pinning.md` voor pinout.

**Verkeerde as na herbekabeling:** `--swap-gpio` (alleen BCM omgewisseld) of `--swap-control-axes` (gx/gy naar andere motor); zie `gimbal_test.py` / `gimbal_level.py` argparse-help.

## Twee gelijkwaardige tooling-paden

| Pad | Wanneer? | Hoe |
|---|---|---|
| **SSH** (lokaal op de Zero) | Eerste calibratie, of als de radio nog niet werkt. | `python scripts/gimbal/servo_calibration.py` — letters `1/2/a/d/A/D/z/x/c/o/p/s/q`. |
| **Radio** (Fase 12) | Op het terrein, geen laptop bij de Zero. | Op de Pico: `!servo` opent een sub-REPL met dezelfde letters; `!park` doet de volle stow-sequence in één commando. Zie [`docs/planning.md`](../../docs/planning.md#fase-12--servo-tuning--parkstow-via-radio-) voor de volledige `SERVO …`-commando-familie en de autonome rail-policy. |

Beide paden schrijven naar dezelfde `config/gimbal/servo_calibration.json`
en laden hem op dezelfde manier (incl. `stow_us`).
