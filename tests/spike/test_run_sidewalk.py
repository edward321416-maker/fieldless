import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from spike import config, run_sidewalk as rs, video
from spike import meta_client as mc

CLIP = "street_b.mp4"


def box_lines(frames: list[int]) -> str:
    return "\n".join(f"<{f}f>0<|box;x1=10;y1=20;x2=110;y2=220;w=320;h=334;c=0.91|>" for f in frames)


def chunk_start(file_id: str) -> int:
    # upload names are "<stem>_<start>_side", so the start is the second-to-last token
    return int(file_id.split("_")[-2])


@pytest.fixture
def fake_io(monkeypatch, tmp_path):
    """Offline stand-ins for ffmpeg, the Meta upload/SAM/delete calls, and a temp truth file."""
    out = tmp_path / "out"
    out.mkdir()
    clips = tmp_path / "clips"
    clips.mkdir()
    monkeypatch.setattr(config, "OUT", out)
    monkeypatch.setattr(config, "CLIPS", clips)
    state = {"duration": 20.0, "respond": lambda concept, file_id: "", "sam_calls": [],
             "uploaded": [], "deleted": []}

    def upload(path):
        file_id = f"file:{Path(path).stem}"
        state["uploaded"].append(file_id)
        return file_id

    monkeypatch.setattr(video, "probe", lambda path: SimpleNamespace(duration_s=state["duration"]))
    monkeypatch.setattr(video, "cut", lambda src, dst, start, end: Path(dst))
    monkeypatch.setattr(mc, "upload", upload)
    monkeypatch.setattr(mc, "video_part", lambda file_id: {"type": "input_video", "file_id": file_id})

    def sam(concept, media, billed_frames):
        state["sam_calls"].append((concept, media["file_id"]))
        return state["respond"](concept, media["file_id"])

    monkeypatch.setattr(mc, "sam", sam)
    monkeypatch.setattr(mc, "delete_file", lambda file_id: state["deleted"].append(file_id))
    return state


def write_truth(clip: dict) -> None:
    (config.CLIPS / "truth.json").write_text(json.dumps({"clips": {CLIP: clip}}), encoding="utf-8")


def read_report() -> dict:
    return json.loads((config.OUT / "sidewalk.json").read_text(encoding="utf-8"))[CLIP]


def test_failed_sam_request_is_an_error_entry_not_a_miss(fake_io):
    fake_io["duration"] = 120.0  # two 60-photo chunks

    def respond(concept, file_id):
        if concept == "bicycle" and chunk_start(file_id) == 60:
            raise RuntimeError("boom")
        if concept == "bicycle":
            return box_lines([11])  # photo 11, inside the first event's window
        return ""

    fake_io["respond"] = respond
    write_truth({
        "entrances": [],
        "tactile_ranges": [[0, 119]],
        "obstacles": [
            {"t_start": 10, "t_end": 12, "label": "bicycle", "on_tactile": False},
            {"t_start": 70, "t_end": 75, "label": "bicycle", "on_tactile": True},
        ],
    })

    rs.main()
    rep = read_report()

    early, late = rep["events"]
    assert early["detected"] is True and early["scored"] is True
    # The window overlaps the failed chunk, so the event is unscored: not detected, not a miss.
    assert late["detected"] is None and late["scored"] is False
    assert rep["errors"] == [
        {"stage": "obstacle", "item": "bicycle", "chunk_start": 60.0, "error": "RuntimeError: boom"}
    ]
    assert rep["coverage_counts"]["tactile paving"] == {"scored": 120, "failed": 0}
    assert fake_io["uploaded"]
    assert set(fake_io["deleted"]) == set(fake_io["uploaded"])  # every upload is deleted


def test_tactile_coverage_counts_only_photos_inside_truth_ranges(fake_io):
    fake_io["duration"] = 20.0
    # Hits on photos 0-4 (inside the range) and 15-19 (outside it).
    fake_io["respond"] = lambda concept, file_id: box_lines([0, 1, 2, 3, 4, 15, 16, 17, 18, 19]) \
        if concept in rs.TACTILE_PHRASES else ""
    write_truth({"entrances": [], "tactile_ranges": [[0, 9]], "obstacles": []})

    rs.main()
    rep = read_report()

    assert rep["coverage"]["tactile paving"] == 0.5  # 5 of the 10 photos in 0..9
    assert rep["coverage_counts"]["tactile paving"] == {"scored": 10, "failed": 0}
    assert rep["best_tactile_phrase"] in rs.TACTILE_PHRASES


def test_obstacle_inside_its_truth_window_with_detection_counts_as_detected(fake_io):
    fake_io["duration"] = 20.0
    fake_io["respond"] = lambda concept, file_id: box_lines([5, 6, 7]) if concept == "electric scooter" else ""
    write_truth({
        "entrances": [],
        "tactile_ranges": [],
        "obstacles": [{"t_start": 5, "t_end": 8, "label": "electric scooter", "on_tactile": True}],
    })

    rs.main()
    rep = read_report()

    (event,) = rep["events"]
    assert event["detected"] is True and event["scored"] is True
    assert event["max_consecutive_photos"] == 3
    assert rep["coverage"] == {p: None for p in rs.TACTILE_PHRASES}


def test_hit_in_second_chunk_maps_to_photo_offset_by_chunk_start(fake_io):
    # <5f> inside the second 60-photo chunk is photo 65, not photo 5.
    fake_io["duration"] = 120.0

    def respond(concept, file_id):
        if chunk_start(file_id) != 60:
            return ""
        if concept == "bicycle":
            return box_lines([5])
        if concept in rs.TACTILE_PHRASES:
            return box_lines([5])
        return ""

    fake_io["respond"] = respond
    write_truth({
        "entrances": [],
        "tactile_ranges": [[60, 70]],
        "obstacles": [{"t_start": 64, "t_end": 66, "label": "bicycle", "on_tactile": True}],
    })

    rs.main()
    rep = read_report()

    (event,) = rep["events"]
    assert event["detected"] is True  # window 63..67 contains photo 65 only if the offset is applied
    assert rep["coverage_any_counts"] == {"scored": 11, "failed": 0}
    assert rep["coverage_any_phrase"] == pytest.approx(1 / 11)  # only photo 65 of 60..70 is hit


def test_any_phrase_coverage_is_the_union_of_phrases(fake_io):
    fake_io["duration"] = 20.0

    def respond(concept, file_id):
        if concept == "tactile paving":
            return box_lines([0, 2, 4, 6, 8])
        if concept == "yellow tactile paving":
            return box_lines([1, 3, 5, 7, 9])
        return ""

    fake_io["respond"] = respond
    write_truth({"entrances": [], "tactile_ranges": [[0, 9]], "obstacles": []})

    rs.main()
    rep = read_report()

    assert rep["coverage"]["tactile paving"] == 0.5
    assert rep["coverage"]["yellow tactile paving"] == 0.5
    assert rep["coverage_any_phrase"] == 1.0  # union is higher than either phrase alone
    assert rep["coverage_any_counts"] == {"scored": 10, "failed": 0}


def test_any_phrase_counts_photos_failed_when_no_phrase_succeeded(fake_io):
    fake_io["duration"] = 120.0

    def respond(concept, file_id):
        if chunk_start(file_id) == 60 and concept in rs.TACTILE_PHRASES:
            raise RuntimeError("down")
        return ""

    fake_io["respond"] = respond
    write_truth({"entrances": [], "tactile_ranges": [[0, 119]], "obstacles": []})

    rs.main()
    rep = read_report()

    assert rep["coverage_any_counts"] == {"scored": 60, "failed": 60}
    assert rep["coverage_any_phrase"] == 0.0


def test_overlap_is_none_when_best_phrase_failed_over_the_event(fake_io):
    fake_io["duration"] = 120.0

    def respond(concept, file_id):
        if concept == "tactile paving":
            if chunk_start(file_id) == 60:
                raise RuntimeError("down")
            return box_lines([10])
        if concept == "bicycle" and chunk_start(file_id) == 60:
            return box_lines([9])  # inside the event window 69..76
        return ""

    fake_io["respond"] = respond
    write_truth({
        "entrances": [],
        "tactile_ranges": [[0, 119]],
        "obstacles": [{"t_start": 70, "t_end": 75, "label": "bicycle", "on_tactile": True}],
    })

    rs.main()
    rep = read_report()

    assert rep["best_tactile_phrase"] == "tactile paving"
    (event,) = rep["events"]
    assert event["detected"] is True
    assert event["overlap_detected"] is None  # best phrase failed over this window: unknown, not False
