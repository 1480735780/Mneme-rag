# M5 T4 前端镜像：多阶段构建（node 构建 → nginx 托管）
# 后端容器名对齐 docker-compose 约定：backend:8000（见 nginx.conf proxy_pass）
FROM node:22-alpine AS build
WORKDIR /app

# 先装依赖以利用 Docker 层缓存
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

# 轻量健康检查（nginx 对任意路径返回 200 或 SPA index.html）
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1
