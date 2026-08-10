# -*- coding: utf-8 -*-
"""selector.py / model_target.py 冒烟测试：验证导入与核心选择逻辑。"""

from core.llm.config.config import (
    AIModelConfig, ModelCandidate, ModelGroup, ProviderConfig, TierConfig,
)
from core.llm.model.model_target import ModelTarget
from core.llm.model.selector import ModelSelector


class FakeHealthStore:
    """模拟健康存储：qwen-turbo 处于熔断中。"""

    def is_unavailable(self, model_id: str) -> bool:
        return model_id == "qwen-turbo"


def build_config() -> AIModelConfig:
    providers = {"qwen": ProviderConfig(url="https://dashscope.aliyuncs.com")}
    chat = ModelGroup(
        default_tier="standard",
        deep_thinking_tier="deep",
        candidates=[
            ModelCandidate(id="qwen-max", provider="qwen", model="qwen-max",
                           supports_thinking=True, priority=1),
            ModelCandidate(id="qwen-turbo", provider="qwen", model="qwen-turbo",
                           supports_thinking=False, priority=10),
        ],
        tiers={
            "standard": TierConfig(candidates=["qwen-turbo", "qwen-max"], timeout_ms=30000),
            "deep": TierConfig(candidates=["qwen-max"], timeout_ms=60000),
        },
    )
    embedding = ModelGroup(
        default_model="emb-v3",
        candidates=[
            ModelCandidate(id="emb-v2", provider="qwen", model="emb-v2", priority=20),
            ModelCandidate(id="emb-v3", provider="qwen", model="emb-v3", priority=10),
        ],
    )
    return AIModelConfig(providers=providers, chat=chat, embedding=embedding)


def main() -> None:
    config = build_config()

    # 1. 无健康存储：standard 档原样输出，超时预算下沉
    selector = ModelSelector(config)
    targets = selector.select_chat_candidates(thinking=False)
    assert [t.id for t in targets] == ["qwen-turbo", "qwen-max"], targets
    assert all(t.timeout_ms == 30000 for t in targets)
    assert isinstance(targets[0], ModelTarget)
    assert targets[0].provider.url == "https://dashscope.aliyuncs.com"
    print("[OK] 默认档位：顺序与超时预算正确")

    # 2. 健康检查前置：qwen-turbo 熔断被剔除
    selector_hs = ModelSelector(config, FakeHealthStore())
    targets = selector_hs.select_chat_candidates(thinking=False)
    assert [t.id for t in targets] == ["qwen-max"], targets
    print("[OK] 健康过滤：熔断模型被前置剔除")

    # 3. thinking=True：命中 deep 档，且过滤不支持思考的模型
    targets = selector.select_chat_candidates(thinking=True)
    assert [t.id for t in targets] == ["qwen-max"], targets
    assert targets[0].timeout_ms == 60000
    print("[OK] 深度思考：命中 deep 档，超时 60000ms")

    # 4. preferred 置队首 + 档位去重
    targets = selector.select_chat_candidates(
        thinking=False, override="standard", preferred_model_id="qwen-max"
    )
    assert [t.id for t in targets] == ["qwen-max", "qwen-turbo"], targets
    print("[OK] preferred 置队首且去重")

    # 5. thinking 请求下 preferred 不支持思考 → 被忽略
    targets = selector.select_chat_candidates(
        thinking=True, preferred_model_id="qwen-turbo"
    )
    assert [t.id for t in targets] == ["qwen-max"], targets
    print("[OK] 思考请求下不支持思考的 preferred 被忽略")

    # 6. embedding：default_model 置顶 + priority 排序 + 无超时预算
    targets = selector.select_embedding_candidates()
    assert [t.id for t in targets] == ["emb-v3", "emb-v2"], targets
    assert all(t.timeout_ms is None for t in targets)
    print("[OK] embedding：default_model 置顶，无超时预算")

    # 7. chat.py / providers/base.py 导入路径回归
    import core.llm.chat  # noqa: F401
    import core.llm.providers.base  # noqa: F401
    print("[OK] chat.py / providers/base.py 导入路径回归通过")

    print("\n全部冒烟测试通过")


if __name__ == "__main__":
    main()
