import pytest


@pytest.fixture(autouse=True)
def data_in_tmp(tmp_path, monkeypatch):
    """Every eval test writes under its own tmp folder, never into meta hackathon/data."""
    monkeypatch.setenv("THRESHOLD_DATA", str(tmp_path / "data"))
