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

    @staticmethod
    def _keys(team_id: str, now: datetime) -> tuple[str, str]:
        return (
            f"budget:{team_id}:daily:{now.strftime('%Y-%m-%d')}",
            f"budget:{team_id}:monthly:{now.strftime('%Y-%m')}",
        )

    async def check_and_charge(
        self, team_id: str, daily_limit: float, monthly_limit: float, cost: float
    ) -> BudgetResult:
        now = datetime.now(UTC)
        daily_key, monthly_key = self._keys(team_id, now)

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

    async def current_spend(self, team_id: str) -> tuple[float, float]:
        """Read-only: (daily_spend, monthly_spend) without charging anything.
        For admin/dashboard use, distinct from check_and_charge.
        """
        daily_key, monthly_key = self._keys(team_id, datetime.now(UTC))
        daily, monthly = await self._redis.mget(daily_key, monthly_key)
        return (
            float(daily) if daily is not None else 0.0,
            float(monthly) if monthly is not None else 0.0,
        )
