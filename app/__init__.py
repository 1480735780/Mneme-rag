"""
app - 应用装配层（对应 ragent bootstrap）

    - config：AppSettings（env 驱动的运行配置）
    - wiring：AppContainer（memory/real 双 profile 装配）
    - factory：create_app（lifespan + 中间件 + 异常处理器 + /health）
    - main：uvicorn 启动入口
"""
from app.factory import create_app

__all__ = ["create_app"]
