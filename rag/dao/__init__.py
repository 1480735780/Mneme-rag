# -*- coding: utf-8 -*-
"""
rag.dao - P4 在线服务数据访问层（对应 ragent bootstrap rag.dao.entity + mapper）

dao 层面向 storage.database.DatabaseClient 抽象编程（行 = dict + Condition），
每张在线服务表对应一个 dao 模块：会话 / 消息 / 反馈 / 示例问题 / 追踪 / Agent /
意图树管理 / 术语映射管理。插值与查询的列名对齐 storage.database.schema 单一事实源。

suppport：软删除常量 / 时间戳 / 审计列填充 / 行 → 域对象 / 分页 的公共支撑。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.dao.entity.*DO + *Mapper
    - com.nageoffer.ai.ragent.rag.dao.mapper.*Mapper（MyBatis-Plus BaseMapper）
"""