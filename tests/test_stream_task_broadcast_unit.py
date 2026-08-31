# -*- coding: utf-8 -*-
"""
P3-2 流式任务跨节点取消广播单测（对齐 Java StreamTaskManager 的 RTopic 机制）

覆盖：
    - 跨节点广播：节点 A cancel() → 广播 → 节点 B 订阅回调 → B 本地收尾（CANCEL+DONE + 关流）
    - 本地直发：cancel() 本节点立即收尾，不依赖订阅回环；广播 + 回环双投递由 CAS 幂等
    - 先取消后注册：register 检测 Redis 标记立即收尾（沿用既有语义）
    - 广播不可用兜底：publish False / 抛异常 → 本地收尾照常（单节点语义不回退）
    - start/stop 生命周期：start 订阅 / stop 幂等关闭 / enabled_cross_node=False 不订阅
    - CacheManager 契约：基类 publish=False / subscribe=None（Memory 后端自然落到兜底路径）
    - RedisCacheManager pub/sub：发布委托 redis.publish；订阅派发 + close 幂等 + 回调异常不终止循环
    - 帧协议一致性锚点：CANCEL/DONE 帧编码与 workflow 协议一致
"""
import asyncio
import json

import pytest

from common.web.sse import encode_event
from rag.service.stream.protocol import CompletionPayload, SSEEventType
from rag.service.stream.task_manager import (
    CANCEL_KEY_PREFIX,
    CANCEL_TOPIC,
    StreamTaskManager,
)
from storage.cache.client import CacheManager, RedisCacheManager


# ==================== 测试桩 ====================


class _StubSender:
    """记录帧与关闭的发送器（对齐 SseQueue 消费面：push + close）"""

    def __init__(self):
        self.frames = []
        self.closed = False

    def push(self, frame: str) -> None:
        self.frames.append(frame)

    def close(self) -> None:
        self.closed = True


class _Handle:
    def __init__(self, close_fn):
        self._close_fn = close_fn

    async def close(self) -> None:
        self._close_fn()


class _Broker:
    """进程内广播中介（模拟 Redis 频道）：subscribers 为各节点订阅回调"""

    def __init__(self):
        self.subscribers = []


class _BroadcastCache(CacheManager):
    """dict 缓存 + 共享 broker 的假广播后端（多 StreamTaskManager 共享 broker 即跨节点）"""

    def __init__(self, broker: _Broker):
        self._data = {}
        self._broker = broker
        self.published = []
        self.publish_enabled = True
        self.publish_error = None

    async def get(self, key):
        return self._data.get(key)

    async def set(self, key, value, ttl=None):
        self._data[key] = value
        return True

    async def delete(self, key):
        self._data.pop(key, None)
        return True

    async def publish(self, channel, message):
        if self.publish_error is not None:
            raise self.publish_error
        if not self.publish_enabled:
            return False
        self.published.append((channel, message))
        for handler in list(self._broker.subscribers):
            handler(message)
        return True

    async def subscribe(self, channel, handler):
        self._broker.subscribers.append(handler)
        return _Handle(lambda: self._broker.subscribers.remove(handler))


def _supplier():
    return CompletionPayload(message_id="m1", title="t")


def _settled_frames(sender: _StubSender) -> list:
    return [f.split("\n", 1)[0] for f in sender.frames]


# ==================== 跨节点广播 ====================


class TestCrossNodeBroadcast:
    def test_cancel_on_node_a_cancels_task_on_node_b(self):
        broker = _Broker()
        cache_a = _BroadcastCache(broker)
        cache_b = _BroadcastCache(broker)
        tm_a = StreamTaskManager(cache=cache_a)
        tm_b = StreamTaskManager(cache=cache_b)

        async def run():
            await tm_b.start()  # B 已订阅
            sender_b = _StubSender()
            tm_b.register("t-1", sender_b, _supplier)
            assert not sender_b.closed

            # 节点 A 取消该任务（A 本地无此任务，cancelLocal no-op）→ 广播 → B 收尾
            await tm_a.cancel("t-1")

            await tm_a.stop()
            await tm_b.stop()
            return sender_b

        sender_b = asyncio.run(run())
        # R-B：载荷为 taskId|requester（cancel() 系统侧 = __system__）
        assert cache_a.published == [(CANCEL_TOPIC, "t-1|__system__")]
        assert sender_b.closed
        assert _settled_frames(sender_b) == [
            f"event: {SSEEventType.CANCEL.value}",
            f"event: {SSEEventType.DONE.value}",
        ]

    def test_local_settle_immediate_and_broadcast_idempotent(self):
        broker = _Broker()
        cache = _BroadcastCache(broker)
        tm = StreamTaskManager(cache=cache)

        async def run():
            await tm.start()
            sender = _StubSender()
            tm.register("t-1", sender, _supplier)
            await tm.cancel("t-1")
            await tm.stop()
            return sender

        sender = asyncio.run(run())
        # 本地直发先收尾；自身订阅回环收到广播（发布者同收，对齐 Java RTopic）由 CAS 幂等
        assert _settled_frames(sender) == [
            f"event: {SSEEventType.CANCEL.value}",
            f"event: {SSEEventType.DONE.value}",
        ]

    def test_registered_marked_cancelled_settles_immediately(self):
        # 两节点共享同一 cache 实例（= 同一 Redis：取消标记同源可见）
        cache = _BroadcastCache(_Broker())
        tm_a = StreamTaskManager(cache=cache)
        tm_b = StreamTaskManager(cache=cache)

        async def run():
            await tm_a.cancel("t-9")  # A 取消（B 尚未注册）
            sender_b = _StubSender()
            tm_b.register("t-9", sender_b, _supplier)  # B 注册时命中标记
            return sender_b

        sender_b = asyncio.run(run())
        # 先取消后注册（跨节点）：register 检测 Redis 标记 → 立即收尾（沿用既有语义）
        assert sender_b.closed


# ==================== 属主复核（R-B，对齐 Java cancelByUser/cancelLocal 复核） ====================


class TestOwnerVerification:
    def test_register_writes_owner_key(self):
        cache = _BroadcastCache(_Broker())
        tm = StreamTaskManager(cache=cache)

        async def run():
            tm.register("t-1", _StubSender(), _supplier, owner_user_id="u1")
            tm.unregister("t-1")
            return dict(cache._data)

        data = asyncio.run(run())
        assert data.get("ragent:stream:owner:t-1") is None  # unregister 已清
        # 注册期间 owner 键存在（重新注册后直接读缓存验证）
        async def run2():
            tm.register("t-1", _StubSender(), _supplier, owner_user_id="u1")
            owner = await cache.get("ragent:stream:owner:t-1")
            return owner

        assert asyncio.run(run2()) == "u1"

    def test_cancel_by_user_owner_mismatch_rejected(self):
        cache = _BroadcastCache(_Broker())
        tm = StreamTaskManager(cache=cache)

        async def run():
            sender = _StubSender()
            tm.register("t-1", sender, _supplier, owner_user_id="u1")
            with pytest.raises(Exception) as exc_info:
                await tm.cancel_by_user("t-1", "u2")
            return sender, exc_info

        sender, exc_info = asyncio.run(run())
        assert not sender.closed  # 越权：不收尾、连 cancelled 都不置
        assert not tm.is_cancelled("t-1")
        assert "任务不存在或已结束" in str(exc_info.value)

    def test_cancel_by_user_owner_match_settles(self):
        cache = _BroadcastCache(_Broker())
        tm = StreamTaskManager(cache=cache)

        async def run():
            sender = _StubSender()
            tm.register("t-1", sender, _supplier, owner_user_id="u1")
            await tm.cancel_by_user("t-1", "u1")
            return sender

        sender = asyncio.run(run())
        assert sender.closed
        assert tm.is_cancelled("t-1")

    def test_remote_node_rejects_mismatched_requester(self):
        # 双节点：B 的任务属主 u1；A 以 u2 身份取消 → 广播到 B 后执行端复核拒绝
        broker = _Broker()
        cache_a = _BroadcastCache(broker)
        cache_b = _BroadcastCache(broker)
        tm_a = StreamTaskManager(cache=cache_a)
        tm_b = StreamTaskManager(cache=cache_b)

        async def run():
            await tm_b.start()
            sender_b = _StubSender()
            tm_b.register("t-1", sender_b, _supplier, owner_user_id="u1")
            try:
                await tm_a.cancel_by_user("t-1", "u2")
            except Exception:
                pass  # A 侧无属主登记（注册没跑在 A），发布端放行；复核在 B 执行端
            ok_sender = _StubSender()
            tm_b.register("t-2", ok_sender, _supplier, owner_user_id="u1")
            await tm_a.cancel_by_user("t-2", "u1")
            await tm_a.stop()
            await tm_b.stop()
            return sender_b, ok_sender

        sender_b, ok_sender = asyncio.run(run())
        assert not sender_b.closed  # 越权广播被 B 执行端复核拒绝
        assert not tm_b.is_cancelled("t-1")
        assert ok_sender.closed  # 属主本人取消正常收尾

    def test_register_recheck_rejects_mismatched_marker(self):
        # 先取消后注册 + 属主不符：标记被忽略（对齐 Java isTaskCancelledInRedis 复核）
        cache = _BroadcastCache(_Broker())
        tm = StreamTaskManager(cache=cache)

        async def run():
            await cache.set("ragent:stream:cancel:t-1", "u2", ttl=60)  # 非属主埋的标记
            sender = _StubSender()
            tm.register("t-1", sender, _supplier, owner_user_id="u1")
            return sender

        sender = asyncio.run(run())
        assert not sender.closed
        assert not tm.is_cancelled("t-1")

    def test_register_recheck_accepts_owner_marker(self):
        cache = _BroadcastCache(_Broker())
        tm = StreamTaskManager(cache=cache)

        async def run():
            await cache.set("ragent:stream:cancel:t-1", "u1", ttl=60)  # 属主本人埋的标记
            sender = _StubSender()
            tm.register("t-1", sender, _supplier, owner_user_id="u1")
            return sender

        sender = asyncio.run(run())
        assert sender.closed  # 复核通过 → 注册即取消补偿
        assert tm.is_cancelled("t-1")

    def test_system_cancel_bypasses_owner(self):
        # 系统侧回收（超时/断连）无条件放行
        cache = _BroadcastCache(_Broker())
        tm = StreamTaskManager(cache=cache)

        async def run():
            sender = _StubSender()
            tm.register("t-1", sender, _supplier, owner_user_id="u1")
            await tm.cancel("t-1")
            return sender

        sender = asyncio.run(run())
        assert sender.closed

    def test_legacy_bare_payload_treated_as_system(self):
        # 滚动升级兼容：老节点广播裸 taskId → 按系统侧收
        cache = _BroadcastCache(_Broker())
        tm = StreamTaskManager(cache=cache)

        async def run():
            await tm.start()  # 订阅建立后老节点裸载荷才会被本节点收到
            sender = _StubSender()
            tm.register("t-1", sender, _supplier, owner_user_id="u1")
            await cache.publish(CANCEL_TOPIC, "t-1")  # 模拟老节点裸载荷（不经 cancel()）
            await tm.stop()
            return sender

        sender = asyncio.run(run())
        assert sender.closed

    def test_cancel_by_user_without_owner_registration_allowed(self):
        # 属主查不到（任务已结束 / 注册未落地）→ 发布端放行；本例本地也无任务 → no-op
        cache = _BroadcastCache(_Broker())
        tm = StreamTaskManager(cache=cache)

        async def run():
            # 未注册任何任务（无属主）
            await tm.cancel_by_user("t-404", "u1")

        asyncio.run(run())  # 不抛异常


# ==================== 兜底与生命周期 ====================


class TestFallbackAndLifecycle:
    def test_publish_unsupported_falls_back_to_local(self):
        cache = _BroadcastCache(_Broker())
        cache.publish_enabled = False
        tm = StreamTaskManager(cache=cache)

        async def run():
            sender = _StubSender()
            tm.register("t-1", sender, _supplier)
            await tm.cancel("t-1")
            return sender

        sender = asyncio.run(run())
        assert cache.published == []
        assert sender.closed

    def test_publish_error_falls_back_to_local(self):
        cache = _BroadcastCache(_Broker())
        cache.publish_error = RuntimeError("redis down")
        tm = StreamTaskManager(cache=cache)

        async def run():
            sender = _StubSender()
            tm.register("t-1", sender, _supplier)
            await tm.cancel("t-1")
            return sender

        sender = asyncio.run(run())
        assert sender.closed

    def test_start_stop_lifecycle(self):
        broker = _Broker()
        cache = _BroadcastCache(broker)
        tm = StreamTaskManager(cache=cache)

        async def run():
            await tm.start()
            subscribed = len(broker.subscribers)
            await tm.stop()
            after_stop = len(broker.subscribers)
            await tm.stop()  # 重复 stop 幂等
            return subscribed, after_stop

        subscribed, after_stop = asyncio.run(run())
        assert subscribed == 1
        assert after_stop == 0

    def test_disabled_cross_node_never_subscribes(self):
        cache = _BroadcastCache(_Broker())
        tm = StreamTaskManager(cache=cache, enabled_cross_node=False)

        async def run():
            await tm.start()
            subscriber_count = len(cache._broker.subscribers)
            sender = _StubSender()
            tm.register("t-1", sender, _supplier)
            await tm.cancel("t-1")
            return subscriber_count, sender, dict(cache._data)

        subscriber_count, sender, data = asyncio.run(run())
        assert subscriber_count == 0  # 不订阅
        assert sender.closed  # 本地照常收尾
        assert data == {}  # 且无 Redis 标记写入

    def test_unregister_clears_marker(self):
        cache = _BroadcastCache(_Broker())
        tm = StreamTaskManager(cache=cache)

        async def run():
            sender = _StubSender()
            tm.register("t-1", sender, _supplier)
            await tm.cancel("t-1")
            tm.unregister("t-1")

        asyncio.run(run())
        assert f"{CANCEL_KEY_PREFIX}t-1" not in cache._data


class TestCacheManagerBroadcastContract:
    def test_base_cache_publish_unsupported(self):
        # 基类默认不支持广播（Memory 后端自然落到 cancel 的本地兜底路径）；
        # 契约用最小具体子类验证（get/set/delete 保持缺省抛 NotImplementedError 的桩形态）
        class _BareCache(CacheManager):
            async def get(self, key):
                return None

            async def set(self, key, value, ttl=None):
                return False

            async def delete(self, key):
                return False

        async def run():
            cache = _BareCache()
            published = await cache.publish(CANCEL_TOPIC, "t-1")
            subscribed = await cache.subscribe(CANCEL_TOPIC, lambda m: None)
            return published, subscribed

        published, subscribed = asyncio.run(run())
        assert published is False
        assert subscribed is None


# ==================== RedisCacheManager pub/sub ====================


class _FakePubSub:
    def __init__(self, queue):
        self._queue = queue
        self.subscribed = []
        self.closed = False

    async def subscribe(self, channel):
        self.subscribed.append(channel)

    async def get_message(self, ignore_subscribe_messages=False, timeout=None):
        if not self._queue:
            await asyncio.sleep(0.01)  # 模拟真实 get_message 的等待语义
            return None
        return self._queue.pop(0)

    async def aclose(self):
        self.closed = True


class _FakeRedis:
    def __init__(self):
        self.published = []
        self.queue = []
        self.pubsub_client = None

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return 1

    def pubsub(self):
        self.pubsub_client = _FakePubSub(self.queue)
        return self.pubsub_client


class TestRedisCacheManagerPubSub:
    def test_publish_delegates_to_redis(self):
        fake = _FakeRedis()
        manager = RedisCacheManager(redis=fake)

        async def run():
            return await manager.publish("ch", "payload")

        assert asyncio.run(run()) is True
        assert fake.published == [("ch", "payload")]

    def test_subscribe_dispatches_and_closes(self):
        fake = _FakeRedis()
        manager = RedisCacheManager(redis=fake)
        received = []

        async def run():
            handle = await manager.subscribe("ch", received.append)
            assert handle is not None
            fake.queue.append({"type": "message", "data": b"t-1"})
            for _ in range(100):
                if received:
                    break
                await asyncio.sleep(0.01)
            await handle.close()
            await handle.close()  # close 幂等

        asyncio.run(run())
        assert received == ["t-1"]
        assert fake.pubsub_client.closed

    def test_subscribe_ignores_non_message_frames(self):
        fake = _FakeRedis()
        manager = RedisCacheManager(redis=fake)
        received = []

        async def run():
            handle = await manager.subscribe("ch", received.append)
            fake.queue.append({"type": "subscribe", "data": 1})  # 订阅确认帧不派发
            fake.queue.append({"type": "message", "data": "raw-str"})
            for _ in range(100):
                if received:
                    break
                await asyncio.sleep(0.01)
            await handle.close()

        asyncio.run(run())
        assert received == ["raw-str"]  # bytes 已解码，确认帧被忽略

    def test_handler_exception_does_not_kill_loop(self):
        fake = _FakeRedis()
        manager = RedisCacheManager(redis=fake)
        received = []

        def bad_handler(message):
            received.append(message)
            raise RuntimeError("boom")

        async def run():
            handle = await manager.subscribe("ch", bad_handler)
            fake.queue.append({"type": "message", "data": "a"})
            fake.queue.append({"type": "message", "data": "b"})
            for _ in range(200):
                if len(received) >= 2:
                    break
                await asyncio.sleep(0.01)
            await handle.close()

        asyncio.run(run())
        assert received == ["a", "b"]  # 单条回调异常不终止消费循环


# ==================== 帧协议一致性（防回归锚点） ====================


def test_cancel_done_frame_encoding():
    # CANCEL + DONE 帧载荷形态（与 workflow 协议一致，JSON + [DONE]）
    payload = CompletionPayload(message_id="m1", title="t")
    cancel_frame = encode_event(SSEEventType.CANCEL.value, payload.to_json())
    done_frame = encode_event(SSEEventType.DONE.value, "[DONE]")
    assert cancel_frame.startswith(f"event: {SSEEventType.CANCEL.value}\ndata: ")
    assert json.loads(cancel_frame.split("data: ", 1)[1].strip())["messageStatus"] == "NORMAL"
    assert done_frame == f"event: {SSEEventType.DONE.value}\ndata: [DONE]\n\n"
