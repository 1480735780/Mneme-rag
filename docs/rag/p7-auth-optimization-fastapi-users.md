# P7 认证优化点：fastapi-users 库选型评估

> 关联：[p7-platform-implementation-plan.md](p7-platform-implementation-plan.md) §2 决策 D1/D2。
> 本文档评估第三方认证库 **fastapi-users** 作为 Sa-Token「平替」的可行性，作为 P7 认证实现的**优化点登记**，
> 不改变当前 P7 实施路径（自研轻量 token + Redis 会话），仅供后续按需切换时决策。
>
> 更新记录：2026-08-22 立项登记。

## 1. 背景

P7 认证域当前采用自研方案（[p7-platform-implementation-plan.md](p7-platform-implementation-plan.md) 决策 D1/D2）：

| 项 | 当前自研方案（D1/D2） |
|---|---|
| 会话载体 | opaque token（uuid）+ Redis TTL 7 天（缺省内存 dict 兜底） |
| 认证开关 | `RAGENT_AUTH_ENABLED`，默认关闭；关闭时维持 X-User-Id 匿名兜底 |
| 密码 | PBKDF2-SHA256（标准库），明文兼容层 |
| 登出 | `logout(token)` → 删除 Redis 会话（服务端主动失效） |

用户提出 **fastapi-users** 作为更接近 Java Sa-Token 的开箱即用平替，本文档做能力对照与取舍评估。

## 2. fastapi-users 简介

- 完整用户模型（register / login / forgot-password / verify 等端点开箱即用）
- JWT / Cookie 双认证策略
- 权限装饰器、Depends 注入
- SQLAlchemy / Tortoise ORM 适配，与 FastAPI 生态无缝集成

**与 Sa-Token 对应关系**：

| Sa-Token（Java） | fastapi-users（Python） | 当前自研方案 |
|---|---|---|
| `StpUtil.login / logout` | `BearerTransport` + 认证 backend | `SessionManager.login / logout` |
| 会话存储（Redis） | 默认 JWT（无状态）；需自定义 backend 或 Redis Session | Redis 会话（服务端状态） |
| `StpInterfaceImpl` 角色鉴权 | `get_user_manager` + 自定义依赖 / 装饰器 | `@require_role(ADMIN)` 装饰器（U7） |
| `UserDO` + 密码 | UserManager + password helper | UserDao + PBKDF2（U1/U3） |

## 3. 能力对照与关键差异

| 维度 | fastapi-users | 当前自研方案 | 说明 |
|---|---|---|---|
| 注册/登录/找回密码 | ✅ 全套开箱即用 | 需自行实现 | fastapi-users 优势明显 |
| 主动登出（服务端失效） | ⚠️ JWT 默认无状态，需自定义 Backend 或 Redis Session | ✅ 天然支持（删会话） | **与 Sa-Token 的核心差异点**：Sa-Token 是服务端会话，JWT 是客户端自证 |
| 权限控制 | 依赖 + 装饰器（需自接） | `@require_role` 装饰器（U7） | 两者相当 |
| 密码哈希 | 内置（argon2/bcrypt 等） | PBKDF2（标准库零依赖） | 两者相当；fastapi-users 可选更强算法 |
| ORM 绑定 | 倾向 SQLAlchemy/Tortoise | DatabaseClient 抽象（无 ORM 依赖） | 当前方案不绑定 ORM，P6 已统一 SQLAlchemy executor |
| 认证开关/匿名兜底 | 需自行改造 | 内置（`RAGENT_AUTH_ENABLED`） | 当前方案与既有测试/匿名语义衔接好 |

## 4. 局限与代价（引入 fastapi-users 需付出）

1. **默认 JWT 与服务端主动登出冲突**：P7 需要「登出后 token 立即失效」（Sa-Token 语义），JWT 做不到——必须自定义 Backend 改回服务端会话（Redis），等于把 fastapi-users 的会话逻辑换掉大半，省下的只剩用户模型与注册/找回密码端点。
2. **ORM 依赖**：fastapi-users 绑定 SQLAlchemy/Tortoise 用户模型；当前项目是 DatabaseClient 抽象 + P6 统一 SQLAlchemy executor，引入会引入第二套用户数据访问范式。
3. **新依赖 + 版本兼容**：需评估与 fastapi/pydantic 版本矩阵（项目 fastapi>=0.115 / pydantic>=2.8）。
4. **认证开关双模式**：默认关闭的匿名兜底需要改库默认行为，改造面不小。

## 5. 结论与建议（优化点登记）

| 判断 | 结论 |
|---|---|
| 当前 P7 是否引入 | **不引入**。D1/D2 自研方案与 Sa-Token 的服务端会话语义一致、零新依赖、与既有匿名兜底/测试无缝衔接；fastapi-users 的核心价值（JWT 开箱即用）恰与「服务端主动登出」需求相悖 |
| 何时值得切换 | 当出现以下任一需求时重新评估：① 需要「忘记密码/邮箱验证」完整流程开箱即用；② 产品化后希望拥抱无状态 JWT 分布式会话（接受登出降级为前端清 token + 短 TTL）；③ 多认证源（OAuth2/微信等）接入需求增强 |
| 切换成本 | 中等：用户模型/密码/会话三处需适配 DatabaseClient，认证开关与匿名兜底需保留 |

**登记状态**：🔧 优化点（不阻塞 M1–M5 实施）。若后续切换，本 P7 的 U2（SessionManager）/U3（password）为隔离点，替换范围可控。

## 6. 维护说明

- 本文档随 P7 实施演进：若认证需求变化触发切换，更新 §5 结论并关联 p7-platform-implementation-plan.md §2 决策表；
- 与 [ragent-porting-gap-analysis.md](../ragent-porting-gap-analysis.md) §7.1（user 域）联动。
