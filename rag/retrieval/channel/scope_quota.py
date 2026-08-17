"""
主路与补充路的候选名额划分（对应 ragent ScopeQuota）

三条通道共用这一条规则：各自把自己的产出额度按同一比例切一片给未命中库。
补充证据必须有固定名额而非与命中库证据自由竞争——意图判错时正确证据只在未命中库里，
拼相关度分必然抢不过命中库里那些「表面很像但答非所问」的证据。
名额兑现到通道出口为止：入列有保证，能否活过下游融合截断不在此处承诺。

两个字段恒为名额语义，不设「非正表示不封顶」的哨兵：名额与哨兵共用取值空间时，
「补充路 0 名额」会被读成「补充路不封顶」，配置意图与实际执行正好相反。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.retrieval.channel.ScopeQuota
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, TypeVar

from rag.retrieval.schema import RetrievalScope

T = TypeVar("T")


@dataclass(frozen=True)
class ScopeQuota:
    """
    主路与补充路的候选名额划分（对应 Java ScopeQuota record）

    Attributes:
        primary:    主路候选名额，补充生效时恒 >= 1
        supplement: 补充路候选名额，0 表示本次不补
    """

    primary: int
    supplement: int

    @staticmethod
    def split(scope: RetrievalScope, budget: int, supplement_ratio: float) -> "ScopeQuota":
        """
        按作用域切分通道产出额度（对应 Java ScopeQuota.split）

        全局作用域、无未命中库（如单库部署）、比例置零、无额度可分，
        四种情况都不补，额度全归主路。

        补充路名额 = budget * supplementRatio 四舍五入后夹在 [1, budget-1] 之间：
        上界保证主路至少留 1 个名额——被挤成 0 意味着高置信命中库一条都不召回，
        与「定向优先、补充兜底」完全相反；额度只剩 1 时上界自然收到 0，退化为不补。

        Args:
            scope:           检索作用域（是否定向 / 有无未命中库）
            budget:          本通道的产出额度
            supplement_ratio:划给补充路的比例

        Returns:
            ScopeQuota: 主路 + 补充路名额
        """
        if (
            not scope.directed
            or not scope.supplement_collections
            or supplement_ratio <= 0
            or budget <= 0
        ):
            return ScopeQuota(primary=budget, supplement=0)
        # 与 Java Math.round 对齐：floor(x + 0.5) 四舍五入（.5 向上），
        # Python 内置 round() 是银行家舍入（.5 向偶数），结果会不一致
        supplement = min(
            budget - 1, max(1, int(math.floor(budget * supplement_ratio + 0.5)))
        )
        return ScopeQuota(primary=budget - supplement, supplement=supplement)

    @staticmethod
    def cap(chunks: List[T], limit: int) -> List[T]:
        """
        按名额截断已按相关性降序的候选；名额为 0 即取零条（对应 Java ScopeQuota.cap）

        0 若退化为不封顶，「补充路 0 名额」就会变成「补充路不封顶」，与配置意图相反。

        Args:
            chunks: 已按相关性降序的候选列表
            limit:  名额上限

        Returns:
            List[T]: 截断后的候选；limit <= 0 返回空列表
        """
        if limit <= 0:
            return []
        return chunks[:limit] if len(chunks) > limit else chunks
