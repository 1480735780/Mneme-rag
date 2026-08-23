# -*- coding: utf-8 -*-
"""
ingestion.engine.engine - 流水线执行引擎（对应 Java IngestionEngine）

基于节点连线的链式执行（async 化）：
    - execute：logs 初始化 → status=RUNNING → 节点映射 → validate_pipeline（沿 next_node_id
      走链环检测 + 引用存在性）→ find_start_nodes（未被引用的节点，必须恰好 1 个）→
      execute_chain（防死循环上限 = 节点数）→ 节点失败置 FAILED+error 并断链 → 正常结束 COMPLETED
    - execute_node：条件不满足 → NodeResult.skip + NodeLog(0ms)；执行异常 → fail + NodeLog；
      每节点记 NodeLog（node_id/node_type/message/duration_ms/success/error/output）

Java 为同步引擎；Python 因节点内 async IO（拉取/嵌入/落库）整体 async，语义不变。

对应 ragent 源码：
    - ingestion/engine/IngestionEngine
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

from common.exception.business import ClientException
from ingestion.domain.context import IngestionContext, NodeLog
from ingestion.domain.enums import IngestionStatus
from ingestion.domain.pipeline import NodeConfig, PipelineDefinition
from ingestion.domain.result import NodeResult
from ingestion.engine.condition_evaluator import ConditionEvaluator
from ingestion.engine.node_output_extractor import NodeOutputExtractor
from ingestion.node.base import IngestionNode

logger = logging.getLogger(__name__)


class IngestionEngine:
    """流水线执行引擎（对应 Java IngestionEngine）"""

    def __init__(
        self,
        nodes: List[IngestionNode],
        condition_evaluator: Optional[ConditionEvaluator] = None,
        output_extractor: Optional[NodeOutputExtractor] = None,
    ):
        self._node_map: Dict[str, IngestionNode] = {n.get_node_type(): n for n in nodes}
        self._condition_evaluator = condition_evaluator or ConditionEvaluator()
        self._output_extractor = output_extractor or NodeOutputExtractor()

    async def execute(self, pipeline: PipelineDefinition,
                      context: IngestionContext) -> IngestionContext:
        """执行流水线；返回携带最终状态/日志/错误的上下文"""
        if context.logs is None:
            context.logs = []
        context.status = IngestionStatus.RUNNING

        node_config_map = self._build_node_config_map(pipeline.nodes)
        self.validate_pipeline(node_config_map)

        start_node_ids = self.find_start_nodes(node_config_map)
        if not start_node_ids:
            raise ClientException("流水线未找到起始节点")
        if len(start_node_ids) > 1:
            raise ClientException(f"流水线存在多个起始节点: {', '.join(start_node_ids)}")
        start_node_id = start_node_ids[0]
        if not start_node_id:
            raise ClientException("流水线未找到起始节点")

        logger.info("流水线从节点开始执行: %s", start_node_id)
        await self.execute_chain(start_node_id, node_config_map, context)

        if context.status == IngestionStatus.RUNNING:
            context.status = IngestionStatus.COMPLETED
        return context

    @staticmethod
    def _build_node_config_map(nodes: Optional[List[NodeConfig]]) -> Dict[str, NodeConfig]:
        if not nodes:
            return {}
        return {node.node_id: node for node in nodes if node is not None}

    def validate_pipeline(self, node_config_map: Dict[str, NodeConfig]) -> None:
        """验证：沿 next_node_id 走链环检测 + 引用存在性（对齐 Java validatePipeline）"""
        visited: set = set()
        for node_id in node_config_map:
            if node_id in visited:
                continue
            path: set = set()
            current: Optional[str] = node_id
            while current is not None:
                if current in path:
                    raise ClientException(f"流水线存在环: {current}")
                path.add(current)
                visited.add(current)
                config = node_config_map.get(current)
                if config is None:
                    break
                next_id = config.next_node_id
                if next_id and next_id.strip():
                    if next_id not in node_config_map:
                        raise ClientException(
                            f"找不到下一个节点: {next_id}，被节点 {current} 引用"
                        )
                    current = next_id
                else:
                    break

    def find_start_nodes(self, node_config_map: Dict[str, NodeConfig]) -> List[str]:
        """起始节点：未被任何节点引用的节点（排序后返回，对齐 Java findStartNodes）"""
        referenced = {
            config.next_node_id
            for config in node_config_map.values()
            if config.next_node_id and config.next_node_id.strip()
        }
        return sorted(
            node_id for node_id in node_config_map if node_id not in referenced
        )

    async def execute_chain(
        self,
        node_id: str,
        node_config_map: Dict[str, NodeConfig],
        context: IngestionContext,
    ) -> None:
        """链式执行节点（防死循环上限 = 节点数，对齐 Java executeChain）"""
        current_node_id: Optional[str] = node_id
        executed_count = 0
        max_nodes = len(node_config_map)
        while current_node_id is not None:
            if executed_count > max_nodes:
                raise ClientException("执行节点数超过上限，可能存在死循环")
            executed_count += 1

            config = node_config_map.get(current_node_id)
            if config is None:
                logger.warning("未找到节点配置: %s", current_node_id)
                break

            logger.info("开始执行节点: %s", current_node_id)
            result = await self.execute_node(context, config)

            if not result.success:
                context.status = IngestionStatus.FAILED
                context.error = result.error
                logger.error("节点 %s 执行失败: %s", current_node_id, result.message)
                break

            if not result.should_continue:
                logger.info("流水线在节点 %s 停止", current_node_id)
                break

            current_node_id = config.next_node_id

        logger.info("流水线执行完成，共执行 %d 个节点", executed_count)

    async def execute_node(self, context: IngestionContext,
                           node_config: NodeConfig) -> NodeResult:
        """执行单个节点：条件检查 → 执行 → NodeLog 落日志（对齐 Java executeNode）"""
        node_type = node_config.node_type
        node_id = node_config.node_id
        node = self._node_map.get(node_type)
        if node is None:
            return NodeResult.fail(RuntimeError(f"未找到节点类型: {node_type}"))

        # 条件检查：配置了非空条件且不满足 → skip
        if node_config.condition is not None and node_config.condition:
            if not self._condition_evaluator.evaluate(context, node_config.condition):
                skip = NodeResult.skip("条件未满足")
                context.logs.append(NodeLog(
                    node_id=node_id,
                    node_type=node_type,
                    message=skip.message,
                    duration_ms=0,
                    success=True,
                    output=self._output_extractor.extract(context, node_config),
                ))
                return skip

        start = time.monotonic()
        try:
            result = await node.execute(context, node_config)
            duration_ms = int((time.monotonic() - start) * 1000)
            context.logs.append(NodeLog(
                node_id=node_id,
                node_type=node_type,
                message=result.message,
                duration_ms=duration_ms,
                success=result.success,
                error=str(result.error) if result.error is not None else None,
                output=self._output_extractor.extract(context, node_config),
            ))
            return result
        except Exception as exc:  # noqa: BLE001 —— 节点执行异常统一 fail + NodeLog
            duration_ms = int((time.monotonic() - start) * 1000)
            context.logs.append(NodeLog(
                node_id=node_id,
                node_type=node_type,
                message=str(exc),
                duration_ms=duration_ms,
                success=False,
                error=str(exc),
                output=self._output_extractor.extract(context, node_config),
            ))
            logger.error("节点 %s 执行失败，耗时 %dms", node_id, duration_ms, exc_info=True)
            return NodeResult.fail(exc)
