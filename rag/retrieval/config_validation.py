# -*- coding: utf-8 -*-
"""
rag.retrieval.config_validation - 检索通道「后端装配 vs 通道启用」一致性校验
（对应 Java rag/config/validation/{RetrievalChannelConfigValidator,RetrievalConfigException,RetrievalConfigFailureAnalyzer}）

两层完全正交（对齐 Java 注释）：
    - 后端装配：keyword.type / graph.type 决定后端实现是否注册（none 或非法值 → 通道类根本不进容器）；
    - 通道启用：RAGENT_RETRIEVAL_KEYWORD / RAGENT_RETRIEVAL_GRAPH（RetrievalProperties）在检索期被读取。
有效参与 = 后端已装配 AND 通道已启用。故「type=none 但 enabled=true」是哑标志：用户以为开了该路检索，
实际通道类都没注册——本校验器专抓这种单向矛盾（反过来的 type=es 但 enabled=false 合法，不报）。

纯逻辑、不依赖 wiring（type_reader / enabled_reader 注入），便于单测；wiring 启动期调用 validate_env()
仅告警不阻断（保持既有装配行为不变）；严格校验可 raise RetrievalConfigException（format_failure 对齐
FailureAnalyzer 的 Description / Action 渲染）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, List, Optional

from rag.retrieval.config import RetrievalProperties


@dataclass(frozen=True)
class Violation:
    """一条「后端未装配却开了检索通道」的矛盾（对齐 Java Violation record）"""

    channel_label: str
    type_key: str
    actual_type: str
    required_type: str
    enabled_key: str
    enable_hint: str


@dataclass(frozen=True)
class ChannelSpec:
    """待校验的通道规格：新增一路检索只需在 _SPECS 加一条"""

    label: str
    type_key: str
    required_type: str
    enabled_key: str
    enable_hint: str


_SPECS: List[ChannelSpec] = [
    ChannelSpec("关键词检索", "keyword.type", "es", "RAGENT_RETRIEVAL_KEYWORD", "并配置 rag.keyword.es.*"),
    ChannelSpec("图谱检索", "graph.type", "lightrag", "RAGENT_RETRIEVAL_GRAPH", "并确保 LightRAG 服务可达（rag.graph.lightrag.base-url）"),
]

# type 配置键 → 环境变量（Python 无 Spring 配置，type 走 env，默认 none）
_TYPE_ENV = {
    "keyword.type": "RAGENT_KEYWORD_TYPE",
    "graph.type": "RAGENT_GRAPH_TYPE",
}


def _read_type_env(type_key: str) -> str:
    return os.environ.get(_TYPE_ENV[type_key], "none") or "none"


def validate(
    type_reader: Callable[[str], Optional[str]],
    enabled_reader: Callable[[str], bool],
) -> List[Violation]:
    """校验所有通道，一次性收集全部违规（不撞到第一条就停，便于一次改完）

    Args:
        type_reader:    读取后端类型键的实际值（不存在返回 None）
        enabled_reader: 读取通道启用开关（不存在按 False）
    """
    violations: List[Violation] = []
    for spec in _SPECS:
        actual_type = type_reader(spec.type_key)
        # 后端未装配：type 缺省 / 空白 / 非所需值（大小写不敏感，对齐 @ConditionalOnProperty 判定）
        backend_off = (
            actual_type is None
            or not actual_type.strip()
            or actual_type.strip().lower() != spec.required_type
        )
        if backend_off and enabled_reader(spec.enabled_key):
            violations.append(
                Violation(
                    spec.label,
                    spec.type_key,
                    actual_type if actual_type else "",
                    spec.required_type,
                    spec.enabled_key,
                    spec.enable_hint,
                )
            )
    return violations


def validate_env() -> List[Violation]:
    """从环境变量 + RetrievalProperties 校验（wiring 启动期调用）"""
    props = RetrievalProperties.from_env()
    enabled_map = {
        "RAGENT_RETRIEVAL_KEYWORD": props.keyword_enabled,
        "RAGENT_RETRIEVAL_GRAPH": props.graph_enabled,
    }
    return validate(_read_type_env, lambda key: enabled_map.get(key, False))


class RetrievalConfigException(RuntimeError):
    """检索通道配置矛盾异常（对应 Java RetrievalConfigException + FailureAnalyzer 渲染）"""

    def __init__(self, violations: List[Violation]):
        self.violations = violations
        super().__init__(self.format_failure())

    def format_failure(self) -> str:
        """渲染诊断文案（对齐 Java RetrievalConfigFailureAnalyzer 的 Description / Action）"""
        description = f"检索通道配置存在矛盾（{len(self.violations)} 项）："
        action = "按需二选一修正："
        for index, v in enumerate(self.violations, start=1):
            actual = v.actual_type if v.actual_type else "<未设置>"
            description += (
                f"\n  {index}. {v.enabled_key}=true，但{v.channel_label}后端未启用"
                f"（{v.type_key}={actual}，需为 {v.required_type}）"
                f"\n     → 该通道不会被注册，启用标志形同虚设"
            )
            action += (
                f"\n  {v.channel_label}："
                f"\n    • 启用该检索：设 {v.type_key}={v.required_type} {v.enable_hint}"
                f"\n    • 关闭该通道：设 {v.enabled_key}=false"
            )
        return f"{description}\n{action}"
