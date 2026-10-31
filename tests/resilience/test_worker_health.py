"""Case 4: the worker thread dies or hangs. Nobody is watching the server, so the app must notice for itself, and
/api/health must say so to anything that polls it."""
import subprocess
import threading
from concurrent.futures import Future

import pytest

from pipeline import media, sam_output
from tests.resilience.harness import SEATTLE, Container, Hanging


@pytest.fixture
def live(tmp_path, monkeypatch):
    container = Container(tmp_path, "disk-1", monkeypatch)
    container.seed_finished_survey()
    return container.boot()


@pytest.mark.xfail(strict=True, reason="/api/health returns ok: true without looking at the worker")
def test_health_shows_a_dead_worker_thread(live):
    before = live.client.get("/api/health").json()
    live.worker.stop()
    assert not live.worker._thread.is_alive()
    assert live.client.get("/api/health").json() != before


@pytest.mark.xfail(strict=True, reason="submit() only puts the job on a queue nothing reads: the visitor gets a 202 and "
                                       "'Waiting for the survey ahead of it to finish' for good")
def test_a_new_survey_is_refused_or_the_thread_comes_back_when_the_worker_is_dead(live):
    live.worker.stop()
    reply = live.client.post("/api/scans", json={"bbox": SEATTLE})
    assert reply.status_code == 503 or live.worker._thread.is_alive()


@pytest.mark.xfail(strict=True, reason="a job has no time limit and no one watches it: health stays the same and every "
                                       "later survey is accepted into a queue that does not move")
def test_health_shows_a_hung_survey(live):
    before = live.client.get("/api/health").json()
    job = Hanging(1.0, live.db_path)
    live.worker._run = job
    live.client.post("/api/scans", json={"bbox": SEATTLE})
    assert job.started.wait(5)
    try:
        assert live.client.get("/api/health").json() != before
    finally:
        job.release()


def test_a_hung_survey_does_not_stop_the_finished_surveys(live):
    job = Hanging(1.0, live.db_path)
    live.worker._run = job
    live.client.post("/api/scans", json={"bbox": SEATTLE})
    assert job.started.wait(5)
    try:
        assert live.client.get("/api/scans/1").json()["status"] == "done"
        assert live.client.get("/api/evidence/scan1_issue1_123.jpg").status_code == 200
        assert live.client.get("/api/scans/1/report").status_code == 200
    finally:
        job.release()


@pytest.mark.xfail(strict=True, reason="pool.submit(...).result() has no timeout: a decoder process that stops answering "
                                       "without dying holds the survey, and the one worker, for good")
def test_a_mask_decoder_that_stops_answering_does_not_hold_the_worker(monkeypatch):
    never = Future()

    class StuckPool:
        def submit(self, fn, *args):
            return never

    monkeypatch.setattr(sam_output, "_pool", StuckPool())
    monkeypatch.setattr(sam_output, "_pool_safe", lambda: True)
    monkeypatch.setattr(sam_output, "PARSE_PROCESSES", 2)
    monkeypatch.setattr(sam_output, "parse", lambda text, video: [])
    caller = threading.Thread(target=sam_output.parse_away, args=("x" * sam_output.POOL_MIN_CHARS, False), daemon=True)
    caller.start()
    caller.join(2)
    stuck = caller.is_alive()
    never.set_result([])
    assert not stuck


@pytest.mark.xfail(strict=True, reason="media._run and media.probe call ffmpeg with no timeout: a stuck ffmpeg holds the "
                                       "survey, and the one worker, for good")
def test_ffmpeg_cannot_hold_the_worker_for_good(monkeypatch, tmp_path):
    seen = {}

    def run(args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr=b"")

    monkeypatch.setattr(media.subprocess, "run", run)
    media._run(["ffmpeg", "-version"])
    assert seen.get("timeout")


def test_a_crashed_job_does_not_stop_the_thread(live):
    """Also tests/server/test_jobs.py: Exception, RunAborted (a BaseException) and a failed crash record."""
    def crash(job_id):
        raise MemoryError("the decoder ran out")

    live.worker._run = crash
    scan_id = live.client.post("/api/scans", json={"bbox": SEATTLE}).json()["id"]
    live.worker.join()
    assert live.worker._thread.is_alive()
    assert live.client.get(f"/api/scans/{scan_id}").json()["status"] == "failed"
    live.worker.stop()
