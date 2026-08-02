-- Records a success. Only meaningful state change is half_open -> closed
-- (the trial passed); a success while already closed is a no-op.
--
-- KEYS[1]: state hash key
-- KEYS[2]: failures zset key
-- KEYS[3]: trial ticket key
--
-- Returns {to_state, from_state} - from_state == to_state means no
-- transition happened (metrics code uses this to decide what to count).

local state_key = KEYS[1]
local failures_key = KEYS[2]
local trial_key = KEYS[3]

local current_state = redis.call("HGET", state_key, "state")
local from_state = current_state == false and "closed" or current_state

if current_state == "half_open" then
  redis.call("HSET", state_key, "state", "closed")
  redis.call("DEL", failures_key)
  redis.call("DEL", trial_key)
  return {"closed", from_state}
end

return {from_state, from_state}
