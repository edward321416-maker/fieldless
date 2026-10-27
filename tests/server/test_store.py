import sqlite3
import time
from contextlib import closing
from pathlib import Path

from pipeline import db
from server.store import DB_NAME, DatasetStore


def no_download(*args, **kwargs):
    raise AssertionError("no download expected")


class FakeApi:
    def __init__(self):
        self.uploads = []

    def upload_folder(self, *, repo_id, repo_type, folder_path, commit_message):
        folder = Path(folder_path)
        with closing(sqlite3.connect(folder / DB_NAME)) as conn:
            scans = conn.execute("SELECT COUNT(*) FROM scan").fetchone()[0]
        files = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
        self.uploads.append({"repo": repo_id, "type": repo_type, "files": files, "scans": scans})


def test_restore_downloads_only_into_a_folder_without_a_database(tmp_path):
    calls = []

    def download(repo_id, **kwargs):
        calls.append((repo_id, kwargs))
        (Path(kwargs["local_dir"]) / DB_NAME).write_bytes(b"")

    store = DatasetStore("me/data", tmp_path / "run", api=FakeApi(), download=download)
    assert store.restore() is True
    assert calls[0][0] == "me/data" and calls[0][1]["repo_type"] == "dataset"
    assert store.restore() is False and len(calls) == 1  # the database is there now


def test_a_failed_restore_still_lets_the_server_start(tmp_path):
    def offline(repo_id, **kwargs):
        raise OSError("offline")

    assert DatasetStore("me/data", tmp_path, api=FakeApi(), download=offline).restore() is False


def test_save_uploads_a_copy_of_the_database_and_the_evidence_only(tmp_path):
    with closing(db.connect(tmp_path / DB_NAME)) as conn:
        db.create_scan(conn, "mapillary", [0, 0, 1, 1])
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "scan1_issue1.jpg").write_bytes(b"jpg")
    (tmp_path / "calls.jsonl").write_text("{}", encoding="utf-8")  # the API log stays on the server
    api = FakeApi()
    DatasetStore("me/data", tmp_path, api=api, download=no_download).save_now()
    assert api.uploads == [{"repo": "me/data", "type": "dataset", "files": ["evidence/scan1_issue1.jpg", DB_NAME],
                            "scans": 1}]


def test_a_burst_of_save_requests_is_one_upload(tmp_path):
    db.connect(tmp_path / DB_NAME).close()
    api = FakeApi()
    store = DatasetStore("me/data", tmp_path, api=api, download=no_download, delay_s=0.2)
    for _ in range(5):
        store.request_save()
    deadline = time.time() + 5
    while not api.uploads and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.5)
    assert len(api.uploads) == 1
