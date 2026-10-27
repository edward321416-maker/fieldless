import cv2
import numpy as np
import pytest

from pipeline import media
from pipeline.sam_output import Seg


def write_photos(folder, n, size=(400, 300)):
    paths = []
    for i in range(n):
        img = np.full((size[1], size[0], 3), 40 * i, np.uint8)
        cv2.rectangle(img, (10 * i, 10), (10 * i + 50, 60), (0, 0, 255), -1)
        path = folder / f"{i}.jpg"
        cv2.imwrite(str(path), img)
        paths.append(path)
    return paths


def read_frames(path):
    cap = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    return frames


def test_letterbox_math():
    assert media.letterbox((2048, 1536), (1280, 960)) == (0.625, 0, 0)
    scale, ox, oy = media.letterbox((1536, 2048), (1280, 960))
    assert (scale, ox, oy) == (pytest.approx(0.46875), 280, 0)


def test_video_size_keeps_the_long_side_at_1280():
    assert media.video_size((2048, 1536)) == (1280, 960)
    assert media.video_size((1536, 2048)) == (960, 1280)


def test_video_box_to_photo_undoes_scaling_and_letterboxing():
    assert media.video_box_to_photo((0, 0, 1280, 960), (1280, 960), (2048, 1536)) == (0, 0, 2048, 1536)
    assert media.video_box_to_photo((280, 0, 1000, 960), (1280, 960), (1536, 2048)) == (0, 0, 1536, 2048)


def test_seg_to_photo_resizes_the_mask_to_the_new_box():
    seg = Seg(0, "0", (0, 0, 64, 48), 0.9, np.ones((48, 64), bool))
    moved = media.seg_to_photo(seg, (1280, 960), (2048, 1536))
    assert moved.box == (0, 0, 102, 77)
    assert moved.mask.shape == (77, 102) and moved.mask.all()


def test_images_to_mp4_makes_one_frame_per_photo(tmp_path):
    photos = write_photos(tmp_path, 3)
    size = media.images_to_mp4(photos, tmp_path / "v.mp4")
    info = media.probe(tmp_path / "v.mp4")
    assert size == (1280, 960) == (info.width, info.height)
    assert info.duration_s == pytest.approx(3.0, abs=0.2)


def test_frame_n_is_photo_n(tmp_path):
    media.images_to_mp4(write_photos(tmp_path, 3), tmp_path / "v.mp4")
    frames = read_frames(tmp_path / "v.mp4")[:3]
    means = [frame.mean() for frame in frames]
    assert means[0] + 10 < means[1] and means[1] + 10 < means[2]


def test_burn_photo_numbers_stamps_a_white_label_in_the_top_left(tmp_path):
    media.images_to_mp4(write_photos(tmp_path, 3), tmp_path / "v.mp4")
    media.burn_photo_numbers(tmp_path / "v.mp4", tmp_path / "n.mp4")
    plain = read_frames(tmp_path / "v.mp4")[0][:60, :150]
    stamped = read_frames(tmp_path / "n.mp4")[0][:60, :150]
    assert np.abs(stamped.astype(int) - plain.astype(int)).mean() > 5
    assert (stamped.min(axis=2) >= 200).sum() > 50  # white text pixels


def test_burn_then_hold_doubles_the_duration(tmp_path):
    media.images_to_mp4(write_photos(tmp_path, 3), tmp_path / "v.mp4")
    stamped = media.burn_photo_numbers(tmp_path / "v.mp4", tmp_path / "n.mp4")
    held = media.hold_frames(stamped, tmp_path / "h.mp4", 2)
    assert media.probe(held).duration_s == pytest.approx(6.0, abs=0.3)
    assert not (tmp_path / "h_raw.mp4").exists()


def test_hold_frames_repeats_each_frame_twice(tmp_path):
    media.images_to_mp4(write_photos(tmp_path, 3), tmp_path / "v.mp4")
    held = media.hold_frames(tmp_path / "v.mp4", tmp_path / "h.mp4", 2)
    frames = read_frames(held)
    diff = lambda a, b: np.abs(a.astype(int) - b.astype(int)).mean()
    assert diff(frames[0], frames[1]) < 3  # same photo held
    assert diff(frames[1], frames[2]) > 10  # photo 0 gives way to photo 1
