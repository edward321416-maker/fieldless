import shutil
import subprocess

import cv2
import numpy as np
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


def _first_frame(path):
    cap = cv2.VideoCapture(str(path))
    ok, frame = cap.read()
    cap.release()
    assert ok
    return frame


def test_probe_reads_duration_fps_and_size(sample):
    info = video.probe(sample)
    assert abs(info.duration_s - 3.0) < 0.2
    assert abs(info.fps - 10) < 0.01
    assert (info.width, info.height) == (320, 240)


def test_normalize_changes_fps(sample, tmp_path):
    out = video.normalize(sample, tmp_path / "n.mp4", fps=5)
    assert abs(video.probe(out).fps - 5) < 0.01


def test_normalize_handles_odd_width(tmp_path):
    src = tmp_path / "odd.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=321x240:rate=10",
         "-c:v", "mpeg4", "-pix_fmt", "yuv420p", str(src)],  # libx264 yuv420p itself rejects odd width
        check=True, capture_output=True,
    )
    out = video.normalize(src, tmp_path / "n.mp4")
    info = video.probe(out)
    assert info.width == 320
    assert info.width % 2 == 0


def test_normalize_drops_audio_and_caps_width(tmp_path):
    src = tmp_path / "av.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=1920x1080:rate=10",
         "-f", "lavfi", "-i", "sine=duration=2", "-c:a", "aac", "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True,
    )
    assert _audio_streams(src) == "audio"  # the source really has audio
    out = video.normalize(src, tmp_path / "n.mp4")
    assert video.probe(out).width == 1280
    assert _audio_streams(out) == ""


def _audio_streams(path):
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def test_cut_returns_requested_span(sample, tmp_path):
    out = video.cut(sample, tmp_path / "c.mp4", 1.0, 2.0)
    assert abs(video.probe(out).duration_s - 1.0) < 0.25


def test_burn_timecode_keeps_duration_and_size(sample, tmp_path):
    out = video.burn_timecode(sample, tmp_path / "b.mp4", offset_s=60.0)
    info = video.probe(out)
    assert abs(info.duration_s - 3.0) < 0.3
    assert (info.width, info.height) == (320, 240)


def test_burn_timecode_draws_label_top_left(sample, tmp_path):
    out = video.burn_timecode(sample, tmp_path / "b.mp4", offset_s=60.0)
    src_region = _first_frame(sample)[0:20, 0:60].astype(float)
    out_region = _first_frame(out)[0:20, 0:60].astype(float)
    assert np.abs(src_region - out_region).mean() > 30


def test_ffmpeg_failure_raises_runtime_error_with_stderr(tmp_path):
    with pytest.raises(RuntimeError, match="ffmpeg failed") as exc:
        video.frame_at(tmp_path / "missing.mp4", 0.0, tmp_path / "f.jpg")
    assert "No such file" in str(exc.value) or "does not exist" in str(exc.value)


def test_frame_at_writes_jpeg(sample, tmp_path):
    out = video.frame_at(sample, 1.5, tmp_path / "f.jpg")
    assert out.exists() and out.stat().st_size > 0


def _frame_means(path):
    cap = cv2.VideoCapture(str(path))
    means = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        means.append(float(frame.mean()))
    cap.release()
    return means


def test_hold_frames_repeats_each_frame(tmp_path):
    imgs = []
    for i, gray in enumerate((40, 120, 200)):
        p = tmp_path / f"{i:04d}.jpg"
        cv2.imwrite(str(p), np.full((300, 400, 3), gray, np.uint8))
        imgs.append(p)
    src = video.images_to_mp4(imgs, tmp_path / "seq.mp4", fps=1)
    src_means = _frame_means(src)
    assert len(src_means) == 3

    out = video.hold_frames(src, tmp_path / "held.mp4", repeat=2)
    info = video.probe(out)
    assert abs(info.fps - 1) < 0.01
    assert abs(info.duration_s - 6.0) < 0.3

    means = _frame_means(out)
    assert len(means) == 6
    for pair, source_mean in zip(((0, 1), (2, 3), (4, 5)), src_means):
        for i in pair:
            assert abs(means[i] - source_mean) < 3
    assert means[0] < means[2] < means[4]


def test_images_to_mp4_one_image_per_second(tmp_path):
    paths = []
    for i, gray in enumerate((40, 120, 200)):
        p = tmp_path / f"{i:04d}.jpg"
        cv2.imwrite(str(p), np.full((300, 400, 3), gray, np.uint8))
        paths.append(p)
    paths.append(tmp_path / "missing.jpg")  # failed download keeps its slot as a black frame
    out = video.images_to_mp4(paths, tmp_path / "seq.mp4", fps=1)
    info = video.probe(out)
    assert abs(info.fps - 1) < 0.01
    assert abs(info.duration_s - 4.0) < 0.3
    assert (info.width, info.height) == (1280, 960)

    cap = cv2.VideoCapture(str(out))
    means = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        means.append(float(frame.mean()))
    cap.release()
    assert len(means) == 4
    assert means[0] < means[1] < means[2]
    assert means[3] < 10
