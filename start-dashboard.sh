#!/usr/bin/env bash
# 启动 mneme-rag 的 Understand-Anything 知识图谱 Dashboard。
# 用法：
#   bash start-dashboard.sh            # 启动并自动打开浏览器
#   bash start-dashboard.sh --no-open  # 启动但不打开浏览器
# 停止：在终端按 Ctrl+C。
set -euo pipefail

# 项目根目录（本脚本位于项目根）
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

# understand-anything 插件根目录，可用环境变量 UA_PLUGIN_ROOT 覆盖
PLUGIN_ROOT="${UA_PLUGIN_ROOT:-C:/Users/陆hongyi/.agents/skills/understand-anything/understand-anything-plugin}"

AUTO_OPEN=1
if [ "${1:-}" = "--no-open" ]; then
  AUTO_OPEN=0
fi

# 数据目录：优先旧的 .understand-anything/，否则 .ua/
UA_DIR="$PROJECT_DIR/$([ -d "$PROJECT_DIR/.understand-anything" ] && echo .understand-anything || echo .ua)"
if [ ! -f "$UA_DIR/knowledge-graph.json" ]; then
  echo "未找到知识图谱：$UA_DIR/knowledge-graph.json"
  echo "请先运行 /understand 分析本项目。"
  exit 1
fi

DASHBOARD_DIR="$PLUGIN_ROOT/packages/dashboard"
if [ ! -d "$DASHBOARD_DIR" ]; then
  echo "未找到 dashboard 目录：$DASHBOARD_DIR"
  echo "（可通过环境变量 UA_PLUGIN_ROOT 指定插件根目录）"
  exit 1
fi

# 依赖缺失时先安装
if [ ! -x "$DASHBOARD_DIR/node_modules/.bin/vite" ]; then
  echo "首次运行：安装 dashboard 依赖…"
  (cd "$DASHBOARD_DIR" && (pnpm install --frozen-lockfile 2>/dev/null || pnpm install))
fi

LOG_FILE="$UA_DIR/dashboard.log"
: > "$LOG_FILE"
echo "启动 Dashboard（日志：$LOG_FILE）…"

(cd "$DASHBOARD_DIR" && GRAPH_DIR="$PROJECT_DIR" npx vite --host 127.0.0.1) >"$LOG_FILE" 2>&1 &
VITE_PID=$!

cleanup() { kill "$VITE_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

# 等待带 token 的访问地址出现（最多 60 秒）
URL=""
for _ in $(seq 1 60); do
  URL="$(grep -o 'http://127\.0\.0\.1:[0-9]*/?token=[a-f0-9]*' "$LOG_FILE" | head -1 || true)"
  [ -n "$URL" ] && break
  if ! kill -0 "$VITE_PID" 2>/dev/null; then
    echo "Dashboard 进程异常退出："
    tail -20 "$LOG_FILE"
    exit 1
  fi
  sleep 1
done

if [ -z "$URL" ]; then
  echo "未能获取 Dashboard URL，日志末尾："
  tail -20 "$LOG_FILE"
  exit 1
fi

echo ""
echo "🔑  Dashboard URL: $URL"
echo "    Viewing: $UA_DIR/knowledge-graph.json"
echo "    按 Ctrl+C 停止"
echo ""

if [ "$AUTO_OPEN" = 1 ]; then
  cmd //c start "" "$URL" 2>/dev/null || true
fi

wait "$VITE_PID"
