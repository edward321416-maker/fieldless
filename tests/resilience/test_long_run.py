"""Case 5: disk and memory on a container that runs for days. The spending caps bound the paid work, so what matters is
what grows without being paid for, and what is never given back."""
import tempfile

import pytest

from pipeline import db, sam_output
from pipeline.runner import Scan
from server.jobs import ScanWorker
from server.store import DB_NAME, DatasetStore, FolderBackup
from server.web_files import WholeFiles
from tests.resilience.harness import SEATTLE


@pytest.mark.xfail(strict=True, reason="nothing removes work/scan<N>: each survey leaves its videos (tens of MB each) on "
                                       "the container's disk. Only the $40 cap bounds them, and a survey Meta fails "
                                       "costs nothing, so it does not count toward that bound")
def test_a_finished_surveys_work_folder_is_given_back(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    scan_id = db.create_scan(conn, "mapillary", SEATTLE, "US")

    def run(job_id):
        video = Scan(conn, job_id, None, tmp_path, SEATTLE).work / "checks_0000.mp4"
        video.write_bytes(b"video")

    worker = ScanWorker(tmp_path / "t.db", tmp_path, run=run)
    worker.start()
    worker.submit(scan_id)
    worker.join()
    worker.stop()
    left = list((tmp_path / "work").rglob("*.mp4"))
    conn.close()
    assert left == []


def test_a_backup_leaves_no_temporary_files_even_when_it_fails(tmp_path, monkeypatch):
    run, scratch = tmp_path / "run", tmp_path / "tmp"
    run.mkdir()
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    db.connect(run / DB_NAME).close()
    (run / "evidence").mkdir()
    (run / "evidence" / "scan1_issue1_123.jpg").write_bytes(b"jpg")
    calls = []

    class Failing:
        def upload_folder(self, **kwargs):
            calls.append(kwargs)
            raise OSError("Volume write error")

    with pytest.raises(OSError):
        DatasetStore("volume", run, api=Failing(), download=None).save_now()
    backup = FolderBackup(tmp_path / "volume")
    DatasetStore("volume", run, api=backup, download=backup.download).save_now()
    assert calls and list(scratch.iterdir()) == []


def test_the_web_files_are_kept_once_each(tmp_path):
    (tmp_path / "index.html").write_text("<p>" * 1000, encoding="utf-8")
    files = WholeFiles(directory=tmp_path, html=True)
    scope = {"type": "http", "headers": [], "method": "GET"}
    for _ in range(50):
        path = tmp_path / "index.html"
        files.file_response(path, path.stat(), scope)
    assert len(files._kept) == 1


def test_a_broken_decoder_pool_is_replaced_by_one_pool_and_the_answer_is_still_decoded(monkeypatch):
    made = []

    class BrokenPool:
        def __init__(self):
            made.append(self)

        def submit(self, fn, *args):
            raise RuntimeError("a child process died")

    monkeypatch.setattr(sam_output, "_pool", None)
    monkeypatch.setattr(sam_output, "_new_pool", BrokenPool)
    monkeypatch.setattr(sam_output, "_pool_safe", lambda: True)
    monkeypatch.setattr(sam_output, "PARSE_PROCESSES", 2)
    monkeypatch.setattr(sam_output, "parse", lambda text, video: ["decoded here"])
    for _ in range(3):
        assert sam_output.parse_away("x" * sam_output.POOL_MIN_CHARS, False) == ["decoded here"]
    assert len(made) == 3 and sam_output._pool is None  # never more than one at a time, and the broken one is dropped


def test_only_one_decoder_pool_serves_every_answer(monkeypatch):
    made = []

    class Result:
        def result(self):
            return []

    class Pool:
        def __init__(self):
            made.append(self)

        def submit(self, fn, *args):
            return Result()

    monkeypatch.setattr(sam_output, "_pool", None)
    monkeypatch.setattr(sam_output, "_new_pool", Pool)
    monkeypatch.setattr(sam_output, "_pool_safe", lambda: True)
    monkeypatch.setattr(sam_output, "PARSE_PROCESSES", 2)
    for _ in range(100):
        sam_output.parse_away("x" * sam_output.POOL_MIN_CHARS, False)
    assert len(made) == 1


def test_a_photo_download_leaves_no_part_file_behind(tmp_path, monkeypatch):
    from pipeline import mapillary

    class Reply:
        status_code = 200
        content = b"jpg"

    monkeypatch.setattr(mapillary.httpx, "get", lambda url, **kwargs: Reply())
    dst = tmp_path / "1.jpg"
    assert mapillary.download("https://photo", dst) is True
    assert sorted(p.name for p in tmp_path.iterdir()) == ["1.jpg"]
