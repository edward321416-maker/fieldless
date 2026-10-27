from server import settings


def test_defaults():
    s = settings.load()
    assert (s.run_name, s.invite_code, s.scans_per_ip_per_day, s.demo_scans) == ("server", None, 3, ())
    assert s.max_bbox_deg2 == 0.0001
    assert s.static_dir.name == "dist" and s.static_dir.parent.name == "web"


def test_environment_overrides(monkeypatch):
    monkeypatch.setenv("INVITE_CODE", "letmein")
    monkeypatch.setenv("SCANS_PER_IP_PER_DAY", "5")
    monkeypatch.setenv("DEMO_SCANS", "3:Myeongdong;7:Seongsu Atelier-gil;9")
    monkeypatch.setenv("THRESHOLD_RUN", "demo")
    s = settings.load()
    assert s.invite_code == "letmein" and s.scans_per_ip_per_day == 5 and s.run_name == "demo"
    assert s.demo_scans == ((3, "Myeongdong"), (7, "Seongsu Atelier-gil"), (9, "Scan 9"))


def test_an_empty_invite_code_means_none(monkeypatch):
    monkeypatch.setenv("INVITE_CODE", "")
    assert settings.load().invite_code is None


def test_secrets_never_appear_in_the_settings_repr(monkeypatch):
    monkeypatch.setenv("INVITE_CODE", "letmein")
    monkeypatch.setenv("IP_HASH_SALT", "pepper")
    text = repr(settings.load())
    assert "letmein" not in text and "pepper" not in text


def test_measured_hand_seconds(monkeypatch):
    monkeypatch.setenv("MEASURED_HAND_SECONDS", "3:1260; 7:905.5")
    assert settings.load().measured_hand_seconds == ((3, 1260.0), (7, 905.5))
    monkeypatch.setenv("MEASURED_HAND_SECONDS", "")
    assert settings.load().measured_hand_seconds == ()
