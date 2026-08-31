# P7 平台化实施计划：认证用户 + 审计日志 + 管理大盘 + framework 补齐

> 目标：补齐 [ragent-porting-gap-analysis.md](../ragent-porting-gap-analysis.md) §9 中 P7——
> `user/`（认证登录 + 用户管理）+ `audit/`（业务变更日志）+ `admin/`（数据大盘）+ framework 基建补齐
> （幂等控制），把平台从「引擎可用」升级到「可运营、可审计、可管理」。
>
> 口径：能力等价替代（与全项目一致）；Python 侧已有组件（UserContext/雪花 ID/RAG 追踪）直接复用销案，不重复建设。
> 本文档只做计划与台账；每步落地后同步更新状态。

## 1. 背景与现状基线

**差距来源**：ragent-porting-gap-analysis.md §7.1（user 20 文件 / admin 10 文件 / audit 12 文件全部未开始）+ §4（framework 的 idempotent 未实现）。

**Java 对标结构**（`ragent-study/bootstrap/.../`）：

| 包 | 规模 | 核心内容 |
|---|:---:|---|
| `user/` | 20 | AuthController（`POST /auth/login`、`POST /auth/logout`）+ UserController（`GET /user/me`、`GET /users` 分页、`POST /users`、`PUT /users/{id}`、`DELETE /users/{id}`、`PUT /user/password`）；Sa-Token 会话（Redis）；UserContextInterceptor；StpInterfaceImpl（角色→权限）；UserDO（t_user）；UserRole 枚举（ADMIN/USER） |
| `audit/` | 12 | BizChangeLogController（`GET /biz-change-logs` 分页、`GET /biz-change-logs/{id}`）；BizChangeLogDO（t_biz_change_log：bizType/bizId/operationType/actionDesc/before/after/diff JSONB/operator 三元组/success/errorMessage/className/methodName/ip/userAgent）；BizChangeLogContext（mzt-biz-log SpEL 变量注入快照）+ RecordService + OperatorGetService |
| `admin/` | 10 | DashboardController（`GET /admin/dashboard/overview`、`/performance`、`/trends`）；聚合 user/conversation/message/trace_run 四表统计（KPI 总量+窗口增量+环比、P95/平均延迟、成功率、慢查询、无文档回答率、day/hour 粒度趋势） |
| framework `idempotent/` | 4 | `@IdempotentSubmit` 注解 + 切面（Redis setnx + SpEL key + 重复提交抛 ClientException）；`@IdempotentConsume`（消费幂等） |

**Python 侧已有基础（直接复用，不重建）**：
| 组件 | 落点 | 说明 |
|---|---|---|
| 用户上下文 | [common/context/user_context.py](../../common/context/user_context.py) + [user_context_middleware.py](../../common/middleware/user_context_middleware.py) | UserContext/LoginUser（contextvars）+ 中间件（P4 已实现，P7 仅升级为 token 校验后填充） |
| 雪花 ID | [common/util/snowflake.py](../../common/util/snowflake.py) | **distributedid 销案**：CustomIdentifierGenerator 等价物已交付 |
| RAG 链路追踪 | [rag/service/stream/trace_runner.py](../../rag/service/stream/trace_runner.py) + trace_service/trace_dao | RagTraceContext（traceId/nodeStack）语义已由 rag 域承载，common/tracing 空壳**归并销案** |
| Redis | [storage/cache/client.py](../../storage/cache/client.py) | CacheManager 抽象 + Redis 实现（token 会话、幂等锁的存储底座） |
| DAO 模式 | [rag/dao/](../../rag/dao/) + [storage/database/schema.py](../../storage/database/schema.py) | 21 表已定义；Controller-Service-DAO 分层与 Result 包装模式成熟（P4/P5 范式） |
| 幂等（业务级） | [rag/service/idempotent.py](../../rag/service/idempotent.py) | 消息幂等已有（M6），P7 补提交幂等通用件 |

**缺口确认**：`t_user` / `t_biz_change_log` 两表未建；认证/用户/审计/大盘四域全部未开始。

**测试基线**：新测试体系 **189 passed**（2026-08-22 重建后，TDD 先行）。

---

## 2. 关键决策记录

| # | 决策 | 理由 |
|---|---|---|
| D1 | **认证：自研轻量 token（opaque uuid + Redis TTL）替代 Sa-Token** | Sa-Token 是 Java 框架；Python 侧 Redis CacheManager 已就绪，opaque token 语义与 StpUtil.login 等价（服务端会话、可登出、可续期），无 JWT 签名密钥管理负担；缺 Redis 时回落内存会话（与 cache 兜底同模式） |
| D2 | **认证默认关闭（`RAGENT_AUTH_ENABLED=0`），开启后覆盖 X-User-Id 直填语义** | 不破坏既有 189 例测试与 anonymous 兜底（P4 决策 D3 的延续）；开启后 middleware 校验 `Authorization: Bearer <token>` → Redis 查会话 → 填充 UserContext |
| D3 | **密码存储：PBKDF2-SHA256（标准库 hashlib，格式 `pbkdf2$<iter>$<salt>$<hash>`）；无前缀的存量值按明文比对** | Java 原样是明文（passwordMatches 直接 equals），Python 侧以哈希为默认、明文为兼容层：新用户必然哈希，与 Java 共库时不炸登录 |
| D4 | **审计上下文：contextvars 上下文管理器 + 显式 put(bizId, before, after)，替代 mzt-biz-log 的 AOP + SpEL 模板** | Python 无无侵入 AOP；快照在服务层变更前后显式声明，语义与 LogRecordContext.putVariable 等价且可调试；装饰器 `@record_biz_change` 只负责落库与异常兜底 |
| D5 | **幂等：`@idempotent_submit` 装饰器（async 兼容）+ Redis SET NX EX；key = (路由, 用户, 参数摘要)** | 对齐 IdempotentSubmitAspect 的 key 构造（servlet path + 用户 + 参数 MD5）；SpEL 用 key 提取函数替代；重复提交抛 ClientException（A000409 语义） |
| D6 | **mq（RocketMQ 消息化）：显式不执行** | P5 分块消费已由进程内 dispatcher 承载且 P6 压测未暴露正确性缺口；RocketMQ-Python 生态弱于 Java 客户端，消息化属部署架构升级，按需另立项 |
| D7 | **distributedid / tracing：直接销案** | snowflake.py（P4）与 trace_runner.py（P4）已等价覆盖；差距文档 §4 对应行刷新 |
| D8 | **认证库选型优化点：fastapi-users 不引入（当前）；后续按需切换** | 见 [p7-auth-optimization-fastapi-users.md](p7-auth-optimization-fastapi-users.md)：fastapi-users 默认 JWT 与服务端主动登出（Sa-Token 语义）冲突，需自定义 Backend 改造大半；引入会绑定 ORM 范式并新增依赖。当前自研方案（D1/D2）零依赖且语义一致。登记为优化点，出现忘记密码/无状态 JWT/多认证源需求时重评 |

---

## 3. 任务分解

### 3.1 U 组：认证与用户域（对标 `user/` 20 文件）

| # | 任务 | Java 对齐 | Python 落点 | 依赖 |
|---|---|---|---|---|
| U1 | 用户表 + DO/DAO | `UserDO` + `UserMapper` + t_user | ✅ [schema.py](../../storage/database/schema.py) 追加 t_user（8 列 + deleted 软删，入 DEFAULT_TABLES）+ [user/dao/user_dao.py](../../user/dao/user_dao.py)（insert 查重抛 ClientException / find 软删过滤 / list_page / update / 软删 delete）+ [user/enums.py](../../user/enums.py)（UserRole），11 例单测绿 | — |
| U2 | 会话管理器 | Sa-Token StpUtil（login/logout/token 校验） | ✅ [user/service/session_manager.py](../../user/service/session_manager.py)（async opaque token `ragent_` 前缀 + CacheManager 会话（Redis TTL 7 天 / 内存兜底）+ login/resolve/logout 服务端主动失效），10 例单测绿 | U1 |
| U3 | 密码工具 | `passwordMatches`（明文） | ✅ [user/service/password.py](../../user/service/password.py)（PBKDF2-SHA256 哈希 `pbkdf2$<iter>$<salt>$<hash>` + hmac 常量时间校验 + 明文兼容层，9 例单测绿） | — |
| U4 | Auth 端点 | `AuthController`（login/logout） | ✅ [user/controller/auth_controller.py](../../user/controller/auth_controller.py)（`POST /auth/login` → LoginVO camelCase + `POST /auth/logout` Bearer 解析）+ [user/service/auth_service.py](../../user/service/auth_service.py)（组合 UserDao+SessionManager+password）+ [request.py/vo.py](../../user/controller/request.py) + wiring `_wire_auth_services`（双 profile）+ factory 挂载，15 例单测绿 | U2/U3 |
| U5 | 用户管理端点 | `UserController` 六端点 | ✅ [user/controller/user_controller.py](../../user/controller/user_controller.py)（me/分页/创建/更新/删除/改密 + `@require_role("admin")` 门禁）+ [user/service/user_service.py](../../user/service/user_service.py)（默认 admin 保护/角色归一/查重排除自身/雪花 id）+ wiring `user_service` + factory 挂载，32 例单测绿 | U2 |
| U6 | 认证中间件接线 | `SaTokenConfig` + `UserContextInterceptor` | ✅ [user_context_middleware.py](../../common/middleware/user_context_middleware.py) 双模式（D2）：`auth_enabled=False`（默认）X-User-Id 直填现状不变；`=True` Bearer token → 会话 → UserContext（含 role/avatar，覆盖 X-User-Id）；`RAGENT_AUTH_ENABLED` env + [config.py](../../app/config.py) auth_enabled + factory 注入 + 延迟取容器 session_manager，14 例单测绿 | U2 |
| U7 | 角色权限 | `StpInterfaceImpl` + `UserRole` | ✅ [user/security.py](../../user/security.py) `@require_role(role)` 装饰器（UserContext.get_role → 不满足抛 ClientException，对齐 StpUtil.checkRole）+ [user/enums.py](../../user/enums.py)（UserRole），随 U5 端点验证 | U6 |

### 3.2 A 组：审计日志域（对标 `audit/` 12 文件）

| # | 任务 | Java 对齐 | Python 落点 | 依赖 |
|---|---|---|---|---|
| A1 | 审计表 + DO/DAO | `BizChangeLogDO` + t_biz_change_log | ✅ [schema.py](../../storage/database/schema.py) 追加 t_biz_change_log（18 列：biz/快照 TEXT/operator 三元组/success/ip/userAgent，入 DEFAULT_TABLES）+ [audit/dao/change_log_dao.py](../../audit/dao/change_log_dao.py)（insert/find/list_page 过滤 bizType+operation+operator+success+时间窗/create_time 倒序/count）；Condition 新增 gte（client+postgres），8 例单测绿 | — |
| A2 | 审计上下文 + 记录服务 | `BizChangeLogContext` + `BizChangeLogRecordService` | ✅ [audit/support/context.py](../../audit/support/context.py)（contextvars put/put_name/skip/clear + 字段级 diff 计算：JSON Pointer 转义/对象/数组/叶子）+ [audit/service/record_service.py](../../audit/service/record_service.py)（落库 + 快照 JSON 序列化 + 操作人回落 SYSTEM + 列长截断）+ [audit/service/operator_service.py](../../audit/service/operator_service.py)（UserContext 取操作人），15 例单测绿 | A1 |
| A3 | 装饰器落库 | mzt-biz-log AOP | ✅ [audit/support/decorator.py](../../audit/support/decorator.py)（`@record_biz_change(biz_type, operation, desc)` async/sync 双兼容——成功后从 BizChangeLogContext 取快照落库（success=1），失败落 errorMessage（desc+异常）并**原样重抛不吞异常**，skip 则跳过，finally 清理上下文；操作人经 UserContextOperatorService 提取回落 SYSTEM；`set_record_service()` 由宿主注入，未注册旁路降级不打断业务；class/method 名自动解析）+ [tests/test_audit_record_unit.py](../../tests/test_audit_record_unit.py)，13 例单测绿 | A2 |
| A4 | 查询端点 | `BizChangeLogController` 两端点 | ✅ [audit/controller/change_log_controller.py](../../audit/controller/change_log_controller.py)（`GET /biz-change-logs` 分页 + bizType/operationType/operatorId/success/时间窗过滤 + `GET /biz-change-logs/{id}` 详情，未命中抛 ClientException；camelCase 边界转换）+ [audit/service/change_log_query_service.py](../../audit/service/change_log_query_service.py)（page/get，对齐 BizChangeLogServiceImpl）+ wiring `_wire_audit_services` + factory 挂载，8 例单测绿 | A1 |
| A5 | 接入既有写路径 | UserServiceImpl 等调用点 | ✅ 采样接入 3 个代表性写路径（快照语义对齐 Java `bizChangeLogContext.put(bizId, before, after)`）：① 用户 CRUD（U5）——[user/service/user_service.py](../../user/service/user_service.py) create/update/delete 加 `@record_biz_change`（before 空/前后 VO/删除前 VO+null）；② 知识库删除——[knowledge/service/base.py](../../knowledge/service/base.py) `delete`（before=删除前行，after=null）；③ 文档删除（async）——[knowledge/service/document.py](../../knowledge/service/document.py) `delete`（before=删除前行，after=null）；wiring `_wire_audit_services` 增 `set_record_service(BizChangeLogRecordService(...))` 落库注册；`tests/test_audit_write_paths_unit.py` 7 例单测绿（成功快照/失败 errorMessage 不吞/操作人取自 UserContext/两删除路径） | A3 |

### 3.3 D 组：管理大盘（对标 `admin/` 10 文件）

| # | 任务 | Java 对齐 | Python 落点 | 依赖 |
|---|---|---|---|---|
| D1 | Overview 端点 | `loadOverview`：总量/窗口增量/环比 KPI | ✅ [admin/service/dashboard_service.py](../../admin/service/dashboard_service.py) + [admin/controller/dashboard_controller.py](../../admin/controller/dashboard_controller.py)：`GET /admin/dashboard/overview?window=24h`（六 KPI：总用户/活跃用户/总会话/窗口会话/总消息/窗口消息，含环比）；窗口解析 h/d 后缀/非法回落/`prev_` 环比标签，默认窗口标签写作 `24h`；wiring `_wire_dashboard_services` 装配 + factory 挂载；`tests/test_dashboard_service_unit.py` TestOverview + `tests/test_dashboard_controller_unit.py` TestOverviewEndpoint 全绿 | U1（user 表） |
| D2 | Performance 端点 | `loadPerformance`：P95/平均/成功率/慢查询 | ✅ `GET /admin/dashboard/performance`：trace_run 聚合（avg/p95 延迟、SUCCESS/ERROR 计数、慢阈值 20s、无文档回答率）；`tests/test_dashboard_service_unit.py` TestPerformance + `tests/test_dashboard_controller_unit.py` TestPerformanceEndpoint 全绿 | — |
| D3 | Trends 端点 | `loadTrends`：day/hour 粒度序列 | ✅ `GET /admin/dashboard/trends?granularity=day|hour&window=7d|24h`（会话/消息/活跃用户/平均延迟/质量【错误率+无知识率】序列），默认粒度解析（<=48h→hour 否则 day）、小时窗对齐起点；`tests/test_dashboard_service_unit.py` TestTrends + `tests/test_dashboard_controller_unit.py` TestTrendsEndpoint 全绿 | — |

### 3.4 F 组：framework 补齐

| # | 任务 | Java 对齐 | Python 落点 | 依赖 |
|---|---|---|---|---|
| F1 | 提交幂等装饰器 | `@IdempotentSubmit` + 切面 | ✅ [common/idempotent/submit.py](../../common/idempotent/submit.py)：`@idempotent_submit` async/sync 双兼容装饰器（asyncio.iscoroutinefunction 分流）+ key/key_fn/message/ttl + 显式 key 与默认「签名+参数 md5」双分支（对齐 buildLockKey）；async 复用 [rag/service/idempotent.py](../../rag/service/idempotent.py) IdempotentSubmitGuard（CacheManager get+set 模拟 setnx/TTL），sync 走进程内 threading 非阻塞 tryAcquire（对齐 RLock.tryLock）；全局 set_guard/get_guard 注册槽 + 内存兜底；重复抛 ClientException；`tests/test_idempotent_submit_unit.py` 12 例绿（并发拦截/异常释放/不同 key 不互斥/key_fn 提取/兜底） | — |
| F2 | 幂等接线采样 | 调用点 | ✅ ① 用户创建——[user/service/user_service.py](../../user/service/user_service.py) create 叠加 `@idempotent_submit(key_fn=_user_create_submit_key)`（外层先拦、不触发失败审计；key=user:create:{username}）；② KB 创建——[knowledge/service/base.py](../../knowledge/service/base.py) create 叠加 `@idempotent_submit(key_fn=_kb_create_submit_key)`（key=kb:create:{name}）；wiring `_wire_idempotent_framework` 把容器级 idempotent_guard 注入全局槽（memory/real 均装配）；`tests/test_idempotent_wiring_unit.py` 5 例绿（同名预占拦截/释放放行/不同名不互斥，user + KB） | F1 |
| F3 | distributedid/tracing/mq 销案登记 | — | ✅ [差距文档 §4](../ragent-porting-gap-analysis.md)：distributedid ✅（snowflake.py）、idempotent ✅（common/idempotent/submit.py）、trace ✅（trace_runner.py 归并销案）、mq ⛔（D6 显式放弃，附理由） | — |

---

## 4. 测试保障

**TDD 先行**（延续新测试体系，当前基线 189 passed）：
- `tests/test_user_dao_unit.py` / `test_user_session_unit.py` / `test_user_password_unit.py`
- `tests/test_auth_controller_unit.py` / `test_user_controller_unit.py`（登录/登出/CRUD/改密/角色门禁/认证开关双模式）
- `tests/test_audit_context_unit.py` / `test_audit_record_unit.py`（diff 计算/装饰器成功失败两径/上下文隔离）
- `tests/test_change_log_controller_unit.py`（分页过滤）
- `tests/test_dashboard_service_unit.py`（KPI 环比/窗口解析/P95/趋势粒度，造数用 InMemory DAO）
- `tests/test_idempotent_submit_unit.py`（首次放行/重复拦截/异常释放锁/内存兜底）

**流程保障**：每步完成后跑全量 `tests/` 确保基线只增不减；调试脚本随手删除（用户规则）。

---

## 5. 验收标准

- [x] `POST /auth/login`（有效/无效凭据/登出后 token 失效）与六用户端点全通，ADMIN 门禁生效
- [x] 认证开关双模式：`RAGENT_AUTH_ENABLED=0` 时既有行为（X-User-Id 匿名兜底）不变；`=1` 时 Bearer token 必经会话校验
- [x] 审计：用户 CRUD / KB 删除 / 文档删除三类写路径产出含 before/after/diff 的变更记录；查询端点分页过滤可用
- [x] 大盘三端点返回 KPI（含环比）、P95/成功率、day/hour 趋势序列
- [x] `@idempotent_submit` 重复提交被拦截且不残留锁
- [x] 差距文档 §4/§7.1/§9 的 P7 相关行销案或标注显式放弃（mq）
- [x] 全量回归基线只增不减（≥189 + 本期新增，收官 **363 passed**）

---

## 6. 里程碑与执行顺序

| 里程碑 | 内容 | 出口 |
|---|---|---|
| M1 | U 组：t_user + 会话/密码 + Auth/User 端点 + 中间件接线 + 角色门禁 | 认证双模式全绿 |
| M2 | A 组：t_biz_change_log + 上下文/记录/装饰器 + 查询端点 + 3 写路径接入 | 审计链路全绿 |
| M3 | D 组：大盘三端点（overview/performance/trends） | 造数断言全绿 |
| M4 | F 组：幂等装饰器 + 接线 + framework 销案登记 | 幂等语义全绿 |
| M5 | 全量回归 + 差距文档销案 + 计划文档收官记录 | ✅ **已完成（收官 **363 passed**，P7 销案）** |

> 执行顺序依赖：M1 先行（user 表是大盘 KPI 依赖）；M2/M3/F1 相互独立可并行推进。

---

## 7. 维护说明

- 本文档与代码同步演进：每完成一个 # 项将状态改为 ✅ 并注明落点；
- 状态标记规则：❌ 未开始 / 🚧 进行中 / ✅ 已完成（附测试通过）/ ⛔ 显式放弃（附理由）；
- 与 [ragent-porting-gap-analysis.md](../ragent-porting-gap-analysis.md) §9 联动：P7 销案时同步更新差距文档；
- 与 [p1-chunk-parser-completeness-plan.md](p1-chunk-parser-completeness-plan.md) 同风格维护收官记录。

---

## 8. P7 收官记录（2026-08-23）

**里程碑关闭声明**：P7（平台化：认证用户 + 审计日志 + 管理大盘 + framework 补齐）全部交付并销案。

**交付汇总**：

| 里程碑 | 交付物 | 测试 |
|---|---|---|
| M1（U 组） | [user/](../../user/)（t_user + UserDAO + 会话管理 + PBKDF2 密码 + Auth/User 六端点 + ADMIN 门禁）+ [user_context_middleware.py](../../common/middleware/user_context_middleware.py)（认证开关双模式）+ [config.py](../../app/config.py) auth_enabled | user 域单测（test_user_dao/session/password/auth_controller/user_controller_unit） |
| M2（A 组） | [audit/](../../audit/)（t_biz_change_log + 上下文/记录服务 + @record_biz_change + 查询端点 + 用户 CRUD/KB 删除/文档删除 3 写路径） | audit 域单测（test_audit_context/record/change_log_controller/write_paths_unit），A5 收官累计 330 passed |
| M3（D 组） | [admin/service/dashboard_service.py](../../admin/service/dashboard_service.py) + [admin/controller/dashboard_controller.py](../../admin/controller/dashboard_controller.py)（overview/performance/trends 三端点，camelCase 边界） | test_dashboard_service/controller_unit，D 组收官累计 346 passed |
| M4（F 组） | [common/idempotent/submit.py](../../common/idempotent/submit.py)（@idempotent_submit async/sync 双兼容 + key/key_fn）+ 接线（[user_service.py](../../user/service/user_service.py) 用户创建 / [base.py](../../knowledge/service/base.py) KB 创建）+ wiring `_wire_idempotent_framework` | test_idempotent_submit/wiring_unit，F 组收官累计 363 passed |
| M5 | 全量回归 + 差距文档销案 + 计划文档收官记录 | **363 passed** |

**附带改动**：
- [storage/schema](../../storage/database/schema.py) 新增 t_user / t_biz_change_log 两表（DEFAULT_TABLES）
- [wiring.py](../../app/wiring.py) 新增 `_wire_auth_services` / `_wire_audit_services` / `_wire_dashboard_services` / `_wire_idempotent_framework`
- [factory.py](../../app/factory.py) 挂载 auth/user/change_log/dashboard 路由

**偏离说明**：
- mq（RocketMQ 消息化）⛔ 显式放弃（D6）：P5 进程内 dispatcher 等价承载、P6 压测未暴露缺口，消息化属部署架构升级另立项
- distributedid / tracing 归并销案（D7）：snowflake.py 与 trace_runner.py 已等价覆盖，common/tracing 空壳不再补
- framework 少量未补：database 自动填充 / common/security 空壳 / config 自动装配 / RedisKeySerializer（非平台化正确性缺口，登记后续）
- 大盘聚合面向 DatabaseClient 抽象（InMemory/Sql 无感知），真实后端延迟/成功率由集成测试覆盖（本机无 PG/Redis 服务）

**遗留（不阻塞）**：P8（mcp-server 真实服务 + agent/ + evaluation/）为下一候选；infra-ai 剩余 provider 客户端按需补。
