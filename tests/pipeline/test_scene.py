import pytest

from pipeline import media, scene
from tests.pipeline.fakes import FakeApi, synthetic_photo


def frames(n, clip, start):
    return [{"idx": start + i, "clip": clip} for i in range(n)]


def test_make_chunks_splits_each_clip_into_60_photo_chunks():
    chunks = scene.make_chunks(frames(100, 0, 0) + frames(30, 1, 100))
    assert [len(c) for c in chunks] == [60, 40, 30]
    assert [c[0]["idx"] for c in chunks] == [0, 60, 100]
    assert {f["clip"] for f in chunks[2]} == {1}


def chunk(idxs, clip=0):
    return scene.Chunk(clip=clip, idxs=idxs, video="v.mp4", spark_video="s.mp4", width=1280, height=960)


def test_to_candidates_maps_photo_numbers_to_frames_and_drops_out_of_range():
    answer = {"entrances": [
        {"t": 0.0, "side": "left", "name": " Cafe ", "category": "cafe", "has_visible_steps": False},
        {"t": 12.4, "side": "right", "name": "명동칼국수", "category": "restaurant", "has_visible_steps": True},
        {"t": 60.0, "side": "left", "name": "x", "category": "y", "has_visible_steps": False}],
        "obstacle_types": ["Bicycle ", "bicycle", "", "traffic cone"]}
    out = scene.to_candidates(answer, chunk(list(range(60, 120))))
    assert out["out_of_range"] == 1
    assert out["entrances"] == [
        {"frame_idx": 60, "clip": 0, "side": "left", "name": "Cafe", "category": "cafe", "has_visible_steps": False},
        {"frame_idx": 72, "clip": 0, "side": "right", "name": "명동칼국수", "category": "restaurant",
         "has_visible_steps": True}]
    assert out["obstacle_types"] == ["bicycle", "traffic cone"]


def test_build_chunk_makes_the_sam_video_and_the_held_numbered_spark_copy(tmp_path):
    photos = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=i) for i in (5, 6, 7)}
    built = scene.build_chunk([{"idx": i, "clip": 2} for i in (5, 6, 7)], photos, tmp_path)
    assert (built.clip, built.idxs, built.width, built.height) == (2, [5, 6, 7], 1280, 960)
    assert media.probe(built.video).duration_s == pytest.approx(3.0, abs=0.2)
    assert media.probe(built.spark_video).duration_s == pytest.approx(6.0, abs=0.3)


def test_scan_chunk_uploads_the_spark_copy_and_deletes_it():
    answer = {"entrances": [{"t": 1, "side": "left", "name": "A", "category": "cafe", "has_visible_steps": False}],
              "obstacle_types": ["bicycle"]}
    api = FakeApi(spark=lambda name, parts: answer)
    out = scene.scan_chunk(api, scene.Chunk(0, [7, 8], "v.mp4", "dir/chunk_0007_spark.mp4", 1280, 960))
    assert out["entrances"][0]["frame_idx"] == 8
    assert api.calls == [("upload", "chunk_0007_spark.mp4"),
                         ("spark_video", "street_scan", "file:chunk_0007_spark.mp4")]
    assert api.deleted == ["file:chunk_0007_spark.mp4"]
