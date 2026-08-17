"""
图谱文档来源标识 {collectionName}_{docId} 的编解码（对应 ragent GraphFileSource）

docId 为雪花纯数字，从右锚定「末段 _数字」即可唯一还原两个分量；库名用户自填、可含下划线且可互为前缀
（kb 与 kb_hr 合法共存），从左做子串匹配必然串库——读侧把别库证据划进主路，删侧连带删光别库数据且不可逆，
归属判定必须走这里的解析 + 全名等值。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.graph.GraphFileSource
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 右锚定格式：主体严格为 {collectionName}_{docId}，容忍服务端追加的扩展名修饰
_FORMAT = re.compile(r"^(.+)_(\d+)((?:\.[A-Za-z0-9]+)*)$")


@dataclass(frozen=True)
class GraphFileSource:
    """图谱文档来源标识（对应 Java GraphFileSource record）"""

    collection_name: str
    doc_id: str

    @staticmethod
    def encode(collection_name: str, doc_id: str) -> str:
        """写入侧编码，与 parse 互为逆运算（对应 Java encode）"""
        return f"{collection_name}_{doc_id}"

    @staticmethod
    def parse(file_path) -> "GraphFileSource | None":
        """
        从 LightRAG 回传的 file_path 还原来源（对应 Java parse）

        先取 basename 以跳过可能的目录前缀；不符合编码返回 None。

        Args:
            file_path: LightRAG 回传的 file_path

        Returns:
            GraphFileSource | None: 解析出的来源标识，不符合编码或空白返回 None
        """
        if not file_path or not file_path.strip():
            return None
        base_name = file_path[file_path.rfind("/") + 1:]
        matcher = _FORMAT.match(base_name)
        if matcher is None:
            return None
        return GraphFileSource(matcher.group(1), matcher.group(2))
