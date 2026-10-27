import pytest

from pipeline import config


def test_find_env_file_returns_the_nearest_one(tmp_path):
    (tmp_path / ".env").write_text("A=1\n")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / ".env").write_text("A=2\n")
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert config.find_env_file(deep) == tmp_path / "a" / ".env"


def test_data_dir_prefers_threshold_data(tmp_path, monkeypatch):
    monkeypatch.setenv("THRESHOLD_DATA", str(tmp_path / "d"))
    assert config.data_dir() == tmp_path / "d"
    assert (tmp_path / "d").is_dir()


def test_data_dir_falls_back_to_the_env_file_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("THRESHOLD_DATA", raising=False)
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / ".env")
    assert config.data_dir() == tmp_path / "data"


def test_data_dir_raises_without_either(monkeypatch):
    monkeypatch.delenv("THRESHOLD_DATA", raising=False)
    monkeypatch.setattr(config, "ENV_FILE", None)
    with pytest.raises(RuntimeError, match="THRESHOLD_DATA"):
        config.data_dir()


def test_run_dir_and_photo_cache_live_under_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("THRESHOLD_DATA", str(tmp_path))
    assert config.run_dir("dev") == tmp_path / "runs" / "dev"
    assert (tmp_path / "runs" / "dev").is_dir()
    assert config.photo_cache() == tmp_path / "photos"


def test_missing_keys_raise(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("MAPILLARY_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        config.api_key()
    with pytest.raises(RuntimeError):
        config.mapillary_token()


def test_tests_never_see_the_real_keys():
    from tests.conftest import FAKE_KEY
    assert config.api_key() == FAKE_KEY and config.mapillary_token() == FAKE_KEY


def test_photo_budget_keeps_a_scan_inside_its_cost_cap():
    budget = config.photo_budget(999)
    assert budget <= config.MAX_PHOTOS_PER_SCAN
    assert budget * config.EST_USD_PER_100_PHOTOS / 100 <= config.SCAN_COST_CAP_USD * 0.9 + 1e-9
    assert config.photo_budget(50) == 50 and config.photo_budget(0) == 1


def test_each_photo_check_adds_to_the_estimate_and_shrinks_the_budget():
    assert config.est_usd_per_100_photos() == config.EST_USD_PER_100_PHOTOS
    five = config.est_usd_per_100_photos(5)
    assert five == pytest.approx(config.EST_USD_PER_100_PHOTOS + 5 * config.EST_USD_PER_100_PHOTOS_PER_CHECK)
    budget = config.photo_budget(999, checks=5)
    assert budget < config.photo_budget(999)
    assert budget * five / 100 <= config.SCAN_COST_CAP_USD * 0.9 + 1e-9


def test_a_larger_cap_pays_for_more_photos_up_to_the_scan_limit():
    assert config.photo_budget(999, checks=5, cap_usd=6.0) > config.photo_budget(999, checks=5)
    assert config.photo_budget(999, checks=5, cap_usd=1000.0) == config.MAX_PHOTOS_PER_SCAN
