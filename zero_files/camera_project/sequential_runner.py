import os
import sys
import json
import time
import socket
import threading
import subprocess
from datetime import datetime

from tag_metrics import compute_calibration_k, compute_metrics_from_corners

from picamera2 import Picamera2
import paho.mqtt.client as mqtt

PHOTO_DIR = os.environ.get("PHOTO_DIR", "/home/icw/photos")
os.makedirs(PHOTO_DIR, exist_ok=True)

MQTT_BROKER = os.environ["CANSAT_MQTT_BROKER"]
MQTT_PORT = int(os.environ.get("CANSAT_MQTT_PORT", "1883"))
MQTT_USER = os.environ["CANSAT_MQTT_USER"]
MQTT_PASS = os.environ["CANSAT_MQTT_PASS"]

CONTROL_TOPIC = os.environ.get("CONTROL_TOPIC", "rpi/camera/control")
STATUS_TOPIC = os.environ.get("STATUS_TOPIC", "rpi/camera/status")
FRAME_TOPIC = os.environ.get("FRAME_TOPIC", "rpi/camera/frame")
TAGS_TOPIC = os.environ.get("TAGS_TOPIC", "rpi/camera/tags")

TAG_FAMILIES = os.environ.get("TAG_FAMILIES", "tag36h11")
QUAD_DECIMATE = float(os.environ.get("QUAD_DECIMATE", "2.0"))
MAX_W = int(os.environ.get("MAX_W", "800"))

CALIBRATION_DATA = os.environ.get(
    "CALIBRATION_DATA",
    "195.0:0.80,118.9:1.30,85.5:1.80,78.4:2.00,40.1:3.80,34.0:4.50",
)

FX = float(os.environ.get("FX", "1000"))
FY = float(os.environ.get("FY", "1000"))

HOSTNAME = socket.gethostname()
CLIENT_ID = os.environ.get("MQTT_CLIENT_ID", f"rpi-camera-seq-{HOSTNAME}")

running = False


class AprilTagWorker:
    def __init__(self):
        env = os.environ.copy()
        env["TAG_FAMILIES"] = TAG_FAMILIES
        env["QUAD_DECIMATE"] = str(QUAD_DECIMATE)
        env["MAX_W"] = str(MAX_W)

        self.proc = subprocess.Popen(
            [sys.executable, "-u", os.path.join(os.path.dirname(__file__), "apriltag_worker.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )

    def detect(self, filepath: str):
        if self.proc.poll() is not None:
            return None
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None

        self.proc.stdin.write(filepath + "\n")
        self.proc.stdin.flush()

        line = self.proc.stdout.readline()
        if not line:
            return None
        try:
            return json.loads(line)
        except Exception:
            return None

    def close(self):
        try:
            if self.proc.stdin:
                self.proc.stdin.write("__quit__\n")
                self.proc.stdin.flush()
        except Exception:
            pass
        try:
            self.proc.terminate()
        except Exception:
            pass


def _parse_command(payload_raw: str) -> str:
    payload_raw = (payload_raw or "").strip()
    try:
        data = json.loads(payload_raw)
        if isinstance(data, dict) and "command" in data:
            return str(data["command"]).strip().lower()
    except Exception:
        pass
    return payload_raw.strip().lower()


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"MQTT verbonden (rc={rc})")
        client.subscribe(CONTROL_TOPIC)
    else:
        print(f"MQTT connect rc={rc}")


def on_message(client, userdata, msg):
    global running
    raw = msg.payload.decode(errors="replace")
    cmd = _parse_command(raw)
    print(f"Control bericht ontvangen: {raw.strip()}")

    if cmd == "start":
        if not running:
            running = True
            client.publish(STATUS_TOPIC, json.dumps({"status": "running", "mode": "sequential"}), qos=1)
    elif cmd == "stop":
        if running:
            running = False
            client.publish(STATUS_TOPIC, json.dumps({"status": "stopped", "mode": "sequential"}), qos=1)
    elif cmd == "status":
        state = "running" if running else "stopped"
        client.publish(STATUS_TOPIC, json.dumps({"status": state, "mode": "sequential"}), qos=1)


def main():
    client = mqtt.Client(client_id=CLIENT_ID)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()

    picam2 = Picamera2()
    config = picam2.create_still_configuration(main={"size": (1600, 1300)})
    picam2.configure(config)
    picam2.start()

    worker = AprilTagWorker()

    calib_pairs = []
    try:
        for part in (CALIBRATION_DATA or "").split(","):
            part = part.strip()
            if not part:
                continue
            px_s, m_s = part.split(":", 1)
            calib_pairs.append((float(px_s), float(m_s)))
    except Exception:
        calib_pairs = []
    if not calib_pairs:
        calib_pairs = [(78.4, 2.00)]
    k = compute_calibration_k(calib_pairs)

    print("Sequential runner gestart – wacht op MQTT start/stop")

    try:
        while True:
            if not running:
                time.sleep(0.05)
                continue

            t0 = time.monotonic()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"photo_{timestamp}.jpg"
            filepath = os.path.join(PHOTO_DIR, filename)

            t_cap0 = time.monotonic()
            picam2.capture_file(filepath)
            t_cap1 = time.monotonic()

            det0 = time.monotonic()
            res = worker.detect(filepath)
            det1 = time.monotonic()

            tags = []
            detect_s = det1 - det0
            detections = []
            if isinstance(res, dict):
                if isinstance(res.get("tags"), list):
                    tags = [int(t) for t in res.get("tags", [])]
                if isinstance(res.get("detect_s"), (int, float)):
                    detect_s = float(res["detect_s"])

                img_meta = res.get("image") if isinstance(res.get("image"), dict) else None
                image_w = int(img_meta.get("w")) if img_meta and isinstance(img_meta.get("w"), (int, float)) else 0
                image_h = int(img_meta.get("h")) if img_meta and isinstance(img_meta.get("h"), (int, float)) else 0

                det_list = res.get("detections")
                if isinstance(det_list, list) and image_w > 0 and image_h > 0:
                    for d in det_list:
                        if not isinstance(d, dict):
                            continue
                        tag_id = d.get("tag_id")
                        corners_px = d.get("corners_px")
                        if tag_id is None or not isinstance(corners_px, list) or len(corners_px) != 4:
                            continue
                        try:
                            metrics = compute_metrics_from_corners(
                                tag_id=tag_id,
                                corners_px=corners_px,
                                image_w=image_w,
                                image_h=image_h,
                                k=k,
                                fx=FX,
                                fy=FY,
                            )
                            detections.append(metrics)
                        except Exception:
                            continue

            payload = {
                "file": filename,
                "tags": tags,
                "detections": detections,
                "timestamp": time.time(),
                "host": HOSTNAME,
                "mode": "sequential",
                "timing": {
                    "capture_s": t_cap1 - t_cap0,
                    "detect_s": detect_s,
                    "total_s": (t_cap1 - t0) + detect_s,
                },
            }

            client.publish(FRAME_TOPIC, json.dumps(payload), qos=1)
            if tags:
                client.publish(TAGS_TOPIC, json.dumps({"tags": tags, "timestamp": time.time(), "file": filename}), qos=1)

    finally:
        try:
            picam2.stop()
        except Exception:
            pass
        try:
            worker.close()
        except Exception:
            pass
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
