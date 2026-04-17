# Camera — afdaling / landing (Pi Zero, NoIR global shutter)

Doel: **snel foto’s** + **AprilTag** + **sensorlog per frame** (druk/temp/RH, IMU), naar **SD**.  
Radio: apart bestand **`radio_snapshot.json`** met de **beste regel sinds start** (of sinds vorige handmatige reset) — de radio-stack kan dat periodiek uitlezen zonder volle JPEG’s te sturen.

## Afgeleid uit test-CrashPics (bestandsnamen)

Patroon: `photo_YYYYMMDD_HHMMSS_<µs>.jpg`. In een dichte burst lag de gemiddelde stap tussen frames rond **0,15 s** ⇒ **~6–7 fps** (afhankelijk van CPU, SD, en AprilTag).

## NoIR + global shutter: AWB

- **AWB uit** (`AwbEnable`: false) waar libcamera dat toestaat — op mono weinig nut.
- Tijdswinst is meestal **klein** t.o.v. JPEG + AprilTag + I²C; echte winst: **korte exposure** (minder blur), bracketing, eventueel lagere resolutie voor een selectiestap.

## Scripts

| Bestand | Beschrijving |
|---------|----------------|
| `descent_telemetry.py` | Hoofdloop: foto’s + BME280 + BNO055 + AprilTag-subprocess → **CSV** + **`radio_snapshot.json`**. Optioneel **exposure-bracket** (`--bracket-us`). |
| `focus_preview.py` | HDMI (**DRM**): live score (Laplacian), `s` = snapshot, `q` = stop. |
| `apriltag_worker.py` | Subprocess: één pad per stdin-regel → JSON op stdout (zware detectie buiten de hoofdloop). |
| `tag_metrics.py` | Afstand / offset uit tag-hoeken (zelfde idee als demo). |

## Starten (op de Pi, repo-root, venv actief)

```bash
# sensoren + picamera2 + apriltag nodig; zie onder
python scripts/camera/descent_telemetry.py --frames 100 --photo-dir ~/photos --log ~/logs/descent.csv

# Exposure-bracket (microseconden, roteert per foto):
python scripts/camera/descent_telemetry.py --frames 200 --bracket-us 8000,12000,20000 --photo-dir ~/photos --log ~/logs/descent.csv

# Scherpstellen met HDMI:
python scripts/camera/focus_preview.py
```

## Dependencies

- **Systeem:** `python3-picamera2` (apt), I²C aan, gebruiker in `i2c`.
- **Venv:** apt zet Picamera2 in `/usr/lib/python3/.../dist-packages`. De scripts roepen `picamera2_bootstrap.ensure_apt_picamera2_on_path()` aan zodat een **normale venv** het module nog vindt. Alternatief: venv met `python3 -m venv --system-site-packages .venv`.
- **NumPy / OpenCV:** **`numpy<2`** is nodig voor apt-**Picamera2** (``simplejpeg``). **OpenCV 4.12+** vraagt NumPy 2 — daarom pinnt de repo **opencv**-headless **onder 4.12** samen met **numpy onder 2**. Na wijzigingen: ``pip install -e ".[sensors,camera]"``.
- **Repo:** `pip install -e ".[sensors,camera]"` vanuit repo-root (één commando; combineer extras met **komma**, geen tweede `-e ".[…]"`).
- **AprilTag + OpenCV:** o.a. `pip install numpy opencv-python-headless` en **`pupil-apriltag`** (import `apriltag`) — controleer compatibiliteit met jullie Python op de Zero.

## Referentie

Oorspronkelijke MQTT-variant: `zero_files/camera_project/`.
