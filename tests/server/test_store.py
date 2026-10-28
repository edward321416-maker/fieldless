import sqlite3
import time
from contextlib import closing
from pathlib import Path

from pipeline import db
from server.store import DB_NAME, DatasetStore, FolderBackup, folder_store


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


def test_a_backup_folder_round_trip_keeps_the_database_and_evidence(tmp_path):
    run, backup = tmp_path / "run", tmp_path / "volume"
    run.mkdir()
    with closing(db.connect(run / DB_NAME)) as conn:
        db.create_scan(conn, "mapillary", [0, 0, 1, 1])
    (run / "evidence").mkdir()
    (run / "evidence" / "scan1_issue1.jpg").write_bytes(b"jpg")
    (run / "calls.jsonl").write_text("{}", encoding="utf-8")
    folder_store(backup, run).save_now()
    assert sorted(p.relative_to(backup).as_posix() for p in backup.rglob("*") if p.is_file()) == [
        "evidence/scan1_issue1.jpg", DB_NAME]
    fresh = tmp_path / "fresh"  # a new container: empty local disk, the same volume
    assert folder_store(backup, fresh).restore() is True
    with closing(db.connect(fresh / DB_NAME)) as conn:
        assert len(db.list_scans(conn)) == 1
    assert (fresh / "evidence" / "scan1_issue1.jpg").read_bytes() == b"jpg"


def test_an_empty_backup_folder_restores_nothing(tmp_path):
    assert folder_store(tmp_path / "volume", tmp_path / "run").restore() is False


def test_a_save_replaces_the_backup_database_whole(tmp_path):
    run, backup = tmp_path / "run", tmp_path / "volume"
    run.mkdir()
    backup.mkdir()
    (backup / DB_NAME).write_bytes(b"old")
    db.connect(run / DB_NAME).close()
    FolderBackup(backup).upload_folder(repo_id=str(backup), repo_type="dataset", folder_path=run, commit_message="x")
    assert (backup / DB_NAME).read_bytes() == (run / DB_NAME).read_bytes()
    assert not list(backup.glob("*.part"))


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
