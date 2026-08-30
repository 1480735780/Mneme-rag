# -*- coding: utf-8 -*-
"""
mneme-rag 一键启动脚本
用法：
    python start.py          # 启动后端 + 前端（dev 模式）
    python start.py --no-frontend   # 只启动后端
"""
import os
import subprocess
import sys
import time
import signal
from pathlib import Path

ROOT = Path(__file__).parent
FRONTEND = ROOT / "frontend"
PROCESSES: list[subprocess.Popen] = []


def check_env() -> None:
    """检查关键环境变量，缺失时给出提示但不阻塞（部分功能可降级运行）"""
    warnings = []
    if not os.environ.get("DASHSCOPE_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        warnings.append("  ⚠ 未设置 DASHSCOPE_API_KEY 或 OPENAI_API_KEY，LLM 调用将失败")
    if not os.environ.get("RAGENT_DATABASE_URL"):
        print("  ℹ RAGENT_DATABASE_URL 未设置，使用默认 postgresql://postgres:postgres@localhost:5432/ragent")
    for w in warnings:
        print(w)


def start_backend() -> subprocess.Popen:
    print("\n🚀 启动后端 (FastAPI :8000)...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=str(ROOT),
    )
    return proc


def start_frontend() -> subprocess.Popen:
    if not FRONTEND.exists():
        print("  ⚠ frontend/ 目录不存在，跳过前端")
        return None
    node_modules = FRONTEND / "node_modules"
    if not node_modules.exists():
        print("  📦 安装前端依赖...")
        subprocess.run(["npm", "install"], cwd=str(FRONTEND), check=True)
    print("\n🎨 启动前端 (Vite :5173)...")
    npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
    proc = subprocess.Popen(
        [npm_cmd, "run", "dev"],
        cwd=str(FRONTEND),
    )
    return proc


def shutdown(*_: object) -> None:
    print("\n\n🛑 正在停止所有服务...")
    for p in PROCESSES:
        if p and p.poll() is None:
            p.terminate()
    for p in PROCESSES:
        if p and p.poll() is None:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("=" * 50)
    print("  Mneme-RAG 一键启动")
    print("=" * 50)
    check_env()

    backend = start_backend()
    PROCESSES.append(backend)

    frontend = None
    if "--no-frontend" not in sys.argv:
        frontend = start_frontend()
        PROCESSES.append(frontend)

    print("\n" + "=" * 50)
    print("  ✅ 服务已启动")
    print("  后端 API: http://localhost:8000/docs")
    if frontend:
        print("  前端页面: http://localhost:5173")
    print("  按 Ctrl+C 停止全部服务")
    print("=" * 50)

    # 等待任一进程退出则整体关闭
    try:
        while True:
            for p in PROCESSES:
                if p and p.poll() is not None:
                    print(f"\n⚠ 进程 {p.args} 已退出（code={p.returncode}），正在关闭全部服务...")
                    shutdown()
            time.sleep(2)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()