from fastapi.testclient import TestClient

from llm_sentinel.main import app
from tests.redis_helpers import requires_redis

pytestmark = requires_redis

ADMIN_HEADERS = {"X-Admin-Key": "sk-admin-demo-999000"}


def test_admin_endpoints_require_admin_key() -> None:
    with TestClient(app) as client:
        resp = client.get("/admin/teams")
    assert resp.status_code == 401


def test_admin_endpoints_reject_wrong_admin_key() -> None:
    with TestClient(app) as client:
        resp = client.get("/admin/teams", headers={"X-Admin-Key": "not-the-real-key"})
    assert resp.status_code == 401


def test_list_teams_excludes_api_keys() -> None:
    with TestClient(app) as client:
        resp = client.get("/admin/teams", headers=ADMIN_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    team_ids = {t["team_id"] for t in body}
    assert {"team-alpha", "team-beta"} <= team_ids
    for team in body:
        assert "api_key" not in team


def test_get_unknown_team_returns_404() -> None:
    with TestClient(app) as client:
        resp = client.get("/admin/teams/does-not-exist", headers=ADMIN_HEADERS)
    assert resp.status_code == 404


def test_update_team_rate_limit_and_it_takes_effect(tmp_path, monkeypatch) -> None:
    import shutil

    from llm_sentinel.core.config import get_settings

    original_path = get_settings().teams_config_path
    temp_path = tmp_path / "teams.yaml"
    shutil.copy(original_path, temp_path)
    monkeypatch.setenv("TEAMS_CONFIG_PATH", str(temp_path))
    get_settings.cache_clear()

    try:
        with TestClient(app) as client:
            resp = client.patch(
                "/admin/teams/team-alpha",
                headers=ADMIN_HEADERS,
                json={"rate_limit": {"rpm": 77}},
            )
            assert resp.status_code == 200
            assert resp.json()["rate_limit"]["rpm"] == 77

            usage = client.get("/admin/teams/team-alpha", headers=ADMIN_HEADERS)
            assert usage.json()["rate_limit"]["rpm"] == 77
    finally:
        get_settings.cache_clear()


def test_update_team_with_no_fields_returns_400() -> None:
    with TestClient(app) as client:
        resp = client.patch("/admin/teams/team-alpha", headers=ADMIN_HEADERS, json={})
    assert resp.status_code == 400


def test_health_and_circuit_breaker_endpoints_cover_all_models() -> None:
    with TestClient(app) as client:
        health_resp = client.get("/admin/health", headers=ADMIN_HEADERS)
        cb_resp = client.get("/admin/circuit-breakers", headers=ADMIN_HEADERS)

    assert health_resp.status_code == 200
    assert cb_resp.status_code == 200
    expected_models = {"llama3.2", "gpt-4o-mini", "claude-3-5-sonnet"}
    assert expected_models <= set(health_resp.json().keys())
    assert expected_models <= set(cb_resp.json().keys())


def test_audit_log_records_team_updates(tmp_path, monkeypatch) -> None:
    import shutil

    from llm_sentinel.core.config import get_settings

    original_path = get_settings().teams_config_path
    temp_path = tmp_path / "teams.yaml"
    shutil.copy(original_path, temp_path)
    monkeypatch.setenv("TEAMS_CONFIG_PATH", str(temp_path))
    get_settings.cache_clear()

    try:
        with TestClient(app) as client:
            client.patch(
                "/admin/teams/team-beta",
                headers=ADMIN_HEADERS,
                json={"system_prompt": "audit-test-marker"},
            )
            audit_resp = client.get("/admin/audit", headers=ADMIN_HEADERS)

        assert audit_resp.status_code == 200
        entries = audit_resp.json()
        assert any(
            e["team_id"] == "team-beta" and e["after"]["system_prompt"] == "audit-test-marker"
            for e in entries
        )
    finally:
        get_settings.cache_clear()
