-- Checks a team's current spend against its daily/monthly limits, then
-- charges the given cost. Blocking is based on spend *before* this call
-- (not spend + cost), so the call that finally crosses the limit still
-- completes and gets charged - it's the *next* call that gets blocked. That
-- avoids the alternative of blocking a request after the underlying LLM
-- provider has already been paid for it.
--
-- KEYS[1]: daily spend key
-- KEYS[2]: monthly spend key
-- ARGV[1]: daily_limit
-- ARGV[2]: monthly_limit
-- ARGV[3]: cost (0 for a pre-flight check-only call)
-- ARGV[4]: daily_ttl_seconds
-- ARGV[5]: monthly_ttl_seconds
--
-- Returns {allowed (0/1), daily_spend (string), monthly_spend (string), warning (0/1)}

local daily_key = KEYS[1]
local monthly_key = KEYS[2]
local daily_limit = tonumber(ARGV[1])
local monthly_limit = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local daily_ttl = tonumber(ARGV[4])
local monthly_ttl = tonumber(ARGV[5])

local daily_spend = tonumber(redis.call("GET", daily_key)) or 0
local monthly_spend = tonumber(redis.call("GET", monthly_key)) or 0

if daily_spend >= daily_limit or monthly_spend >= monthly_limit then
  return {0, tostring(daily_spend), tostring(monthly_spend), 0}
end

local new_daily = daily_spend
local new_monthly = monthly_spend

if cost > 0 then
  new_daily = tonumber(redis.call("INCRBYFLOAT", daily_key, cost))
  redis.call("EXPIRE", daily_key, daily_ttl)
  new_monthly = tonumber(redis.call("INCRBYFLOAT", monthly_key, cost))
  redis.call("EXPIRE", monthly_key, monthly_ttl)
end

local warning = 0
if new_daily > 0.8 * daily_limit or new_monthly > 0.8 * monthly_limit then
  warning = 1
end

return {1, tostring(new_daily), tostring(new_monthly), warning}
