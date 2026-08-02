-- Decides whether a request may proceed to this provider/model, and
-- atomically claims the single half-open trial ticket if the cooldown has
-- elapsed. Concurrent callers during the cooldown all see "open" except the
-- one that wins the SET NX race, who sees "half_open_trial" and gets to
-- actually attempt the request.
--
-- KEYS[1]: state hash key
-- KEYS[2]: trial ticket key
-- ARGV[1]: now_ms
-- ARGV[2]: cooldown_ms
-- ARGV[3]: trial_ttl_ms
--
-- Returns one of: "closed", "open", "half_open_trial"

local state_key = KEYS[1]
local trial_key = KEYS[2]
local now_ms = tonumber(ARGV[1])
local cooldown_ms = tonumber(ARGV[2])
local trial_ttl_ms = tonumber(ARGV[3])

local state = redis.call("HGET", state_key, "state")

if state == false or state == "closed" then
  return "closed"
end

if state == "open" then
  local opened_at = tonumber(redis.call("HGET", state_key, "opened_at"))
  if opened_at ~= nil and (now_ms - opened_at) >= cooldown_ms then
    local claimed = redis.call("SET", trial_key, "1", "NX", "PX", trial_ttl_ms)
    if claimed then
      redis.call("HSET", state_key, "state", "half_open")
      return "half_open_trial"
    end
  end
  return "open"
end

-- state == "half_open": another request already holds the trial ticket.
return "open"
