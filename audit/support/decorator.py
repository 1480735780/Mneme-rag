# -*- coding: utf-8 -*-
"""
audit.support.decorator - 业务变更审计装饰器（对应 Java mzt-biz-log @LogRecord 切面语义，D4 决策）

@record_biz_change(biz_type, operation, desc) 包装业务写方法：
    - 成功：从 BizChangeLogContext 读快照（biz_id / snapshot / skip），skip 则跳过；
      否则调 BizChangeLogRecordService.record 落库（success=True，含 class/method 名）
    - 失败：落库 success=False + errorMessage（desc + 异常信息），随后**原样重抛——绝不吞业务异常**
    - 无论成败，finally 清理审计上下文（防跨请求泄漏）

RecordService 注入：宿主（app/wiring.py，A5 接线）调 set_record_service() 注册；
未注册时审计旁路降级（warn 不打断主流程）——审计是旁路，失败不影响业务。
操作人三元组由 UserContextOperatorService 从 UserContext 提取，未登录回落 SYSTEM。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.audit.aspect / mzt-biz-log @LogRecord(success/fail/type/subType/bizNo/extra)
    - 各 ServiceImpl 上的 @LogRecord 使用点（KnowledgeBaseServiceImpl 等）
"""
from __future__ import annotations

import asyncio
import logging
from functools import wraps
from typing import Any, Callable, Optional

from audit.service.operator_service import UserContextOperatorService
from audit.service.record_service import BizChangeLogRecordService
from audit.support.context import BizChangeLogContext

logger = logging.getLogger(__name__)

_record_service: Optional[BizChangeLogRecordService] = None


def set_record_service(service: Optional[BizChangeLogRecordService]) -> None:
    """注册审计记录服务（wiring 阶段调用；传 None 解除注册，用于测试隔离）"""
    global _record_service
    _record_service = service


def record_biz_change(
    biz_type: str, operation: str, desc: str
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """业务变更审计装饰器（async / sync 双兼容）

    Args:
        biz_type:    业务类型（对应 @LogRecord type，如 "KNOWLEDGE_BASE"）
        operation:   操作类型（对应 subType，如 "CREATE"）
        desc:        操作描述（对应 success/fail 模板文本，如 "创建知识库"）
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        class_name = _resolve_class_name(func)
        method_name = _resolve_method_name(func)

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = await func(*args, **kwargs)
            except Exception as exc:
                _record_failure(biz_type, operation, desc, class_name, method_name, exc)
                raise
            else:
                _record_success(biz_type, operation, desc, class_name, method_name)
                return result
            finally:
                BizChangeLogContext().clear()

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                _record_failure(biz_type, operation, desc, class_name, method_name, exc)
                raise
            else:
                _record_success(biz_type, operation, desc, class_name, method_name)
                return result
            finally:
                BizChangeLogContext().clear()

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator


# ------------------------------------------------------------------ #
# 落库辅助
# ------------------------------------------------------------------ #


def _record_success(biz_type: str, operation: str, desc: str, class_name: str, method_name: str) -> None:
    if _record_service is None:
        logger.warning("审计记录服务未注册，跳过业务变更日志: %s.%s", class_name, method_name)
        return
    ctx = BizChangeLogContext().current()
    if ctx["skip"]:
        return
    try:
        _record_service.record(
            biz_type=biz_type,
            biz_id=ctx["biz_id"],
            operation_type=operation,
            action_desc=desc,
            snapshot=ctx["snapshot"],
            operator=UserContextOperatorService(),
            class_name=class_name,
            method_name=method_name,
            success=True,
        )
    except Exception:
        # 审计落库失败不打断业务（旁路语义）
        logger.exception("业务变更日志落库失败: %s.%s", class_name, method_name)


def _record_failure(
    biz_type: str, operation: str, desc: str, class_name: str, method_name: str, exc: Exception
) -> None:
    if _record_service is None:
        logger.warning("审计记录服务未注册，跳过业务变更日志: %s.%s", class_name, method_name)
        return
    ctx = BizChangeLogContext().current()
    if ctx["skip"]:
        return
    error_message = f"{desc}失败：{exc}" if desc else f"{class_name}.{method_name}失败：{exc}"
    try:
        _record_service.record(
            biz_type=biz_type,
            biz_id=ctx["biz_id"],
            operation_type=operation,
            action_desc=desc,
            snapshot=ctx["snapshot"],
            operator=UserContextOperatorService(),
            class_name=class_name,
            method_name=method_name,
            success=False,
            error_message=error_message,
        )
    except Exception:
        # 审计落库失败不打断业务（旁路语义）
        logger.exception("业务变更日志落库失败: %s.%s", class_name, method_name)


# ------------------------------------------------------------------ #
# 调用点定位（对应 Java CodeVariable 的 className/methodName）
# ------------------------------------------------------------------ #


def _resolve_class_name(func: Callable[..., Any]) -> str:
    """方法 → 所属类名；顶层函数回落模块名（对应 Java CodeVariable.getClassName）"""
    qualname = getattr(func, "__qualname__", "")
    if "." in qualname:
        return qualname.rsplit(".", 1)[0]
    module = getattr(func, "__module__", "") or ""
    return module.rsplit(".", 1)[-1] or "unknown"


def _resolve_method_name(func: Callable[..., Any]) -> str:
    """取方法名（对应 Java CodeVariable.getMethodName）"""
    qualname = getattr(func, "__qualname__", "") or ""
    return qualname.rsplit(".", 1)[-1] or "unknown"
