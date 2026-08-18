"""
基于 LLM 的 MCP 参数提取器（对应 Java LLMMcpParameterExtractor）

流程（对齐 Java extractParameters）：
    无参工具（input_schema 无 properties）→ 直接 SUCCESS、空参调用，不发 LLM；
    有参工具 → system = 自定义提示词或默认模板、user = 渲染 mcp-parameter-extract-user.st
    （{tool_definition} + {user_question}）→ LLM 调用（temperature 0.1 / topP 0.3 / thinking false）
    → 按 schema 逐参分类：
        - JSON 解析失败 / 空响应 / 非对象 / 值类型或枚举非法 = 模型未遵守协议或输出畸形 → FAILED（不调用工具）
        - 必填无默认参数缺失或为 null = 用户确实没提供该信息 → NEED_CLARIFICATION（缺项列 missing_required）
        - 其余正常提取 → SUCCESS + fillDefaults 补默认值
    「值非法一律 FAILED、绝不静默丢弃」：可选/有默认字段值非法同样 FAILED，
    防止过滤条件被无声移除（如误判枚举 → 时间过滤消失 → 范围扩大）。

MVP 差异（相对 Java）：
    - LLM 为异步（Python 引擎 async），接口 await llm_service.chat；
    - 非有限 JSON 数值（NaN/Infinity）以 json.loads parse_constant 在解析期拒绝，
      Java 是在逐字段转换时判非法——两者结局一致（FAILED），仅失败归类粒度不同。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.mcp.LLMMcpParameterExtractor
    - com.nageoffer.ai.ragent.rag.constant.RAGConstant.MCP_PARAMETER_EXTRACT_PROMPT_PATH / _USER_PROMPT_PATH
"""
from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from core.llm.chat import LLMService
from core.llm.schema import ChatRequest, Message
from rag.mcp.extractor import McpParameterExtractor
from rag.mcp.model import McpToolDefinition
from rag.mcp.result import McpExtractionResult, Status
from rag.prompt.formatter import PromptTemplateLoader

logger = logging.getLogger(__name__)

# 模板路径（对应 Java RAGConstant.MCP_PARAMETER_EXTRACT_PROMPT_PATH / MCP_PARAMETER_EXTRACT_USER_PROMPT_PATH）
MCP_PARAMETER_EXTRACT_PROMPT_PATH = "prompt/mcp-parameter-extract.st"
MCP_PARAMETER_EXTRACT_USER_PROMPT_PATH = "prompt/mcp-parameter-extract-user.st"

# 模型偶发包裹 Markdown 代码围栏时剥离（对应 Java LLMResponseCleaner.stripMarkdownCodeFence）
_CODE_FENCE = re.compile(r"```(?:json)?\s*|\s*```", re.IGNORECASE)

# 类型收敛 / 枚举校验失败的哨兵（None 是合法 JSON 值，不能用 None 表达"转换失败"）
_COERCE_FAILED = object()


def _reject_nonfinite_constant(constant: str) -> None:
    """JSON 宽松解析接受 NaN/Infinity 字面量，但它们不是合法 JSON 数值：解析期拒绝"""
    raise ValueError(f"非有限 JSON 数值: {constant}")


def _preview(raw: Optional[str]) -> str:
    """日志预览：截断到 200 字符（对应 Java LogSafe.preview）"""
    if raw is None:
        return ""
    text = raw.strip()
    return text[:200] + ("..." if len(text) > 200 else "")


class LLMMcpParameterExtractor(McpParameterExtractor):
    """
    基于 LLM 的 MCP 参数提取器（对应 Java LLMMcpParameterExtractor）

    Args:
        llm_service:     LLM 服务（async chat）
        template_loader: 模板加载器，默认 PromptTemplateLoader()
    """

    def __init__(
        self,
        llm_service: LLMService,
        template_loader: Optional[PromptTemplateLoader] = None,
    ):
        self._llm = llm_service
        self._template_loader = template_loader or PromptTemplateLoader()

    # ==================== 主流程（对齐 Java extractParameters） ====================

    async def extract_parameters(
        self,
        user_question: str,
        tool: McpToolDefinition,
        custom_prompt_template: Optional[str] = None,
    ) -> McpExtractionResult:
        if not self._properties(tool):
            # 无参工具：直接成功、空参调用（不发 LLM）
            return McpExtractionResult.success({})

        # 构建 Prompt：优先使用自定义提示词（对齐 Java StrUtil.isNotBlank 判空）
        system_prompt = (
            custom_prompt_template
            if custom_prompt_template and custom_prompt_template.strip()
            else self._template_loader.load(MCP_PARAMETER_EXTRACT_PROMPT_PATH)
        )
        user_prompt = self._template_loader.render(
            MCP_PARAMETER_EXTRACT_USER_PROMPT_PATH,
            {
                "tool_definition": self._build_tool_definition(tool),
                "user_question": user_question,
            },
        )
        request = ChatRequest(
            messages=[Message.system(system_prompt), Message.user(user_prompt)],
            temperature=0.1,
            topP=0.3,
            thinking=False,
        )

        # 标准档调用；协议畸形 / 值非法或调用失败均判 FAILED、不调用工具
        try:
            raw = await self._llm.chat(request)
        except Exception:
            logger.warning("MCP 参数提取 LLM 调用失败, toolId: %s", tool.name, exc_info=True)
            return McpExtractionResult.failed()

        result = self._validate_mcp_params(raw, tool)

        # 仅 SUCCESS 才填默认值并交由消费端调用；NEED_CLARIFICATION / FAILED 不调用工具故不填
        if result.status == Status.SUCCESS:
            self._fill_defaults(result.params, tool)
        logger.info(
            "MCP 参数提取完成, toolId: %s, 结局: %s, 参数: %s",
            tool.name, result.status, result.params,
        )
        return result

    # ==================== 校验与分类（对齐 Java validateMcpParams / parseAndClassify） ====================

    def _validate_mcp_params(self, raw: str, tool: McpToolDefinition) -> McpExtractionResult:
        logger.info("MCP 参数提取 LLM 响应: %s", _preview(raw))
        try:
            params, fail_reasons, user_missing = self._parse_and_classify(raw, tool)
        except Exception:
            logger.warning("MCP 参数提取响应解析失败, toolId: %s", tool.name, exc_info=True)
            return McpExtractionResult.failed()

        if fail_reasons:
            logger.warning(
                "MCP 参数提取失败（模型未遵守协议 / 值非法）, toolId: %s, 问题: %s",
                tool.name, fail_reasons,
            )
            return McpExtractionResult.failed()
        if user_missing:
            logger.warning(
                "MCP 参数提取缺少必填参数（用户未提供，触发澄清）, toolId: %s, missing: %s",
                tool.name, user_missing,
            )
            return McpExtractionResult.need_clarification(params, user_missing)
        return McpExtractionResult.success(params)

    def _parse_and_classify(
        self, raw: str, tool: McpToolDefinition
    ) -> Tuple[Dict[str, Any], List[str], List[str]]:
        """
        解析 LLM 响应并按声明参数逐项分类（对应 Java parseAndClassify）

        必填无默认参数缺失或为 null → userMissing（用户没给，触发澄清）；
        值存在时按 schema type/enum 保守校验，非法值一律 FAILED（含可选 / 有默认）。
        """
        properties = self._properties(tool)
        required = self._required(tool)
        obj = self._parse_json_object(raw)

        params: Dict[str, Any] = {}
        fail_reasons: List[str] = []
        user_missing: List[str] = []

        for name, prop_def in properties.items():
            if not isinstance(prop_def, dict):
                prop_def = {}
            is_required = name in required
            has_default = prop_def.get("default") is not None

            present = name in obj
            is_null = present and obj.get(name) is None

            if not present or is_null:
                # 必填无默认且缺失或为 null：都视为"用户未提供该必填信息"，触发澄清
                if is_required and not has_default:
                    user_missing.append(name)
                # 非必填 / 有默认：忽略，交由 fill_defaults 兜底
                continue

            coerced = self._coerce_and_validate(obj[name], prop_def)
            if coerced is not _COERCE_FAILED:
                params[name] = coerced
            else:
                # 字段存在但值类型 / 枚举非法：无论必填与否都判 FAILED
                # 静默丢弃可选 / 有默认字段会让过滤条件被无声移除
                fail_reasons.append(name + "（值类型 / 枚举非法）")

        return params, fail_reasons, user_missing

    # ==================== 工具定义描述（对齐 Java buildToolDefinition） ====================

    def _build_tool_definition(self, tool: McpToolDefinition) -> str:
        lines = ["工具ID: " + tool.name, "功能描述: " + (tool.description or ""), "参数列表:"]
        properties = self._properties(tool)
        required = self._required(tool)
        for name, prop_def in properties.items():
            if not isinstance(prop_def, dict):
                prop_def = {}
            type_name = prop_def.get("type") or "string"
            is_required = name in required
            description = prop_def.get("description") or ""
            default_value = prop_def.get("default")
            enum_values = prop_def.get("enum")

            line = f"  - {name} (类型: {type_name}{', 必填' if is_required else ', 可选'}): {description}"
            if default_value is not None:
                line += f" [默认值: {default_value}]"
            if isinstance(enum_values, list) and enum_values:
                line += " [可选值: " + ", ".join(str(v) for v in enum_values) + "]"
            lines.append(line)
        return "\n".join(lines)

    # ==================== JSON 解析（对齐 Java parseJsonObject） ====================

    def _parse_json_object(self, raw: str) -> Dict[str, Any]:
        if not raw or not raw.strip():
            raise ValueError("MCP 提参响应为空")
        cleaned = _CODE_FENCE.sub("", raw).strip()
        try:
            obj = json.loads(cleaned, parse_constant=_reject_nonfinite_constant)
        except json.JSONDecodeError as e:
            raise ValueError(f"MCP 提参响应 JSON 解析失败: {e}") from e
        if not isinstance(obj, dict):
            raise ValueError("MCP 提参响应不是 JSON 对象")
        return obj

    # ==================== 类型收敛与校验（对齐 Java coerceAndValidate / coerceType） ====================

    def _coerce_and_validate(self, value: Any, prop_def: Dict[str, Any]) -> Any:
        """按 type/enum 保守校验与类型转换；不可转换或越出枚举返回 _COERCE_FAILED（视为非法值）"""
        if value is None:
            return _COERCE_FAILED
        typed = self._coerce_type(value, prop_def.get("type"))
        if typed is _COERCE_FAILED:
            return _COERCE_FAILED
        enum_def = prop_def.get("enum")
        if isinstance(enum_def, list) and enum_def and not self._enum_contains(enum_def, typed):
            return _COERCE_FAILED
        return typed

    def _coerce_type(self, value: Any, type_name: Optional[str]) -> Any:
        """按声明 type 收敛；type 缺省 / 未知时不约束（对齐 Java switch default -> value）"""
        type_name = (type_name or "").strip()
        if not type_name:
            return value

        if type_name == "string":
            if isinstance(value, str):
                return value
            if isinstance(value, bool):
                # Java Boolean.toString() 为 "true"/"false"，对齐大小写（Python str(True) 为 "True"）
                return "true" if value else "false"
            if isinstance(value, (int, float)):
                return str(value)
            return _COERCE_FAILED

        if type_name == "integer":
            if isinstance(value, bool):
                return _COERCE_FAILED  # bool 是 int 子类，显式排除（Java Boolean 非 Integer）
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                return self._parse_long_or_null(value)
            return _COERCE_FAILED

        if type_name == "number":
            if isinstance(value, bool):
                return _COERCE_FAILED
            if isinstance(value, (int, float)):
                return value
            if isinstance(value, str):
                return self._parse_double_or_null(value)
            return _COERCE_FAILED

        if type_name == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return self._parse_boolean_or_null(value)
            return _COERCE_FAILED

        if type_name == "array":
            return value if isinstance(value, list) else _COERCE_FAILED

        if type_name == "object":
            return value if isinstance(value, dict) else _COERCE_FAILED

        return value

    @staticmethod
    def _parse_long_or_null(value: str) -> Any:
        """十进制整数解析（对齐 Java Long.parseLong；不接受 "1.0" 等非整数字面量）"""
        try:
            return int(value.strip())
        except (ValueError, TypeError):
            return _COERCE_FAILED

    @staticmethod
    def _parse_double_or_null(value: str) -> Any:
        """浮点解析并拒绝非有限值（对齐 Java parseDoubleOrNull 的 isFinite 守卫）"""
        try:
            d = float(value.strip())
            return d if math.isfinite(d) else _COERCE_FAILED
        except (ValueError, TypeError):
            return _COERCE_FAILED

    @staticmethod
    def _parse_boolean_or_null(value: str) -> Any:
        t = value.strip().lower()
        if t == "true":
            return True
        if t == "false":
            return False
        return _COERCE_FAILED

    @staticmethod
    def _enum_contains(enum_list: List[Any], value: Any) -> bool:
        """枚举包含判断：先按值相等，再按字符串形态相等（容忍 int/float 与枚举字面差异，对齐 Java enumContains）"""
        value_str = str(value)
        return any(e == value or str(e) == value_str for e in enum_list)

    # ==================== 默认值补齐（对齐 Java fillDefaults） ====================

    @staticmethod
    def _fill_defaults(params: Dict[str, Any], tool: McpToolDefinition) -> None:
        for name, prop_def in LLMMcpParameterExtractor._properties(tool).items():
            if not isinstance(prop_def, dict):
                continue
            default_value = prop_def.get("default")
            if name not in params and default_value is not None:
                params[name] = default_value

    # ==================== schema 访问 ====================

    @staticmethod
    def _properties(tool: Optional[McpToolDefinition]) -> Dict[str, Any]:
        """input_schema.properties（dict 形态；缺失 / 非 dict 返回空）"""
        schema = tool.input_schema if tool is not None else None
        if not isinstance(schema, dict):
            return {}
        properties = schema.get("properties")
        return properties if isinstance(properties, dict) else {}

    @staticmethod
    def _required(tool: Optional[McpToolDefinition]) -> List[str]:
        """input_schema.required（list 形态；缺失 / 非 list 返回空）"""
        schema = tool.input_schema if tool is not None else None
        if not isinstance(schema, dict):
            return []
        required = schema.get("required")
        return required if isinstance(required, list) else []
