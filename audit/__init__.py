# -*- coding: utf-8 -*-
"""
audit - 业务变更审计日志域（对应 ragent audit/ 包）

    - dao：BizChangeLogDao（t_biz_change_log 数据访问）
    - support：BizChangeLogContext（contextvars 快照上下文）+ decorator（@record_biz_change 落库）
    - service：BizChangeLogService（查询）
    - controller：BizChangeLogController（分页/详情端点）
"""
