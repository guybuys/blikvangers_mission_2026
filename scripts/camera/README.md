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
| `descent_telemetry.py` | Hoofdloop: foto’s + BME280 + BNO055 + AprilTag-subprocess → **CSV** + **`radio_snapshot.json`**. Optioneel **exposure-bracket** (`--bracket-us`) en **registry-pinhole** (`--tag-registry`, zie onder). |
| `focus_preview.py` | HDMI (**DRM**): live score (Laplacian), `s` = snapshot, `q` = stop. |
| `apriltag_worker.py` | Subprocess: één pad per stdin-regel → JSON op stdout (zware detectie buiten de hoofdloop). |
| `tag_metrics.py` | Afstand / offset uit tag-hoeken. **Twee modi**: legacy single-size `k` (`compute_metrics_from_corners`) of pinhole + per-tag-grootte (`compute_metrics_pinhole`). Geen numpy-dependency. |

## Starten (op de Pi, repo-root, venv actief)

```bash
# sensoren + picamera2 + apriltag nodig; zie onder
python scripts/camera/descent_telemetry.py --frames 100 --photo-dir ~/photos --log ~/logs/descent.csv

# Exposure-bracket (microseconden, roteert per foto):
python scripts/camera/descent_telemetry.py --frames 200 --bracket-us 8000,12000,20000 --photo-dir ~/photos --log ~/logs/descent.csv

# Pinhole-modus met de mission tag-registry (aanbevolen voor 2026 — corrigeert
# distance per tag-ID, gebruikt dezelfde formule als de live radio-pijplijn):
python scripts/camera/descent_telemetry.py --frames 100 \
    --tag-registry config/camera/tag_registry.json \
    --photo-dir ~/photos --log ~/logs/descent.csv

# Scherpstellen met HDMI:
python scripts/camera/focus_preview.py
```

## Afstandsberekening — twee modi

`tag_metrics.py` ondersteunt twee onafhankelijke afstand-modellen.
`descent_telemetry.py` kiest op basis van of `--tag-registry` is opgegeven.

### Pinhole + tag-registry (aanbevolen, missie-2026)

```bash
python scripts/camera/descent_telemetry.py \
    --tag-registry config/camera/tag_registry.json \
    --photo-dir ~/photos --log ~/logs/descent.csv
```

- **Formule**: `distance_m = focal_length_px × tag_size_m / max_side_px`
  — dezelfde pinhole-formule die de Zero-radio-pijplijn gebruikt
  (`src/cansat_hw/camera/detector.py`). Tests dwingen consistentie af
  tussen offline (`tests/test_tag_metrics.py`) en live (`tests/test_camera_detector.py`).
- **`focal_length_px`** komt uit `lens.focal_length_mm × 1000 /
  sensor.pixel_pitch_um` in de registry. Voor 25 mm + OV2311 = ~8 333 px.
- **`tag_size_m`** komt per detectie uit `registry.size_mm_for(tag_id)
  / 1000`. Onbekende IDs vallen terug op `default_size_mm`.
- **Voordeel**: werkt correct met meerdere tag-groottes door elkaar
  (4,5 m grote tag + vier 1,1 m kleine tags + 0,175 m papier-print).

### Legacy single-size calibratie

```bash
python scripts/camera/descent_telemetry.py \
    --calibration-data "195.0:0.80,118.9:1.30,85.5:1.80,78.4:2.00" \
    --photo-dir ~/photos --log ~/logs/descent.csv
```

- **Formule**: `distance_m = k / max_side_px` met
  `k = mean(pixel_breedte × afstand)` over de meegegeven datapunten.
- Veronderstelt **één vaste tag-grootte** voor alle detecties — ongeschikt
  voor de 2026-missie met meerdere tag-formaten.
- Blijft beschikbaar voor archief-foto's waarvan je geen lens-/sensor-
  specs hebt maar wel een handvol `(pixels, afstand)`-metingen.

## Dependencies

- **Systeem:** `python3-picamera2` (apt), I²C aan, gebruiker in `i2c`.
- **Venv:** apt zet Picamera2 in `/usr/lib/python3/.../dist-packages`. De scripts roepen `picamera2_bootstrap.ensure_apt_picamera2_on_path()` aan zodat een **normale venv** het module nog vindt. Alternatief: venv met `python3 -m venv --system-site-packages .venv`.
- **NumPy / OpenCV:** **`numpy<2`** is nodig voor apt-**Picamera2** (``simplejpeg``). **OpenCV 4.12+** vraagt NumPy 2 — daarom pinnt de repo **opencv**-headless **onder 4.12** samen met **numpy onder 2**. Na wijzigingen: ``pip install -e ".[sensors,camera]"``.
- **Repo:** `pip install -e ".[sensors,camera]"` vanuit repo-root (één commando; combineer extras met **komma**, geen tweede `-e ".[…]"`).
- **AprilTag + OpenCV:** o.a. `pip install numpy opencv-python-headless` en **`pupil-apriltag`** (import `apriltag`) — controleer compatibiliteit met jullie Python op de Zero.

## Referentie

Oorspronkelijke MQTT-variant: `zero_files/camera_project/`.
