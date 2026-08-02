-- Reconciles a TPM bucket after the real token count is known: refunds the
-- surplus if the estimate reserved too much, or consumes the shortfall if it
-- reserved too little. Token counts aren't known until the LLM responds, so
-- the pre-flight check in token_bucket.lua necessarily reserves an estimate.
--
-- KEYS[1]: bucket key
-- ARGV[1]: capacity
-- ARGV[2]: refill_rate_per_sec
-- ARGV[3]: estimated (originally reserved)
-- ARGV[4]: actual (real total tokens used)
-- ARGV[5]: now_ms

local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local estimated = tonumber(ARGV[3])
local actual = tonumber(ARGV[4])
local now_ms = tonumber(ARGV[5])

local bucket = redis.call("HMGET", key, "tokens", "last_refill_ms")
local tokens = tonumber(bucket[1])
local last_refill_ms = tonumber(bucket[2])

if tokens == nil then
  tokens = capacity
  last_refill_ms = now_ms
end

local elapsed_sec = math.max(0, (now_ms - last_refill_ms) / 1000)
tokens = math.min(capacity, tokens + elapsed_sec * refill_rate)

local diff = estimated - actual
tokens = math.max(0, math.min(capacity, tokens + diff))

redis.call("HMSET", key, "tokens", tokens, "last_refill_ms", now_ms)
local ttl = math.max(2, math.ceil((capacity / math.max(refill_rate, 0.001)) * 2))
redis.call("EXPIRE", key, ttl)

return tostring(tokens)
