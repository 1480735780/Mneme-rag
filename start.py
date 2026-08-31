# -*- coding: utf-8 -*-
"""
mneme-rag 一键启动脚本（v1.1 交付形态）

用法：
    python start.py                  # 启动后端 + 前端（dev 模式）
    python start.py --no-frontend   # 只启动后端
    python start.py --no-agent      # 显式退回 workflow 引擎（v1 编排管线）
    python start.py --clean         # 启动前清掉占用 8000/5173 的残留进程
    python start.py --model <name>  # 指定 ollama 模型（默认 qwen2.5:3b）

默认开发环境（对齐 v1.1 P2/P3 实测配置，均可用同名环境变量覆盖）：
    - 引擎：RAGENT_ENGINE_TYPE 默认 agent（v2 ReAct）；--no-agent 显式 workflow
    - Agent 模型：本地 ollama qwen2.5:3b（RAGENT_AGENT_PROVIDER / RAGENT_AGENT_MODEL 可覆盖）
    - 初始管理员：admin / admin123（RAGENT_INIT_ADMIN_USERNAME / PASSWORD 可覆盖，幂等播种）
    - NO_PROXY：本机带系统代理时 localhost 会被劫持（ollama 调用 502、SSE 测试假失败），
      启动前对 localhost/127.0.0.1/::1 强制直连，外部 API 调用不受影响
"""
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(ROOT, "frontend")
PROCESSES: list = []

BACKEND_PORT = 8000
FRONTEND_PORT = 5173


def _setup_env(args) -> None:
    """装配开发默认环境（用户显式设置的 env 优先）。"""
    proxied = {"http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
               "all_proxy", "ALL_PROXY", "no_proxy", "NO_PROXY"}
    saved = {k: os.environ.get(k) for k in proxied}
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
    os.environ["no_proxy"] = "localhost,127.0.0.1,::1"
    for key in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        os.environ.pop(key, None)  # 系统代理只该走外部，本地必须直连
    os.environ.update({k: v for k, v in saved.items() if k in ("no_proxy", "NO_PROXY") and v})

    if not args.no_agent:
        # 决策 3B：默认已是 agent；provider/model 给出开发默认（未显式设置时）
        os.environ.setdefault("RAGENT_ENGINE_TYPE", "agent")
        os.environ.setdefault("RAGENT_AGENT_PROVIDER", "ollama")
        os.environ.setdefault("RAGENT_AGENT_MODEL", args.model)
    else:
        os.environ["RAGENT_ENGINE_TYPE"] = "workflow"
        os.environ.pop("RAGENT_AGENT_PROVIDER", None)
        os.environ.pop("RAGENT_AGENT_MODEL", None)

    os.environ.setdefault("RAGENT_INIT_ADMIN_USERNAME", "admin")
    os.environ.setdefault("RAGENT_INIT_ADMIN_PASSWORD", "admin123")


def _free_port(port: int) -> None:
    """清掉占用端口的残留进程（Windows: netstat+taskkill；POSIX: lsof+kill）。

    中文 Windows 的 netstat/taskkill 输出为 GBK：显式 errors=replace 防解码崩溃。
    """
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["netstat", "-ano"], capture_output=True,
                text=True, encoding="utf-8", errors="replace",
            ).stdout or ""
            pids = {
                line.split()[-1]
                for line in out.splitlines()
                if f":{port}" in line and "LISTENING" in line
            }
            for pid in pids:
                subprocess.run(
                    ["taskkill", "/PID", pid, "/F"], capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                )
        else:
            out = subprocess.run(
                ["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True,
                text=True, encoding="utf-8", errors="replace",
            ).stdout or ""
            for pid in out.split():
                subprocess.run(["kill", "-9", pid], capture_output=True)
    except Exception as exc:  # noqa: BLE001 —— 清理失败不阻断启动，仅提示
        print(f"  ⚠ 释放端口 {port} 失败：{exc}（可手动处理残留进程后重试）")


def _probe(url: str, timeout: float = 2.0) -> bool:
    """探活（NO_PROXY 已在 env 中，urllib 直连 localhost）。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def _wait_ready(url: str, label: str, seconds: int = 60) -> bool:
    print(f"  ⏳ 等待{label}就绪：{url}")
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _probe(url):
            print(f"  ✅ {label}已就绪")
            return True
        time.sleep(2)
    print(f"  ⚠ {label}在 {seconds}s 内未就绪（查看上方日志排查）")
    return False


def _check_prereqs(args) -> None:
    """关键依赖检查：agent 引擎默认走本地 ollama，不可达时提前告知。"""
    provider = os.environ.get("RAGENT_AGENT_PROVIDER", "")
    if os.environ.get("RAGENT_ENGINE_TYPE") == "agent" and provider == "ollama":
        model = os.environ.get("RAGENT_AGENT_MODEL", "")
        if _probe("http://127.0.0.1:11434/api/tags"):
            print(f"  ℹ ollama 可达，Agent 模型：{model}")
        else:
            print("  ⚠ ollama 不可达（127.0.0.1:11434）：Agent 对话将报错；"
                  "可先 `ollama serve` + `ollama pull <model>`，或换云端 provider")
    elif os.environ.get("RAGENT_ENGINE_TYPE") == "agent":
        print(f"  ℹ Agent provider：{provider}（需保证 ai.yaml 中该 provider 的 api_key 可解析）")


def start_backend() -> subprocess.Popen:
    print(f"\n🚀 启动后端 (FastAPI :{BACKEND_PORT})，引擎={os.environ.get('RAGENT_ENGINE_TYPE', 'agent（默认）')}...")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(BACKEND_PORT)],
        cwd=ROOT,
    )


def start_frontend() -> subprocess.Popen | None:
    if not os.path.isdir(FRONTEND):
        print("  ⚠ frontend/ 目录不存在，跳过前端")
        return None
    if not os.path.isdir(os.path.join(FRONTEND, "node_modules")):
        print("  📦 安装前端依赖（首次较慢）...")
        subprocess.run(["npm", "install"], cwd=FRONTEND, check=True)
    print(f"\n🎨 启动前端 (Vite :{FRONTEND_PORT})...")
    npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
    return subprocess.Popen([npm_cmd, "run", "dev"], cwd=FRONTEND)


def shutdown(*_: object) -> None:
    print("\n\n🛑 正在停止所有服务...")
    for proc in PROCESSES:
        if proc and proc.poll() is None:
            proc.terminate()
    for proc in PROCESSES:
        if proc and proc.poll() is None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    sys.exit(0)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="mneme-rag 一键启动")
    parser.add_argument("--no-frontend", action="store_true", help="只启动后端")
    parser.add_argument("--no-agent", action="store_true", help="退回 workflow 引擎（默认 agent）")
    parser.add_argument("--clean", action="store_true", help="启动前清掉占用端口的残留进程")
    parser.add_argument("--model", default="qwen2.5:3b", help="ollama 模型名（默认 qwen2.5:3b）")
    args = parser.parse_args()

    import signal

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("=" * 50)
    print("  Mneme-RAG 一键启动（v1.1）")
    print("=" * 50)

    _setup_env(args)
    if args.clean:
        print("🧹 清理残留进程...")
        _free_port(BACKEND_PORT)
        if not args.no_frontend:
            _free_port(FRONTEND_PORT)
    _check_prereqs(args)

    backend = start_backend()
    PROCESSES.append(backend)
    if not _wait_ready(f"http://127.0.0.1:{BACKEND_PORT}/health", "后端"):
        shutdown()

    frontend = None
    if not args.no_frontend:
        frontend = start_frontend()
        PROCESSES.append(frontend)
        _wait_ready(f"http://localhost:{FRONTEND_PORT}/", "前端", seconds=45)

    print("\n" + "=" * 50)
    print("  ✅ 服务已启动")
    print(f"  后端 API : http://localhost:{BACKEND_PORT}/docs")
    if frontend:
        print(f"  前端页面 : http://localhost:{FRONTEND_PORT}")
        print(f"  登录账号 : {os.environ.get('RAGENT_INIT_ADMIN_USERNAME')} / "
              f"{os.environ.get('RAGENT_INIT_ADMIN_PASSWORD')}")
        print("  智能体页 : 登录后左侧导航「智能体」（/agent，Agent 引擎）")
    print("  按 Ctrl+C 停止全部服务")
    print("=" * 50)

    # 等待任一进程退出则整体关闭
    try:
        while True:
            for proc in PROCESSES:
                if proc and proc.poll() is not None:
                    print(f"\n⚠ 进程 {proc.args} 已退出（code={proc.returncode}），正在关闭全部服务...")
                    shutdown()
            time.sleep(2)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
