import time
import os
import json
import sys
import subprocess
import cv2
import apriltag
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from mqtt_handler import MqttHandler

PHOTO_DIR = "/home/icw/photos"
PROCESSED_DIR = "/home/icw/processed"
os.makedirs(PROCESSED_DIR, exist_ok=True)

PROCESS_EVERY_N = int(os.environ.get("PROCESS_EVERY_N", "5"))
_photo_counter = 0

mqtt = MqttHandler()  # broker/creds uit .env (CANSAT_MQTT_*; zie docs/secrets.md)

options = apriltag.DetectorOptions(
    families="tag36h11",
    quad_decimate=2.0,
)  # pas family aan als nodig
detector = apriltag.Detector(options)

def _detect_tags_subprocess(filepath, families="tag36h11", quad_decimate=2.0):
    code = (
        "import json, sys; "
        "import cv2; "
        "import apriltag; "
        "fp=sys.argv[1]; "
        "img=cv2.imread(fp, cv2.IMREAD_GRAYSCALE); "
        "\n"
        "out={'tags': []}; "
        "\n"
        "\nif img is not None:"
        "\n  h,w=img.shape[:2]"
        "\n  max_w=800"
        "\n  if w>max_w:"
        "\n    scale=max_w/float(w)"
        "\n    img=cv2.resize(img,(max_w,int(h*scale)),interpolation=cv2.INTER_AREA)"
        "\n  opts=apriltag.DetectorOptions(families='" + families + "', quad_decimate=" + str(quad_decimate) + ")"
        "\n  det=apriltag.Detector(opts)"
        "\n  tags=det.detect(img)"
        "\n  out['tags']=[t.tag_id for t in tags]"
        "\n"
        "print(json.dumps(out))"
    )

    res = subprocess.run(
        [sys.executable, "-c", code, filepath],
        capture_output=True,
        text=True,
        timeout=8,
    )
    if res.returncode != 0:
        return None

    try:
        data = json.loads(res.stdout.strip() or "{}")
        tags = data.get("tags", [])
        if isinstance(tags, list):
            return [int(t) for t in tags]
    except Exception:
        return None
    return None

def _wait_for_stable_file(path, timeout_s=3.0, stable_checks=3, interval_s=0.1):
    deadline = time.time() + timeout_s
    last_size = -1
    stable_count = 0
    while time.time() < deadline:
        try:
            size = os.path.getsize(path)
        except OSError:
            time.sleep(interval_s)
            continue

        if size == last_size and size > 0:
            stable_count += 1
            if stable_count >= stable_checks:
                return True
        else:
            stable_count = 0
            last_size = size

        time.sleep(interval_s)
    return False

class PhotoHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".jpg"):
            if _wait_for_stable_file(event.src_path):
                self.process_photo(event.src_path)

    def process_photo(self, filepath):
        try:
            global _photo_counter
            _photo_counter += 1
            should_process = (PROCESS_EVERY_N <= 1) or (_photo_counter % PROCESS_EVERY_N == 0)

            if should_process:
                tag_list = _detect_tags_subprocess(filepath)
                if tag_list is None:
                    print("Processor error: apriltag subprocess failed")
                    tag_list = []

                mqtt.publish_frame(os.path.basename(filepath), tag_list)

                if tag_list:
                    mqtt.publish_tags(tag_list)
                    print(f"Tags in {os.path.basename(filepath)}: {tag_list}")

            new_path = os.path.join(PROCESSED_DIR, os.path.basename(filepath))
            os.rename(filepath, new_path)
            
        except Exception as e:
            print("Processor error:", e)

# ── tijdelijke test ───────────────────────────────────────────────
if __name__ == "__main__":
    event_handler = PhotoHandler()
    observer = Observer()
    observer.schedule(event_handler, PHOTO_DIR, recursive=False)
    observer.start()

    print("Processor gestart – wacht op foto's")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

    # ↓↓↓ test met één foto (verwijder of comment later uit)
    test_photo = "/home/icw/photos/photo_20260315_132553_156297.jpg"
    if os.path.exists(test_photo):
        print(f"\n=== Handmatige test met: {test_photo} ===")
        handler = PhotoHandler()
        handler.process_photo(test_photo)
    else:
        print(f"Testfoto niet gevonden: {test_photo}")
