from contextlib import contextmanager

import pytest

from pipeline import sidewalk, survey_items
from pipeline.cost import CostCapReached
from pipeline.scene import Chunk
from tests.pipeline.fakes import FakeApi, synthetic_photo
from tests.pipeline.sam_text import line, record, text


def test_sampled_takes_every_nth_photo_of_the_clip():
    assert sidewalk.sampled(list(range(10, 20)), 2) == [10, 12, 14, 16, 18]
    assert sidewalk.sampled([3, 4], 1) == [3, 4]


def test_obstacle_labels_start_with_the_standards_objects_and_are_capped():
    kr = survey_items.load("KR").path
    labels = sidewalk.obstacle_labels(["Traffic cone", " bicycle", "", "plastic stool", "Bollard", "a", "b", "c"], kr)
    assert labels == ["electric scooter", "bicycle", "bollard", "pole", "traffic cone", "plastic stool", "a", "b"]
    us = survey_items.load("US").path
    assert sidewalk.obstacle_labels(["utility pole"], us) == [
        "sandwich board sign", "traffic cone", "electric scooter", "cafe table", "utility pole"]


def test_surface_segs_marks_failed_photos_and_keeps_the_rest(tmp_path):
    photos = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=i) for i in range(3)}
    answer = text([line(0, [record("0", 0, 100, 160, 120, 160, 120, 0.9)])])

    def sam(prompt, media):
        assert prompt == "sidewalk"
        if media.name == "1.jpg":
            raise RuntimeError("stream failed")
        return answer if media.name == "0.jpg" else ""

    out = sidewalk.surface_segs(FakeApi(sam=sam), "sidewalk", [0, 1, 2], photos, workers=2)
    assert [s.box for s in out[0]] == [(0, 100, 160, 120)]
    assert out[1] is None and out[2] == []


def test_surface_segs_lets_the_cost_cap_stop_the_run(tmp_path):
    photos = {0: synthetic_photo(tmp_path / "0.jpg")}

    def sam(prompt, media):
        raise CostCapReached("cap")

    with pytest.raises(CostCapReached):
        sidewalk.surface_segs(FakeApi(sam=sam), "yellow tactile paving", [0], photos, workers=1)


def test_obstacle_segs_uploads_the_chunk_once_and_maps_frames_to_idx():
    answer = text([line(0, [record("0", 0, 0, 8, 8, 1280, 960, 0.9)]),
                   line(2, [record("0", 8, 8, 16, 16, 1280, 960, 0.9)])])

    def sam(prompt, media):
        if prompt == "electric scooter":
            raise RuntimeError("stream failed")
        return answer if prompt == "bicycle" else ""

    api = FakeApi(sam=sam)
    chunk = Chunk(0, [10, 11, 12], "dir/chunk_0010.mp4", "s.mp4", 1280, 960)
    out = sidewalk.obstacle_segs(api, chunk, ["electric scooter", "bicycle", "traffic cone"])
    assert out["electric scooter"] is None
    assert sorted(out["bicycle"]) == [10, 12]
    assert out["traffic cone"] == {}
    assert [c for c in api.calls if c[0] == "upload"] == [("upload", "chunk_0010.mp4")]
    assert api.deleted == ["file:chunk_0010.mp4"]


class UploadFailsApi(FakeApi):
    @contextmanager
    def uploaded(self, path):
        raise RuntimeError("upload failed")
        yield


def test_obstacle_segs_marks_every_label_unavailable_when_the_upload_fails():
    api = UploadFailsApi()
    chunk = Chunk(0, [10, 11, 12], "dir/chunk_0010.mp4", "s.mp4", 1280, 960)
    out = sidewalk.obstacle_segs(api, chunk, ["bicycle", "traffic cone"])
    assert out == {"bicycle": None, "traffic cone": None}


def test_obstacle_segs_lets_the_cost_cap_stop_the_run_and_still_deletes_the_upload():
    def sam(prompt, media):
        raise CostCapReached("cap")

    api = FakeApi(sam=sam)
    chunk = Chunk(0, [10, 11, 12], "dir/chunk_0010.mp4", "s.mp4", 1280, 960)
    with pytest.raises(CostCapReached):
        sidewalk.obstacle_segs(api, chunk, ["bicycle"])
    assert api.deleted == ["file:chunk_0010.mp4"]
