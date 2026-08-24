# docker/mcp.Dockerfile —— 独立 MCP Server 轻量镜像（对齐 ragent_mcp/server/main.py 入口与 port 9099）
FROM python:3.11-slim

WORKDIR /app

COPY requirements-mcp.txt ./
RUN pip install --no-cache-dir -r requirements-mcp.txt

COPY ragent_mcp ./ragent_mcp

EXPOSE 9099

CMD ["python", "-m", "ragent_mcp.server.main"]
