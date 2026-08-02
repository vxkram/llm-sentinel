import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from llm_sentinel.budget.cost import compute_cost
from llm_sentinel.core.security import AuthenticatedTeam, require_team
from llm_sentinel.providers.base import ChatRequest, ChatResponse, Message
from llm_sentinel.providers.registry import ModelNotFoundError
from llm_sentinel.ratelimit.estimate import estimate_tokens
from llm_sentinel.resilience.fallback import (
    AllProvidersUnavailableError,
    dispatch_stream_with_fallback,
    dispatch_with_fallback,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_DEFAULT_COMPLETION_ESTIMATE = 256


def _inject_system_prompt(req: ChatRequest, system_prompt: str) -> ChatRequest:
    existing_system = next((m for m in req.messages if m.role == "system"), None)
    other_messages = [m for m in req.messages if m.role != "system"]
    content = f"{system_prompt}\n{existing_system.content}" if existing_system else system_prompt
    return req.model_copy(update={"messages": [Message(role="system", content=content), *other_messages]})


def _prompt_text(req: ChatRequest) -> str:
    return "\n".join(m.content for m in req.messages)


def _fallback_candidates(request: Request, team: AuthenticatedTeam, model: str) -> list[str]:
    """The requested model's tier-wide fallback chain, restricted to models
    this team is actually allowed to use - fallback must never become a way
    to bypass Stage 3's per-team model restriction.
    """
    chain = request.app.state.registry.fallback_chain(model)
    return [m for m in chain if m in team.config.allowed_models]


async def _enforce_budget_preflight(request: Request, team: AuthenticatedTeam) -> None:
    tracker = request.app.state.budget_tracker
    budget = team.config.budget
    result = await tracker.check_and_charge(
        team.team_id, budget.daily_limit_usd, budget.monthly_limit_usd, cost=0.0
    )
    if not result.allowed:
        raise HTTPException(
            status_code=402, detail=f"team '{team.team_id}' has exceeded its budget"
        )


async def _enforce_rate_limits(
    request: Request, team: AuthenticatedTeam, req: ChatRequest
) -> tuple[int, int]:
    """Reserves RPM + TPM capacity. Returns (estimated_prompt_tokens,
    estimated_completion_tokens). Raises HTTPException(429) if over limit.
    """
    limiter = request.app.state.token_bucket
    rate_limit = team.config.rate_limit

    rpm_key = f"ratelimit:{team.team_id}:rpm"
    rpm_result = await limiter.check_and_consume(
        rpm_key, capacity=rate_limit.rpm, refill_rate_per_sec=rate_limit.rpm / 60, requested=1
    )
    if not rpm_result.allowed:
        raise HTTPException(
            status_code=429,
            detail="RPM rate limit exceeded",
            headers={"Retry-After": str(max(1, round(rpm_result.retry_after)))},
        )

    estimated_prompt = estimate_tokens(_prompt_text(req))
    estimated_completion = req.max_tokens or _DEFAULT_COMPLETION_ESTIMATE
    estimated_total = estimated_prompt + estimated_completion

    tpm_key = f"ratelimit:{team.team_id}:tpm"
    tpm_result = await limiter.check_and_consume(
        tpm_key,
        capacity=rate_limit.tpm,
        refill_rate_per_sec=rate_limit.tpm / 60,
        requested=estimated_total,
    )
    if not tpm_result.allowed:
        raise HTTPException(
            status_code=429,
            detail="TPM rate limit exceeded",
            headers={"Retry-After": str(max(1, round(tpm_result.retry_after)))},
        )

    return estimated_prompt, estimated_completion


async def _reconcile_and_charge(
    request: Request,
    team: AuthenticatedTeam,
    served_model: str,
    estimated_prompt: int,
    estimated_completion: int,
    actual_prompt_tokens: int,
    actual_completion_tokens: int,
) -> None:
    limiter = request.app.state.token_bucket
    tracker = request.app.state.budget_tracker
    pricing = request.app.state.pricing
    rate_limit = team.config.rate_limit

    tpm_key = f"ratelimit:{team.team_id}:tpm"
    await limiter.reconcile(
        tpm_key,
        capacity=rate_limit.tpm,
        refill_rate_per_sec=rate_limit.tpm / 60,
        estimated=estimated_prompt + estimated_completion,
        actual=actual_prompt_tokens + actual_completion_tokens,
    )

    cost = compute_cost(pricing, served_model, actual_prompt_tokens, actual_completion_tokens)
    budget = team.config.budget
    result = await tracker.check_and_charge(
        team.team_id, budget.daily_limit_usd, budget.monthly_limit_usd, cost=cost
    )
    if result.warning:
        logger.warning(
            "team %s crossed 80%% budget threshold (daily=$%.4f monthly=$%.4f)",
            team.team_id,
            result.daily_spend,
            result.monthly_spend,
        )


async def _stream_sse(
    request: Request,
    team: AuthenticatedTeam,
    candidates: list[str],
    req: ChatRequest,
    estimated_prompt: int,
    estimated_completion: int,
) -> AsyncIterator[str]:
    registry = request.app.state.registry
    breaker = request.app.state.circuit_breaker

    try:
        stream, served_model = await dispatch_stream_with_fallback(
            registry, breaker, candidates, req
        )
    except AllProvidersUnavailableError as exc:
        yield f"data: {{\"error\": \"{exc}\"}}\n\n"
        yield "data: [DONE]\n\n"
        return

    accumulated: list[str] = []
    async for chunk in stream:
        accumulated.append(chunk.delta)
        yield f"data: {chunk.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"

    # Streaming responses don't carry a usage payload in this gateway's SSE
    # format, so the completion side is re-estimated from the real generated
    # text (a much better number than the pre-flight max_tokens guess) rather
    # than reconciled against an exact count.
    actual_completion = estimate_tokens("".join(accumulated))
    await _reconcile_and_charge(
        request, team, served_model, estimated_prompt, estimated_completion, estimated_prompt, actual_completion
    )


@router.post("/chat/completions", response_model=ChatResponse)
async def chat_completions(
    req: ChatRequest, request: Request, team: AuthenticatedTeam = Depends(require_team)
):
    if req.model not in team.config.allowed_models:
        raise HTTPException(
            status_code=403,
            detail=f"team '{team.team_id}' is not permitted to use model '{req.model}'",
        )

    try:
        candidates = _fallback_candidates(request, team, req.model)
    except ModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await _enforce_budget_preflight(request, team)
    estimated_prompt, estimated_completion = await _enforce_rate_limits(request, team, req)

    if team.config.system_prompt:
        req = _inject_system_prompt(req, team.config.system_prompt)

    if req.stream:
        return StreamingResponse(
            _stream_sse(request, team, candidates, req, estimated_prompt, estimated_completion),
            media_type="text/event-stream",
        )

    registry = request.app.state.registry
    breaker = request.app.state.circuit_breaker
    try:
        resp, served_model = await dispatch_with_fallback(registry, breaker, candidates, req)
    except AllProvidersUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if served_model != req.model:
        resp = resp.model_copy(update={"model": served_model})

    await _reconcile_and_charge(
        request,
        team,
        served_model,
        estimated_prompt,
        estimated_completion,
        resp.usage.prompt_tokens,
        resp.usage.completion_tokens,
    )
    return resp
