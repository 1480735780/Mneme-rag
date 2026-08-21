# -*- coding: utf-8 -*-
"""
knowledge.support.ingestion_spec_schema - 文档级摄取配置的表单 schema 下发（对应 Java IngestionSpecSchemaProvider）

与 IngestionSpecCodec 同居一包，说的是同一份 spec 的两面——一个管怎么读写校验，一个管它
长什么样、每个字段该填多少。线路上的键名与哨兵值一律取自 codec，不在此另立第二份；只暴露
用户真正能控的解析档位与块预算，取值范围一并下发，前端不必自己维护一份校验规则。

档位适用的格式清单从解析器注册表推导、不在任何一端写死：`profile_sensitive_mime_types()`
返回「两档命中不同解析器」的格式，档位对这些格式才有效，`(MIME × 档位)` 表里两档命中同一个
解析器的格式对它是空操作、选项必须藏起来。字段名与选项名一并下发，这三段文案要互相说得通
（「表格结构」配「规整 / 复杂表格」），拆到两端各存一半就是改一处漏一处。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.knowledge.support.IngestionSpecSchemaProvider
    - com.nageoffer.ai.ragent.knowledge.controller.vo.IngestionSpecSchemaVO
"""
from __future__ import annotations

from typing import Any, Dict

from common.exception.business import ServiceException
from knowledge.support.ingestion_spec_codec import (
    KEY_MAX_CHARS,
    KEY_OVERLAP_CHARS,
    KEY_ROWS_PER_CHUNK,
    KEY_TOLERANCE_FACTOR,
    WHOLE_DOCUMENT_SENTINEL,
)
from rag.file_storage import DisplayType
from rag.ingestion.parser.base import ParseProfile
from rag.ingestion.parser.registry import ParserRegistry
from rag.ingestion.splitter.base import (
    MAX_CHARS_LIMIT,
    OVERLAP_DIVISOR,
    ROWS_PER_CHUNK_LIMIT,
    TOLERANCE_FACTOR_LIMIT,
    ChunkBudget,
)

# 档位选项在界面上的字段名：命名操作人员看得见的事实
# （「快速 / 保真」命名的是引擎的成本换保真度权衡轴，而操作人员拿在手上的只有一份文件，
#  他答不出「我需要多少保真度」，只答得出「这张表有没有合并单元格」，故直接问表格结构）
PARSE_PROFILE_LABEL = "表格结构"


class IngestionSpecSchemaProvider:
    """文档级摄取配置的表单 schema 下发：依赖解析器注册表推导档位适用的格式（无状态，可复用单实例）"""

    def __init__(self, parser_registry: ParserRegistry):
        self._parser_registry = parser_registry
        self._check_profile_copy_covers_all_sensitive_formats()

    def _check_profile_copy_covers_all_sensitive_formats(self) -> None:
        """启动期自检：档位文案只覆盖表格类（对应 Java @PostConstruct）

        档位文案说的是合并单元格与多层表头，而 profile_sensitive_mime_types() 只应返回表格类
        格式（XLS/XLSX/CSV）。哪天有非表格格式认领了非兜底档，这份文案就开始对着一份 PDF 谈
        表头，故构造期即失败——那时要么改文案，要么把文案挪到认领该档位的解析器上按格式下发。
        """
        unexpected = [
            mime
            for mime in self._parser_registry.profile_sensitive_mime_types()
            if not DisplayType.of(None, mime).is_tabular()
        ]
        if unexpected:
            raise ServiceException(
                "档位文案只覆盖表格类，以下 MIME 已认领非兜底档却不是表格，文案需跟着改："
                + ", ".join(sorted(unexpected))
            )

    def describe(self) -> Dict[str, Any]:
        """装配表单 schema

        取值范围的权威在 ChunkBudget 的构造期，此处只是把同一份数字告诉前端；档位适用的格式
        清单从注册表推导、不在任何一端写死。返回 snake_case dict，在 controller 边界经
        camelize() 递归转成 camelCase（对齐 Java IngestionSpecSchemaVO 字段名）。
        """
        defaults = ChunkBudget.defaults()
        return {
            "parse_profile_label": PARSE_PROFILE_LABEL,
            "parse_profiles": self._describe_profiles(),
            "parse_profile_extensions": self._describe_profile_extensions(),
            "budget_fields": self._describe_budget_fields(defaults),
            "whole_document_sentinel": WHOLE_DOCUMENT_SENTINEL,
        }

    @staticmethod
    def _describe_profiles() -> list:
        """两个档位选项：value=提交值 / label=展示名 / hint=说明"""
        return [
            {
                "value": ParseProfile.FAST.value,
                "label": "规整表格",
                "hint": "一行一条记录、表头只有一层，秒级完成",
            },
            {
                "value": ParseProfile.FIDELITY.value,
                "label": "复杂表格",
                "hint": "有合并单元格、多层表头或跨页表格；需要数十秒，走外部解析服务",
            },
        ]

    def _describe_profile_extensions(self) -> list:
        """档位真正有区别的文件扩展名（去重排序），其余格式前端不得展示档位选项"""
        extensions = set()
        for mime in self._parser_registry.profile_sensitive_mime_types():
            display_type = DisplayType.of(None, mime)
            if display_type is not DisplayType.OTHER:
                extensions.update(display_type.extensions())
        return sorted(extensions)

    @staticmethod
    def _describe_budget_fields(defaults: ChunkBudget) -> list:
        """四个预算字段：key=提交键（取自 codec）/ label=展示名 / 范围与建议区间 / hint+detail

        hint 答「这个数是什么」、detail 答「调大调小会怎样」，都不是把字段名换句话说一遍；
        detail 收进前端悬浮层，四个字段的长说明并排铺开就是一堵墙。
        """
        return [
            {
                "key": KEY_MAX_CHARS,
                "label": "块大小",
                "default_value": defaults.max_chars,
                "min": 1,
                "max": MAX_CHARS_LIMIT,
                "recommended_min": 512,
                "recommended_max": MAX_CHARS_LIMIT,
                "hint": "一段目标放多少字",
                "detail": (
                    "这是目标，不是硬上限。为保住整章或整张表不被切开，一段最多能撑到"
                    "「块大小 × 结构容忍倍数」。调小则检索更精准，但每段带的上下文更少"
                ),
            },
            {
                "key": KEY_OVERLAP_CHARS,
                "label": "块重叠",
                "default_value": defaults.overlap_chars,
                "min": 0,
                "max": MAX_CHARS_LIMIT - 1,
                "recommended_min": 64,
                "recommended_max": 1024,
                "hint": "相邻两段重复多少字",
                "detail": (
                    "只在单个段落超过块大小、必须拦腰切开时才生效，多数段落到不了这一步。"
                    "它同时是切口回退找句号的最大距离，填太小会切在句子中间。"
                    f"默认取块大小的 1/{OVERLAP_DIVISOR}，且必须小于块大小"
                ),
            },
            {
                "key": KEY_ROWS_PER_CHUNK,
                "label": "表格每块行数",
                "default_value": defaults.rows_per_chunk,
                "min": 1,
                "max": ROWS_PER_CHUNK_LIMIT,
                "recommended_min": 20,
                "recommended_max": 50,
                "hint": "表格每段放多少数据行",
                "detail": "列多、每格字数长的表要调小",
            },
            {
                "key": KEY_TOLERANCE_FACTOR,
                "label": "结构容忍倍数",
                "default_value": defaults.tolerance_factor,
                "min": 1,
                "max": TOLERANCE_FACTOR_LIMIT,
                "recommended_min": 2,
                "recommended_max": 4,
                "hint": "一段最多可超出块大小的倍数",
                "detail": (
                    "为保住整章、整张表、整个代码块不被切开，允许一段撑到块大小的几倍。"
                    "填 1 就是严格按块大小切，章节会被切碎；调大则块更完整，但检索更粗、"
                    "单次问答塞给模型的上下文也更多"
                ),
            },
        ]