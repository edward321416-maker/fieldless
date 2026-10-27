import pytest

FAKE_KEY = "test-not-a-real-key"


@pytest.fixture(autouse=True)
def data_in_tmp(tmp_path, monkeypatch):
    """Every pipeline test writes run data under its own tmp folder, never into meta hackathon/data."""
    monkeypatch.setenv("THRESHOLD_DATA", str(tmp_path / "data"))


@pytest.fixture(autouse=True)
def no_real_keys(monkeypatch):
    """Tests never hold the real keys from the .env file, so a test that reaches the network by mistake fails
    instead of spending money (2026-10-27: a CLI test started a real scan and spent $2.16 before it was stopped)."""
    monkeypatch.setenv("MODEL_API_KEY", FAKE_KEY)
    monkeypatch.setenv("MAPILLARY_TOKEN", FAKE_KEY)
