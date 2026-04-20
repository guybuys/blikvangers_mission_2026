# Camera + AprilTag-pijplijn (Fase 9)

[← Documentatie-index](README.md) · [← Project README](../README.md)

## Afkortingen

Voor de volledige woordenlijst: [`glossary.md`](glossary.md). Een korte
lijst met de abbreviaties die je in dit document tegenkomt:

- **TLM** — Telemetrie (zie [glossary: TLM](glossary.md#radio--protocol)).
- **IMU** — Inertial Measurement Unit (BNO055 aan boord; hier niet gebruikt
  behalve om flight-states te bepalen).
- **AprilTag** — fiducial markers die we herkennen om positie + afstand te
  schatten; familie `tag36h11` voor deze missie.
- **px** / **mm** / **m** — pixels / millimeter / meter (eenheden).

## Doel

Tijdens de **`DEPLOYED`**-fase (parachute uit, CanSat daalt) detecteren
we AprilTags op de grond en sturen de **twee grootste** detecties mee in
elk TLM-frame. Op het grondstation kan zo live geplot worden welke tag
herkend is en hoe ver hij weg is. In alle andere flight-states
(`PAD_IDLE`, `ASCENT`, `LANDED`) **staat de camera-thread stil** — de
spec schrijft voor dat we daar CPU + warmte sparen.

## Architectuur

```
         ┌─────────────────────────────┐
         │ Picamera2 (1600×1300 capture)│
         └──────────────┬──────────────┘
                        │ RGB frame
                        ▼
         ┌─────────────────────────────┐
         │ OpenCV preprocess           │
         │  - cvtColor → grey          │
         │  - resize → detect_width    │
         └──────────────┬──────────────┘
                        │ grey + scale
                        ▼
         ┌─────────────────────────────┐
         │ AprilTag detector            │
         │  family = tag36h11           │
         └──────────────┬──────────────┘
                        │ list[(tag_id, corners)]
                        ▼
         ┌─────────────────────────────┐
         │ compute_metrics              │
         │  d = f_px × size_m / max_side│
         │  dx = (tx-cx)·d/f_px         │
         └──────────────┬──────────────┘
                        │ DetectorMetrics
                        ▼
         ┌─────────────────────────────┐
         │ TagBuffer (thread-safe)      │
         │  top-2 by max_side_px        │
         │  staleness = 2 s             │
         └──────────────┬──────────────┘
                        │ snapshot (called door TLM-loop)
                        ▼
                  build_telemetry_packet(tags=…)
```

Alle files staan onder
[`src/cansat_hw/camera/`](../src/cansat_hw/camera/):

| Module | Wat het doet |
|---|---|
| [`registry.py`](../src/cansat_hw/camera/registry.py) | Laad `tag_registry.json` — per-ID fysieke afmeting, lens/sensor-parameters. |
| [`buffer.py`](../src/cansat_hw/camera/buffer.py) | Thread-safe top-2 tag-buffer met staleness. |
| [`detector.py`](../src/cansat_hw/camera/detector.py) | AprilTag-wrapper + pure-Python afstandsmath (`compute_metrics`). |
| [`thread.py`](../src/cansat_hw/camera/thread.py) | `CameraThread` loop (capture→detect→buffer), activeerbaar per flight-state. |
| [`hardware.py`](../src/cansat_hw/camera/hardware.py) | Lazy Picamera2 + OpenCV fabrieksfuncties (worden pas geïmporteerd als de camera effectief aan staat). |

## Hardware-keuze: OV2311 (Arducam B0381 PiVariety NoIR mono)

Voor 2026 hangt er een **Arducam B0381 — PiVariety 2 MP global-shutter
NoIR mono module (OV2311)** aan de CSI-poort van de Zero. Relevante specs:

| Parameter | Waarde | Waarom belangrijk |
|---|---|---|
| Active array | **1600 × 1300 px** (2 MP) | `full_res_px` in de registry; `Picamera2.create_still_configuration` rapporteert hetzelfde. |
| Pixel pitch | **3,0 µm** | Bepaalt `focal_length_px`. Significant grover dan IMX477 (1,55 µm) — vereist een aparte registry-entry. |
| Shutter | **Global** | Geen rolling-shutter "jelly" tijdens descent; randvoorwaarde voor scherpe AprilTag-corners onder rotatie + verticale snelheid. |
| Sensor type | Mono (geen Bayer) | Geen demosaic-stap — direct grayscale uit, snellere AprilTag-pijplijn. |
| Filter | NoIR (geen IR-cut) | Marginaal voordeel bij low-light / IR-illuminatie; voor de missie weinig effect. |
| libcamera tuning | `arducam-pivariety_mono.json` | Moet apart op de Zero geïnstalleerd zijn — zie [Troubleshooting](#libcamera-tuning-file-ontbreekt). |

> **Historisch**: vóór de switch stond de pipeline gericht op een
> Pi HQ camera (IMX477, 4056×3040, 1,55 µm). Met dezelfde 25 mm lens gaf
> dat `focal_length_px ≈ 16 129`. Met OV2311 is dat **~8 333 px** —
> bijna een factor 2 verschil. Een onbijgewerkte registry rapporteert
> dus afstanden die ~2× te groot zijn. Check altijd dat `tag_registry.json`
> overeenkomt met de fysiek gemonteerde sensor.

## Tag-registry (`config/camera/tag_registry.json`)

Per-ID fysieke afmetingen + lens- en sensor-parameters. Schema:

```jsonc
{
	"lens": {
		"focal_length_mm": 25.0
	},
	"sensor": {
		"name": "OV2311 (Arducam B0381 PiVariety NoIR mono, global shutter)",
		"pixel_pitch_um": 3.0,
		"full_res_px": [1600, 1300]
	},
	"tags": {
		"26": { "size_mm": 4500, "label": "Grote missie-tag (4.5 m)" },
		"1":  { "size_mm": 1100, "label": "Kleine missie-tag #1 (1.1 m, opgemeten)" },
		"2":  { "size_mm": 1100 },
		"3":  { "size_mm": 1100 },
		"4":  { "size_mm": 1100 }
	},
	"default_size_mm": 175
}
```

- **`lens.focal_length_mm`**: de effectieve brandpuntafstand van de
  telelens. Voor 2026 is dat **25 mm**.
- **`sensor.pixel_pitch_um`**: sensor-pixel pitch. De OV2311 (Arducam
  B0381 PiVariety) heeft **3,0 µm**. Samen met `focal_length_mm` rekent
  de registry intern `focal_length_px = focal_length_mm × 1000 /
  pixel_pitch_um` uit — voor 25 mm / 3,0 µm = **~8 333 px** op volle
  resolutie.
- **`tags`**: de IDs die we verwachten. De grote 4,5 m tag heeft ID 26;
  de vier kleine **1,1 m** tags (opgemeten op het terrein, niet de
  oorspronkelijke schatting van 1,5 m) hebben IDs 1–4.
- **`default_size_mm`**: fallback voor onbekende IDs. Leerlingen printen
  tests op papier als 17,5 cm tags (`default_size_mm = 175`).

De loader (`load_tag_registry`) is robuust: missende velden vervallen in
defaults, een kapot/onleesbaar bestand leidt tot een volledig-default
registry. Nooit raised hij — de radio-service blijft dan gewoon draaien
(je ziet wel een `WARN` op stderr).

## Afstandsberekening

Klassieke pinhole-formule:

```
d_m = (f_px * tag_size_m) / max_side_px
```

- `f_px`: brandpuntafstand in **volle-resolutie pixels** (uit de registry).
- `tag_size_m`: fysieke afmeting van de detected tag (registry-lookup op
  `tag_id`; fallback = `default_size_mm`).
- `max_side_px`: langste zijde van de gedetecteerde vierhoek, **naar full-
  res teruggeschaald** (de detector werkt standaard op een gedownscalede
  versie voor snelheid — zie verderop).

Laterale offset (ten opzichte van het beeldcentrum) via de kleine-hoek-
benadering:

```
dx_m = (tx_px − cx_px) * d_m / f_px
dy_m = (ty_px − cy_px) * d_m / f_px
```

Alle waarden worden daarna naar cm afgerond en naar het i16-bereik
(±327 m) geclampt zodat ze in een `TagDetection` passen (zie codec).

**Bekende limitatie (i16 cm):** `dz_cm` clipt op ±327 m. Voor een CanSat
met apogee van 500–1000 m betekent dat dat de afgelezen `dz` op grote
hoogte "vast" zit op 32767 cm — je weet dan alleen "verder dan 327 m".
Het grondstation kan op basis van `tag_id` + registry de theoretische
maximum-afstand uit `max_side_px` reconstrueren als we die later ook in
het TLM-frame zouden zetten; voor nu leven we met de limiet omdat de
twee praktische use-cases (1) rechtzetten tijdens descent onder 300 m
en (2) "er is een tag in beeld"-indicator op grotere hoogte, allebei
ondersteund blijven.

## Thread-model

Eén achtergrond-thread (**`CameraThread`**) in hetzelfde proces als de
radio-service. Waarom geen subprocess?

- **Geen IPC-overhead**: buffer-read is een memory lookup + lock, geen
  JSON over een pipe.
- **Geen tweede runtime om te babysitten**: één systemd-unit, één venv,
  één crash-log.
- **Geen CPU-hongersnood**: de Zero 2 W heeft 4 ARM-cores. De radio-loop
  is I/O-bound (SPI + I²C), en de camera-detectie draait op één core —
  dus we blokkeren elkaar niet.

De thread slaapt op een `threading.Condition` als hij niet actief is.
Activate/deactivate loopt via `camera_thread.set_active(bool)`, dat de
radio-loop elke iteratie aanroept met `state.flight_state == DEPLOYED`.

### Detectie op gedownscalede frames

De AprilTag-detector is O(pixels). Op de OV2311 (1600×1300 = 2 MP) is
volle-resolutie detectie op een Zero 2 W ruwweg ~150–300 ms per frame —
significant beter dan op de eerder geplande IMX477 (4056×3040 = 12 MP,
~1 s/frame). Omdat we toch **grote** tags zoeken (4,5 m → vele duizenden
pixels op korte afstand) kunnen we het frame nog wat downscalen voor
extra marge:

```
target_fps      = 7.0 Hz
detect_width    = 1014 px  (~1.6× downscale van 1600)
```

Corners worden daarna met `inv_scale = 1/scale` teruggeschaald naar full-
res vóór de afstandsberekening, dus het resultaat is **invariant** onder
de gekozen `detect_width`. Wie meer range wil inruilen voor minder fps:
start met `--camera-detect-width 1600` (no downscale) en meet de
effectieve fps via `camera_thread.stats()["frames"]`. Met de OV2311 is
full-res detectie realistisch genoeg om standaard te overwegen — test op
de Zero met de actieve mounting + scènepatroon.

## Integratie met de radio-service

Nieuwe CLI-args voor
[`scripts/cansat_radio_protocol.py`](../scripts/cansat_radio_protocol.py):

| Flag | Default | Betekenis |
|---|---|---|
| `--no-camera` | uit | Schakel de camera-thread uit (radio-only test). |
| `--tag-registry PAD` | `config/camera/tag_registry.json` | Pad naar JSON registry. |
| `--camera-resolution WxH` | `1600x1300` | Picamera2 capture-resolutie. Default = native actieve array van de OV2311 (Arducam B0381 PiVariety NoIR). Zet naar `4056x3040` als je een Pi HQ-camera met IMX477 monteert; libcamera clipt/schaalt bij een mismatch stil naar de actieve sensor. |
| `--camera-detect-width PX` | `1014` | Downscale-breedte voor detectie. |
| `--camera-fps HZ` | `7.0` | Bovengrens voor capture-frequentie. |
| `--camera-tag-families F` | `tag36h11` | AprilTag-familie. |
| `--deploy-save-every-n N` | `7` | Sla tijdens DEPLOYED elke N-de full-res frame als JPEG op in `--photo-dir`. Bij 7 Hz ≈ 1 foto/s. `0` = uit. Dat is de fallback waarmee we achteraf kunnen zien wát de camera zag als er géén tags in de TLM verschenen. |

Per iteratie van de main loop:

1. `sampler.tick(...)` — sensoren.
2. `maybe_advance_flight_state(...)` — state-machine.
3. `_emit_evt_state_if_changed()` — EVT STATE als nodig.
4. `_apply_servo_policy()` — gimbal-rail.
5. **`camera_thread.set_active(state == DEPLOYED)`** — camera aan/uit.
6. `build_telemetry_packet(..., tags=tag_buffer.snapshot())` — TLM met tags.

De `snapshot()` retourneert een lege lijst zodra de buffer stale is
(geen verse frames > 2 s) of gecleared werd bij `set_active(False)`. Dus
zelfs als de thread nog even nauwelijks frames haalt tussen twee
`DEPLOYED → LANDED`-ticks in, zie je geen "ghost tags" in de TLM.

### Synchrone CONFIG-mode commando's (``!shoot`` / ``!detect``)

Naast de thread bestaat er een **synchrone** camera-service die in CONFIG
draait — bedoeld om vóór een missie te checken of de camera überhaupt
tags ziet, op welke afstand, en om JPEGs op de Zero te zetten voor
handmatige review. De service deelt `capture_fn`, `preprocess_fn`,
`detector` en `registry` met de thread zodat de afstandsmath identiek is.

| Radio-commando | Pico-CLI | Gedrag | Voorbeeld-reply (≤ 60 B) |
|---|---|---|---|
| `CAM SHOOT` | `!shoot` | 1 frame capturen, JPEG wegschrijven, AprilTags detecteren. | `OK SHOOT cam_214530Z.jpg 1600x1300 T=2 1=1234 3=2345` |
| `CAM DETECT` | `!detect` | 1 frame capturen, AprilTags detecteren, **niet** opslaan. | `OK DETECT 1600x1300 T=1 1=1234` |
| `GET CAMSTATS` | `!camstats` | Thread-diagnose + service-counters. | `OK CAMSTATS A=off F=420 S=60 E=0 D=7` |

Per tag: `<id>=<afstand_cm>`, gesorteerd op grootste `max_side_px`
(topdetectie eerst). Max 2 tags in de reply zodat we binnen 60 byte
blijven. Geen tags? Dan zie je `T=0` en kun je via `!shoot` → rsync de
JPEG ophalen om te zien waarom (te ver, scheef, reflectie, …).

**Guard-rails:**

* Alleen in CONFIG. In MISSION/TEST krijg je `ERR BUSY <MODE>` via de
  normale allowlist — de missie-TLM-loop is heilig.
* Als de `CameraThread` **actief** is (dus we zitten in DEPLOYED, bv. via
  `SET STATE DEPLOYED` uit CONFIG voor een klas-demo) krijgt de operator
  `ERR CAM BUSY` zodat we geen concurrent Picamera2-access krijgen.
  Debug-foto's tijdens de echte DEPLOYED-fase komen van de thread zelf
  (zie `--deploy-save-every-n` hierboven).
* Zonder camera-hardware (`--no-camera`, of Picamera2/OpenCV niet
  geïnstalleerd): `ERR CAM NOHW`.
* Capture/detect/save-fouten: `ERR CAM <kort bericht>`.

Foto's krijgen een korte naam `cam_<HHMMSSZ>.jpg` (bv.
`cam_214530Z.jpg`); bij twee shots binnen dezelfde seconde volgt een
`_N` suffix. Thread-fallback-foto's krijgen de langere
`deploy_<UTCdate>T<HHMMSSZ>_<frame>[_tags-..].jpg` zodat een ls
chronologisch + zelfdocumenterend is.

## Gracieus gedrag bij ontbrekende hardware

Zoals `BME280`/`BNO055` is ook de camera **volledig optioneel** op
import-niveau. `cansat_hw.camera.detector` importeert `apriltag` pas
binnen `load_apriltag_detector()`; `cansat_hw.camera.hardware` importeert
`picamera2` / `cv2` pas binnen de fabrieksfuncties. Op een Mac/dev-
machine kun je dus:

```bash
python -m pytest tests/test_camera_*.py       # alle 31 tests groen
python -c "from cansat_hw.camera import CameraThread; print(CameraThread)"
```

— zonder dat OpenCV of AprilTag geïnstalleerd zijn. Op een kale Zero
zonder `python3-picamera2` (bv. een Pi zonder CSI-camera) loopt de
service gewoon door zonder tags in de TLM; je ziet een eenmalig
`WARN: camera-thread uit — picamera2 niet beschikbaar (…)` op stderr.

## Installatie op de Zero

Zie [`pyproject.toml`](../pyproject.toml), optioneel `[camera]`:

```bash
sudo apt install python3-picamera2 python3-libcamera
pip install -e ".[camera]"
pip install pupil-apriltags        # → import apriltag
```

De `[camera]` extra installeert `numpy<2` + `opencv-python-headless<4.12`.
`numpy<2` is een harde constraint omdat apt's `python3-picamera2` via
`simplejpeg` tegen NumPy 1.x is gebouwd — met NumPy 2 in de venv faalt
`import picamera2`. `opencv-python-headless<4.12` volgt uit die
NumPy-constraint (OpenCV 4.12+ eist NumPy ≥ 2).

## Tests

| Bestand | Wat er gedekt is |
|---|---|
| [`tests/test_camera_registry.py`](../tests/test_camera_registry.py) | JSON-loader, defaults, u16-clamping, tag-lookup + labels, bundled-registry parse-check. |
| [`tests/test_camera_buffer.py`](../tests/test_camera_buffer.py) | Top-2 sortering, staleness-policy, clear, stats, thread-safety smoke-test. |
| [`tests/test_camera_detector.py`](../tests/test_camera_detector.py) | Pinhole-afstand, lateral offset, clamping naar i16-cm, tag_id u8-mask, detection-scale. |
| [`tests/test_camera_thread.py`](../tests/test_camera_thread.py) | `run_once()` → top-2 in buffer, set_active(False) cleart, errors worden opgevangen, thread start/stop, inactive thread ticked niet. |

Draai lokaal:

```bash
.venv/bin/python -m pytest tests/test_camera_* -q
```

## Troubleshooting

### "Camera-thread actief" — maar geen tags in TLM
- Check `state.flight_state == DEPLOYED`: tags komen **alleen** in die state.
  Forceer met `SET STATE DEPLOYED` (alleen toegestaan in MISSION) voor een
  ground-test, of gebruik `SET MODE TEST` (start default in DEPLOYED).
- Check `camera_thread.stats()["frames"]`: als `frames == 0` krijgt de
  thread geen capture-loops gedraaid (Picamera2-init faalt, of de thread
  staat nog op inactive).
- Check de **staleness**: default 2 s. Als de detector traag is (full-res
  detect, bv.) kan een frame te oud zijn geworden tegen de volgende
  TLM-tick (1 Hz). Verlaag `--camera-detect-width`, of pas `TagBuffer.max_age_s` aan.

### "WARN: picamera2 niet beschikbaar"
Op een dev-machine normaal. Op de Zero: `sudo apt install python3-picamera2`.
Zorg dat je venv met `--system-site-packages` werd gemaakt, anders ziet hij
de apt-installatie niet.

### "AttributeError: module 'numpy' has no attribute 'bool'"
Je hebt een te nieuwe NumPy. Pin naar `numpy<2` (dat is wat
`pip install -e ".[camera]"` doet).

### Afstand komt niet overeen met meting
- Verifieer `focal_length_mm` in de registry — 25 mm is de missie-
  configuratie; een kortere lens (bv. 6 mm kit-lens) geeft totaal andere
  getallen.
- Verifieer `pixel_pitch_um`: **3,0 voor OV2311** (Arducam B0381
  PiVariety, missie-2026), 1,55 voor IMX477 (HQ camera), ander voor
  Camera v2 / v3. Een mismatch tussen registry en gemonteerde sensor is
  de meest voorkomende oorzaak van factor-2-afwijkingen.
- Verifieer dat de gemeten `tag_size_mm` in de registry overeenkomt met
  de **fysieke** tag op het terrein. De kleine tags zijn opgemeten op
  **1100 mm** (1,1 m), niet de oorspronkelijke schatting van 1,5 m.
- Check dat `max_side_px` **op full-res** is: als je detecteert op een
  downscaled frame en vergeet terug te schalen, kom je er ongeveer een
  factor `detect_width/full_width` naast uit. De code in
  [`detector.py`](../src/cansat_hw/camera/detector.py) (`inv_scale`)
  doet dat al automatisch.

### libcamera tuning-file ontbreekt
Bij eerste run zie je mogelijk:

```
ERROR IPAProxy ipa_proxy.cpp:185 Configuration file
  'arducam-pivariety_mono.json' not found for IPA module 'rpi/vc4'
```

Picamera2/libcamera detecteert de Arducam Pivariety-sensor wel (de
camera werkt, libcamera valt terug op een generieke tuning), maar de
beeld-tuning is suboptimaal. Fix:

```bash
# Op de Zero
wget -O install_pivariety_pkgs.sh https://github.com/ArduCAM/Arducam-Pivariety-V4L2-Driver/releases/download/install_script/install_pivariety_pkgs.sh
chmod +x install_pivariety_pkgs.sh
./install_pivariety_pkgs.sh -p libcamera_apps
./install_pivariety_pkgs.sh -p libcamera_dev
```

Of plaats handmatig `arducam-pivariety_mono.json` (vanaf de Arducam
GitHub-repo) in `/usr/share/libcamera/ipa/raspberrypi/` en herstart de
service.
