"""
storage.database - 关系库访问抽象

    - client：DatabaseClient 抽象（查询/批查/建表）+ Condition + InMemoryDatabaseClient（进程内假实现）
    - schema：表结构规格（ColumnSpec / TableSchema / DEFAULT_TABLES，对齐 t_* DO 语义）
    - executor：原始 SQL 执行器（SqlExecutor + RecordingSqlExecutor + SqlAlchemySqlExecutor，对应 JdbcTemplate）
    - postgres：关系库 SQL 实现（SqlDatabaseClient，方言可注入，Postgres 默认，对应 BaseMapper）

对应 ragent 源码：
    - rag/dao/mapper/AgentProfileMapper 等（MyBatis-Plus BaseMapper<DO>）
    - knowledge/dao/mapper/KnowledgeBaseMapper
    - org.springframework.jdbc.core.JdbcTemplate（PgVector 三件套）
"""
from storage.database.client import (
    Condition,
    DatabaseClient,
    InMemoryDatabaseClient,
    Row,
)
from storage.database.executor import (
    RecordingSqlExecutor,
    SqlAlchemySqlExecutor,
    SqlExecutor,
)
from storage.database.postgres import SqlDatabaseClient
from storage.database.schema import (
    DEFAULT_TABLES,
    ColumnSpec,
    TableSchema,
)

__all__ = [
    "Condition",
    "DatabaseClient",
    "InMemoryDatabaseClient",
    "Row",
    "ColumnSpec",
    "TableSchema",
    "DEFAULT_TABLES",
    "SqlExecutor",
    "RecordingSqlExecutor",
    "SqlAlchemySqlExecutor",
    "SqlDatabaseClient",
]
