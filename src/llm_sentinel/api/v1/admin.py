from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from llm_sentinel.admin.audit import AuditLog
from llm_sentinel.core.security import (
    BudgetConfig,
    RateLimitConfig,
    TeamConfig,
    TeamsStore,
    require_admin,
)
from llm_sentinel.health_checker.store import HealthStore
from llm_sentinel.providers.registry import ProviderRegistry
from llm_sentinel.resilience.circuit_breaker import CircuitBreaker

router = APIRouter(dependencies=[Depends(require_admin)])


class TeamConfigOut(BaseModel):
    team_id: str
    allowed_models: list[str]
    system_prompt: str | None
    rate_limit: RateLimitConfig
    budget: BudgetConfig


class TeamUpdateRequest(BaseModel):
    allowed_models: list[str] | None = None
    system_prompt: str | None = None
    rate_limit: dict | None = None
    budget: dict | None = None


class TeamUsageOut(BaseModel):
    team_id: str
    rpm_remaining: float
    tpm_remaining: float
    daily_spend_usd: float
    monthly_spend_usd: float
    daily_limit_usd: float
    monthly_limit_usd: float


def _team_out(team_id: str, config: TeamConfig) -> TeamConfigOut:
    return TeamConfigOut(
        team_id=team_id,
        allowed_models=config.allowed_models,
        system_prompt=config.system_prompt,
        rate_limit=config.rate_limit,
        budget=config.budget,
    )


@router.get("/teams", response_model=list[TeamConfigOut])
async def list_teams(request: Request):
    store: TeamsStore = request.app.state.teams_store
    return [_team_out(team_id, cfg) for team_id, cfg in store.list_teams().items()]


@router.get("/teams/{team_id}", response_model=TeamConfigOut)
async def get_team(team_id: str, request: Request):
    store: TeamsStore = request.app.state.teams_store
    config = store.get_team(team_id)
    if config is None:
        raise HTTPException(status_code=404, detail=f"unknown team: {team_id}")
    return _team_out(team_id, config)


@router.patch("/teams/{team_id}", response_model=TeamConfigOut)
async def update_team(team_id: str, body: TeamUpdateRequest, request: Request):
    store: TeamsStore = request.app.state.teams_store
    before = store.get_team(team_id)
    if before is None:
        raise HTTPException(status_code=404, detail=f"unknown team: {team_id}")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")

    after = store.update_team(team_id, updates)

    audit: AuditLog = request.app.state.audit_log
    await audit.record(
        actor="admin",
        action="update_team",
        team_id=team_id,
        before=before.model_dump(exclude={"api_key"}),
        after=after.model_dump(exclude={"api_key"}),
    )

    return _team_out(team_id, after)


@router.get("/teams/{team_id}/usage", response_model=TeamUsageOut)
async def team_usage(team_id: str, request: Request):
    store: TeamsStore = request.app.state.teams_store
    team = store.get_team(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail=f"unknown team: {team_id}")

    token_bucket = request.app.state.token_bucket
    budget_tracker = request.app.state.budget_tracker

    rpm_remaining = await token_bucket.peek(f"ratelimit:{team_id}:rpm")
    tpm_remaining = await token_bucket.peek(f"ratelimit:{team_id}:tpm")
    daily_spend, monthly_spend = await budget_tracker.current_spend(team_id)

    return TeamUsageOut(
        team_id=team_id,
        rpm_remaining=rpm_remaining if rpm_remaining is not None else team.rate_limit.rpm,
        tpm_remaining=tpm_remaining if tpm_remaining is not None else team.rate_limit.tpm,
        daily_spend_usd=daily_spend,
        monthly_spend_usd=monthly_spend,
        daily_limit_usd=team.budget.daily_limit_usd,
        monthly_limit_usd=team.budget.monthly_limit_usd,
    )


@router.get("/health")
async def health_status(request: Request):
    registry: ProviderRegistry = request.app.state.registry
    store: HealthStore = request.app.state.health_store
    results = {}
    for canonical_model in registry.canonical_models():
        client, wire_model = registry.resolve(canonical_model)
        results[canonical_model] = await store.status(client.name, wire_model)
    return results


@router.get("/circuit-breakers")
async def circuit_breaker_status(request: Request):
    registry: ProviderRegistry = request.app.state.registry
    breaker: CircuitBreaker = request.app.state.circuit_breaker
    results = {}
    for canonical_model in registry.canonical_models():
        client, wire_model = registry.resolve(canonical_model)
        results[canonical_model] = await breaker.status(client.name, wire_model)
    return results


@router.get("/audit")
async def audit_log(request: Request, limit: int = 50):
    audit: AuditLog = request.app.state.audit_log
    return await audit.recent(limit)
