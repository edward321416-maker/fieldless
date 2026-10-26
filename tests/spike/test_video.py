import shutil
import subprocess

import pytest

from spike import video

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture
def sample(tmp_path):
    p = tmp_path / "sample.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=10",
         "-pix_fmt", "yuv420p", str(p)],
        check=True, capture_output=True,
    )
    return p


def test_probe_reads_duration_fps_and_size(sample):
    info = video.probe(sample)
    assert abs(info.duration_s - 3.0) < 0.2
    assert abs(info.fps - 10) < 0.01
    assert (info.width, info.height) == (320, 240)


def test_normalize_changes_fps(sample, tmp_path):
    out = video.normalize(sample, tmp_path / "n.mp4", fps=5)
    assert abs(video.probe(out).fps - 5) < 0.01


def test_cut_returns_requested_span(sample, tmp_path):
    out = video.cut(sample, tmp_path / "c.mp4", 1.0, 2.0)
    assert abs(video.probe(out).duration_s - 1.0) < 0.25


def test_burn_timecode_keeps_duration_and_size(sample, tmp_path):
    out = video.burn_timecode(sample, tmp_path / "b.mp4", offset_s=60.0)
    info = video.probe(out)
    assert abs(info.duration_s - 3.0) < 0.3
    assert (info.width, info.height) == (320, 240)


def test_frame_at_writes_jpeg(sample, tmp_path):
    out = video.frame_at(sample, 1.5, tmp_path / "f.jpg")
    assert out.exists() and out.stat().st_size > 0


def test_images_to_mp4_one_image_per_second(tmp_path):
    import cv2
    import numpy as np

    paths = []
    for i in range(3):
        p = tmp_path / f"{i:04d}.jpg"
        cv2.imwrite(str(p), np.full((300, 400, 3), i * 80, np.uint8))
        paths.append(p)
    paths.append(tmp_path / "missing.jpg")  # failed download keeps its slot as a black frame
    out = video.images_to_mp4(paths, tmp_path / "seq.mp4", fps=1)
    info = video.probe(out)
    assert abs(info.fps - 1) < 0.01
    assert abs(info.duration_s - 4.0) < 0.3
    assert (info.width, info.height) == (1280, 960)
