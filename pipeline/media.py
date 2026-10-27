"""Video and photo helpers: 1 fps videos from photos, photo numbers burned into frames, frame holding for Spark,
and the mapping between video pixels and photo pixels. Ported from spike/video.py."""
import json
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np

from pipeline.sam_output import Seg

LONG_SIDE = 1280  # D1: SAM and Spark saw 1280-wide videos


@dataclass(frozen=True)
class VideoInfo:
    duration_s: float
    fps: float
    width: int
    height: int


def _run(args: list[str]) -> None:
    try:
        subprocess.run(args, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        tail = "\n".join(e.stderr.decode(errors="replace").splitlines()[-20:])
        raise RuntimeError(f"{args[0]} failed (exit {e.returncode}):\n{tail}") from e


def _encode_args(dst: Path) -> list[str]:
    return ["-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", str(dst)]


def probe(path: Path) -> VideoInfo:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    num, den = stream["r_frame_rate"].split("/")
    return VideoInfo(float(data["format"]["duration"]), float(num) / float(den), int(stream["width"]),
                     int(stream["height"]))


def _normalize(src: Path, dst: Path) -> None:
    """Re-encode as H.264, at most LONG_SIDE wide, with even dimensions."""
    _run(["ffmpeg", "-y", "-i", str(src), "-vf", f"scale=trunc(min({LONG_SIDE}\\,iw)/2)*2:-2"] + _encode_args(dst))


def letterbox(src_wh: tuple[int, int], dst_wh: tuple[int, int]) -> tuple[float, int, int]:
    """(scale, x offset, y offset) that fits a src_wh image centred inside a dst_wh frame."""
    sw, sh = src_wh
    dw, dh = dst_wh
    scale = min(dw / sw, dh / sh)
    rw, rh = max(1, round(sw * scale)), max(1, round(sh * scale))
    return scale, (dw - rw) // 2, (dh - rh) // 2


def video_size(photo_wh: tuple[int, int]) -> tuple[int, int]:
    """Frame size of a video made from photo_wh photos: long side LONG_SIDE, even dimensions."""
    w, h = photo_wh
    if w >= h:
        return LONG_SIDE, round(LONG_SIDE * h / w / 2) * 2
    return round(LONG_SIDE * w / h / 2) * 2, LONG_SIDE


def _fit(img: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    h, w = img.shape[:2]
    scale, ox, oy = letterbox((w, h), size)
    resized = cv2.resize(img, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((size[1], size[0], 3), np.uint8)
    canvas[oy:oy + resized.shape[0], ox:ox + resized.shape[1]] = resized
    return canvas


def _write(frames, size: tuple[int, int], fps: float, dst: Path) -> None:
    """Write BGR frames of `size` as mp4v, then re-encode as H.264."""
    tmp = Path(dst).with_name(Path(dst).stem + "_raw.mp4")
    writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    if not writer.isOpened():
        raise RuntimeError(f"cv2.VideoWriter could not open {tmp}")
    try:
        try:
            for frame in frames:
                writer.write(frame)
        finally:
            writer.release()
        _normalize(tmp, Path(dst))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.unlink()


def images_to_mp4(images: list[Path], dst: Path, fps: float = 1.0) -> tuple[int, int]:
    """One frame per photo, letterboxed into the first readable photo's video size. An unreadable photo becomes a
    black frame, so frame n is always photo n. Returns the frame size (width, height)."""
    first = next((img for img in (cv2.imread(str(p)) for p in images) if img is not None), None)
    if first is None:
        raise ValueError("no readable images")
    size = video_size((first.shape[1], first.shape[0]))

    def frames():
        for path in images:
            img = cv2.imread(str(path))
            yield _fit(img, size) if img is not None else np.zeros((size[1], size[0], 3), np.uint8)

    _write(frames(), size, fps, dst)
    return size


def _read(src: Path):
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS)
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))

    def frames():
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    return
                yield frame
        finally:
            cap.release()

    return fps, size, frames()


def burn_photo_numbers(src: Path, dst: Path) -> Path:
    """Stamp each frame's photo number in the top-left corner, in the D1 format ("0012.0s" is photo 12 at 1 fps).
    Spark reads entrance positions from this number instead of estimating them (spec 7.1)."""
    fps, size, frames = _read(src)
    scale = max(0.6, size[1] / 360)

    def stamped():
        for index, frame in enumerate(frames):
            label = f"{index / fps:06.1f}s"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
            cv2.rectangle(frame, (0, 0), (tw + 20, th + 20), (0, 0, 0), -1)
            cv2.putText(frame, label, (10, th + 10), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2)
            yield frame

    _write(stamped(), size, fps, dst)
    return Path(dst)


def hold_frames(src: Path, dst: Path, repeat: int) -> Path:
    """Write every frame `repeat` times, so each photo stays on screen repeat/fps seconds (D1: Spark samples an
    uploaded video once every 2 s)."""
    fps, size, frames = _read(src)
    _write((frame for frame in frames for _ in range(repeat)), size, fps, dst)
    return Path(dst)


def video_box_to_photo(box: tuple[int, int, int, int], video_wh: tuple[int, int],
                       photo_wh: tuple[int, int]) -> tuple[int, int, int, int]:
    """A box in video pixels as photo pixels, undoing images_to_mp4's scaling and letterboxing; clamped."""
    scale, ox, oy = letterbox(photo_wh, video_wh)
    pw, ph = photo_wh
    left, top, right, bottom = box

    def x(v):
        return min(pw, max(0, round((v - ox) / scale)))

    def y(v):
        return min(ph, max(0, round((v - oy) / scale)))

    return x(left), y(top), x(right), y(bottom)


def seg_to_photo(seg: Seg, video_wh: tuple[int, int], photo_wh: tuple[int, int]) -> Seg:
    """seg moved from video pixels to photo pixels; the mask is resized to the new box."""
    box = video_box_to_photo(seg.box, video_wh, photo_wh)
    w, h = box[2] - box[0], box[3] - box[1]
    mask = seg.mask
    if mask is not None:
        if w > 0 and h > 0:
            mask = cv2.resize(mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)
        else:
            mask = np.zeros((max(0, h), max(0, w)), bool)
    return replace(seg, box=box, mask=mask)
