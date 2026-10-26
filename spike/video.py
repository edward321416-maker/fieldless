import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


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
         "-show_entries", "stream=width,height,r_frame_rate:format=duration",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    num, den = stream["r_frame_rate"].split("/")
    return VideoInfo(
        duration_s=float(data["format"]["duration"]),
        fps=float(num) / float(den),
        width=int(stream["width"]),
        height=int(stream["height"]),
    )


def normalize(src: Path, dst: Path, max_seconds: float | None = None, fps: float | None = None) -> Path:
    args = ["ffmpeg", "-y", "-i", str(src)]
    if max_seconds:
        args += ["-t", f"{max_seconds:.2f}"]
    filters = [f"fps={fps}"] if fps else []
    filters.append("scale=trunc(min(1280\\,iw)/2)*2:-2")
    args += ["-vf", ",".join(filters)]
    _run(args + _encode_args(dst))
    return Path(dst)


def cut(src: Path, dst: Path, start_s: float, end_s: float) -> Path:
    start = max(0.0, start_s)
    _run(["ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", str(src), "-t", f"{end_s - start:.2f}"] + _encode_args(dst))
    return Path(dst)


def burn_timecode(src: Path, dst: Path, offset_s: float = 0.0) -> Path:
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    tmp = Path(dst).with_name(Path(dst).stem + "_raw.mp4")
    writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    scale = max(0.6, height / 360)
    index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        label = f"{offset_s + index / fps:06.1f}s"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
        cv2.rectangle(frame, (0, 0), (tw + 20, th + 20), (0, 0, 0), -1)
        cv2.putText(frame, label, (10, th + 10), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2)
        writer.write(frame)
        index += 1
    cap.release()
    writer.release()
    normalize(tmp, Path(dst))
    tmp.unlink()
    return Path(dst)


def hold_frames(src: Path, dst: Path, repeat: int) -> Path:
    """Write every source frame `repeat` times at the source fps, so each photo stays on screen for repeat/fps seconds."""
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    tmp = Path(dst).with_name(Path(dst).stem + "_raw.mp4")
    writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        for _ in range(repeat):
            writer.write(frame)
    cap.release()
    writer.release()
    normalize(tmp, Path(dst))
    tmp.unlink()
    return Path(dst)


def frame_at(src: Path, t_s: float, dst: Path) -> Path:
    _run(["ffmpeg", "-y", "-ss", f"{max(0.0, t_s):.2f}", "-i", str(src), "-frames:v", "1", "-q:v", "2", str(dst)])
    return Path(dst)


def _letterbox(img: np.ndarray, width: int, height: int) -> np.ndarray:
    h, w = img.shape[:2]
    scale = min(width / w, height / h)
    resized = cv2.resize(img, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((height, width, 3), np.uint8)
    y = (height - resized.shape[0]) // 2
    x = (width - resized.shape[1]) // 2
    canvas[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    return canvas


def images_to_mp4(images: list[Path], dst: Path, fps: float = 1.0, long_side: int = 1280) -> Path:
    first = next((img for img in (cv2.imread(str(p)) for p in images) if img is not None), None)
    if first is None:
        raise ValueError("no readable images")
    ih, iw = first.shape[:2]
    if iw >= ih:
        width, height = long_side, round(long_side * ih / iw / 2) * 2
    else:
        width, height = round(long_side * iw / ih / 2) * 2, long_side
    tmp = Path(dst).with_name(Path(dst).stem + "_raw.mp4")
    writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    for path in images:
        img = cv2.imread(str(path))
        writer.write(_letterbox(img, width, height) if img is not None else np.zeros((height, width, 3), np.uint8))
    writer.release()
    normalize(tmp, Path(dst))
    tmp.unlink()
    return Path(dst)
