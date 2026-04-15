""" CANSAT PICO RECEIVER node

Receives message requiring ACK over RFM69HCW SPI module - RECEIVER node
Must be tested togheter with test_emitter

See Tutorial : https://wiki.mchobby.be/index.php?title=ENG-CANSAT-PICO-RFM69HCW-TEST
See GitHub : https://github.com/mchobby/cansat-belgium-micropython/tree/main/test-rfm69

RFM69HCW breakout : https://shop.mchobby.be/product.php?id_product=1390
RFM69HCW breakout : https://www.adafruit.com/product/3071
"""

from machine import SPI, Pin
from rfm69 import RFM69

# Moet exact overeenkomen met de zender (rfm69test_emitter.py): freq + 16-byte key
FREQ = 433.0
ENCRYPTION_KEY = bytes("CANSAT_2025-2026", "utf-8")
NODE_ID = 100  # basisstation / ontvanger-adres (emitter stuurt naar destination 100)

spi = SPI(
	0,
	miso=Pin(4),
	mosi=Pin(7),
	sck=Pin(6),
	baudrate=50000,
	polarity=0,
	phase=0,
	firstbit=SPI.MSB,
)
nss = Pin(5, Pin.OUT, value=True)
rst = Pin(3, Pin.OUT, value=False)

rfm = RFM69(spi=spi, nss=nss, reset=rst)
rfm.frequency_mhz = FREQ

# 16-byte AES key — identiek aan de emitter
rfm.encryption_key = ENCRYPTION_KEY
rfm.node = NODE_ID  # dit apparaat is node 100

print("Freq            :", rfm.frequency_mhz)
print("NODE            :", rfm.node)

print("Waiting for packets...")
while True:
	packet = rfm.receive(with_ack=True)
	# packet = rfm.receive(timeout=5.0)
	if packet is None:
		pass
	else:
		print("Received (raw bytes):", packet)
		try:
			packet_text = str(packet, "ascii")
			print("Received (ASCII):", packet_text)
		except Exception:
			print("(payload is not ASCII)")
		# RSSI van het ontvangen pakket (receive() zet last_rssi al)
		print("RSSI (last pkt) : %3.2f" % rfm.last_rssi)
		print("-" * 40)
