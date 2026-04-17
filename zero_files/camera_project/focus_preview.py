import os
import time
from datetime import datetime

import numpy as np
import cv2
from picamera2 import Picamera2, Preview


OUTPUT_DIR = os.environ.get("FOCUS_OUTPUT_DIR", "/home/icw/focus_snaps")
os.makedirs(OUTPUT_DIR, exist_ok=True)

PREVIEW_W = int(os.environ.get("FOCUS_PREVIEW_W", "1280"))
PREVIEW_H = int(os.environ.get("FOCUS_PREVIEW_H", "720"))

ROI_SIZE = float(os.environ.get("FOCUS_ROI_SIZE", "0.35"))
OVERLAY_W = int(os.environ.get("FOCUS_OVERLAY_W", "640"))
OVERLAY_H = int(os.environ.get("FOCUS_OVERLAY_H", "360"))

CAPTURE_INTERVAL_S = float(os.environ.get("FOCUS_INTERVAL_S", "0.05"))


def _roi_box(w: int, h: int, frac: float):
    frac = max(0.05, min(1.0, float(frac)))
    rw = int(w * frac)
    rh = int(h * frac)
    x0 = max(0, (w - rw) // 2)
    y0 = max(0, (h - rh) // 2)
    return x0, y0, rw, rh


def focus_score(gray: np.ndarray, roi_frac: float) -> float:
    h, w = gray.shape[:2]
    x0, y0, rw, rh = _roi_box(w, h, roi_frac)
    roi = gray[y0 : y0 + rh, x0 : x0 + rw]
    lap = cv2.Laplacian(roi, cv2.CV_64F)
    return float(lap.var())


def make_overlay(text_lines: list[str], *, w: int, h: int) -> np.ndarray:
    img = np.zeros((h, w, 4), dtype=np.uint8)

    font = cv2.FONT_HERSHEY_SIMPLEX
    y = 30
    for line in text_lines:
        cv2.putText(img, line, (10, y), font, 0.8, (255, 255, 255, 255), 2, cv2.LINE_AA)
        y += 30

    return img


def main():
    picam2 = Picamera2()
    config = picam2.create_preview_configuration(main={"size": (PREVIEW_W, PREVIEW_H), "format": "RGB888"})
    picam2.configure(config)

    picam2.start_preview(Preview.DRM)
    picam2.start()

    last_overlay_t = 0.0
    last_score = 0.0

    try:
        while True:
            rgb = picam2.capture_array("main")
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            last_score = focus_score(gray, ROI_SIZE)

            now = time.monotonic()
            if now - last_overlay_t > 0.2:
                lines = [
                    "Focus preview (DRM)",
                    f"score: {last_score:,.0f}",
                    f"ROI: {int(ROI_SIZE * 100)}% center",
                    "Keys: s=save, q=quit",
                ]
                overlay = make_overlay(lines, w=OVERLAY_W, h=OVERLAY_H)
                picam2.set_overlay(overlay)
                last_overlay_t = now

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("s"):
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                fn = f"focus_{ts}_score_{int(last_score)}.jpg"
                path = os.path.join(OUTPUT_DIR, fn)
                picam2.capture_file(path)

            if CAPTURE_INTERVAL_S > 0:
                time.sleep(CAPTURE_INTERVAL_S)

    finally:
        try:
            picam2.set_overlay(None)
        except Exception:
            pass
        try:
            picam2.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
