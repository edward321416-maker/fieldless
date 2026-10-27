"""Test stand-ins: a fake MetaApi whose answers come from functions each test supplies, and synthetic photos."""
from contextlib import contextmanager
from pathlib import Path

import cv2
import numpy as np

from pipeline.cost import CostMeter


def synthetic_photo(path: Path, size: tuple[int, int] = (160, 120), seed: int = 0) -> Path:
    """A small JPEG with a seed-dependent colour and rectangle, so frames differ."""
    w, h = size
    img = np.full((h, w, 3), (37 * seed) % 256, np.uint8)
    cv2.rectangle(img, (seed % max(1, w - 10), 5), (seed % max(1, w - 10) + 10, 15), (0, 0, 255), -1)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)
    return Path(path)


class FakeApi:
    """Stands in for pipeline.meta_api.MetaApi. spark(schema_name, parts) -> dict; sam(prompt, media) -> str,
    where media is a Path for an image request and "video:<file_id>:<frames>" for a video request."""

    def __init__(self, spark=None, sam=None):
        self.spark = spark or (lambda schema_name, parts: {})
        self.sam = sam or (lambda prompt, media: "")
        self.calls: list[tuple] = []
        self.deleted: list[str] = []
        self.meter = CostMeter(cap_usd=100.0)  # the runner reads meter.spent_usd; fakes never spend

    @contextmanager
    def uploaded(self, path):
        file_id = f"file:{Path(path).name}"
        self.calls.append(("upload", Path(path).name))
        try:
            yield file_id
        finally:
            self.deleted.append(file_id)

    def spark_json(self, parts, schema_name, schema, estimate_usd=0.0):
        self.calls.append(("spark", schema_name))
        return self.spark(schema_name, parts)

    def spark_video_json(self, prompt, file_id, schema_name, schema):
        self.calls.append(("spark_video", schema_name, file_id))
        return self.spark(schema_name, [prompt, file_id])

    def sam_image(self, prompt, image):
        self.calls.append(("sam_image", prompt, Path(image).name))
        return self.sam(prompt, Path(image))

    def sam_video(self, prompt, file_id, frames):
        self.calls.append(("sam_video", prompt, file_id, frames))
        return self.sam(prompt, f"video:{file_id}:{frames}")
