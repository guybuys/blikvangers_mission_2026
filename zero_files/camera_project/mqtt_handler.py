import paho.mqtt.client as mqtt
import json
import time

class MqttHandler:
    def __init__(self, broker="mqtt.2-wire.xyz", port=1883, username="cansat", password="C2N$@T6tw"):
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        
        if username and password:
            self.client.username_pw_set(username, password)
        
        self.broker = broker
        self.port = port
        self.connected = False
        self._connect()

    def _connect(self):
        try:
            self.client.connect_async(self.broker, self.port, 60)
            self.client.loop_start()  # non-blocking
        except Exception:
            pass  # geen crash

    def on_connect(self, client, userdata, flags, rc):
        self.connected = (rc == 0)
        if self.connected:
            print("MQTT verbonden")

    def on_disconnect(self, client, userdata, rc):
        self.connected = False
        print("MQTT verloren – probeert reconnect")

    def publish_tags(self, tags_list):
        if not self.connected:
            return
        payload = json.dumps({"tags": tags_list, "timestamp": time.time()})
        self.client.publish("rpi/camera/tags", payload, qos=1)

    def publish_frame(self, filename, tags_list):
        if not self.connected:
            return
        payload = json.dumps({"file": filename, "tags": tags_list, "timestamp": time.time()})
        self.client.publish("rpi/camera/frame", payload, qos=1)
