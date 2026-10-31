"""Case 3: Meta's API, Mapillary, Baseten or the Volume fails. One failure must not stop the worker thread, leave a
survey stuck, or take the finished surveys with it."""
import asyncio
import threading

import httpx
import httpx2
import openai
import pytest

from pipeline import collect, db, item_runs, mapillary, runner
from pipeline.cost import CostMeter
from pipeline.meta_api import ATTEMPTS, MetaApi
from server import jobs
from server.app import create_app
from server.jobs import ScanWorker
from server.settings import Settings
from server.store import DatasetStore
from tests.pipeline.test_runner import fake_frames
from tests.resilience.harness import RUN, SEATTLE, Container, wait_for

REQUEST = httpx2.Request("POST", "https://api.meta.ai/v1/responses")


class Everything:
    """A fake OpenAI client whose every request fails the same way."""

    def __init__(self, error):
        self.error = error
        self.calls = 0

    @property
    def responses(self):
        return self

    @property
    def files(self):
        return self

    def _fail(self, *args, **kwargs):
        self.calls += 1
        raise self.error

    create = stream = delete = _fail


def down_api(monkeypatch, error):
    client = Everything(error)
    monkeypatch.setattr(jobs, "MetaApi", lambda log, meter: MetaApi(log, meter, client=client, sleep=lambda s: None))
    return client


def two_surveys(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.collect, "collect", lambda bbox, max_photos: fake_frames())
    monkeypatch.setattr(runner.entrance_read, "original_photo", collect.photo_path)
    conn = db.connect(tmp_path / "t.db")
    first = db.create_scan(conn, "mapillary", SEATTLE, "US")
    second = db.create_scan(conn, "mapillary", SEATTLE, "US")
    done = []
    worker = ScanWorker(tmp_path / "t.db", tmp_path, on_done=done.append)
    worker.start()
    worker.join()
    worker.stop()
    return conn, first, second, done


@pytest.mark.parametrize("error", [openai.APITimeoutError(request=REQUEST), openai.APIConnectionError(request=REQUEST),
                                   openai.AuthenticationError("expired", response=httpx2.Response(401, request=REQUEST),
                                                              body=None)],
                         ids=["timeouts", "connection errors", "a key that stopped working"])
def test_meta_down_ends_each_survey_as_failed_and_the_worker_goes_on(tmp_path, monkeypatch, error):
    client = down_api(monkeypatch, error)
    conn, first, second, done = two_surveys(tmp_path, monkeypatch)
    for scan_id in (first, second):
        scan = db.get_scan(conn, scan_id)
        assert scan["status"] == "failed" and "requests in a row failed" in scan["error"]
        assert "running" not in scan["stage_status"].values()
    assert done == [first, second] and client.calls > 0
    conn.close()


def test_a_stalled_request_is_tried_again_and_then_given_up_on(tmp_path):
    """A stall surfaces from the SDK as APITimeoutError, before the first byte or part way through a stream."""
    client = Everything(openai.APITimeoutError(request=REQUEST))
    api = MetaApi(tmp_path / "calls.jsonl", CostMeter(1.0), client=client, sleep=lambda s: None)
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"jpg")
    with pytest.raises(openai.APITimeoutError):
        api.sam_image("person", photo)
    assert client.calls == ATTEMPTS


class DownMapillary:
    """httpx.get with Mapillary's hosts unreachable; no retry waits."""

    def __init__(self, monkeypatch):
        self.calls = 0
        monkeypatch.setattr(mapillary.httpx, "get", self)
        monkeypatch.setattr(mapillary.time, "sleep", lambda s: None)

    def __call__(self, url, **kwargs):
        self.calls += 1
        raise httpx.ConnectError("no route to host")


def test_mapillary_down_during_collect_ends_the_survey_as_failed_and_the_worker_goes_on(tmp_path, monkeypatch):
    DownMapillary(monkeypatch)
    conn = db.connect(tmp_path / "t.db")
    first = db.create_scan(conn, "mapillary", SEATTLE, "US")
    second = db.create_scan(conn, "mapillary", SEATTLE, "US")
    worker = ScanWorker(tmp_path / "t.db", tmp_path)
    worker.start()
    worker.join()
    worker.stop()
    for scan_id in (first, second):
        scan = db.get_scan(conn, scan_id)
        assert scan["status"] == "failed" and scan["error"].startswith("MapillaryError")
        assert scan["stage_status"]["collect"].startswith("failed")
        assert "test-not-a-real-key" not in scan["error"]
    conn.close()


@pytest.mark.parametrize("status, error", [("failed", "NoPhotos: no recent pedestrian photos in this area"),
                                           ("failed", "MapillaryError: Mapillary HTTP 503 for images: down"),
                                           ("stopped_cost_cap", "cost cap $3.00: spent $3.0000, reserved $0, next $0.02")])
def test_a_survey_that_ended_with_no_photos_still_opens_every_link_the_screen_shows(tmp_path, monkeypatch, status, error):
    live = Container(tmp_path, "disk-1", monkeypatch)
    with live.conn() as conn:
        scan_id = db.create_scan(conn, "mapillary", SEATTLE, "US")
        db.update_scan(conn, scan_id, status=status, error=error, stage_status={"collect": "failed: x"})
    for path in ("", "/entrances", "/issues", "/walks", "/items", "/report", "/files.zip"):
        assert live.client.get(f"/api/scans/{scan_id}{path}").status_code == 200, path


def scan_with_frames(conn, count: int) -> int:
    scan_id = db.create_scan(conn, "mapillary", SEATTLE, "US")
    db.add_frames(conn, scan_id, [{"idx": i, "clip": 0, "image_id": f"img{i}", "sequence_id": "s", "lat": 47.61,
                                   "lng": -122.32, "compass": 90.0, "captured_at": "2025-09-24T10:00:00Z",
                                   "creator": "c", "width": 160, "height": 120} for i in range(count)])
    db.update_scan(conn, scan_id, status="done")
    return scan_id


def test_mapillary_down_during_an_item_run_ends_it_with_a_sentence(tmp_path, monkeypatch):
    DownMapillary(monkeypatch)
    conn = db.connect(tmp_path / "t.db")
    run_id = db.create_item_run(conn, scan_with_frames(conn, 3), "bench")
    worker = ScanWorker(tmp_path / "t.db", tmp_path, run=lambda s: None, items=object())
    worker.start()
    worker.submit_item(run_id)
    worker.join()
    worker.stop()
    run = db.item_run(conn, run_id)
    conn.close()
    assert (run["status"], run["error"]) == ("failed", "The survey's photos could not be downloaded from Mapillary")


@pytest.mark.xfail(strict=True, reason="scan_photos asks for every photo with no early stop. With Mapillary stalled, "
                                       "each photo takes about 5 minutes of timeouts and retries, 16 at a time: a "
                                       "400-photo survey holds the run, and the one worker, for about two hours")
def test_mapillary_down_during_an_item_run_is_given_up_on_soon(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    scan_id = scan_with_frames(conn, 400)
    asked = []

    def unavailable(rec):
        asked.append(rec["id"])

    item_runs.scan_photos(conn, scan_id, fetch=unavailable, workers=1)
    conn.close()
    assert len(asked) <= 32


def test_a_second_opinion_that_fails_changes_no_row(tmp_path):
    from pipeline import second_opinion
    from tests.pipeline.test_second_opinion import CHECK, row

    class Failing:
        model = second_opinion.MODEL

        def answer(self, check, photo, box=None):
            raise openai.APIConnectionError(request=REQUEST)

    (out,) = second_opinion.apply(Failing(), CHECK, [row("no")], {4: tmp_path / "4.jpg"}, 1)
    assert out["object_label"] == "no" and "error" in out["model_outputs"]["second_opinion"]


def test_a_server_without_a_baseten_key_runs_surveys_without_a_second_opinion(tmp_path):
    from pipeline import second_opinion
    assert second_opinion.from_env(tmp_path) is None  # no_real_keys removed BASETEN_API_KEY


def backup_failing(times: int, tmp_path):
    calls = []

    def upload_folder(**kwargs):
        calls.append(kwargs)
        if len(calls) <= times:
            raise OSError("Volume write error")

    return calls, DatasetStore("volume", tmp_path, delay_s=0.01,
                               api=type("Api", (), {"upload_folder": staticmethod(upload_folder)}), download=None)


def test_a_failed_backup_does_not_touch_the_worker(tmp_path):
    db.connect(tmp_path / "threshold.db").close()
    calls, store = backup_failing(99, tmp_path)
    ran = []
    worker = ScanWorker(tmp_path / "threshold.db", tmp_path, run=ran.append, on_done=lambda _: store.request_save())
    worker.start()
    worker.submit(1)
    worker.submit(2)
    worker.join()
    assert wait_for(lambda: calls)
    worker.submit(3)
    worker.join()
    worker.stop()
    assert ran == [1, 2, 3]


@pytest.mark.xfail(strict=True, reason="_loop clears the request before it saves and only logs a failure: one Volume "
                                       "write error leaves the finished survey off the Volume until something else "
                                       "asks for a save, and the next restart loses it")
def test_a_failed_backup_is_tried_again(tmp_path):
    db.connect(tmp_path / "threshold.db").close()
    calls, store = backup_failing(1, tmp_path)
    store.request_save()
    assert wait_for(lambda: len(calls) >= 2, 1.0)


@pytest.mark.xfail(strict=True, reason="/api/health says the same thing whether or not the last backup worked")
def test_a_failed_backup_shows_in_health(tmp_path, monkeypatch):
    live = Container(tmp_path, "disk-1", monkeypatch, delay_s=0.01)
    live.seed_finished_survey()
    before = live.client.get("/api/health").json()
    uploads = []

    def fail(**kwargs):
        uploads.append(1)
        raise OSError("Volume write error")

    live.store._api = type("Api", (), {"upload_folder": staticmethod(fail)})
    live.store.request_save()
    assert wait_for(lambda: uploads)
    assert live.client.get("/api/health").json() != before


class FakeWorker:
    def start(self):
        pass

    def stop(self):
        pass


@pytest.mark.xfail(strict=True, reason="tiles, previews and every other sync route share anyio's 40 threads. Each stuck "
                                       "tile holds one for up to 115 s (5 tries of 20 s), and a failed tile is not "
                                       "kept, so a Mapillary stall stops the finished surveys, /api/health and the web "
                                       "files as well")
def test_a_mapillary_stall_on_coverage_tiles_does_not_stop_the_finished_surveys(tmp_path, monkeypatch):
    monkeypatch.setenv("THRESHOLD_DATA", str(tmp_path / "disk"))
    entered, release = [], threading.Event()

    def stuck_tile(z, x, y):
        entered.append((z, x, y))
        release.wait(20)
        raise mapillary.MapillaryError("stalled")

    app = create_app(Settings(run_name=RUN, static_dir=tmp_path / "no-web"), worker=FakeWorker(), tile=stuck_tile)

    async def scenario() -> int | None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                     base_url="http://test") as client:
            stuck = [asyncio.create_task(client.get(f"/api/mapillary/tiles/13/{x}/0")) for x in range(40)]
            try:
                while len(entered) < 40:
                    await asyncio.sleep(0.01)
                try:
                    return (await asyncio.wait_for(client.get("/api/demo"), 2)).status_code
                except TimeoutError:
                    return None
            finally:
                release.set()
                await asyncio.gather(*stuck, return_exceptions=True)

    assert asyncio.run(scenario()) == 200
