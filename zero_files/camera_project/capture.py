from picamera2 import Picamera2
import time
from datetime import datetime
import os
import threading
import paho.mqtt.client as mqtt
import json

# ── Config ────────────────────────────────────────────────────────────────
PHOTO_DIR = "/home/icw/photos"
os.makedirs(PHOTO_DIR, exist_ok=True)

CAPTURE_INTERVAL = float(os.environ.get("CAPTURE_INTERVAL", "0"))

MQTT_BROKER = "mqtt.2-wire.xyz"
MQTT_PORT = 1883
MQTT_USER = "cansat"
MQTT_PASS = "C2N$@T6tw"
CONTROL_TOPIC = "rpi/camera/control"
STATUS_TOPIC = "rpi/camera/status"

# Globale vlaggen
running = False           # of de foto-loop actief is
running_lock = threading.Lock()

# MQTT callbacks
def on_connect(client, userdata, flags, rc):
    print(f"MQTT verbonden (rc={rc})")
    client.subscribe(CONTROL_TOPIC)

def on_message(client, userdata, msg):
    global running
    payload_raw = msg.payload.decode(errors="replace").strip()
    payload = payload_raw
    try:
        data = json.loads(payload_raw)
        if isinstance(data, dict) and "command" in data:
            payload = str(data["command"])
    except Exception:
        pass

    payload = payload.strip().lower()
    print(f"Control bericht ontvangen: {payload_raw}")

    if payload == "start":
        with running_lock:
            if not running:
                running = True
                print("Capture gestart")
                client.publish(STATUS_TOPIC, json.dumps({"status": "running"}), qos=1)
    elif payload == "stop":
        with running_lock:
            if running:
                running = False
                print("Capture gestopt")
                client.publish(STATUS_TOPIC, json.dumps({"status": "stopped"}), qos=1)
    elif payload == "status":
        with running_lock:
            state = "running" if running else "stopped"
            client.publish(STATUS_TOPIC, json.dumps({"status": state}), qos=1)

# MQTT client opzetten
client = mqtt.Client()
client.username_pw_set(MQTT_USER, MQTT_PASS)
client.on_connect = on_connect
client.on_message = on_message

try:
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()  # achtergrond thread voor MQTT
except Exception as e:
    print(f"MQTT connectie mislukt: {e}")
    raise SystemExit(1)

# Camera opzetten
picam2 = Picamera2()
config = picam2.create_still_configuration(main={"size": (1600, 1300)})
picam2.configure(config)
picam2.start()

print("Capture script gestart – wacht op MQTT commando's")

# Hoofdloop
try:
    while True:
        with running_lock:
            is_running = running

        if is_running:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = os.path.join(PHOTO_DIR, f"photo_{timestamp}.jpg")
            try:
                picam2.capture_file(filename)
                print(f"Foto gemaakt: {filename}")
            except Exception as e:
                print(f"Capture error: {e}")

            if CAPTURE_INTERVAL > 0:
                time.sleep(CAPTURE_INTERVAL)
        else:
            time.sleep(0.1)

except KeyboardInterrupt:
    print("Afsluiten...")
finally:
    picam2.stop()
    client.loop_stop()
    client.disconnect()

'''
from picamera2 import Picamera2
import time
from datetime import datetime
import os

PHOTO_DIR = "/home/icw/photos"
os.makedirs(PHOTO_DIR, exist_ok=True)

picam2 = Picamera2()
config = picam2.create_still_configuration(main={"size": (1600, 1300)})
picam2.configure(config)
picam2.start()

print("Capture gestart – foto's non-stop")

i = 0
while True:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = os.path.join(PHOTO_DIR, f"photo_{timestamp}.jpg")
    
    try:
        picam2.capture_file(filename)
        i += 1
        if i % 10 == 0:
            print(f"Foto {i}: {filename}")
    except Exception as e:
        print("Capture error:", e)
    
    time.sleep(0.005)  # 0 = max snelheid; pas aan naar 0.1 als te warm/CPU hoog
'''
