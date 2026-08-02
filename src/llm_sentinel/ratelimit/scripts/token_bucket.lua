-- Atomic check-and-consume for a token bucket.
-- KEYS[1]: bucket key
-- ARGV[1]: capacity
-- ARGV[2]: refill_rate_per_sec
-- ARGV[3]: requested
-- ARGV[4]: now_ms
--
-- Returns {allowed (0/1), tokens_remaining (string), retry_after_seconds (string)}
-- Numeric returns are stringified because Redis truncates Lua numbers to
-- integers on return, which would silently drop fractional tokens/seconds.

local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local requested = tonumber(ARGV[3])
local now_ms = tonumber(ARGV[4])

local bucket = redis.call("HMGET", key, "tokens", "last_refill_ms")
local tokens = tonumber(bucket[1])
local last_refill_ms = tonumber(bucket[2])

if tokens == nil then
  tokens = capacity
  last_refill_ms = now_ms
end

local elapsed_sec = math.max(0, (now_ms - last_refill_ms) / 1000)
tokens = math.min(capacity, tokens + elapsed_sec * refill_rate)

local allowed = 0
local retry_after = 0

if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
else
  local deficit = requested - tokens
  if refill_rate > 0 then
    retry_after = deficit / refill_rate
  else
    retry_after = -1
  end
end

redis.call("HMSET", key, "tokens", tokens, "last_refill_ms", now_ms)
local ttl = math.max(2, math.ceil((capacity / math.max(refill_rate, 0.001)) * 2))
redis.call("EXPIRE", key, ttl)

return {allowed, tostring(tokens), tostring(retry_after)}
