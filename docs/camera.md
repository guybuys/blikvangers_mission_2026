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
         │ Picamera2 (4056×3040 capture)│
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

## Tag-registry (`config/camera/tag_registry.json`)

Per-ID fysieke afmetingen + lens- en sensor-parameters. Schema:

```jsonc
{
	"lens": {
		"focal_length_mm": 25.0
	},
	"sensor": {
		"name": "IMX477 (Raspberry Pi HQ camera)",
		"pixel_pitch_um": 1.55,
		"full_res_px": [4056, 3040]
	},
	"tags": {
		"26": { "size_mm": 4500, "label": "Grote missie-tag (4.5 m)" },
		"1":  { "size_mm": 1500, "label": "Kleine missie-tag #1" },
		"2":  { "size_mm": 1500 },
		"3":  { "size_mm": 1500 },
		"4":  { "size_mm": 1500 }
	},
	"default_size_mm": 175
}
```

- **`lens.focal_length_mm`**: de effectieve brandpuntafstand van de
  telelens. Voor 2026 is dat **25 mm**.
- **`sensor.pixel_pitch_um`**: sensor-pixel pitch. De IMX477 (Pi HQ camera)
  heeft **1.55 µm**. Samen met `focal_length_mm` rekent de registry intern
  `focal_length_px = focal_length_mm × 1000 / pixel_pitch_um` uit — voor
  25 mm / 1.55 µm = **~16 129 px** op volle resolutie.
- **`tags`**: de IDs die we verwachten. De grote 4.5 m tag heeft ID 26; de
  vier kleine 1.5 m tags hebben IDs 1–4.
- **`default_size_mm`**: fallback voor onbekende IDs. Leerlingen printen
  tests op papier als 17.5 cm tags (`default_size_mm = 175`).

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

De AprilTag-detector is O(pixels) — detectie op volle resolutie (4056×3040
= 12 MP) duurt op een Zero 2 W al snel ~1 s per frame. Omdat we **grote**
tags zoeken (4.5 m → vele duizenden pixels op korte afstand) kunnen we het
frame safe downscalen tot ~1000 px breed:

```
target_fps      = 7.0 Hz
detect_width    = 1014 px  (4× downscale van 4056)
```

Corners worden daarna met `inv_scale = 1/scale` teruggeschaald naar full-
res vóór de afstandsberekening, dus het resultaat is **invariant** onder
de gekozen `detect_width`. Wie meer range wil inruilen voor minder fps:
start met `--camera-detect-width 2028` (2× downscale) of zelfs
`--camera-detect-width 4056` (no downscale) en meet de effectieve fps via
`camera_thread.stats()["frames"]`.

## Integratie met de radio-service

Nieuwe CLI-args voor
[`scripts/cansat_radio_protocol.py`](../scripts/cansat_radio_protocol.py):

| Flag | Default | Betekenis |
|---|---|---|
| `--no-camera` | uit | Schakel de camera-thread uit (radio-only test). |
| `--tag-registry PAD` | `config/camera/tag_registry.json` | Pad naar JSON registry. |
| `--camera-resolution WxH` | `4056x3040` | Picamera2 capture-resolutie. |
| `--camera-detect-width PX` | `1014` | Downscale-breedte voor detectie. |
| `--camera-fps HZ` | `7.0` | Bovengrens voor capture-frequentie. |
| `--camera-tag-families F` | `tag36h11` | AprilTag-familie. |

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
- Verifieer `pixel_pitch_um`: 1.55 voor IMX477. Voor andere sensors
  (CSI Camera v2, v3, GS) is de pitch anders.
- Check dat `max_side_px` **op full-res** is: als je detecteert op een
  downscaled frame en vergeet terug te schalen, kom je er ongeveer een
  factor `detect_width/full_width` naast uit. De code in
  [`detector.py`](../src/cansat_hw/camera/detector.py) (`inv_scale`)
  doet dat al automatisch.
