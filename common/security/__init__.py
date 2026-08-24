# -*- coding: utf-8 -*-
"""
common.security - 认证/鉴权（对应 Java Sa-Token；P7 D7 归并销案）

Java framework 层无 security 包（Sa-Token 配置在 bootstrap/user/config），Python 侧
认证/鉴权能力已由以下落点承载，本空壳包不再补实现：
    - user.security.require_role：角色门禁装饰器（对应 StpUtil.checkRole）
    - common.middleware.user_context_middleware：UserContext 解析 + Bearer token → 会话（对应 UserContextInterceptor）
    - common.context.user_context：UserContext（对应 LoginUser / UserContext.getUserId）

详见 docs/ragent-porting-gap-analysis.md §4 security 行（归并销案）。
"""
