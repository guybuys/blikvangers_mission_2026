import os
import sys
import json
import time

import cv2
import apriltag

TAG_FAMILIES = os.environ.get("TAG_FAMILIES", "tag36h11")
QUAD_DECIMATE = float(os.environ.get("QUAD_DECIMATE", "2.0"))
MAX_W = int(os.environ.get("MAX_W", "800"))

opts = apriltag.DetectorOptions(
    families=TAG_FAMILIES,
    quad_decimate=QUAD_DECIMATE,
)
detector = apriltag.Detector(opts)


def detect_file(path: str):
    t0 = time.monotonic()
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return {"ok": False, "error": "imread_failed", "tags": [], "detect_s": time.monotonic() - t0}

    orig_h, orig_w = img.shape[:2]
    scale = 1.0
    if orig_w > MAX_W:
        scale = MAX_W / float(orig_w)
        img = cv2.resize(img, (MAX_W, int(orig_h * scale)), interpolation=cv2.INTER_AREA)

    dets = detector.detect(img)
    inv_scale = 1.0 / scale if scale != 0 else 1.0

    detections = []
    tag_list = []
    for d in dets:
        tag_id = int(d.tag_id)
        tag_list.append(tag_id)

        corners = getattr(d, "corners", None)
        if corners is None:
            continue

        corners_list = []
        for c in corners:
            corners_list.append([float(c[0]) * inv_scale, float(c[1]) * inv_scale])

        center = getattr(d, "center", None)
        if center is None:
            cx = sum(p[0] for p in corners_list) / 4.0
            cy = sum(p[1] for p in corners_list) / 4.0
            center_list = [float(cx), float(cy)]
        else:
            center_list = [float(center[0]) * inv_scale, float(center[1]) * inv_scale]

        detections.append({"tag_id": tag_id, "corners_px": corners_list, "center_px": center_list})

    return {
        "ok": True,
        "tags": tag_list,
        "detections": detections,
        "image": {
            "w": int(orig_w),
            "h": int(orig_h),
            "scaled": bool(scale != 1.0),
            "scale": float(scale),
        },
        "detect_s": time.monotonic() - t0,
    }


def main():
    for line in sys.stdin:
        path = line.strip()
        if not path:
            continue
        if path == "__quit__":
            break

        try:
            res = detect_file(path)
        except Exception as e:
            res = {"ok": False, "error": str(e), "tags": []}

        sys.stdout.write(json.dumps(res) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
