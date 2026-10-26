import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from spike import config, run_sam_stairs as rss, video
from spike import meta_client as mc
from spike.truth import ClipTruth, Entrance

HIT = "<0f>0<|box;x1=10;y1=20;x2=110;y2=220;w=320;h=334;c=0.91|><|mask;x=0;y=0;c=0.88;data=8,8,~zz|>"
HIT_AT_2F = "<2f>0<|box;x1=10;y1=20;x2=110;y2=220;w=320;h=334;c=0.91|><|mask;x=0;y=0;c=0.88;data=8,8,~zz|>"


def default_respond(concept, file_id, state):
    if file_id in state["fail_sam_for"]:
        raise RuntimeError("400 bad request")
    return HIT if concept in ("stairs", "steps") else ""


@pytest.fixture
def fake_io(monkeypatch, tmp_path):
    """Offline stand-ins for ffmpeg, the Meta upload/SAM/delete calls and the truth file."""
    monkeypatch.setattr(config, "OUT", tmp_path)
    state = {
        "uploads": [], "deleted": [], "sam_calls": [], "cut_calls": [],
        "fail_sam_for": set(), "fail_cut_for": set(),
        "entrances": [(10.0, 2), (20.0, 2), (30.0, 2)],  # (t, step_count)
    }
    state["respond"] = lambda concept, file_id: default_respond(concept, file_id, state)

    def cut(src, dst, start, end):
        index = int(Path(dst).stem.rsplit("_e", 1)[1])
        state["cut_calls"].append((start, end))
        if index in state["fail_cut_for"]:
            raise RuntimeError("ffmpeg failed (exit 1)")
        return Path(dst)

    def upload(path):
        state["uploads"].append(Path(path).name)
        return Path(path).name

    def sam(concept, media, billed_frames):
        state["sam_calls"].append((concept, media["file_id"], billed_frames))
        return state["respond"](concept, media["file_id"])

    def load_truth():
        return [ClipTruth(
            file="a.mp4",
            entrances=[Entrance(t=t, name=f"door{i}", step_count=s, has_ramp=False, door_type="glass")
                       for i, (t, s) in enumerate(state["entrances"])],
            tactile_ranges=[],
            obstacles=[],
        )]

    monkeypatch.setattr(video, "cut", cut)
    monkeypatch.setattr(video, "probe", lambda path: SimpleNamespace(duration_s=5.0))
    monkeypatch.setattr(mc, "upload", upload)
    monkeypatch.setattr(mc, "sam", sam)
    monkeypatch.setattr(mc, "delete_file", lambda file_id: state["deleted"].append(file_id))
    monkeypatch.setattr(rss, "load_truth", load_truth)
    return state


def read_out() -> dict:
    return json.loads((config.OUT / "sam_stairs.json").read_text(encoding="utf-8"))


def test_failing_entrance_keeps_other_results_and_records_error(fake_io):
    fake_io["fail_sam_for"] = {"a_e1.mp4"}

    rss.main()

    out = read_out()
    rows = out["rows"]
    assert [r["index"] for r in rows] == [0, 1, 2]
    assert rows[0]["phrases"]["stairs"]["detected"] is True
    assert rows[2]["phrases"]["steps"]["detected"] is True
    assert "error" not in rows[0] and "error" not in rows[2]
    for phrase in rss.PHRASES:
        assert rows[1]["phrases"][phrase] == {"error": "RuntimeError: 400 bad request"}
    assert out["rates"]["stairs"] == 1.0
    assert out["rates"]["steps"] == 1.0
    assert out["rates"]["staircase"] == 0.0
    assert out["counts"]["stairs"] == {"scored": 2, "failed": 1}
    assert out["best_phrase"] in ("stairs", "steps")
    # the window upload is deleted even when its requests fail
    assert sorted(fake_io["deleted"]) == sorted(fake_io["uploads"]) == ["a_e0.mp4", "a_e1.mp4", "a_e2.mp4"]
    assert all(billed == 50 for _, _, billed in fake_io["sam_calls"])


def test_failed_window_cut_is_recorded_and_others_continue(fake_io):
    fake_io["fail_cut_for"] = {0}

    rss.main()

    out = read_out()
    rows = out["rows"]
    assert rows[0]["phrases"] == {}
    assert rows[0]["error"] == "RuntimeError: ffmpeg failed (exit 1)"
    assert rows[1]["phrases"]["stairs"]["detected"] is True
    assert rows[2]["phrases"]["stairs"]["detected"] is True
    assert out["rates"]["stairs"] == 1.0
    assert out["counts"]["stairs"] == {"scored": 2, "failed": 1}  # a failed window counts as failed
    assert sorted(fake_io["deleted"]) == sorted(fake_io["uploads"]) == ["a_e1.mp4", "a_e2.mp4"]


def test_window_math_and_best_frame_time(fake_io):
    fake_io["entrances"] = [(10.0, 2), (1.0, 2)]
    fake_io["respond"] = lambda concept, file_id: HIT_AT_2F if concept == "stairs" else ""

    rss.main()

    rows = read_out()["rows"]
    assert fake_io["cut_calls"] == [(8.0, 13.0), (0.0, 4.0)]
    assert rows[0]["window_start"] == 8.0
    assert rows[0]["phrases"]["stairs"]["best_frame_t"] == rows[0]["window_start"] + 2 == 10.0
    assert rows[1]["window_start"] == 0.0
    assert rows[1]["phrases"]["stairs"]["best_frame_t"] == 2.0


def test_rates_exclude_unstepped_entrances(fake_io):
    fake_io["entrances"] = [(10.0, 2), (20.0, 0)]  # second entrance has no steps
    fake_io["respond"] = lambda concept, file_id: HIT if (concept == "stairs" and file_id == "a_e1.mp4") else ""

    rss.main()

    out = read_out()
    assert out["rows"][1]["phrases"]["stairs"]["detected"] is True  # still recorded
    assert out["rates"]["stairs"] == 0.0  # but not counted: only the stepped entrance scored, and it did not detect
    assert out["counts"]["stairs"] == {"scored": 1, "failed": 0}


def test_phrase_scored_on_fewer_entrances_cannot_be_best(fake_io):
    # "entrance steps" is scored on 1 stepped entrance (detects there), "stairs" on 3 (detects on 2).
    def respond(concept, file_id):
        if concept == "entrance steps":
            if file_id != "a_e0.mp4":
                raise RuntimeError("400 bad request")
            return HIT
        if concept == "stairs":
            return "" if file_id == "a_e2.mp4" else HIT
        return ""

    fake_io["respond"] = respond

    rss.main()

    out = read_out()
    assert out["counts"]["entrance steps"] == {"scored": 1, "failed": 2}
    assert out["rates"]["entrance steps"] == 1.0
    assert out["counts"]["stairs"]["scored"] == 3
    assert out["best_phrase"] == "stairs"


def test_cp949_console_does_not_crash_the_run(fake_io, monkeypatch):
    # A piped stdout on this machine is cp949; 'Café' used to raise UnicodeEncodeError mid-run.
    fake_stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp949")
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    monkeypatch.setattr(rss, "load_truth", lambda: [ClipTruth(
        file="a.mp4",
        entrances=[Entrance(t=10.0, name="Café ⚪", step_count=2, has_ramp=False, door_type="swing")],
        tactile_ranges=[], obstacles=[])])

    rss.main()

    fake_stdout.flush()
    assert "Café ⚪" in fake_stdout.buffer.getvalue().decode("utf-8")
    assert read_out()["rows"][0]["phrases"]["stairs"]["detected"] is True


def test_results_are_saved_before_each_entrance_is_printed(fake_io, monkeypatch):
    printed_rows = []

    def fake_print(*args, **kwargs):
        printed_rows.append(len(read_out()["rows"]))  # the file already holds the row being printed

    monkeypatch.setattr("builtins.print", fake_print)
    rss.main()
    assert printed_rows[:3] == [1, 2, 3]


def test_no_stepped_entrance_scored_gives_none_and_message(fake_io, capsys):
    fake_io["fail_sam_for"] = {"a_e0.mp4", "a_e1.mp4", "a_e2.mp4"}

    rss.main()

    out = read_out()
    assert out["best_phrase"] is None
    assert out["rates"] == {}
    assert all(c["scored"] == 0 for c in out["counts"].values())
    assert "no stepped entrances scored" in capsys.readouterr().out
