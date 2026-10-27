"""Keep the server's data in a private Hugging Face dataset, for hosts whose disk does not survive a restart (a free
Hugging Face Space sleeps after 48 hours without visits and wakes with an empty disk).

On start, the database and evidence photos are restored from the dataset. After a scan finishes or a review is saved,
they are uploaded again. The photo cache is not kept: finished scans never read it. The token comes from HF_TOKEN in
the environment (huggingface_hub reads it), never from code. Off unless HF_DATA_REPO is set."""
import logging
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

DB_NAME = "threshold.db"
SAVE_DELAY_S = 20.0  # reviews come in bursts; one upload covers a burst


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

    def restore(self) -> bool:
        """Download the dataset into run_dir unless a database is already there. True if one was restored. Never
        raises: a server that cannot reach the dataset still starts, empty."""
        if (self.run_dir / DB_NAME).exists():
            return False
        self.run_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._download(self.repo_id, repo_type="dataset", local_dir=self.run_dir,
                           allow_patterns=[DB_NAME, "evidence/*"])
        except Exception:
            log.exception("could not restore data from %s", self.repo_id)
            return False
        return (self.run_dir / DB_NAME).exists()

    def save_now(self) -> None:
        """Upload a consistent copy of the database (SQLite's backup, so a write in progress is never half-copied)
        and the evidence photos."""
        with self._save_lock, tempfile.TemporaryDirectory() as tmp:
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
                shutil.copytree(evidence, staging / "evidence")
            self._api.upload_folder(repo_id=self.repo_id, repo_type="dataset", folder_path=staging,
                                    commit_message="Save survey data")

    def request_save(self) -> None:
        """Upload after SAVE_DELAY_S on a background thread. Never raises or blocks the caller."""
        self._pending.set()
        with self._start_lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="dataset-store", daemon=True)
                self._thread.start()

    def _loop(self) -> None:
        while True:
            self._pending.wait()
            time.sleep(self._delay)
            self._pending.clear()
            try:
                self.save_now()
            except Exception:
                log.exception("could not save data to %s", self.repo_id)
