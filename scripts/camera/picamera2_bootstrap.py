"""Maak ``python3-picamera2`` (apt) zichtbaar in een venv zonder ``--system-site-packages``.

Op Raspberry Pi OS staat Picamera2 in de system ``dist-packages``; een standaard-venv
filtert die weg. Deze helper voegt die paden **achteraan** toe vóór ``import picamera2``.
"""

from __future__ import annotations

import os
import sys


def ensure_apt_picamera2_on_path() -> None:
	if sys.prefix == sys.base_prefix:
		return
	v = f"{sys.version_info.major}.{sys.version_info.minor}"
	for p in (
		f"/usr/lib/python{v}/dist-packages",
		"/usr/lib/python3/dist-packages",
	):
		if os.path.isdir(p) and p not in sys.path:
			sys.path.append(p)
