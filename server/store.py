"""Keep the server's data in a private Hugging Face dataset, for hosts whose disk does not survive a restart (a free
Hugging Face Space sleeps after 48 hours without visits and wakes with an empty disk), or in a backup folder such as a
mounted Modal Volume (FolderBackup), where SQLite should not run directly.

On start, the database and evidence photos are restored from the dataset. When a survey starts, as it spends, after it
finishes and after a review, they are uploaded again. The photo cache is not kept: finished scans never read it. The
token comes from HF_TOKEN in the environment (huggingface_hub reads it), never from code. Off unless HF_DATA_REPO is
set.

A backup that cannot be read at start is never written over: restore_failed stops every save until a restart reads
it, because the server's empty database would replace the finished surveys. The worker and the health route look at
it too (server.app): the empty database also knows nothing of what was spent."""
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path

log = logging.getLogger(__name__)

DB_NAME = "threshold.db"
PREVIOUS_DB = f"{DB_NAME}.prev"  # the backup before the last, for a restore that finds the last one broken
GPU_LOG = "calls_glimmer.jsonl"  # Muse Glimmer's calls: kept so the server's GPU total survives a restart
# offline files shown with a scan: the comparison with a city's records (eval) and the call record
# (pipeline/call_stats.py)
SCAN_FILES = ("city_check_scan*.json", "call_stats_scan*.json")
SAVE_DELAY_S = 20.0  # reviews come in bursts; one upload covers a burst
SAVE_RETRY_MAX_S = 300.0  # a failed save is tried again, waiting twice as long each time, up to this
RESTORE_ATTEMPTS = 3
RESTORE_WAIT_S = 2.0
READ_ATTEMPTS = 3  # for one photo or side file, read from the backup at start


def _is_whole(path: Path) -> bool:
    try:
        with closing(sqlite3.connect(path)) as conn:
            return conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    except sqlite3.DatabaseError:
        return False


def _copy_whole(src: Path, dst: Path) -> None:
    """dst is the old file or the whole new one, never part of one."""
    part = dst.with_name(f"{dst.name}.part")
    shutil.copy2(src, part)
    part.replace(dst)


def _copy_changed(src: Path, dst: Path) -> None:
    """A photo's name fixes its content, so one of the same size is left alone. A different size is a copy a restart
    or a save during the survey cut short."""
    if not dst.exists() or dst.stat().st_size != src.stat().st_size:
        _copy_whole(src, dst)


def _link(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:  # a disk without hard links
        shutil.copy2(src, dst)


def _read_in(src: Path, dst: Path, copy=_copy_changed) -> None:
    """A file that still cannot be read after READ_ATTEMPTS is left out: one photo must not cost the site its
    surveys."""
    for attempt in range(READ_ATTEMPTS):
        try:
            return copy(src, dst)
        except OSError:
            if attempt == READ_ATTEMPTS - 1:
                log.exception("could not read %s from the backup", src.name)


class DatasetStore:
    def __init__(self, repo_id: str, run_dir: Path, api=None, download=None, delay_s: float = SAVE_DELAY_S):
        if api is None or download is None:
            from huggingface_hub import HfApi, snapshot_download
            api = api or HfApi()
            download = download or snapshot_download
        self.repo_id = repo_id
        self.run_dir = Path(run_dir)
        self._api, self._download, self._delay = api, download, delay_s
        self._pending = threading.Event()
        self._save_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.restore_failed = False
        self.last_save_error: str | None = None  # the class of the last save's error, for /api/health

    def restore(self) -> bool:
        """Download the dataset into run_dir unless a database is already there. True if one was restored. Never
        raises: a server that cannot read the dataset still starts, empty, with restore_failed set."""
        if (self.run_dir / DB_NAME).exists():
            return False
        self.run_dir.mkdir(parents=True, exist_ok=True)
        for attempt in range(RESTORE_ATTEMPTS):
            try:
                self._download(self.repo_id, repo_type="dataset", local_dir=self.run_dir,
                               allow_patterns=[DB_NAME, GPU_LOG, *SCAN_FILES, "evidence/*"])
                return (self.run_dir / DB_NAME).exists()
            except Exception:
                log.exception("could not restore data from %s", self.repo_id)
            if attempt < RESTORE_ATTEMPTS - 1:
                time.sleep(RESTORE_WAIT_S)
        self.restore_failed = True
        return False

    def health(self) -> dict:
        return {"restore_failed": self.restore_failed, "last_save_error": self.last_save_error}

    def save_now(self) -> None:
        """Upload a consistent copy of the database (SQLite's backup, so a write in progress is never half-copied)
        and the evidence photos. The copy is put together on the data disk, with the photos linked, so a save does not
        copy every photo again. Does nothing after a failed restore."""
        if self.restore_failed:
            log.error("not saving to %s: its data could not be read at start", self.repo_id)
            return
        with self._save_lock, tempfile.TemporaryDirectory(dir=self.run_dir, prefix=".save-") as tmp:
            staging = Path(tmp)
            src = sqlite3.connect(self.run_dir / DB_NAME)
            dst = sqlite3.connect(staging / DB_NAME)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            evidence = self.run_dir / "evidence"
            if evidence.is_dir():
                (staging / "evidence").mkdir()
                for photo in evidence.iterdir():
                    _link(photo, staging / "evidence" / photo.name)
            if (self.run_dir / GPU_LOG).exists():
                shutil.copy2(self.run_dir / GPU_LOG, staging / GPU_LOG)
            for check in (f for pattern in SCAN_FILES for f in self.run_dir.glob(pattern)):
                shutil.copy2(check, staging / check.name)
            self._api.upload_folder(repo_id=self.repo_id, repo_type="dataset", folder_path=staging,
                                    commit_message="Save survey data")

    def request_save(self) -> None:
        """Upload after SAVE_DELAY_S on a background thread. Never raises or blocks the caller."""
        self._pending.set()
        with self._start_lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="dataset-store", daemon=True)
                self._thread.start()

    def flush(self) -> None:
        """Save what is still waiting for its backup, at shutdown; a save under way finishes first. Never raises."""
        with self._save_lock:
            pass
        if not self._pending.is_set():
            return
        self._pending.clear()
        try:
            self.save_now()
        except Exception:
            log.exception("could not save data to %s", self.repo_id)

    def _loop(self) -> None:
        failures = 0
        while True:
            self._pending.wait()
            time.sleep(min(self._delay * 2 ** failures, SAVE_RETRY_MAX_S))
            self._pending.clear()
            try:
                self.save_now()
            except Exception as exc:
                log.exception("could not save data to %s", self.repo_id)
                self.last_save_error = type(exc).__name__
                failures += 1
                self._pending.set()  # what it was to save is not on the backup yet
            else:
                self.last_save_error = None
                failures = 0


class FolderBackup:
    """DatasetStore's api and download for a backup folder: the same restore and save, copied to and from a folder
    (a Modal Volume mount, which commits what is written to it) instead of a Hugging Face dataset."""

    def __init__(self, backup_dir: Path):
        self.backup_dir = Path(backup_dir)

    def download(self, repo_id, *, repo_type, local_dir, allow_patterns):
        """The photos and side files, then the database, last and renamed into place: a read error part way leaves
        no database, so restore() goes again, and a database never arrives ahead of its photos. A database that is not
        whole is passed over for the one before it. Raises when there are databases and none is whole."""
        local = Path(local_dir)
        if (self.backup_dir / "evidence").is_dir():
            (local / "evidence").mkdir(exist_ok=True)
            for photo in (self.backup_dir / "evidence").iterdir():
                _read_in(photo, local / "evidence" / photo.name)
        if (self.backup_dir / GPU_LOG).exists():
            _read_in(self.backup_dir / GPU_LOG, local / GPU_LOG, _copy_whole)
        for check in (f for pattern in SCAN_FILES for f in self.backup_dir.glob(pattern)):
            _read_in(check, local / check.name, _copy_whole)
        found = [self.backup_dir / name for name in (DB_NAME, PREVIOUS_DB) if (self.backup_dir / name).exists()]
        part = local / f"{DB_NAME}.part"
        for database in found:
            shutil.copy2(database, part)
            if _is_whole(part):
                part.replace(local / DB_NAME)
                return
            log.error("%s in the backup is not a whole database", database.name)
            part.unlink()
        if found:
            raise OSError("no whole database in the backup")

    def upload_folder(self, *, repo_id, repo_type, folder_path, commit_message):
        folder = Path(folder_path)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        if (folder / "evidence").is_dir():
            (self.backup_dir / "evidence").mkdir(exist_ok=True)
            for photo in (folder / "evidence").iterdir():
                _copy_changed(photo, self.backup_dir / "evidence" / photo.name)
        if (folder / GPU_LOG).exists():
            _copy_whole(folder / GPU_LOG, self.backup_dir / GPU_LOG)
        for check in (f for pattern in SCAN_FILES for f in folder.glob(pattern)):
            _copy_whole(check, self.backup_dir / check.name)
        current = self.backup_dir / DB_NAME
        part = self.backup_dir / f"{DB_NAME}.part"
        shutil.copy2(folder / DB_NAME, part)
        if current.exists():
            _copy_whole(current, self.backup_dir / PREVIOUS_DB)
        part.replace(current)  # the backup database is never half-written


def folder_store(backup_dir: Path, run_dir: Path) -> DatasetStore:
    backup = FolderBackup(backup_dir)
    return DatasetStore(str(backup_dir), run_dir, api=backup, download=backup.download)
