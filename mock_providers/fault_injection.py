import asyncio
import random
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel


class FaultConfig(BaseModel):
    mode: Literal["none", "error", "timeout", "connection_reset"] = "none"
    status_code: int = 500
    rate: float = 1.0
    delay_seconds: float = 5.0


class FaultState:
    def __init__(self) -> None:
        self.config = FaultConfig()

    def _should_trigger(self) -> bool:
        return random.random() < self.config.rate

    async def maybe_raise_or_delay(self) -> None:
        if self.config.mode not in ("error", "timeout") or not self._should_trigger():
            return
        if self.config.mode == "error":
            raise HTTPException(status_code=self.config.status_code, detail="injected fault")
        await asyncio.sleep(self.config.delay_seconds)
        raise HTTPException(status_code=504, detail="injected timeout")

    def should_reset(self) -> bool:
        return self.config.mode == "connection_reset" and self._should_trigger()
