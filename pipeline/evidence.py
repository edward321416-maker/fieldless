"""Evidence photos (spec 7.4 step 10, spec 16). People and licence plates are blurred again with SAM before a
photo is stored, because Mapillary's own blurring misses some; blur is only ever added. Every record carries the
attribution Mapillary's terms require on photos we show: creator, a link to the photo, and the licence."""
from pathlib import Path

import cv2
import numpy as np

from pipeline import config
from pipeline.sam_output import Seg, confident, parse_away

BLUR_CONCEPTS = ["person", "license plate"]
BLUR_CONF = 0.3  # lower than the detection cut: missing a face is worse than blurring a little extra
BLUR_GROW_PX = 12
MAX_SIDE = 1600
JPEG_QUALITY = 88
LICENSE = "CC BY-SA 4.0"


def mapillary_link(image_id: str) -> str:
    return config.MAPILLARY_PHOTO_LINK.format(image_id=image_id)


def blur(img: np.ndarray, segs: list[Seg]) -> np.ndarray:
    """A copy of img in which every segment's mask, grown by BLUR_GROW_PX, is replaced by a strong blur."""
    h, w = img.shape[:2]
    region = np.zeros((h, w), np.uint8)
    for s in segs:
        left, top, right, bottom = s.box
        x0, y0, x1, y1 = max(0, left), max(0, top), min(w, right), min(h, bottom)
        if x0 >= x1 or y0 >= y1:
            continue
        mask = s.mask if s.mask is not None else np.ones((bottom - top, right - left), bool)
        region[y0:y1, x0:x1] |= mask[y0 - top:y1 - top, x0 - left:x1 - left].astype(np.uint8)
    out = img.copy()
    if not region.any():
        return out
    if BLUR_GROW_PX:
        region = cv2.dilate(region, np.ones((2 * BLUR_GROW_PX + 1, 2 * BLUR_GROW_PX + 1), np.uint8))
    blurred = cv2.GaussianBlur(img, (0, 0), sigmaX=max(8.0, w / 100))
    selected = region.astype(bool)
    out[selected] = blurred[selected]
    return out


def make_evidence(api, photo: Path, frame: dict, dst: Path) -> dict:
    """Blur people and plates in photo, save it to dst with its long side at most MAX_SIDE, and return the evidence
    record. A failed blur request raises, so an unblurred photo is never stored."""
    image_id, creator, captured_at = frame["image_id"], frame["creator"], frame["captured_at"]
    img = cv2.imread(str(photo))
    if img is None:
        raise ValueError(f"cannot read {photo}")
    segs: list[Seg] = []
    for concept in BLUR_CONCEPTS:
        segs += confident(parse_away(api.sam_image(concept, photo), video=False), BLUR_CONF)
    out = blur(img, segs)
    scale = MAX_SIDE / max(out.shape[:2])
    if scale < 1:
        out = cv2.resize(out, (round(out.shape[1] * scale), round(out.shape[0] * scale)),
                         interpolation=cv2.INTER_AREA)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(dst), out, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]):
        raise RuntimeError(f"could not write {dst}")
    return {"image": dst.name, "image_id": image_id, "creator": creator,
            "captured_at": captured_at, "link": mapillary_link(image_id), "license": LICENSE,
            "blurred_regions": len(segs)}
