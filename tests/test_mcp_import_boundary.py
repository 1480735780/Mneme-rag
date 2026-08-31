# -*- coding: utf-8 -*-
"""
B3/D7 import 边界自动检查：ragent_mcp/server/ 源文件不得 import rag/app/core 任何模块

Java 靠 Maven 模块物理隔离（编译期保证）；Python 无此保证，一个手滑 from rag.xxx import
隔离即破。本测试用 ast 解析 ragent_mcp/server/**/*.py 的 import 语句，断言顶层模块非
rag/app/core，把物理隔离翻译为可断言的约束（注释/字符串内的引用天然被 ast 排除）。

边界范围 = server/（独立部署的 MCP Server 进程）；ragent_mcp/client.py 是主应用侧协议层
（产出 rag.mcp 编排层模型，依赖 rag.mcp.model 属设计内），不在独立部署边界内。
"""
import ast
from pathlib import Path

# 禁止依赖的顶层模块（mcp-server 独立部署边界）
_FORBIDDEN_ROOTS = ("rag", "app", "core")

_RAGENT_MCP_SERVER = Path(__file__).resolve().parent.parent / "ragent_mcp" / "server"


def _python_files() -> list:
    return sorted(p for p in _RAGENT_MCP_SERVER.rglob("*.py") if "__pycache__" not in str(p))


def _imported_roots(tree: ast.AST) -> set:
    """收集模块顶层名：import a.b / from a.b import c → 'a'"""
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


class TestImportBoundary:
    def test_all_files_covered(self):
        files = _python_files()
        assert files, "未扫描到 ragent_mcp/server 源文件"
        # main.py / weather.py 应已存在（M1' 交付）；database/search 为 M2' 占位
        names = {p.name for p in files}
        assert "weather.py" in names
        assert "main.py" in names

    def test_no_forbidden_imports(self):
        violations = []
        for path in _python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            forbidden = _imported_roots(tree) & set(_FORBIDDEN_ROOTS)
            if forbidden:
                violations.append(f"{path.name}: {sorted(forbidden)}")
        assert not violations, f"ragent_mcp 越界依赖（独立部署边界被破坏）:\n" + "\n".join(violations)
