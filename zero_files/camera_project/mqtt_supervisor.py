import os
import time
import json
import socket
import threading
import paho.mqtt.client as mqtt

MQTT_BROKER = os.environ.get("MQTT_BROKER", "mqtt.2-wire.xyz")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "cansat")
MQTT_PASS = os.environ.get("MQTT_PASS", "C2N$@T6tw")

PING_TOPIC = os.environ.get("PING_TOPIC", "rpi/camera/ping")
PONG_TOPIC = os.environ.get("PONG_TOPIC", "rpi/camera/pong")
AVAIL_TOPIC = os.environ.get("AVAIL_TOPIC", "rpi/camera/availability")
CONTROL_TOPIC = os.environ.get("CONTROL_TOPIC", "rpi/camera/control")

PROCESSED_DIR = os.environ.get("PROCESSED_DIR", "/home/icw/processed")

CLIENT_ID = os.environ.get("MQTT_CLIENT_ID", f"rpi-camera-supervisor-{socket.gethostname()}")
HOSTNAME = socket.gethostname()


def _detect_tags_subprocess(filepath, families="tag36h11", quad_decimate=2.0):
    code = (
        "import json, sys; "
        "import cv2; "
        "import apriltag; "
        "fp=sys.argv[1]; "
        "img=cv2.imread(fp, cv2.IMREAD_GRAYSCALE); "
        "out={'tags': []}; "
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

    import subprocess
    import sys

    res = subprocess.run(
        [sys.executable, "-c", code, filepath],
        capture_output=True,
        text=True,
        timeout=12,
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


class Supervisor:
    def __init__(self):
        self.client = mqtt.Client(client_id=CLIENT_ID)
        self.client.username_pw_set(MQTT_USER, MQTT_PASS)

        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        self.client.will_set(
            AVAIL_TOPIC,
            payload=json.dumps({"status": "offline", "host": HOSTNAME, "ts": time.time()}),
            qos=1,
            retain=True,
        )

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.publish(
                AVAIL_TOPIC,
                json.dumps({"status": "online", "host": HOSTNAME, "ts": time.time()}),
                qos=1,
                retain=True,
            )
            client.subscribe(PING_TOPIC)
            client.subscribe(CONTROL_TOPIC)

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        raw = msg.payload.decode(errors="replace").strip()

        if topic == PING_TOPIC:
            payload = {"host": HOSTNAME, "ts": time.time(), "echo": raw}
            client.publish(PONG_TOPIC, json.dumps(payload), qos=1)
            return

        if topic == CONTROL_TOPIC:
            command = None
            try:
                data = json.loads(raw)
                if isinstance(data, dict) and "command" in data:
                    command = str(data["command"]).strip().lower()
            except Exception:
                command = raw.strip().lower()

            if command == "reprocess_processed":
                t = threading.Thread(target=self.reprocess_processed, daemon=True)
                t.start()

    def reprocess_processed(self):
        try:
            files = [
                f
                for f in os.listdir(PROCESSED_DIR)
                if f.lower().endswith(".jpg")
            ]
            files.sort()
        except Exception:
            return

        for name in files:
            path = os.path.join(PROCESSED_DIR, name)
            tags = _detect_tags_subprocess(path)
            if tags is None:
                continue
            payload = {"file": name, "tags": tags, "timestamp": time.time(), "source": "reprocess"}
            self.client.publish("rpi/camera/frame", json.dumps(payload), qos=1)
            if tags:
                self.client.publish("rpi/camera/tags", json.dumps({"tags": tags, "timestamp": time.time(), "file": name}), qos=1)

    def run(self):
        self.client.connect(MQTT_BROKER, MQTT_PORT, 60)
        self.client.loop_forever()


if __name__ == "__main__":
    Supervisor().run()
