from datetime import UTC, datetime, timedelta
from pathlib import Path

from redis.asyncio import Redis

_SCRIPTS_DIR = Path(__file__).parent / "scripts"


class BudgetResult:
    def __init__(self, allowed: bool, daily_spend: float, monthly_spend: float, warning: bool):
        self.allowed = allowed
        self.daily_spend = daily_spend
        self.monthly_spend = monthly_spend
        self.warning = warning


def _seconds_until_next_midnight_utc(now: datetime) -> int:
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


def _seconds_until_next_month_utc(now: datetime) -> int:
    if now.month == 12:
        next_month = now.replace(
            year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
    else:
        next_month = now.replace(
            month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
    return max(1, int((next_month - now).total_seconds()))


class BudgetTracker:
    def __init__(self, redis_client: Redis):
        self._redis = redis_client
        self._check_script = redis_client.register_script(
            (_SCRIPTS_DIR / "budget_check.lua").read_text()
        )

    async def check_and_charge(
        self, team_id: str, daily_limit: float, monthly_limit: float, cost: float
    ) -> BudgetResult:
        now = datetime.now(UTC)
        daily_key = f"budget:{team_id}:daily:{now.strftime('%Y-%m-%d')}"
        monthly_key = f"budget:{team_id}:monthly:{now.strftime('%Y-%m')}"

        allowed, daily_spend, monthly_spend, warning = await self._check_script(
            keys=[daily_key, monthly_key],
            args=[
                daily_limit,
                monthly_limit,
                cost,
                _seconds_until_next_midnight_utc(now),
                _seconds_until_next_month_utc(now),
            ],
        )
        return BudgetResult(
            allowed=bool(int(allowed)),
            daily_spend=float(daily_spend),
            monthly_spend=float(monthly_spend),
            warning=bool(int(warning)),
        )
