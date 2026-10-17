# -*- coding: utf-8 -*-
"""P10.4a T4.1 单测：告警通道（yaml 配置 + D10 三格式 + webhook 投递线程）

覆盖：
    - 配置解析：完整结构 / 缺省值 / 三格式字面量；
    - 校验即拒绝（AlertConfigError）：未知键、未知 format、非法数值、非字符串头、
      非布尔开关、非映射根；出厂样例 resources/alerts.yaml 必须可解析（enabled=false 起步）；
    - 消息渲染：D10 最小形态全字段在文本底稿 + [已恢复] 标记 + 空字段省略；
    - 三格式 payload 形态（feishu/dingtalk 信封 + generic 结构化全字段）；
    - 投递线程：注入 sender 桩断言 payload（不寒真 webhook）；计数/最近触发时间/
      满载丢弃/停机丢弃/投递失败吞掉计数——任何一条未发出的告警都可见（D2/D13）。
"""
from pathlib import Path

import pytest

from core.llm.observation.alert import (
    LEVEL_P1,
    LEVEL_P2,
    FORMATTERS,
    AlertMessage,
    WebhookAlerter,
    format_dingtalk,
    format_feishu,
    format_generic,
    render_text,
)
from core.llm.observation.alert_config import (
    AlertConfig,
    AlertConfigError,
    load_alert_config,
    load_alert_from_dict,
)

_ROOT = Path(__file__).resolve().parent.parent


def _message(**overrides) -> AlertMessage:
    fields = dict(
        level=LEVEL_P1,
        rule="breaker.open",
        current="连续失败 3 次",
        threshold="连续失败达熔断阈值",
        ts="2026-10-17T10:00:00",
        dimensions={"model": "qwen-max"},
        env="dev",
        recovered=False,
        action="检查模型上游可用性",
    )
    fields.update(overrides)
    return AlertMessage(**fields)


# ==================== 配置解析 ====================


def _full_dict() -> dict:
    return {
        "alerts": {
            "enabled": True,
            "env": "prod",
            "format": "dingtalk",
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=x",
            "headers": {"Authorization": "Bearer t"},
            "timeout_seconds": 3.5,
            "silence_seconds": 120.0,
        }
    }


def test_full_structure_parses():
    cfg = load_alert_from_dict(_full_dict())
    assert cfg.enabled is True
    assert cfg.env == "prod"
    assert cfg.fmt == "dingtalk"
    assert cfg.webhook_url.endswith("access_token=x")
    assert cfg.headers == {"Authorization": "Bearer t"}
    assert cfg.timeout_seconds == 3.5
    assert cfg.silence_seconds == 120.0


def test_defaults_minimal_dict():
    cfg = load_alert_from_dict({"alerts": {}})
    assert cfg.enabled is False            # 诚实起点：不显式开就不投递
    assert cfg.env == ""
    assert cfg.fmt == "feishu"             # D10 默认 feishu
    assert cfg.webhook_url == ""
    assert cfg.headers == {}
    assert cfg.timeout_seconds == 5.0
    assert cfg.silence_seconds == 600.0    # D11①：熔断 OPEN 同模型 10min


def test_three_channel_formats_accepted():
    for fmt in ("feishu", "dingtalk", "generic"):
        cfg = load_alert_from_dict({"alerts": {"format": fmt}})
        assert cfg.fmt == fmt


# ==================== 校验即拒绝 ====================


def test_unknown_key_rejected():
    """拼写错的键（webhoook_url）必须拒绝启动——静默失效比报错危险得多"""
    with pytest.raises(AlertConfigError, match="未知键"):
        load_alert_from_dict({"alerts": {"webhoook_url": "https://x"}})


def test_unknown_format_rejected():
    with pytest.raises(AlertConfigError, match="format"):
        load_alert_from_dict({"alerts": {"format": "slack"}})


def test_non_mapping_root_and_missing_alerts_rejected():
    with pytest.raises(AlertConfigError, match="映射"):
        load_alert_from_dict([])
    with pytest.raises(AlertConfigError, match="alerts"):
        load_alert_from_dict({"pricing": {}})


def test_illegal_numeric_and_bool_rejected():
    with pytest.raises(AlertConfigError, match="timeout_seconds"):
        load_alert_from_dict({"alerts": {"timeout_seconds": 0}})
    with pytest.raises(AlertConfigError, match="silence_seconds"):
        load_alert_from_dict({"alerts": {"silence_seconds": -1}})
    with pytest.raises(AlertConfigError, match="timeout_seconds 须为数值"):
        load_alert_from_dict({"alerts": {"timeout_seconds": "slow"}})
    with pytest.raises(AlertConfigError, match="enabled"):
        load_alert_from_dict({"alerts": {"enabled": "yes"}})


def test_illegal_headers_rejected():
    with pytest.raises(AlertConfigError, match="headers"):
        load_alert_from_dict({"alerts": {"headers": ["Authorization"]}})
    with pytest.raises(AlertConfigError, match="headers"):
        load_alert_from_dict({"alerts": {"headers": {"Authorization": 1}}})


def test_load_from_file_and_missing_file(tmp_path):
    cfg_path = tmp_path / "alerts.yaml"
    cfg_path.write_text(
        "alerts:\n  enabled: true\n  format: generic\n  webhook_url: 'https://in'\n",
        encoding="utf-8",
    )
    cfg = load_alert_config(str(cfg_path))
    assert cfg.enabled is True and cfg.fmt == "generic"
    with pytest.raises(FileNotFoundError):
        load_alert_config(str(tmp_path / "nope.yaml"))


def test_shipped_sample_alerts_yaml_parses():
    """出厂样例必须可解析（enabled=false 诚实起点；silence 10min 进 D11① 契约）"""
    cfg = load_alert_config(str(_ROOT / "resources" / "alerts.yaml"))
    assert cfg.enabled is False
    assert cfg.fmt == "feishu"
    assert cfg.silence_seconds == 600.0


# ==================== 消息渲染（D10 最小形态） ====================


def test_render_text_contains_all_d10_fields():
    text = render_text(_message())
    assert text.startswith("[P1] breaker.open（env=dev）")
    assert "当前值: 连续失败 3 次 | 阈值: 连续失败达熔断阈值" in text
    assert "维度: model=qwen-max" in text
    assert "时间: 2026-10-17T10:00:00" in text
    assert "建议动作: 检查模型上游可用性" in text
    assert "已恢复" not in text  # 未恢复不带标记


def test_render_text_recovered_and_optional_fields_omitted():
    text = render_text(
        _message(level=LEVEL_P2, recovered=True, env="", dimensions={}, action="")
    )
    assert "[P2][已恢复] breaker.open" in text  # D10 [已恢复] 标记
    assert "env=" not in text       # 空 env 省略
    assert "维度:" not in text      # 空维度省略
    assert "建议动作:" not in text  # 空建议省略


# ==================== 三格式 payload（可配置断言，不发真网） ====================


def test_format_payloads_for_three_channels():
    msg = _message()
    feishu = format_feishu(msg)
    assert set(feishu) == {"msg_type", "content"}
    assert feishu["msg_type"] == "text"
    assert "维度: model=qwen-max" in feishu["content"]["text"]

    dingtalk = format_dingtalk(msg)
    assert set(dingtalk) == {"msgtype", "text"}
    assert "维度: model=qwen-max" in dingtalk["text"]["content"]

    generic = format_generic(msg)
    for key in ("level", "rule", "current", "threshold", "dimensions", "env",
                "ts", "recovered", "action", "text"):
        assert key in generic
    assert generic["dimensions"] == {"model": "qwen-max"}
    assert generic["text"] == render_text(msg)

    # FORMATTERS 注册表随配置切换（T4.1 验收：消息格式可配置）
    assert set(FORMATTERS) == {"feishu", "dingtalk", "generic"}


# ==================== 投递线程与计数 ====================


def _alerter(config: AlertConfig, sender=None, **kwargs) -> WebhookAlerter:
    return WebhookAlerter(config, sender=sender, **kwargs)


def test_alerter_delivers_injected_payload_and_accounts():
    sent: list = []
    cfg = load_alert_from_dict(_full_dict())  # enabled + url
    alerter = _alerter(cfg, sender=sent.append)
    assert alerter.delivers() is True

    assert alerter.send(_message()) is True
    alerter.stop(timeout=2.0)
    assert len(sent) == 1
    # 注入 sender 直收格式化 payload（_full_dict 配置 format=dingtalk → 信封同形态）
    assert "维度: model=qwen-max" in sent[0]["text"]["content"]
    stats = alerter.stats_snapshot()
    assert stats["accepted"] == 1 and stats["delivered"] == 1
    assert stats["last_sent_by_rule"] == {"breaker.open": "2026-10-17T10:00:00"}  # D13


def test_alerter_disabled_channel_skips_and_counts():
    sent: list = []
    cfg = AlertConfig()  # 总开关 false
    alerter = _alerter(cfg, sender=sent.append)
    assert alerter.delivers() is False
    assert alerter.send(_message()) is False
    alerter.stop(timeout=0.1)
    assert sent == []
    assert alerter.stats_snapshot()["skipped_disabled"] == 1
    assert alerter._worker is None  # 未启用路径不生产线程


def test_alerter_enabled_without_url_not_delivering():
    cfg = AlertConfig(enabled=True)  # 开关开但无 webhook_url → 仍不可投递
    alerter = _alerter(cfg)
    assert alerter.delivers() is False


def test_alerter_full_queue_drops_and_counts():
    """满即丢 + 计数（D2）：注入 sender 桩且线程虚位，探测背压路径"""
    cfg = load_alert_from_dict(_full_dict())
    alerter = _alerter(cfg, sender=lambda _payload: None, maxsize=1)
    alerter._ensure_worker = lambda: None  # 单测：线程不启动，队列保持满载

    assert alerter.send(_message(ts="t1")) is True
    assert alerter.send(_message(ts="t2")) is False  # 满载丢弃
    stats = alerter.stats_snapshot()
    assert stats["accepted"] == 1 and stats["dropped_full"] == 1
    # 丢的不假记账：最近触发时间只记真入队那条
    assert stats["last_sent_by_rule"] == {"breaker.open": "t1"}


def test_alerter_send_after_stop_dropped():
    sent: list = []
    cfg = load_alert_from_dict(_full_dict())
    alerter = _alerter(cfg, sender=sent.append)
    alerter.stop(timeout=0.5)
    assert alerter.send(_message()) is False
    assert alerter.stats_snapshot()["dropped_full"] == 1


def test_alerter_send_error_swallowed_and_counted():
    """投递失败吞掉 + 计数（不重试，D10）；通道不失活，后续消息照常投"""
    sent: list = []
    state = {"raised": False}

    def _faulty_sender(payload):
        if not state["raised"]:
            state["raised"] = True
            raise RuntimeError("webhook down")
        sent.append(payload)

    cfg = load_alert_from_dict(_full_dict())
    alerter = _alerter(cfg, sender=_faulty_sender)

    assert alerter.send(_message()) is True          # P1 → 投递失败
    assert alerter.send(_message(ts="t2", level=LEVEL_P2)) is True  # P2 → 投递成功
    alerter.stop(timeout=2.0)
    stats = alerter.stats_snapshot()
    assert stats["delivered"] == 1 and stats["send_errors"] == 1
    assert len(sent) == 1 and "t2" == stats["last_sent_by_rule"]["breaker.open"]
