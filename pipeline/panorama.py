"""Flat views cut from 360-degree photos. Mapillary's newest street photos are often panoramas, which SAM and Muse Spark
cannot read as they are: straight poles bend and the camera holder fills the bottom of the frame."""
import math

import cv2
import numpy as np

FOV = 80.0  # degrees across a view: wide enough for a few metres of position error
PITCH = -8.0  # looking slightly down, so that curbs and the feet of poles are in the view
SIZE = (1280, 960)  # about the panorama's own detail for a 5760-pixel photo at this FOV


def view(img: np.ndarray, yaw: float, pitch: float = PITCH, fov: float = FOV, size: tuple[int, int] = SIZE) -> np.ndarray:
    """What a flat camera would see looking yaw degrees right of the panorama's centre and pitch degrees above level.
    img is equirectangular: its width covers 360 degrees and its centre is straight ahead."""
    height, width = img.shape[:2]
    out_w, out_h = size
    focal = (out_w / 2) / math.tan(math.radians(fov) / 2)
    xs, ys = np.meshgrid(np.arange(out_w, dtype=np.float64) - (out_w - 1) / 2,
                         np.arange(out_h, dtype=np.float64) - (out_h - 1) / 2)
    tilt, turn = math.radians(pitch), math.radians(yaw)
    down = ys * math.cos(tilt) - focal * math.sin(tilt)
    ahead = ys * math.sin(tilt) + focal * math.cos(tilt)
    right = xs * math.cos(turn) + ahead * math.sin(turn)
    ahead = -xs * math.sin(turn) + ahead * math.cos(turn)
    longitude = np.arctan2(right, ahead)
    latitude = np.arctan2(down, np.hypot(right, ahead))
    map_x = (longitude / (2 * math.pi) + 0.5) * width - 0.5
    map_y = (latitude / math.pi + 0.5) * height - 0.5
    return cv2.remap(img, map_x.astype(np.float32), map_y.astype(np.float32), cv2.INTER_CUBIC, borderMode=cv2.BORDER_WRAP)


def box_angle(box: list[int], fov: float = FOV) -> float:
    """Degrees right of a view's centre to the middle of a box on the 0-1000 scale."""
    across = (box[0] + box[2]) / 2000 - 0.5
    return math.degrees(math.atan(across * 2 * math.tan(math.radians(fov) / 2)))
