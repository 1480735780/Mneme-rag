-- 原子条件释放（许可归还 / 取消清理共用，对齐 Java releaseHeldPermit 语义）
--
-- 单次 EVAL 保证「真实持释放才归还许可」，三问题一修：
--   1) P-R1 迟到/重复释放超发：ZREM(held, rid)==1 才 INCR（1:1 上界，已释放/已回收的不再还）；
--   2) 释放非原子：zrem/incr/delete 三跳合并单次 EVAL——父任务在任一点被取消都不会留下
--      「held 已删、许可未还」的永久 -1 半态（脚本原子执行：要么全成要么全无）；
--   3) 在途取消立即还槽：_cleanup_waiting 对已 held 的 entry 走本脚本，不等 lease 兜底回收。
--
-- Keys:  1=held ZSet  2=permits 计数  3=entry 前缀（三者同 {name} 哈希槽）
-- Args:  1=request_id
-- Returns: 1=真实持释放（许可已还）；0=非持有（未超发，仅清理 entry）
local held_key = KEYS[1]
local permits_key = KEYS[2]
local entry_prefix = KEYS[3]
local request_id = ARGV[1]

local removed = redis.call('ZREM', held_key, request_id)
if removed == 1 then
    redis.call('INCR', permits_key)
end
redis.call('DEL', entry_prefix .. request_id)
return removed
