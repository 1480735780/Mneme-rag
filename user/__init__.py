# -*- coding: utf-8 -*-
"""
ragent user 域（认证登录 + 用户管理，对应 ragent user/ 包）

    - enums：UserRole（admin / user）
    - dao：UserDao（t_user 数据访问，对应 UserMapper）
    - service：SessionManager（token 会话）、password（PBKDF2 哈希）
    - controller：AuthController / UserController
"""
