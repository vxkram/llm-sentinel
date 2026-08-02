import logging
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from llm_sentinel.budget.cost import compute_cost
from llm_sentinel.core.security import AuthenticatedTeam, require_team
from llm_sentinel.observability.metrics import (
    BUDGET_UTILIZATION_RATIO,
    COST_USD_TOTAL,
    ERRORS_TOTAL,
    FALLBACK_TRIGGERED_TOTAL,
    REQUEST_LATENCY_SECONDS,
    REQUESTS_TOTAL,
    TOKENS_TOTAL,
)
from llm_sentinel.observability.tracing import get_tracer
from llm_sentinel.priority.concurrency import semaphore_for
from llm_sentinel.providers.base import ChatRequest, ChatResponse, Message
from llm_sentinel.providers.registry import ModelNotFoundError
from llm_sentinel.ratelimit.estimate import estimate_tokens
from llm_sentinel.resilience.fallback import (
    AllProvidersUnavailableError,
    dispatch_stream_with_fallback,
    dispatch_with_fallback,
)

logger = logging.getLogger(__name__)
tracer = get_tracer()

router = APIRouter()

_DEFAULT_COMPLETION_ESTIMATE = 256
_NO_PROVIDER = "none"


def _inject_system_prompt(req: ChatRequest, system_prompt: str) -> ChatRequest:
    existing_system = next((m for m in req.messages if m.role == "system"), None)
    other_messages = [m for m in req.messages if m.role != "system"]
    content = f"{system_prompt}\n{existing_system.content}" if existing_system else system_prompt
    return req.model_copy(update={"messages": [Message(role="system", content=content), *other_messages]})


def _prompt_text(req: ChatRequest) -> str:
    return "\n".join(m.content for m in req.messages)


def _record_error(team_id: str, model: str, provider: str, error_type: str) -> None:
    ERRORS_TOTAL.labels(team=team_id, model=model, provider=provider, error_type=error_type).inc()


def _fallback_candidates(request: Request, team: AuthenticatedTeam, model: str) -> list[str]:
    """The requested model's tier-wide fallback chain, restricted to models
    this team is actually allowed to use - fallback must never become a way
    to bypass Stage 3's per-team model restriction.
    """
    chain = request.app.state.registry.fallback_chain(model)
    return [m for m in chain if m in team.config.allowed_models]


async def _enforce_budget_preflight(request: Request, team: AuthenticatedTeam, model: str) -> None:
    tracker = request.app.state.budget_tracker
    budget = team.config.budget
    result = await tracker.check_and_charge(
        team.team_id, budget.daily_limit_usd, budget.monthly_limit_usd, cost=0.0
    )
    if not result.allowed:
        _record_error(team.team_id, model, _NO_PROVIDER, "budget_exceeded")
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
        _record_error(team.team_id, req.model, _NO_PROVIDER, "rate_limited")
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
        _record_error(team.team_id, req.model, _NO_PROVIDER, "rate_limited")
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

    TOKENS_TOTAL.labels(team=team.team_id, model=served_model, direction="prompt").inc(actual_prompt_tokens)
    TOKENS_TOTAL.labels(team=team.team_id, model=served_model, direction="completion").inc(
        actual_completion_tokens
    )

    cost = compute_cost(pricing, served_model, actual_prompt_tokens, actual_completion_tokens)
    COST_USD_TOTAL.labels(team=team.team_id).inc(cost)

    budget = team.config.budget
    result = await tracker.check_and_charge(
        team.team_id, budget.daily_limit_usd, budget.monthly_limit_usd, cost=cost
    )
    if budget.daily_limit_usd > 0:
        BUDGET_UTILIZATION_RATIO.labels(team=team.team_id, period="daily").set(
            result.daily_spend / budget.daily_limit_usd
        )
    if budget.monthly_limit_usd > 0:
        BUDGET_UTILIZATION_RATIO.labels(team=team.team_id, period="monthly").set(
            result.monthly_spend / budget.monthly_limit_usd
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
    accumulated: list[str] = []

    # Held for the stream's full duration, not just the initial dispatch - a
    # slow batch-priority stream should count against batch concurrency for
    # as long as the connection is actually open.
    async with semaphore_for(req.priority):
        start = time.monotonic()
        with tracer.start_as_current_span("provider_dispatch") as span:
            span.set_attribute("requested_model", req.model)
            try:
                stream, served_model = await dispatch_stream_with_fallback(
                    registry, breaker, candidates, req
                )
            except AllProvidersUnavailableError as exc:
                _record_error(team.team_id, req.model, _NO_PROVIDER, "all_providers_unavailable")
                yield f"data: {{\"error\": \"{exc}\"}}\n\n"
                yield "data: [DONE]\n\n"
                return
            span.set_attribute("served_model", served_model)

        async for chunk in stream:
            accumulated.append(chunk.delta)
            yield f"data: {chunk.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"

    provider = registry.resolve(served_model)[0].name
    REQUESTS_TOTAL.labels(team=team.team_id, model=served_model, provider=provider).inc()
    REQUEST_LATENCY_SECONDS.labels(team=team.team_id, model=served_model, provider=provider).observe(
        time.monotonic() - start
    )
    if served_model != req.model:
        FALLBACK_TRIGGERED_TOTAL.labels(from_model=req.model, to_model=served_model).inc()

    # Streaming responses don't carry a usage payload in this gateway's SSE
    # format, so the completion side is re-estimated from the real generated
    # text (a much better number than the pre-flight max_tokens guess) rather
    # than reconciled against an exact count.
    with tracer.start_as_current_span("response_processing"):
        actual_completion = estimate_tokens("".join(accumulated))
        await _reconcile_and_charge(
            request, team, served_model, estimated_prompt, estimated_completion, estimated_prompt, actual_completion
        )


@router.post("/chat/completions", response_model=ChatResponse)
async def chat_completions(
    req: ChatRequest, request: Request, team: AuthenticatedTeam = Depends(require_team)
):
    if req.model not in team.config.allowed_models:
        _record_error(team.team_id, req.model, _NO_PROVIDER, "model_not_allowed")
        raise HTTPException(
            status_code=403,
            detail=f"team '{team.team_id}' is not permitted to use model '{req.model}'",
        )

    try:
        candidates = _fallback_candidates(request, team, req.model)
    except ModelNotFoundError as exc:
        _record_error(team.team_id, req.model, _NO_PROVIDER, "model_not_found")
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    with tracer.start_as_current_span("budget_check"):
        await _enforce_budget_preflight(request, team, req.model)

    with tracer.start_as_current_span("rate_limit_check") as span:
        estimated_prompt, estimated_completion = await _enforce_rate_limits(request, team, req)
        span.set_attribute("estimated_prompt_tokens", estimated_prompt)
        span.set_attribute("estimated_completion_tokens", estimated_completion)

    if team.config.system_prompt:
        req = _inject_system_prompt(req, team.config.system_prompt)

    if req.stream:
        return StreamingResponse(
            _stream_sse(request, team, candidates, req, estimated_prompt, estimated_completion),
            media_type="text/event-stream",
        )

    registry = request.app.state.registry
    breaker = request.app.state.circuit_breaker
    start = time.monotonic()
    async with semaphore_for(req.priority):
        with tracer.start_as_current_span("provider_dispatch") as span:
            span.set_attribute("requested_model", req.model)
            try:
                resp, served_model = await dispatch_with_fallback(registry, breaker, candidates, req)
            except AllProvidersUnavailableError as exc:
                _record_error(team.team_id, req.model, _NO_PROVIDER, "all_providers_unavailable")
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            span.set_attribute("served_model", served_model)
            span.set_attribute("provider", resp.provider)

    REQUESTS_TOTAL.labels(team=team.team_id, model=served_model, provider=resp.provider).inc()
    REQUEST_LATENCY_SECONDS.labels(team=team.team_id, model=served_model, provider=resp.provider).observe(
        time.monotonic() - start
    )
    if served_model != req.model:
        FALLBACK_TRIGGERED_TOTAL.labels(from_model=req.model, to_model=served_model).inc()
        resp = resp.model_copy(update={"model": served_model})

    with tracer.start_as_current_span("response_processing"):
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
