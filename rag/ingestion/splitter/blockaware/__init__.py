# -*- coding: utf-8 -*-
"""
rag.ingestion.splitter.blockaware - Block 感知切分（对应 ragent core.chunk.blockaware）

标题/段落/表格/列表/代码/图片/HTML 表格的分块器 + 遍历上下文 + 打包器。
当前已实现：model（ChunkDraft + ChunkAssembler）。
"""
