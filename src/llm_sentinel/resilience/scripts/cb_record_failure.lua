-- Records a failure. A failure during a half-open trial reopens the circuit
-- immediately (the whole point of the trial was testing recovery - one
-- failure means it hasn't). Otherwise failures accumulate in a rolling
-- window and open the circuit once the threshold is reached.
--
-- KEYS[1]: failures zset key
-- KEYS[2]: state hash key
-- KEYS[3]: trial ticket key
-- ARGV[1]: now_ms
-- ARGV[2]: window_ms
-- ARGV[3]: failure_threshold
--
-- Returns the resulting state: "open" or "closed"

local failures_key = KEYS[1]
local state_key = KEYS[2]
local trial_key = KEYS[3]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local threshold = tonumber(ARGV[3])

local current_state = redis.call("HGET", state_key, "state")

if current_state == "half_open" then
  redis.call("HSET", state_key, "state", "open", "opened_at", now_ms)
  redis.call("EXPIRE", state_key, 3600)
  redis.call("DEL", failures_key)
  redis.call("DEL", trial_key)
  return "open"
end

redis.call("ZADD", failures_key, now_ms, now_ms .. "-" .. math.random(1, 1000000))
redis.call("ZREMRANGEBYSCORE", failures_key, 0, now_ms - window_ms)
redis.call("EXPIRE", failures_key, math.ceil(window_ms / 1000) + 5)

local count = redis.call("ZCARD", failures_key)

if count >= threshold and current_state ~= "open" then
  redis.call("HSET", state_key, "state", "open", "opened_at", now_ms)
  redis.call("EXPIRE", state_key, 3600)
  return "open"
end

return current_state == false and "closed" or current_state
