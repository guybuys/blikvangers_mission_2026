"""CanSat — gevoelige radio-instellingen voor de Pico (base station).

Kopieer dit bestand naar ``secrets.py`` naast ``basestation_cli.py`` op de Pico
(via Thonny: Save copy as ...) en pas aan. ``secrets.py`` staat in .gitignore
en wordt dus NIET in git gezet; ``secrets.example.py`` wél, als template.

Dit is de MicroPython/Adafruit-conventie — op de Zero gebruiken we een .env-bestand
(zie ``.env.example`` in de repo-root). De waarde van ``RADIO_KEY`` MOET exact
gelijk zijn op Pico én Zero, anders blijven pakketten stil.

Alle constanten hieronder zijn optioneel: als dit bestand of een veld ontbreekt,
valt ``basestation_cli.py`` terug op de ingebouwde demo-waarden.
"""

# --- Geheim (niet in git) -----------------------------------------------------
# 16-byte UTF-8 AES-sleutel voor RFM69. Telt letters als bytes; emoji/UTF-8 met
# multi-byte chars = minder letters. Niet korter of langer dan 16 bytes.
RADIO_KEY = b"CANSAT_2025-2026"

# --- Team-configuratie (optioneel) -------------------------------------------
# Lokale ``!freq`` overschrijft RADIO_FREQ_MHZ alleen in geheugen; na een
# geslaagde ``SET FREQ``-roundtrip wordt de nieuwe freq in radio_freq.json
# bewaard en bij de volgende boot daaruit geladen.
RADIO_FREQ_MHZ = 433.0
RADIO_NODE = 100  # Pico = base station
RADIO_DEST = 120  # CanSat = Zero 2 W
