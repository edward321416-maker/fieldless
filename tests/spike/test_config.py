from spike import config


def test_find_env_file_prefers_nearest(tmp_path):
    (tmp_path / ".env").write_text("A=1\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    inner = repo / ".env"
    inner.write_text("A=2\n")
    assert config.find_env_file(repo) == inner


def test_find_env_file_walks_up_from_worktree(tmp_path):
    shared = tmp_path / ".env"
    shared.write_text("A=1\n")
    worktree = tmp_path / "wt" / "web-map"
    worktree.mkdir(parents=True)
    assert config.find_env_file(worktree) == shared


def test_find_env_file_skips_directories_named_env(tmp_path):
    (tmp_path / "repo" / ".env").mkdir(parents=True)
    shared = tmp_path / ".env"
    shared.write_text("A=1\n")
    assert config.find_env_file(tmp_path / "repo") == shared
