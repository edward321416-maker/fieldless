import pytest

from pipeline import config, entrance_read as er
from tests.pipeline.fakes import FakeApi, synthetic_photo
from tests.pipeline.sam_text import line, record, text


def reading(steps=0, sill=False, conf=0.9, box=(0, 0, 0, 0)):
    return {"step_count": steps, "has_step_or_sill": sill,
            "step_box": dict(zip(("x1", "y1", "x2", "y2"), box)), "confidence": conf, "notes": ""}


@pytest.mark.parametrize("stairs, r, count, reasons", [
    (True, reading(3), 3, []),
    (True, reading(1, sill=True), 2, ["count_mismatch"]),
    (False, reading(2, sill=True), 2, ["count_mismatch"]),
    (False, reading(0, sill=True), 1, ["possible_sill"]),
    (False, reading(1, sill=False), 1, ["possible_sill"]),
    (False, reading(0), 0, []),
    (False, reading(0, conf=0.5), 0, ["low_confidence"]),
])
def test_measure(stairs, r, count, reasons):
    m = er.measure(stairs, r)
    assert m == {"step_count": count, "review_reasons": reasons,
                 "review_status": "needs_review" if reasons else "auto"}


def test_the_reading_asks_only_for_steps_and_sill():
    assert er.READ_SCHEMA["required"] == ["step_count", "has_step_or_sill", "step_box", "confidence", "notes"]
    assert set(er.READ_SCHEMA["properties"]) == set(er.READ_SCHEMA["required"])


def test_read_entrance_sends_the_photo_with_the_reading_schema(tmp_path):
    seen = {}

    def spark(name, parts):
        seen["name"], seen["parts"] = name, parts
        return reading(2)

    assert er.read_entrance(FakeApi(spark=spark), synthetic_photo(tmp_path / "p.jpg"))["step_count"] == 2
    assert seen["name"] == "entrance_read"
    assert seen["parts"][0]["type"] == "input_text" and seen["parts"][1]["type"] == "input_image"


def test_outline_sill_prompts_sam_with_the_spark_box_in_pixels(tmp_path):
    photo = synthetic_photo(tmp_path / "p.jpg", size=(200, 100))
    prompts = []

    def sam(prompt, media):
        prompts.append(prompt)
        return text([line(0, [record("1", 20, 50, 121, 91, 200, 100, 0.7)])])

    out = er.outline_sill(FakeApi(sam=sam), photo, reading(1, sill=True, box=(100, 500, 600, 900)))
    assert prompts == ["<0f>1<|box;x1=20;y1=50;x2=120;y2=90;w=200;h=100|>"]
    assert out == {"box": [20, 50, 120, 90], "conf": pytest.approx(0.7), "masked": True}


def test_outline_sill_without_a_sill_or_a_box_asks_nothing(tmp_path):
    photo = synthetic_photo(tmp_path / "p.jpg")
    api = FakeApi()
    assert er.outline_sill(api, photo, reading(0, sill=False)) is None
    assert er.outline_sill(api, photo, reading(1, sill=True, box=(0, 0, 0, 0))) is None
    assert api.calls == []


def test_original_photo_uses_the_cache(tmp_path, monkeypatch):
    cached = synthetic_photo(config.photo_cache() / "original" / "abc.jpg")
    monkeypatch.setattr(er.mapillary, "original_image", lambda image_id, dst: pytest.fail("cached"))
    assert er.original_photo("abc") == cached


def test_original_photo_downloads_when_missing(monkeypatch):
    def fake_original(image_id, dst):
        return synthetic_photo(dst)

    monkeypatch.setattr(er.mapillary, "original_image", fake_original)
    path = er.original_photo("xyz")
    assert path == config.photo_cache() / "original" / "xyz.jpg" and path.exists()
