# -*- coding: utf-8 -*-
"""
knowledge.support.ingestion_spec_codec - 文档级摄取配置的读写、校验与归一化（对应 Java IngestionSpecCodec）

取值范围由 ChunkBudget 与 ParseProfile 的构造期保证，此处只把构造异常翻译成用户可读报错；
缺失字段一律回落 IngestionSpec.defaults()，全系统只此一份默认值；
前端提交什么形状都在此收敛成规整 JSON 落库，读路径不必再探测。

线路形状（wire）与领域对象的唯一区别：整篇不分块在线上用 -1 哨兵表达，
领域内部用 ChunkBudget 的 WHOLE_DOCUMENT（Integer.MAX_VALUE）——两者的翻译只发生在本类，
直接序列化领域对象会让前端提交的 -1 变成库里/出参的 2147483647（编辑弹窗出现天文数字）。

键名常量与 IngestionSpecSchemaProvider 必须共用这一份：两边各写一份字符串的话，
键名一改就是「提交了却静默走默认值」，任何一步都不会报错。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.knowledge.support.IngestionSpecCodec
    - com.nageoffer.ai.ragent.core.ingest.IngestionSpec（record）
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from common.exception.business import ClientException
from rag.ingestion.kernel import INGESTION_SPEC_VERSION, IngestionSpec
from rag.ingestion.parser.base import ParseProfile
from rag.ingestion.splitter.base import ChunkBudget

logger = logging.getLogger(__name__)

# ==================== 线路键名（与 IngestionSpecSchemaProvider 共用同一份常量） ====================
KEY_MAX_CHARS = "maxChars"
KEY_OVERLAP_CHARS = "overlapChars"
KEY_ROWS_PER_CHUNK = "rowsPerChunk"
KEY_TOLERANCE_FACTOR = "toleranceFactor"
KEY_PARSE_PROFILE = "parseProfile"
KEY_VERSION = "version"
KEY_BUDGET = "budget"

# 不分块哨兵：线路上（提交、落库、出参、schema 下发）只有这一个表示（前端既有约定）
WHOLE_DOCUMENT_SENTINEL = -1


class IngestionSpecCodec:
    """文档级摄取配置 JSON ↔ IngestionSpec（无状态，可复用单实例）"""

    # ==================== 公开入口（read / write / normalize） ====================

    def read(self, json_text: Optional[str]) -> IngestionSpec:
        """读：库里 JSON → 配置对象；空值或损坏一律回落默认（对齐 Java read）

        必须走 wire 形状而非直接 json.loads 成领域对象：库里 maxChars 是 -1，
        撞上 ChunkBudget 的「必须 > 0」校验会被 catch 悄悄换成默认配置，
        整篇不分块的文档于是无声地按 1024 切开。
        """
        if not json_text or not json_text.strip():
            return IngestionSpec.defaults()
        try:
            raw = json.loads(json_text)
            return self._wire_to_domain(raw)
        except Exception:  # noqa: BLE001 —— 读取路径失败一律回落默认，不阻断调用方
            logger.warning("摄取配置解析失败，回落默认配置：%s", json_text[:200], exc_info=True)
            return IngestionSpec.defaults()

    def write(self, spec: Optional[IngestionSpec]) -> str:
        """写：配置对象 → 落库 JSON（整篇不分块用 -1 哨兵表达）"""
        try:
            return json.dumps(
                self._domain_to_wire(spec if spec is not None else IngestionSpec.defaults()),
                ensure_ascii=False,
            )
        except Exception:  # noqa: BLE001
            raise ClientException("摄取配置序列化失败")

    def normalize(self, raw_json: Optional[str]) -> Optional[str]:
        """校验并归一化前端提交的配置 JSON，返回落库用规整 JSON；空入参返回 None（列留空即走默认）

        异常封闭性：`_from_map` 依赖链只抛 ValueError（ParseProfile.from_code 未知档位 /
        ChunkBudget 构造越界 / IngestionSpec 版本非法），故此处只捕 ValueError 即能保证
        「非法输入 → 400 级 ClientException」而非 500；若未来依赖新增其他异常类型需同步扩捕。
        """
        if not raw_json or not raw_json.strip():
            return None
        try:
            raw = json.loads(raw_json.strip())
            if not isinstance(raw, dict):
                raise ClientException("摄取配置 JSON 格式不合法")
        except json.JSONDecodeError:
            raise ClientException("摄取配置 JSON 格式不合法")
        try:
            return self.write(self._from_map(raw))
        except ValueError as e:
            raise ClientException(f"摄取配置不合法：{e}")

    # ==================== wire 形状（嵌套 {version, parseProfile, budget:{...}}） ====================

    def _domain_to_wire(self, spec: IngestionSpec) -> Dict[str, Any]:
        """领域对象 → 线路形状：整篇不分块翻译成 -1 哨兵（对齐 Java SpecWire.of）"""
        budget = spec.budget
        if budget.is_whole_document():
            budget_wire = {
                KEY_MAX_CHARS: WHOLE_DOCUMENT_SENTINEL,
                KEY_OVERLAP_CHARS: 0,
                KEY_ROWS_PER_CHUNK: WHOLE_DOCUMENT_SENTINEL,
                KEY_TOLERANCE_FACTOR: budget.tolerance_factor,
            }
        else:
            budget_wire = {
                KEY_MAX_CHARS: budget.max_chars,
                KEY_OVERLAP_CHARS: budget.overlap_chars,
                KEY_ROWS_PER_CHUNK: budget.rows_per_chunk,
                KEY_TOLERANCE_FACTOR: budget.tolerance_factor,
            }
        return {
            KEY_VERSION: spec.version,
            KEY_PARSE_PROFILE: spec.parse_profile.value,
            KEY_BUDGET: budget_wire,
        }

    def _wire_to_domain(self, raw: Dict[str, Any]) -> IngestionSpec:
        """线路形状 → 领域对象（对齐 Java SpecWire.toDomain）"""
        version = self._read_int(raw, KEY_VERSION)
        profile = ParseProfile.from_code(self._read_string(raw, KEY_PARSE_PROFILE))
        budget_raw = raw.get(KEY_BUDGET)
        if not isinstance(budget_raw, dict):
            budget_raw = {}
        budget = self._to_budget(
            self._read_int(budget_raw, KEY_MAX_CHARS),
            self._read_int(budget_raw, KEY_OVERLAP_CHARS),
            self._read_int(budget_raw, KEY_ROWS_PER_CHUNK),
            self._read_int(budget_raw, KEY_TOLERANCE_FACTOR),
        )
        return IngestionSpec(
            version if (version is not None and version > 0) else INGESTION_SPEC_VERSION,
            profile,
            budget,
        )

    # ==================== 扁平提交（前端 normalize 入参） ====================

    def _from_map(self, raw: Dict[str, Any]) -> IngestionSpec:
        """扁平键 → 领域对象（对齐 Java fromMap）"""
        return IngestionSpec.of(
            ParseProfile.from_code(self._read_string(raw, KEY_PARSE_PROFILE)),
            self._to_budget(
                self._read_int(raw, KEY_MAX_CHARS),
                self._read_int(raw, KEY_OVERLAP_CHARS),
                self._read_int(raw, KEY_ROWS_PER_CHUNK),
                self._read_int(raw, KEY_TOLERANCE_FACTOR),
            ),
        )

    # ==================== 哨兵翻译与缺失回落（唯一一份） ====================

    def _to_budget(
        self,
        max_chars: Optional[int],
        overlap: Optional[int],
        rows: Optional[int],
        tolerance: Optional[int],
    ) -> ChunkBudget:
        """四个整数 → 分块预算：-1 哨兵、缺失回落、越界抛错只有这一份（对齐 Java toBudget）"""
        if max_chars is not None and max_chars == WHOLE_DOCUMENT_SENTINEL:
            return ChunkBudget.whole_document()
        defaults = ChunkBudget.defaults()
        budget = max_chars if (max_chars is not None and max_chars > 0) else defaults.max_chars
        return ChunkBudget(
            budget,
            # 缺省重叠按块大小等比给：默认预算里那个数是配 1024 的，照搬到小块上会被压到 budget-1，
            # 切一片只前进一个字（对齐 Java defaultOverlapFor(budget)）
            overlap if (overlap is not None and overlap >= 0) else ChunkBudget.default_overlap_for(budget),
            rows if (rows is not None and rows > 0) else defaults.rows_per_chunk,
            tolerance if (tolerance is not None and tolerance > 0) else defaults.tolerance_factor,
        )

    @staticmethod
    def _read_int(raw: Dict[str, Any], key: str) -> Optional[int]:
        """宽松读整数：Number → int；数字字符串 → int（非法回 None）；其余 None（对齐 Java readInt）"""
        value = raw.get(key)
        if isinstance(value, bool):  # bool 是 int 子类，排除
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip():
            try:
                return int(value.strip())
            except ValueError:
                return None
        return None

    @staticmethod
    def _read_string(raw: Dict[str, Any], key: str) -> Optional[str]:
        """宽松读字符串（对齐 Java readString）"""
        value = raw.get(key)
        return None if value is None else str(value)
