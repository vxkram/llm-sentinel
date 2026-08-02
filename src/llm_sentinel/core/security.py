from pathlib import Path

import yaml
from fastapi import Header, HTTPException, Request
from pydantic import BaseModel


class RateLimitConfig(BaseModel):
    rpm: int
    tpm: int


class BudgetConfig(BaseModel):
    daily_limit_usd: float
    monthly_limit_usd: float


class TeamConfig(BaseModel):
    api_key: str
    allowed_models: list[str]
    system_prompt: str | None = None
    rate_limit: RateLimitConfig
    budget: BudgetConfig


class TeamsConfig(BaseModel):
    teams: dict[str, TeamConfig]


class TeamsStore:
    """Loads configs/teams.yaml and hot-reloads it by polling mtime, so a
    limits/model change doesn't require restarting the gateway.
    """

    def __init__(self, path: str):
        self._path = Path(path)
        self._mtime: float | None = None
        self._by_api_key: dict[str, tuple[str, TeamConfig]] = {}
        self._load()

    def _load(self) -> None:
        raw = yaml.safe_load(self._path.read_text())
        parsed = TeamsConfig.model_validate(raw)
        self._by_api_key = {
            team.api_key: (team_id, team) for team_id, team in parsed.teams.items()
        }
        self._mtime = self._path.stat().st_mtime

    def _maybe_reload(self) -> None:
        current_mtime = self._path.stat().st_mtime
        if current_mtime != self._mtime:
            self._load()

    def resolve_api_key(self, api_key: str) -> tuple[str, TeamConfig] | None:
        self._maybe_reload()
        return self._by_api_key.get(api_key)


class AuthenticatedTeam(BaseModel):
    team_id: str
    config: TeamConfig


async def require_team(
    request: Request, authorization: str | None = Header(default=None)
) -> AuthenticatedTeam:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail="missing or malformed Authorization header"
        )
    api_key = authorization[len("Bearer ") :]
    store: TeamsStore = request.app.state.teams_store
    resolved = store.resolve_api_key(api_key)
    if resolved is None:
        raise HTTPException(status_code=401, detail="invalid API key")
    team_id, config = resolved
    return AuthenticatedTeam(team_id=team_id, config=config)
