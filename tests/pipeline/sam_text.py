"""Builds SAM answer text for tests, in the format the API returns (checked against real D1 answers).

A record is a box token followed by a one_bit mask token. Boxes are written the way SAM writes them, with inclusive
x2/y2; meta_sam_parser turns them into exclusive right/bottom. The mask payload comes from the parser package's own
encoder (a private helper, used only in tests) so the parser accepts it.
"""
import numpy as np
from meta_sam_parser._mask_codec import _encode_raster


def record(obj: str, left: int, top: int, right: int, bottom: int, width: int, height: int, conf: float,
           mask: np.ndarray | None = None) -> str:
    """One object with box [left, right) x [top, bottom) in a width x height frame; mask defaults to the full box."""
    w, h = right - left, bottom - top
    bits = np.ones((h, w), np.uint8) if mask is None else np.asarray(mask, np.uint8)
    payload = _encode_raster(bits.ravel().tolist(), w, h)
    return (f"{obj}<|box;x1={left};y1={top};x2={right - 1};y2={bottom - 1};w={width};h={height};c={conf}|>"
            f"<|mask;x=0;y=0;c={conf};data={h},{w},{payload}|>")


def line(frame: int, records: list[str]) -> str:
    return f"<{frame}f>" + ",".join(records)


def text(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"
