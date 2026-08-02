import os

import pytest

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


def test_get_team_and_list_teams(tmp_path) -> None:
    path = tmp_path / "teams.yaml"
    path.write_text(TEAMS_V1)
    store = TeamsStore(str(path))

    assert store.get_team("team-x").allowed_models == ["llama3.2"]
    assert store.get_team("nonexistent") is None
    assert list(store.list_teams().keys()) == ["team-x"]


def test_update_team_merges_nested_dict_fields(tmp_path) -> None:
    path = tmp_path / "teams.yaml"
    path.write_text(TEAMS_V1)
    store = TeamsStore(str(path))

    updated = store.update_team("team-x", {"rate_limit": {"rpm": 99}})

    assert updated.rate_limit.rpm == 99
    assert updated.rate_limit.tpm == 10000  # untouched sibling field survives the merge


def test_update_team_replaces_non_dict_fields(tmp_path) -> None:
    path = tmp_path / "teams.yaml"
    path.write_text(TEAMS_V1)
    store = TeamsStore(str(path))

    updated = store.update_team("team-x", {"allowed_models": ["claude-3-5-sonnet"]})

    assert updated.allowed_models == ["claude-3-5-sonnet"]


def test_update_team_persists_to_disk(tmp_path) -> None:
    path = tmp_path / "teams.yaml"
    path.write_text(TEAMS_V1)
    store = TeamsStore(str(path))

    store.update_team("team-x", {"rate_limit": {"rpm": 42}})

    reloaded = TeamsStore(str(path))
    assert reloaded.get_team("team-x").rate_limit.rpm == 42


def test_update_team_unknown_team_raises(tmp_path) -> None:
    path = tmp_path / "teams.yaml"
    path.write_text(TEAMS_V1)
    store = TeamsStore(str(path))

    with pytest.raises(KeyError):
        store.update_team("nonexistent", {"rate_limit": {"rpm": 1}})
