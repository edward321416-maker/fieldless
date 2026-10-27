import pytest


@pytest.fixture(autouse=True)
def data_in_tmp(tmp_path, monkeypatch):
    """Every server test keeps its data under its own tmp folder."""
    monkeypatch.setenv("THRESHOLD_DATA", str(tmp_path / "data"))
    for name in ("INVITE_CODE", "IP_HASH_SALT", "SCANS_PER_IP_PER_DAY", "DEMO_SCANS", "THRESHOLD_RUN"):
        monkeypatch.delenv(name, raising=False)
