import pytest

from server import settings


def test_defaults():
    s = settings.load()
    assert (s.run_name, s.invite_code, s.scans_per_ip_per_day, s.demo_scans) == ("server", None, 3, ())
    assert s.public_cost_cap_usd is None and s.backup_dir is None
    assert s.glimmer_daily_usd == 3.0 and s.glimmer_total_usd is None
    assert s.max_bbox_deg2 == 0.0001
    assert s.static_dir.name == "dist" and s.static_dir.parent.name == "web"


def test_environment_overrides(monkeypatch):
    monkeypatch.setenv("INVITE_CODE", "letmein")
    monkeypatch.setenv("SCANS_PER_IP_PER_DAY", "5")
    monkeypatch.setenv("DEMO_SCANS", "3:Myeongdong;7:Seongsu Atelier-gil;9")
    monkeypatch.setenv("THRESHOLD_RUN", "demo")
    monkeypatch.setenv("HF_DATA_REPO", "me/fieldless-data")
    monkeypatch.setenv("PUBLIC_COST_CAP_USD", "40")
    monkeypatch.setenv("BACKUP_DIR", "/backup")
    monkeypatch.setenv("GLIMMER_DAILY_USD", "1.5")
    monkeypatch.setenv("GLIMMER_TOTAL_USD", "10")
    s = settings.load()
    assert s.glimmer_daily_usd == 1.5 and s.glimmer_total_usd == 10.0
    assert s.backup_dir is not None and s.backup_dir.name == "backup"
    assert s.public_cost_cap_usd == 40.0
    assert s.invite_code == "letmein" and s.scans_per_ip_per_day == 5 and s.run_name == "demo"
    assert s.hf_data_repo == "me/fieldless-data"
    assert s.demo_scans == ((3, "Myeongdong"), (7, "Seongsu Atelier-gil"), (9, "Scan 9"))


def test_an_empty_invite_code_means_none(monkeypatch):
    monkeypatch.setenv("INVITE_CODE", "")
    assert settings.load().invite_code is None


def test_secrets_never_appear_in_the_settings_repr(monkeypatch):
    monkeypatch.setenv("INVITE_CODE", "letmein")
    monkeypatch.setenv("IP_HASH_SALT", "pepper")
    text = repr(settings.load())
    assert "letmein" not in text and "pepper" not in text


def test_bad_numbers_name_their_variable(monkeypatch):
    monkeypatch.setenv("DEMO_SCANS", "three:Seongsu")
    with pytest.raises(ValueError, match="DEMO_SCANS"):
        settings.load()


@pytest.mark.parametrize("marker", ["RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID", "SPACE_ID", "MODAL_TASK_ID",
                                    "MODAL_IS_REMOTE"])
def test_any_railway_variable_needs_a_salt(monkeypatch, marker):
    monkeypatch.setenv(marker, "x")
    with pytest.raises(RuntimeError, match="IP_HASH_SALT"):
        settings.load()


def test_railway_needs_a_salt(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="IP_HASH_SALT"):
        settings.load()
    monkeypatch.setenv("IP_HASH_SALT", "a-random-salt")
    assert settings.load().ip_hash_salt == "a-random-salt"
