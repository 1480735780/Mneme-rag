-- queue_claim_atomic.lua
-- Redis 公平限流器：原子「清扫死队头 + 判头 + 消耗许可 + 登记持有」（对齐 Java queue_claim_atomic.lua 语义）
--
-- KEYS[1] = 等待队列 ZSet（member=request_id，score=排队序号）
-- KEYS[2] = 持有登记 ZSet（member=request_id，score=到期 unix 秒）
-- KEYS[3] = 可用许可计数 key
-- KEYS[4] = entry 标记 key 前缀（接 request_id；所有 key 带同一 {name} 哈希槽）
-- ARGV[1] = 本次请求 request_id
-- ARGV[2] = 当前 unix 秒（由客户端传入，免依赖 redis TIME）
-- ARGV[3] = lease 秒数（持有时长）
--
-- 全程单次 EVAL 原子执行，多实例/多进程安全。

local queue, held, permits_key, entry_prefix = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
local request_id, now, lease_s = ARGV[1], tonumber(ARGV[2]), tonumber(ARGV[3])

-- 1) 清扫死队头：等待态 entry 已不存在（排队进程崩溃/取消已删除）→ 逐个弹出，
--    直到遇到存活队头或空队。防止死头永踞队头卡死整条队列（6.3 核验 bug 1）。
while true do
  local head = redis.call('ZRANGE', queue, 0, 0)
  if #head == 0 then break end
  if redis.call('EXISTS', entry_prefix .. head[1]) == 0 then
    redis.call('ZREM', queue, head[1])
  else
    break
  end
end

-- 2) 许可耗尽时回收过期持有者：持有登记 ZSet 中 score<=now 的成员已过 lease，
--    持有者崩溃未 release → 归还许可并移除登记（6.3 核验 bug 3/4）。
local permits = tonumber(redis.call('GET', permits_key) or '0')
if permits <= 0 then
  local expired = redis.call('ZRANGEBYSCORE', held, 0, now)
  for i = 1, #expired do
    redis.call('ZREM', held, expired[i])
    redis.call('INCR', permits_key)
  end
  permits = tonumber(redis.call('GET', permits_key) or '0')
end

-- 3) 许可不足 / 非队头 → 不抢占
if permits <= 0 then return {0} end
local head = redis.call('ZRANGE', queue, 0, 0)
if #head == 0 or head[1] ~= request_id then return {0} end

-- 4) 抢占 + 登记持有：出队、扣许可、entry 改写为 held 并 PX lease、记入持有登记
--    （持有期 entry 存活，lease 到期后由第 2 步回收，防容量单向泄漏）。
redis.call('ZREM', queue, request_id)
redis.call('DECR', permits_key)
redis.call('SET', entry_prefix .. request_id, 'held', 'PX', lease_s * 1000)
redis.call('ZADD', held, now + lease_s, request_id)
return {1}