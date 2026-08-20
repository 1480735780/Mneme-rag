# -*- coding: utf-8 -*-
"""
rag.service.trace_service - RAG 链路追踪 service（对应 Java RagTraceRecordService + RagTraceQueryService）

域职责（M5 5.1 + 5.2）：
    - RagTraceRecordService（5.1）：startRun/finishRun/startNode/finishNode 落库
      ——由 stream/trace_runner（M3）经鸭子类型 record_service 调用（start_run/finish_run/start_node/finish_node）。
      **错误信息截断由 trace_runner 的 _properties.max_error_length 在上游完成**（RagTraceContext），
      本服务只做持久化，不重复截断；
    - RagTraceQueryService（5.2）：pageRuns / detail / listNodes 后台查询，富化展示字段
      ——username（注入式 resolver，P5 用户域未纳入）、ttftMs（USER_TTFT 节点 duration_ms）、
      question（解析 extra_data JSON 的 question 字段，对齐 parseQuestion）。

方案 B：本层输出 snake_case dict（trace_id/duration_ms/start_time 等），camelCase 序列化由
controller 边界 pydantic VO（vo.py，步骤 5.7）完成。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.RagTraceRecordService / RagTraceQueryService
    - com.nageoffer.ai.ragent.rag.service.impl.RagTraceRecordServiceImpl / RagTraceQueryServiceImpl
    - com.nageoffer.ai.ragent.rag.controller.vo.RagTraceRunVO / RagTraceNodeVO / RagTraceDetailVO
"""

from __future__ import annotations

import json
import logging
from typing import Callable, Dict, List, Optional

from rag.dao.trace_dao import RagTraceNodeDao, RagTraceRunDao

logger = logging.getLogger(__name__)

# 分页缺省（对齐 MyBatis-Plus Page 常用缺省）
DEFAULT_CURRENT = 1
DEFAULT_SIZE = 10


class RagTraceRecordService:
    """追踪记录服务（5.1，对应 Java RagTraceRecordServiceImpl）：start/finish 落库"""

    def __init__(self, run_dao: RagTraceRunDao, node_dao: RagTraceNodeDao):
        self._run_dao = run_dao
        self._node_dao = node_dao

    # ---- run（对齐 startRun/finishRun） ----

    def start_run(self, run: Dict) -> Optional[str]:
        """记录 run 开始（对应 startRun：runMapper.insert），返回主键 ID"""
        return self._run_dao.insert(run)

    def finish_run(
        self,
        trace_id: str,
        status: Optional[str],
        error_message: Optional[str],
        end_time: str,
        duration_ms: int,
    ) -> bool:
        """完成 run（对应 finishRun：按 trace_id 更新收尾字段），返回是否命中"""
        return self._run_dao.finish(trace_id, status, end_time, duration_ms, error_message)

    # ---- node（对齐 startNode/finishNode） ----

    def start_node(self, node: Dict) -> Optional[str]:
        """记录 node 开始（对应 startNode：nodeMapper.insert），返回主键 ID"""
        return self._node_dao.insert(node)

    def finish_node(
        self,
        trace_id: str,
        node_id: str,
        status: Optional[str],
        end_time: str,
        duration_ms: int,
        error_message: Optional[str] = None,
    ) -> bool:
        """完成 node（对应 finishNode：按 trace_id+node_id 更新收尾字段），返回是否命中"""
        return self._node_dao.finish(trace_id, node_id, status, end_time, duration_ms, error_message)


class RagTraceQueryService:
    """追踪查询服务（5.2，对应 Java RagTraceQueryServiceImpl）：pageRuns/detail/listNodes"""

    def __init__(
        self,
        run_dao: RagTraceRunDao,
        node_dao: RagTraceNodeDao,
        username_resolver: Optional[Callable[[str], Optional[str]]] = None,
    ):
        self._run_dao = run_dao
        self._node_dao = node_dao
        # 用户域未纳入（P5）；缺省不解析用户名（None），注入式 resolver 可替换（对齐 Java loadUsernameMap）
        self._username_resolver = username_resolver

    def page_runs(
        self,
        current: Optional[int] = DEFAULT_CURRENT,
        size: Optional[int] = DEFAULT_SIZE,
        trace_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        task_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict:
        """
        分页查询 run（start_time 倒序 + 可选过滤，对齐 pageRuns）
        返回 {records, total, current, size}；每记录富化 username/ttftMs/question。
        """
        current = current if current and current >= 1 else DEFAULT_CURRENT
        size_val = DEFAULT_SIZE if size is None else max(0, size)
        offset = (current - 1) * size_val if size_val > 0 else 0
        rows, total = self._run_dao.page_query(
            limit=size_val,
            offset=offset,
            trace_id=_blank_to_none(trace_id),
            conversation_id=_blank_to_none(conversation_id),
            task_id=_blank_to_none(task_id),
            status=_blank_to_none(status),
        )
        # 批量 loadTtftMap：一次查询整页 USER_TTFT 时长，避免逐 trace N+1（对齐 Java loadTtftMap）
        trace_ids = [r.get("trace_id") for r in rows if r.get("trace_id")]
        ttft_map = self._node_dao.get_ttft_durations(trace_ids)
        records = [
            _to_run_vo(r, self._resolve_username(r), ttft_map.get(r.get("trace_id")))
            for r in rows
        ]
        return {"records": records, "total": total, "current": current, "size": size_val}

    def detail(self, trace_id: str) -> Optional[Dict]:
        """run 详情（对齐 detail：run VO + nodes）；traceId 不存在返回 None"""
        run = self._run_dao.find_by_trace_id(trace_id)
        if run is None:
            return None
        run_vo = _to_run_vo(run, self._resolve_username(run), self._load_ttft(trace_id))
        nodes = [_to_node_vo(n) for n in self._node_dao.list_by_trace_id(trace_id)]
        return {"run": run_vo, "nodes": nodes}

    def list_nodes(self, trace_id: str) -> List[Dict]:
        """节点列表（对齐 listNodes；start_time asc + id asc）"""
        return [_to_node_vo(n) for n in self._node_dao.list_by_trace_id(trace_id)]

    # ==================== 内部辅助 ====================

    def _resolve_username(self, run: Dict) -> Optional[str]:
        """解析用户名（对齐 resolveUsername）；无 resolver 或不命中返回 None"""
        if self._username_resolver is None:
            return None
        user_id = run.get("user_id")
        if not user_id:
            return None
        return self._username_resolver(user_id)

    def _load_ttft(self, trace_id: Optional[str]) -> Optional[int]:
        """取单 trace 的 USER_TTFT 节点 duration_ms（详情用；复用批量 dao 方法单元素查询）"""
        if not trace_id:
            return None
        return self._node_dao.get_ttft_durations([trace_id]).get(trace_id)


# ==================== VO / 工具（方案 B：snake_case 输出） ====================


def _to_run_vo(run: Dict, username: Optional[str], ttft_ms: Optional[int]) -> Dict:
    """run 行 → VO dict（对齐 RagTraceRunVO）"""
    return {
        "trace_id": run.get("trace_id"),
        "trace_name": run.get("trace_name"),
        "entry_method": run.get("entry_method"),
        "conversation_id": run.get("conversation_id"),
        "task_id": run.get("task_id"),
        "user_id": run.get("user_id"),
        "username": username,
        "status": run.get("status"),
        "error_message": run.get("error_message"),
        "duration_ms": run.get("duration_ms"),
        "ttft_ms": ttft_ms,
        "question": _parse_question(run.get("extra_data")),
        "start_time": run.get("start_time"),
        "end_time": run.get("end_time"),
    }


def _to_node_vo(node: Dict) -> Dict:
    """node 行 → VO dict（对齐 RagTraceNodeVO）"""
    return {
        "trace_id": node.get("trace_id"),
        "node_id": node.get("node_id"),
        "parent_node_id": node.get("parent_node_id"),
        "depth": node.get("depth"),
        "node_type": node.get("node_type"),
        "node_name": node.get("node_name"),
        "class_name": node.get("class_name"),
        "method_name": node.get("method_name"),
        "status": node.get("status"),
        "error_message": node.get("error_message"),
        "duration_ms": node.get("duration_ms"),
        "start_time": node.get("start_time"),
        "end_time": node.get("end_time"),
    }


def _parse_question(extra_data) -> Optional[str]:
    """从 extra_data（JSONB，str/dict 兼容）取 question（对齐 parseQuestion；非法 JSON/缺失返回 None）"""
    if extra_data is None:
        return None
    if isinstance(extra_data, dict):
        return extra_data.get("question")
    if isinstance(extra_data, str):
        if not extra_data.strip():
            return None
        try:
            return json.loads(extra_data).get("question")
        except Exception:  # noqa: BLE001 —— 非法 JSON 视为无 question，不阻断
            return None
    return None


def _blank_to_none(value: Optional[str]) -> Optional[str]:
    """空白字符串归一 None（对齐 StrUtil.isNotBlank 语义）"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None