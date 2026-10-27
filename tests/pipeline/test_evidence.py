import cv2
import numpy as np
import pytest

from pipeline import evidence
from pipeline.sam_output import Seg
from tests.pipeline.fakes import FakeApi, synthetic_photo
from tests.pipeline.sam_text import line, record, text


def noisy(w=100, h=80):
    return np.random.default_rng(0).integers(0, 256, (h, w, 3), dtype=np.uint8)


def test_blur_changes_only_the_grown_region():
    img = noisy()
    out = evidence.blur(img, [Seg(0, "0", (10, 10, 30, 30), 0.9, np.ones((20, 20), bool))])
    assert not np.array_equal(out[10:30, 10:30], img[10:30, 10:30])
    assert np.array_equal(out[50:, 50:], img[50:, 50:])  # more than BLUR_GROW_PX away from the box
    assert np.array_equal(evidence.blur(img, []), img)


def test_make_evidence_blurs_saves_small_and_attributes(tmp_path):
    photo = tmp_path / "p.jpg"
    src = noisy(2000, 1000)
    cv2.imwrite(str(photo), src)
    person = text([line(0, [record("0", 100, 100, 140, 180, 2000, 1000, 0.4)])])
    api = FakeApi(sam=lambda prompt, media: person if prompt == "person" else "")
    frame = {"image_id": "123", "creator": "filipb", "captured_at": "2025-09-24T07:21:37Z"}
    record_ = evidence.make_evidence(api, photo, frame, tmp_path / "out" / "e.jpg")
    assert record_ == {"image": "e.jpg", "image_id": "123", "creator": "filipb",
                       "captured_at": "2025-09-24T07:21:37Z", "link": "https://www.mapillary.com/app/?pKey=123",
                       "license": "CC BY-SA 4.0", "blurred_regions": 1}
    assert cv2.imread(str(tmp_path / "out" / "e.jpg")).shape[:2] == (800, 1600)
    assert [c[1] for c in api.calls] == ["person", "license plate"]
    # the person region is blurred in the saved photo, not just downscaled
    saved = cv2.imread(str(tmp_path / "out" / "e.jpg")).astype(float)
    plain = cv2.resize(src, (1600, 800), interpolation=cv2.INTER_AREA).astype(float)
    assert saved[90:130, 85:105].std() < plain[90:130, 85:105].std() / 2


def test_an_unreadable_photo_raises_before_any_sam_call(tmp_path):
    photo = tmp_path / "bad.jpg"
    photo.write_bytes(b"not a jpeg")
    api = FakeApi(sam=lambda prompt, media: "")
    with pytest.raises(ValueError):
        evidence.make_evidence(api, photo, {"image_id": "1", "creator": "c", "captured_at": "t"},
                               tmp_path / "e.jpg")
    assert api.calls == []


def test_a_failed_blur_request_stores_nothing(tmp_path):
    photo = synthetic_photo(tmp_path / "p.jpg")

    def sam(prompt, media):
        raise RuntimeError("stream failed")

    with pytest.raises(RuntimeError):
        evidence.make_evidence(FakeApi(sam=sam), photo, {"image_id": "1", "creator": "c", "captured_at": "t"},
                               tmp_path / "e.jpg")
    assert not (tmp_path / "e.jpg").exists()
