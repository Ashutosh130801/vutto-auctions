"""Distributed token-bucket rate limiter.

Implemented as a Lua script so check-and-consume is atomic on the Redis side —
which matters because the API runs as N replicas.  The bucket refills
continuously (not in fixed windows), so a client cannot burst 2x the limit by
straddling a window boundary.

Fails **open**: if Redis is unreachable we let the request through and emit a
warning rather than taking the whole platform down with the limiter.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, cast

from redis.asyncio import Redis

from app.core.logging import get_logger

log = get_logger(__name__)

_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_per_sec = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local bucket = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(bucket[1])
local ts = tonumber(bucket[2])
if tokens == nil then
  tokens = capacity
  ts = now
end

local elapsed = math.max(0, now - ts)
tokens = math.min(capacity, tokens + elapsed * refill_per_sec)

local allowed = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
end

redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, math.ceil(capacity / refill_per_sec) + 60)

local retry_after = 0
if allowed == 0 then
  retry_after = (cost - tokens) / refill_per_sec
end
return {allowed, tostring(tokens), tostring(retry_after)}
"""


@dataclass(slots=True)
class RateLimitResult:
    allowed: bool
    remaining: float
    retry_after: float
    limit: int


class RateLimiter:
    def __init__(self, redis: Redis | None) -> None:
        self._redis = redis
        self._sha: str | None = None

    async def _script_sha(self) -> str | None:
        if self._redis is None:
            return None
        if self._sha is None:
            # redis-py types this as `Awaitable[str] | str` to cover both the
            # sync and async clients; on the async client it is always awaitable.
            self._sha = cast(
                str, await cast("Awaitable[str]", self._redis.script_load(_BUCKET_LUA))
            )
        return self._sha

    async def consume(self, key: str, *, limit_per_minute: int, cost: int = 1) -> RateLimitResult:
        if self._redis is None or limit_per_minute <= 0:
            return RateLimitResult(True, float(limit_per_minute), 0.0, limit_per_minute)
        try:
            sha = await self._script_sha()
            assert sha is not None
            # Redis sends every EVALSHA argument as a bulk string; being
            # explicit here keeps the wire format obvious and the types honest.
            allowed, remaining, retry_after = await cast(
                "Awaitable[list[Any]]",
                self._redis.evalsha(
                    sha,
                    1,
                    f"rl:{key}",
                    str(limit_per_minute),
                    str(limit_per_minute / 60.0),
                    str(time.time()),
                    str(cost),
                ),
            )
            return RateLimitResult(
                allowed=bool(int(allowed)),
                remaining=float(remaining),
                retry_after=float(retry_after),
                limit=limit_per_minute,
            )
        except Exception as exc:  # pragma: no cover - resilience path
            log.warning("ratelimit.unavailable", error=str(exc), key=key)
            return RateLimitResult(True, float(limit_per_minute), 0.0, limit_per_minute)
