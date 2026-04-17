import numpy as np


def compute_calibration_k(calibration_data):
    pixel_sizes = np.array([d[0] for d in calibration_data], dtype=float)
    distances = np.array([d[1] for d in calibration_data], dtype=float)
    k_values = pixel_sizes * distances
    return float(np.mean(k_values))


def calculate_center_distance(tag_center_px, camera_center_px, cx, cy, fx, fy, distance_m, max_side=None):
    offset_x_px = tag_center_px[0] - camera_center_px[0]
    offset_y_px = tag_center_px[1] - camera_center_px[1]

    distance_px = np.sqrt(offset_x_px**2 + offset_y_px**2)

    angle_x = np.arctan2(offset_x_px, fx)
    angle_y = np.arctan2(offset_y_px, fy)

    offset_x_m = distance_m * np.tan(angle_x)
    offset_y_m = distance_m * np.tan(angle_y)

    offset_in_plane = np.sqrt(offset_x_m**2 + offset_y_m**2)
    distance_3d_m = np.sqrt(distance_m**2 - offset_in_plane**2) if distance_m**2 >= offset_in_plane**2 else 0

    return distance_px, distance_3d_m, offset_x_px, offset_y_px, offset_x_m, offset_y_m


def compute_metrics_from_corners(
    *,
    tag_id,
    corners_px,
    image_w,
    image_h,
    k,
    fx=1000.0,
    fy=1000.0,
):
    corners = np.array(corners_px, dtype=float)

    side1 = np.linalg.norm(corners[1] - corners[0])
    side2 = np.linalg.norm(corners[2] - corners[1])
    side3 = np.linalg.norm(corners[3] - corners[2])
    side4 = np.linalg.norm(corners[0] - corners[3])
    max_side = float(np.max([side1, side2, side3, side4]))

    distance_m = float(k / max_side) if max_side > 0 else 0.0

    cx_actual = image_w / 2.0
    cy_actual = image_h / 2.0
    tag_center = np.mean(corners, axis=0)
    camera_center = np.array([cx_actual, cy_actual])

    dist_px, dist_3d_m, off_x_px, off_y_px, off_x_m, off_y_m = calculate_center_distance(
        tag_center,
        camera_center,
        cx_actual,
        cy_actual,
        fx,
        fy,
        distance_m,
        max_side=max_side,
    )

    offset_in_plane = np.sqrt(off_x_m**2 + off_y_m**2)
    actual_distance_m = float(np.sqrt(distance_m**2 - offset_in_plane**2)) if distance_m**2 >= offset_in_plane**2 else 0.0

    return {
        "tag_id": int(tag_id),
        "corners_px": corners.tolist(),
        "center_px": tag_center.tolist(),
        "max_side_px": max_side,
        "distance_m": distance_m,
        "actual_distance_m": actual_distance_m,
        "center_distance_px": float(dist_px),
        "offset_px": {"x": float(off_x_px), "y": float(off_y_px)},
        "offset_m": {"x": float(off_x_m), "y": float(off_y_m)},
        "image": {"w": int(image_w), "h": int(image_h), "cx": float(cx_actual), "cy": float(cy_actual)},
    }
