# Mneme-rag 后端镜像：FastAPI + uvicorn
# 构建上下文 = 仓库根目录（mneme-rag/）；与 docker/app.compose.yml 的 build.context 对应
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖以利用 Docker 层缓存
COPY requirements.txt ./
RUN pip install -r requirements.txt

# 拷贝应用源码（.dockerignore 已排除前端/文档/测试等）
COPY . .

EXPOSE 8000

# 对齐 app/main.py：uvicorn 启动（env 由 compose 注入 RAGENT_*）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
