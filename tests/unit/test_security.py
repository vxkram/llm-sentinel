import os

from llm_sentinel.core.security import TeamsStore

TEAMS_V1 = """
teams:
  team-x:
    api_key: key-v1
    allowed_models: [llama3.2]
    rate_limit:
      rpm: 10
      tpm: 10000
    budget:
      daily_limit_usd: 1.0
      monthly_limit_usd: 10.0
"""

TEAMS_V2 = """
teams:
  team-x:
    api_key: key-v2
    allowed_models: [llama3.2, gpt-4o-mini]
    rate_limit:
      rpm: 10
      tpm: 10000
    budget:
      daily_limit_usd: 1.0
      monthly_limit_usd: 10.0
"""


def test_resolve_known_api_key(tmp_path) -> None:
    path = tmp_path / "teams.yaml"
    path.write_text(TEAMS_V1)
    store = TeamsStore(str(path))

    resolved = store.resolve_api_key("key-v1")

    assert resolved is not None
    team_id, config = resolved
    assert team_id == "team-x"
    assert config.allowed_models == ["llama3.2"]


def test_resolve_unknown_api_key_returns_none(tmp_path) -> None:
    path = tmp_path / "teams.yaml"
    path.write_text(TEAMS_V1)
    store = TeamsStore(str(path))

    assert store.resolve_api_key("nonexistent") is None


def test_hot_reload_picks_up_config_changes(tmp_path) -> None:
    path = tmp_path / "teams.yaml"
    path.write_text(TEAMS_V1)
    store = TeamsStore(str(path))
    assert store.resolve_api_key("key-v2") is None

    path.write_text(TEAMS_V2)
    new_mtime = path.stat().st_mtime + 1
    os.utime(path, (new_mtime, new_mtime))

    resolved = store.resolve_api_key("key-v2")
    assert resolved is not None
    assert resolved[1].allowed_models == ["llama3.2", "gpt-4o-mini"]
    assert store.resolve_api_key("key-v1") is None
